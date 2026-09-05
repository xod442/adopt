# ADOPT — working notes for Claude

Bulk AOS-CX-to-Mist adoption dashboard. No login (single-purpose ops tool).
Sibling app to opal/opal-mist/holo/focus/vista but standalone — not part of
their shared DESIGN.md app list, though it reuses their style.css token
system as-is (copied into app/static/style.css; app-specific additions in
app/static/adopt.css). Read README.md first — it has the full contract with
both external APIs and the one unconfirmed assumption (the AOS-CX
`aruba_central` field name) that most needs validating against real
hardware before this goes to production.

## Stack & layout
- FastAPI (`app/main.py`) + Jinja2 (`app/templates/`) + SQLite (temp db only
  — job/switch/code state, never credentials).
- `app/mist_client.py`: Mist org-inventory GET, confirmed contract (see
  README). `app/cx_client.py`: pyaoscx-based switch push — see its docstring
  for two real pyaoscx bugs worked around (Session.open()'s hardcoded https
  + buggy host:port cookie check, and Device's process-global Singleton).
- `app/worker.py`: background thread + ThreadPoolExecutor pushing to
  switches concurrently. Has a `_code_assignment_lock` guarding the
  "claim the oldest unused code" step — remove it and tests will flake
  (two threads can otherwise grab the same code before either commits).

## Local dev / verify
- `.venv` in this repo (gitignored). `pip install -r requirements-dev.txt`.
- Real regression check: `python -m pytest tests/` — runs actual uvicorn
  instances of `mock/mock_mist.py` and `mock/mock_switch.py` and exercises
  the real Mist-client/pyaoscx code against them, not mocks-of-the-mocks.
- `python -m py_compile app/*.py` for a fast post-edit sanity check.
- pyaoscx==2.6.0 (pinned) only ships REST bindings for API versions 10.04,
  10.08, 10.09 — NOT 10.13 despite that being a real firmware release. Check
  `python -c "import pyaoscx.rest as r, pkgutil; print([m.name for m in pkgutil.iter_modules(r.__path__)])"`
  before bumping `ADOPT_AOSCX_API_VERSION`.

## Git
- New app, not yet pushed anywhere. Follow the same rules as opal/holo when
  it is: commit only when asked, exclude `*.db`/`.env`/`.venv`/`data/`.
