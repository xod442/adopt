# ADOPT — working notes for Claude

Bulk AOS-CX-to-Mist adoption dashboard. No login (single-purpose ops tool).
Sibling app to opal/opal-mist/holo/focus/vista but standalone — not part of
their shared DESIGN.md app list, though it reuses their style.css token
system as-is (copied into app/static/style.css; app-specific additions in
app/static/adopt.css). Read README.md first — it has the full, confirmed
contract with both external APIs (see "Mist API — confirmed" below for how
that got nailed down and what was ruled out along the way).

## Stack & layout
- FastAPI (`app/main.py`) + Jinja2 (`app/templates/`) + SQLite (temp db only
  — job/switch/code state, never credentials).
- `app/mist_client.py`: mints one fresh Mist registration code per switch
  (confirmed contract, see README). `app/cx_client.py`: pyaoscx-based switch
  push — see its docstring for two real pyaoscx bugs worked around
  (Session.open()'s hardcoded https + buggy host:port cookie check, and
  Device's process-global Singleton).
- `app/worker.py`: background thread + ThreadPoolExecutor pushing to
  switches concurrently. Has a `_code_assignment_lock` guarding the
  "claim the oldest unused code" step — remove it and tests will flake
  (two threads can otherwise grab the same code before either commits).
- `app/ssh_client.py` + `app/clear_worker.py`: the rollback flow (undo an
  accidental bulk adoption) — SSHes into each switch (paramiko) and runs
  `clear mist registration-info`, since AOS-CX's REST `/cli` endpoint
  can't run config-changing commands at all (confirmed live — see below).
  Deliberately separate from the adopt flow's models/worker since it needs
  no Mist API access, just switch SSH credentials.

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
  so, same rules as opal/holo: exclude `*.db`/`.env`/`.venv`/`data/`. Also
  never commit real Mist API tokens or org IDs used for live testing.

## Mist API — confirmed (2026-09-08, GA day)

The full adoption contract has been confirmed live against the real Mist
staging API (`api.mistsys.com`) plus the official HPE AOS-CX 10.18.xxxx
Fundamentals Guide ("Mist onboarding" / "Use case: brownfield onboarding"
pages), replacing an earlier, incorrect design. Full detail lives in
README.md's "Contract with the two external APIs" section and the module
docstrings in `app/mist_client.py` / `app/cx_client.py` — short version:

- **Mist side:** `GET /api/v1/orgs/{org_id}/aoscx/register_cmd` mints one
  fresh, one-time registration code per call — verified by calling it
  several times in a row and decoding the returned JWT: `challenge`/`iat`
  differ every time. There is no bulk "give me N codes" call and no
  per-device parameter, so `app/mist_client.py` calls this once per switch.
- **AOS-CX side:** `PUT /rest/{version}/system/mist
  {"registration_code": "<CODE>"}` (equivalent to CLI `mist
  registration-code <CODE>`). **Note the verb**: the official HPE doc's own
  curl example shows `-X POST`, and that was this app's original
  implementation — but live testing against real hardware (2026-09-09)
  found POST gets HTTP 405 "Not Allowed" **from nginx itself** (the path
  isn't wired up for POST at the switch's reverse-proxy layer at all),
  while PUT succeeds and is reflected back correctly on a follow-up GET.
  Confirmed on a switch already verified clean/unregistered, ruling out
  "already registered" as the 405's cause. If this needs revisiting on a
  different firmware version, the verb is the first thing to re-check —
  the fastest way to tell is exactly how this was found: try the write,
  then immediately GET the same resource back and see whether your value
  stuck.

### What this replaced, and why (for context if this needs revisiting)

The original design bulk-fetched pre-existing `claim_code` values from
`GET /orgs/{org_id}/inventory` — that endpoint/field are both real (and
still correctly documented/implemented if anyone needs it), but it turned
out to describe **greenfield** onboarding (a switch with a QR/claim code
already printed on the chassis, claimed via *Organization → Inventory →
Switches → Claim Switches*), a different use case from this app's
**brownfield** flow (existing switches with no such code, adopted via
*Organization → Inventory → Switches → Adopt Switch → CX*). Live testing
against a real staging org with 4 already-connected CX switches confirmed
`GET /inventory` returns no `claim_code` field at all for them (it returns
`magic` instead) — that mismatch is what surfaced the wrong assumption.

Two more endpoints were investigated (via the Network tab while clicking
"Adopt Switch → CX" in the Mist staging console) and ruled out before
finding the real one:
- `GET /orgs/{org_id}/jsi/devices/outbound_ssh_cmd` — documented in the
  official `mistapi` Python SDK as the Juniper EX zero-touch-provisioning
  bootstrap-script endpoint.
- `GET /orgs/{org_id}/ocdevices/outbound_ssh_cmd` — an undocumented,
  near-identical sibling; also returns a Junos-flavored script. Both
  returned **byte-identical output across repeated calls** — a static,
  org-wide value, not a fresh per-switch code — which is what ruled them
  out (`aoscx/register_cmd`'s JWT payload, by contrast, has a different
  `challenge`/`iat` every call).

If a future Mist API change breaks this again, that history — and the
"call it 3× and diff the response" verification technique — is the fastest
way to tell a real per-switch code from shared/static bootstrap plumbing.

