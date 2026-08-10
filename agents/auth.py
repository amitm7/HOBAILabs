"""Operator authentication & identity (Gap #1 — the commercial floor).

Before this, the server had *no* auth: anyone reaching it could run, spend, and
approve, claiming any `operator_id` as a free-text body field. This module gives
the platform a verified actor behind every money/rights action.

Pieces:
- `operators` table (bcrypt-style pbkdf2 hash) on the shared db.py storage layer.
- `POST /login` (wired in web_app) verifies credentials → a signed HS256 JWT.
- `require_operator` decorator validates the token and injects the *verified*
  operator into the handler, so routes stop trusting client-supplied identity.
- Roles gate sensitive actions (e.g. only an `approver` clears the brand gate).
- A small admin CLI seeds operators (no self-signup):
      python -m agents.auth add-operator alice alice@hob.tv --role approver
      python -m agents.auth list

Token signing/verification is a standard HS256 JWT implemented on the stdlib so it
runs with zero extra deps offline; `PyJWT` (a declared requirement) produces and
verifies the identical token shape in production. Secret comes from `HOB_AUTH_SECRET`.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import time

from agents import db

_PBKDF2_ROUNDS = 200_000
_TOKEN_TTL = int(os.environ.get("HOB_AUTH_TTL_SECONDS", str(12 * 3600)))
ROLES = ("operator", "approver", "admin")


# ── secret ─────────────────────────────────────────────────────────────────────
def _secret() -> bytes:
    s = os.environ.get("HOB_AUTH_SECRET", "")
    if not s:
        # Dev fallback: stable within a process so tokens verify, but NOT durable.
        # Production MUST set HOB_AUTH_SECRET (via SSM) so tokens survive restarts.
        s = getattr(_secret, "_dev", None) or secrets.token_hex(32)
        _secret._dev = s  # type: ignore[attr-defined]
    return s.encode()


# ── schema ───────────────────────────────────────────────────────────────────
def _ensure() -> None:
    con = db.connect("auth")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS operators ("
        f"id {db.autoinc_pk()}, "
        f"operator_id TEXT UNIQUE NOT NULL, email TEXT NOT NULL DEFAULT '', "
        f"password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'operator', "
        f"active INTEGER NOT NULL DEFAULT 1, "
        f"created_at BIGINT NOT NULL DEFAULT ({db.now_ts()}))"
    )
    con.commit()


# ── password hashing (pbkdf2-hmac-sha256; bcrypt-swappable) ─────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── operator records ───────────────────────────────────────────────────────────
def add_operator(operator_id: str, email: str = "", password: str = "",
                 role: str = "operator") -> str:
    """Create an operator; returns the plaintext password (generated if omitted)."""
    _ensure()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    password = password or secrets.token_urlsafe(12)
    con = db.connect("auth")
    con.execute(
        db.adapt("INSERT INTO operators(operator_id, email, password_hash, role) VALUES (?, ?, ?, ?)"),
        (operator_id.strip(), email.strip(), hash_password(password), role),
    )
    con.commit()
    return password


def get_operator(operator_id: str) -> dict | None:
    _ensure()
    cur = db.connect("auth").execute(
        db.adapt("SELECT operator_id, email, role, active, password_hash FROM operators WHERE operator_id=?"),
        (operator_id.strip(),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ── token (HS256 JWT) ──────────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(operator_id: str, role: str, *, now: int | None = None) -> str:
    now = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": operator_id, "role": role, "iat": now, "exp": now + _TOKEN_TTL}
    seg = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    sig = hmac.new(_secret(), seg.encode(), hashlib.sha256).digest()
    return f"{seg}.{_b64(sig)}"


def verify_token(token: str, *, now: int | None = None) -> dict | None:
    """Return the validated claims, or None if signature/exp invalid."""
    try:
        seg, sig_b64 = token.rsplit(".", 1)
        expected = hmac.new(_secret(), seg.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig_b64), expected):
            return None
        claims = json.loads(_b64d(seg.split(".", 1)[1]))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    if int(claims.get("exp", 0)) < int(now if now is not None else time.time()):
        return None
    return claims


def authenticate(operator_id: str, password: str) -> str | None:
    """Verify credentials → a signed token, or None on failure."""
    op = get_operator(operator_id)
    if not op or not op.get("active") or not verify_password(password, op["password_hash"]):
        return None
    return issue_token(op["operator_id"], op["role"])


# ── Flask decorator ────────────────────────────────────────────────────────────
def _extract_token(req) -> str:
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return req.cookies.get("hob_token", "")


def guest_allowed() -> bool:
    """Whether unauthenticated visitors may use guest-open routes (HOB_ALLOW_GUEST=1)."""
    return os.environ.get("HOB_ALLOW_GUEST") == "1"


def require_operator(*roles: str, guest: bool = False):
    """Gate a Flask route: valid token required; optionally a role in `roles`.

    Injects the verified claims into `flask.g.operator` and passes
    `operator=<operator_id>` to the handler so it never trusts request-body identity.
    Auth can be disabled for local dev with HOB_AUTH_DISABLED=1 (never in prod).

    `guest=True` marks a route as usable without a login when HOB_ALLOW_GUEST=1 — the
    caller arrives as operator "guest" with role "guest". A real token still wins, so a
    logged-in operator is never downgraded.

    Only put `guest=True` on a route you are willing to expose to anyone who finds the
    URL. Routes that SPEND must additionally enforce a guest ceiling (see
    web_app._guest_budget_left) — an open endpoint onto a paid API is an open wallet,
    and a role check alone does not stop that.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import request, jsonify, g
            if os.environ.get("HOB_AUTH_DISABLED") == "1":
                g.operator = {"sub": "dev", "role": "admin"}
                return fn(*args, operator="dev", **kwargs)
            claims = verify_token(_extract_token(request))
            if not claims and guest and guest_allowed():
                g.operator = {"sub": "guest", "role": "guest"}
                return fn(*args, operator="guest", **kwargs)
            if not claims:
                return jsonify({"error": "authentication required"}), 401
            if roles and claims.get("role") not in roles and claims.get("role") != "admin":
                return jsonify({"error": "forbidden", "need_role": list(roles)}), 403
            g.operator = claims
            return fn(*args, operator=claims["sub"], **kwargs)
        return wrapper
    return decorator


# ── admin CLI ──────────────────────────────────────────────────────────────────
def _cli(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m agents.auth add-operator <id> [email] [--role operator|approver|admin] [--password PW]\n"
              "       python -m agents.auth list")
        return 0
    cmd = argv[0]
    if cmd == "list":
        _ensure()
        for r in db.connect("auth").execute("SELECT operator_id, email, role, active FROM operators ORDER BY operator_id"):
            d = dict(r)
            print(f"{d['operator_id']:<20} {d['role']:<10} {'active' if d['active'] else 'disabled':<9} {d['email']}")
        return 0
    if cmd == "add-operator":
        rest = argv[1:]
        role, password = "operator", ""
        pos = []
        i = 0
        while i < len(rest):
            if rest[i] == "--role":
                role = rest[i + 1]; i += 2
            elif rest[i] == "--password":
                password = rest[i + 1]; i += 2
            else:
                pos.append(rest[i]); i += 1
        if not pos:
            print("error: operator id required"); return 2
        operator_id = pos[0]
        email = pos[1] if len(pos) > 1 else ""
        pw = add_operator(operator_id, email, password, role)
        print(f"created operator '{operator_id}' (role={role}). password: {pw}")
        return 0
    print(f"unknown command: {cmd}"); return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
