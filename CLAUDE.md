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
- Public repo: github.com/xod442/adopt (main). Commit only when Rick says
  so, same rules as opal/holo: exclude `*.db`/`.env`/`.venv`/`data/`, and
  never hardcode internal/beta hostnames into committed defaults (see
  "Mist public GA timing" below) — this repo is public.

## Mist public GA timing (as of 2026-09-05)
Mist's **public** cloud API doesn't return CX adoption codes yet — that's
landing ~Sept 9, 2026. Rick currently has access to an **internal/beta**
Mist server that already supports it, confirmed to use the same endpoint
path and response shape this app already codes against (same
`/api/v1/orgs/{org_id}/inventory`, same `claim_code` field, same
`Authorization: Token <token>` header, plain HTTPS, no special port/TLS
handling needed) — just a different hostname.

Because of that, **no code change should be needed** to use the internal
server today: the dashboard's "Mist API host" field (`app/config.py`'s
`DEFAULT_MIST_HOST` / env var `ADOPT_MIST_HOST`) is already free-text, not
hardcoded to `api.mist.com`, and `app/mist_client.py` builds `https://{host}`
from whatever host is given. Rick was going to test this Monday
(2026-09-08) by typing the internal hostname into that field.

If Monday's test surfaces a real difference (e.g. a different field name,
an extra required header, pagination that behaves differently, etc.),
that's the first place to look — update `app/mist_client.py`'s
`fetch_cx_adoption_codes()` accordingly, and re-run
`pytest tests/test_mist_client.py` after updating `mock/mock_mist.py` to
match the corrected contract. Do **not** hardcode the internal hostname
anywhere committed (config default, `.env.example`, docs) — it's internal
infrastructure and this repo is public; Rick should only set it in his own
gitignored `.env` (`ADOPT_MIST_HOST=...`) or type it into the dashboard
field per-run.
