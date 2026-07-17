"""Test isolation for the run store.

The suite was writing into the operator's REAL archive DB: every run left permanent
`unit-<pid>` / `perf-<pid>` rows behind (229 of 281 rows in one developer's DB were test
junk). That is bad on its own, and it eventually breaks the suite — `list_performance`
is `LIMIT 100`, so once ~100 accumulated `perf-*` rows existed the feedback-loop test
could no longer find its own row and failed permanently, on a machine where nothing was
actually wrong.

`run_store._DB_PATH` resolves `HOB_RUNS_DB` at import time, so the env has to be set
before any test module imports it — conftest is imported first, which is why this lives
here rather than in a fixture.
"""

import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hob-tests-"))

# Set, not setdefault: a developer with these exported in their shell must still get an
# isolated DB rather than silently testing against their own archive.
os.environ["HOB_RUNS_DB"] = str(_TMP / "runs.db")
os.environ["HOB_RUNS_DIR"] = str(_TMP / "runs")
(_TMP / "runs").mkdir(parents=True, exist_ok=True)
