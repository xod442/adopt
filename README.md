# ADOPT

A no-login dashboard for bulk-adopting HPE Aruba **AOS-CX** switches into
Mist, following the sibling apps' Docker/FastAPI pattern (see `holo`,
`focus`, `vista`, `opal`, `opal-mist`).

> **Status (2026-09-08):** GA day. The contract below has been confirmed
> live against the real Mist staging API (`api.mistsys.com`) plus the
> official HPE AOS-CX 10.18.xxxx Fundamentals Guide's "Mist onboarding" /
> "Use case: brownfield onboarding" pages, replacing an earlier, incorrect
> design (bulk-fetching pre-existing `claim_code` values from the org
> *inventory* listing, which is actually the *greenfield* — pre-printed
> QR/claim-code — onboarding path, not this app's brownfield use case). See
> `CLAUDE.md` for the full investigation.

## What it does

1. You fill in a Mist org, AOS-CX admin credentials, and a list of switch IP
   addresses, then click **Adopt**.
2. ADOPT calls the Mist API once per switch to mint a fresh, one-time CX
   registration code (there is no bulk "give me N codes" call — see
   Contract below). The codes are stored in a small temp SQLite db.
3. If Mist fails to supply any one of them, the run fails immediately —
   nothing is pushed to any switch.
4. Otherwise, for each switch IP (in the order you listed them), ADOPT pops
   the oldest unused code from the temp db, logs into that switch over its
   REST API via **pyaoscx**, PUTs the code to `system/mist`, and saves to
   `startup-config` ("write memory"). Switches are pushed concurrently.
5. A live-updating status page shows per-switch progress/result.

Credentials (the Mist API token and the AOS-CX admin password) are only ever
held in memory for the life of one run — they are never written to the temp
db or logged.

## Contract with the two external APIs

### Mist registration-code minting (confirmed live, 2026-09-08)

```
GET https://{host}/api/v1/orgs/{org_id}/aoscx/register_cmd
Authorization: Token {api_token}
```

Returns `{"cli_commands": "mist registration-code <CODE>"}`. `<CODE>` is a
JWT whose payload carries `org_id`, a random `challenge`, and `iat`/`exp`
(~30 days validity observed). Verified live by calling this endpoint
several times in a row: `challenge`/`iat` differ every call, confirming each
GET mints a genuinely fresh, single code — there is no per-device parameter
and no bulk variant, so ADOPT calls this once per switch it needs a code
for.

Two other endpoints were investigated and ruled out along the way (see
`app/mist_client.py`'s module docstring for the full detail):
- `GET /orgs/{org_id}/inventory` — real and correctly documented (also
  confirmed live), but returns pre-existing devices' `claim_code`/`magic`
  fields for *greenfield* (pre-printed QR code) onboarding, which is a
  different use case from this app's brownfield flow.
- `GET /orgs/{org_id}/{jsi,ocdevices}/devices/outbound_ssh_cmd` — returns a
  *static*, org-wide Junos-flavored outbound-ssh bootstrap script (repeated
  calls returned byte-identical output); that's the Juniper EX zero-touch
  mechanism, not a CX registration code.

### AOS-CX switch push (confirmed live against real hardware, 2026-09-09)

```
PUT https://{IP}/rest/{version}/system/mist
Content-Type: application/json
{ "registration_code": "<CODE>" }
```

(equivalent to the CLI command `mist registration-code <CODE>`). The
official HPE doc's own curl example shows `-X POST` for this call — that
was ADOPT's original implementation, but live testing against real
hardware found POST returns HTTP 405 "Not Allowed" **from nginx itself**
(the path isn't wired up for POST at all at the switch's reverse-proxy
layer), while PUT succeeds (200, with the written value reflected back on
a follow-up GET) — confirmed on a switch already verified to be in a
clean, unregistered state, ruling out "already registered" as the 405's
cause. If this needs revisiting on a different firmware version, the verb
is the first thing to re-check.

This also replaced an earlier, incorrect guess (`system.aruba_central.activation_key`
via GET+PUT) that predates finding the official doc. Per that doc, the
registration code itself is *not* persisted in running/startup-config and is
automatically cleared from the switch after successful use — ADOPT's
create_checkpoint() ("write memory") call is kept for whatever else may be
in running-config, but is not what makes the registration durable.

`config.MIST_REGISTRATION_FIELD` (env var `ADOPT_MIST_REGISTRATION_FIELD`,
default `"registration_code"`) is kept as a single override point in case a
future firmware revision changes this field name.

pyaoscx's `Configuration.create_checkpoint("running-config",
"startup-config")` is the real, confirmed "write memory" equivalent, and is
used as-is.

Also note: `app/cx_client.py` deliberately does **not** use pyaoscx's
`Device` class — `Device` uses a process-global Singleton metaclass, so the
first `Device(session)` call in the process permanently wins; every later
call for a different switch's session silently returns that same cached
instance. Since ADOPT pushes to several switches concurrently, `Device` is
bypassed in favor of a plain `session.request()` POST.

## Local dev / verify

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests run the *actual* Mist client and pyaoscx code against real (not
mocked-away) HTTP servers — see `mock/mock_mist.py` (mints a fresh,
unique registration code per call, matching the real API's confirmed
behavior) and `mock/mock_switch.py` (a minimal but real AOS-CX REST API
simulator: login/logout, `system/mist` GET/POST, `fullconfigs` for the
write-memory checkpoint). `mock/mock_switch.py` is adapted from
`../aos-cx-lab/sim/main.py`'s login/session pattern.

To run the app itself against the mocks instead of real infrastructure:

```
# terminal 1
python mock/mock_mist.py            # port 9100, org "test-org", token "test-token"
# terminal 2, 3, 4 — one per switch
MOCK_SWITCH_PORT=9101 python mock/mock_switch.py
MOCK_SWITCH_PORT=9102 python mock/mock_switch.py
MOCK_SWITCH_PORT=9103 python mock/mock_switch.py
# terminal 5
ADOPT_MIST_BASE_URL_OVERRIDE=http://127.0.0.1:9100 \
ADOPT_CX_SCHEME=http \
ADOPT_AOSCX_API_VERSION=10.09 \
uvicorn app.main:app --reload
```

Then open http://localhost:8000, use org `test-org` / token `test-token` /
any AOS-CX username+password (mock default `admin`/`admin`), and switch IPs
`127.0.0.1:9101`, `127.0.0.1:9102`, `127.0.0.1:9103`.

`python -m py_compile app/*.py` is a fast sanity check after any edit; the
pytest suite above is the real regression check since it exercises pyaoscx
for real.

## Docker

```
cp .env.example .env   # adjust as needed
docker compose up --build
```

Serves on `$ADOPT_PORT` (default 9099). See `.env.example` for all knobs.

## Layout

```
app/
  main.py          FastAPI routes: dashboard, /adopt, /jobs/{id}, /jobs/{id}/data
  config.py        env-driven settings
  db.py / models.py  temp SQLite db: AdoptionJob, AdoptionCode, SwitchResult
  mist_client.py   Mist registration-code minting (one GET per switch)
  cx_client.py     pyaoscx login + system/mist push + write-memory checkpoint
  worker.py        background thread orchestrating one run
  templates/, static/   dashboard + job-status UI (shares holo/focus/vista's
                         style.css token system — see DESIGN.md in those repos)
mock/
  mock_mist.py     fake Mist registration-code server, for tests + local dev
  mock_switch.py   fake AOS-CX switch REST API, for tests + local dev
tests/
  test_mist_client.py, test_cx_client.py, test_integration.py
```
