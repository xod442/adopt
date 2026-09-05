# ADOPT

A no-login dashboard for bulk-adopting HPE Aruba **AOS-CX** switches into
Mist, following the sibling apps' Docker/FastAPI pattern (see `holo`,
`focus`, `vista`, `opal`, `opal-mist`).

> **Status (2026-09-05):** Mist's *public* cloud API doesn't return CX
> adoption codes yet — that's expected ~September 9, 2026. Until then, this
> only works against an internal/beta Mist server. The "Mist API host" field
> below is plain text for exactly this reason — point it at your
> internal/beta hostname (or set `ADOPT_MIST_HOST` in your own gitignored
> `.env`) rather than `api.mist.com`. No code change should be required
> once GA lands, since the internal server was confirmed to use the same
> endpoint, response shape, and auth header this app already codes against
> — see `CLAUDE.md` for the full note and what to check if that turns out
> not to be true.

## What it does

1. You fill in a Mist org, AOS-CX admin credentials, and a list of switch IP
   addresses, then click **Adopt**.
2. ADOPT counts the switches, then calls the Mist org inventory API once to
   collect exactly that many CX adoption ("claim") codes, filtering out any
   Juniper **EX** switches that show up in the same org inventory (an EX
   claim code will not adopt a CX switch). The codes are stored in a small
   temp SQLite db.
3. If Mist can't supply enough codes, the run fails immediately — nothing is
   pushed to any switch.
4. Otherwise, for each switch IP (in the order you listed them), ADOPT pops
   the oldest unused code from the temp db, logs into that switch over its
   REST API via **pyaoscx**, writes the code, and saves it to
   `startup-config` ("write memory"). Switches are pushed concurrently.
5. A live-updating status page shows per-switch progress/result.

Credentials (the Mist API token and the AOS-CX admin password) are only ever
held in memory for the life of one run — they are never written to the temp
db or logged.

## Contract with the two external APIs

### Mist org inventory (confirmed)

```
GET https://{host}/api/v1/orgs/{org_id}/inventory?type=switch
Authorization: Token {api_token}
```

Returns a JSON list of device dicts; each has a `claim_code` field (the
adoption code) when the device hasn't been claimed into the org yet. Mist can
return both EX (Juniper) and CX (Aruba) switches under `type=switch` now that
both live in the same org — see `config.EX_MODEL_PREFIXES` for the model-name
filter that excludes EX (and QFX) devices before their codes are used.

### AOS-CX switch push (best-effort — validate before production use)

**This half of the contract is not confirmed against real switch firmware.**
See the docstring in `app/cx_client.py` for the full reasoning, summarized
here:

- pyaoscx's SDK currently lists `system.aruba_central` as a read-only/
  reported attribute, not one of its standard writable config fields. This
  looks like newer HPE Aruba/Mist-convergence functionality that isn't
  reflected in the public SDK yet.
- ADOPT GETs the switch's current `aruba_central` object, writes the
  adoption code into `config.MIST_CLAIM_FIELD` (env var
  `ADOPT_MIST_CLAIM_FIELD`, default `"activation_key"`), flips any
  `enable`/`enabled` key present, and PUTs it back scoped to that one
  attribute (AOS-CX's REST API has no PATCH verb, confirmed in pyaoscx's own
  design doc, so this is an attribute-scoped GET+PUT rather than a
  full-object PUT).
- **Before relying on this in production**, point `ADOPT_MIST_CLAIM_FIELD`
  at the real field name once you've confirmed it against actual switch
  firmware (or updated vendor docs), or adjust the logic in
  `app/cx_client.py` if the discovered schema differs more substantially.
  The switch's logged `aruba_central` keys (see app logs) are the fastest way
  to find the right field name.

pyaoscx's `Configuration.create_checkpoint("running-config",
"startup-config")` is the real, confirmed "write memory" equivalent, and is
used as-is.

Also note: `app/cx_client.py` deliberately does **not** use pyaoscx's
`Device` class — `Device` uses a process-global Singleton metaclass, so the
first `Device(session)` call in the process permanently wins; every later
call for a different switch's session silently returns that same cached
instance. Since ADOPT pushes to several switches concurrently, `Device` is
bypassed in favor of a plain `session.request()` GET/PUT.

## Local dev / verify

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests run the *actual* Mist client and pyaoscx code against real (not
mocked-away) HTTP servers — see `mock/mock_mist.py` (a mixed CX/EX org
inventory) and `mock/mock_switch.py` (a minimal but real AOS-CX REST API
simulator: login/logout, `system` GET/PUT, `fullconfigs` for the
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
  mist_client.py   Mist org-inventory GET + CX/EX filtering
  cx_client.py     pyaoscx login + aruba_central push + write-memory checkpoint
  worker.py        background thread orchestrating one run
  templates/, static/   dashboard + job-status UI (shares holo/focus/vista's
                         style.css token system — see DESIGN.md in those repos)
mock/
  mock_mist.py     fake Mist org inventory server, for tests + local dev
  mock_switch.py   fake AOS-CX switch REST API, for tests + local dev
tests/
  test_mist_client.py, test_cx_client.py, test_integration.py
```
