# UI Redesign Plan

> **Status (2026-07-03, per docs/L99_EXECUTION_AUDIT.md):** SHIPPED — all 11 items verified (2026-07-03 audit); deferred extras correctly marked.

Professional dark UI shell for Story / Brand / Studio modes.

## Shipped

- [x] Refined dark design tokens + component CSS (`web/static/style.css`)
- [x] Shared Jinja base template (`web/templates/_base.html`)
- [x] Step wizard shell (`web/static/shell.js`)
- [x] Story mode restructure (`web/templates/index.html`)
- [x] Brand + Studio migrate to shared shell
- [x] Horizontal frame cards with collapsible "More" details (`main.js`)
- [x] Preview panel (phone frame + timeline + output)
- [x] Sticky action bar (Preview / Generate / cost chip)
- [x] Settings drawer (AI Credits, IP, Performance)
- [x] Visual progress steps + collapsible technical log
- [x] Docs: `GUIDE.md`, `OPERATOR_GUIDE.html`, `LLD.md`, `CLAUDE.md`

## Deferred

- [ ] Logo asset in header (text wordmark for now)
- [ ] Self-hosted Lucide fallback if CDN blocked
- [ ] Light theme toggle
- [ ] Automated web UI smoke tests
