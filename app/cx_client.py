"""AOS-CX switch client — pushes a Mist/CX adoption code to a switch and
persists it ("write memory").

Uses pyaoscx (the official Aruba AOS-CX Python SDK):
  - pyaoscx.session.Session for connection/version state, but with a
    hand-rolled login (see `_login()` below) instead of Session.open() —
    .open() hardcodes scheme="https" into base_url at construction time
    (unusable for local http test mocks) and its post-login sanity check
    misidentifies any "host:port" address as IPv6 and then fails to account
    for the port when matching the login cookie's domain. `_login()` does
    the same POST-based cookie login Session.open()/Session.login() do,
    without either bug.
  - A raw GET/PUT of `system?attributes=aruba_central` (via
    session.request(), the same low-level call pyaoscx's own modules use)
    rather than pyaoscx.device.Device — Device uses a process-global
    Singleton metaclass, so the *first* Device(session) call in the process
    permanently wins: every later call, for any other switch's session,
    silently returns that same cached instance/session. That's fine for a
    single device but wrong here, where several switches are pushed to
    concurrently (see app/worker.py), so Device is not used at all.
  - AOS-CX's REST API has no PATCH verb (confirmed in pyaoscx's own design
    doc), so this is an attribute-scoped GET+PUT rather than a full-object
    PUT, to avoid clobbering unrelated config.
  - pyaoscx.configuration.Configuration.create_checkpoint("running-config",
    "startup-config") for "write memory" (copy running-config to
    startup-config), pyaoscx's real equivalent of that CLI command
    (Configuration, unlike Device, is a plain — not singleton — class).

IMPORTANT / unconfirmed against real hardware: the exact field inside
`system.aruba_central` that accepts a Mist/Central claim code has not been
verified against real AOS-CX firmware (pyaoscx's SDK lists `aruba_central`
as a read-only/reported attribute today, not one of its standard writable
config attributes — this looks like newer, not-yet-publicly-documented
functionality from the HPE Aruba/Mist convergence). This module:
  1. GETs the switch's current `aruba_central` object and logs its keys.
  2. Writes the code into config.MIST_CLAIM_FIELD (default "activation_key"),
     a single override point (env var ADOPT_MIST_CLAIM_FIELD).
  3. Also flips a boolean "enable"/"enabled" key if present, on the
     assumption that onboarding requires the feature to be turned on.
Validate this against a real switch (or updated vendor docs) and adjust
config.MIST_CLAIM_FIELD / the logic below if the discovered schema differs.
"""
from __future__ import annotations

import json
import logging

from pyaoscx.configuration import Configuration
from pyaoscx.session import Session

from . import config

logger = logging.getLogger("adopt.cx_client")

_ENABLE_KEY_CANDIDATES = ("enable", "enabled")


class CxClientError(Exception):
    """Raised for any AOS-CX switch failure (auth, network, push, or save)."""


def push_adoption_code(
    ip: str,
    username: str,
    password: str,
    claim_code: str,
    api_version: str | None = None,
    scheme: str = "https",
) -> None:
    """Log into the switch at `ip`, write `claim_code` into
    system.aruba_central, then persist it via a checkpoint (write memory).

    `ip` may be "host" or "host:port" (the latter used by the local mock
    switch server in mock/mock_switch.py). `scheme` defaults to "https" for
    real switches; tests point it at the plain-http mock instead.

    Raises CxClientError on any failure. Always logs out of the switch
    session, even on failure.
    """
    api_version = api_version or config.DEFAULT_AOSCX_API_VERSION
    session = Session(ip, api_version)
    # pyaoscx.Session.__init__ hardcodes scheme="https" and bakes it into
    # base_url immediately. session.request() (GET/PUT) reads .scheme
    # dynamically, but our own _login()/close() need base_url rebuilt too.
    session.scheme = scheme
    session.base_url = f"{scheme}://{ip}/{session.version_path}"

    try:
        _login(session, username, password)
    except Exception as exc:  # pyaoscx raises its own LoginError/ConnectTimeout etc.
        raise CxClientError(f"Login to {ip} failed: {exc}") from exc

    try:
        try:
            get_response = session.request(
                "GET", "system", params={"attributes": "aruba_central"}
            )
        except Exception as exc:
            raise CxClientError(f"Could not read system state from {ip}: {exc}") from exc

        if get_response.status_code != 200:
            raise CxClientError(
                f"Could not read system state from {ip} "
                f"(HTTP {get_response.status_code}): {get_response.text[:500]}"
            )

        current = dict(get_response.json().get("aruba_central") or {})
        logger.info("Switch %s current aruba_central keys: %s", ip, sorted(current.keys()))

        updated = dict(current)
        updated[config.MIST_CLAIM_FIELD] = claim_code
        for enable_key in _ENABLE_KEY_CANDIDATES:
            if enable_key in updated:
                updated[enable_key] = True

        try:
            response = session.request(
                "PUT",
                "system",
                params={"attributes": "aruba_central"},
                data=json.dumps({"aruba_central": updated}),
            )
        except Exception as exc:
            raise CxClientError(f"Pushing adoption code to {ip} failed: {exc}") from exc

        if response.status_code not in (200, 204):
            raise CxClientError(
                f"Switch {ip} rejected the adoption code "
                f"(HTTP {response.status_code}): {response.text[:500]}"
            )

        try:
            Configuration(session).create_checkpoint("running-config", "startup-config")
        except Exception as exc:
            raise CxClientError(f"Write memory (checkpoint) failed on {ip}: {exc}") from exc
    finally:
        try:
            session.close()
        except Exception:
            logger.warning("Logout from %s did not complete cleanly", ip, exc_info=True)


def _login(session: Session, username: str, password: str) -> None:
    """Log `session` in, bypassing Session.open()'s buggy post-login check
    (see module docstring). Session.login() already raises LoginError on a
    non-2xx response, so a normal return here means the cookie was set."""
    req_session = Session.login(
        session.base_url,
        username,
        password,
        use_proxy=False,
        handle_zeroized_device=True,
    )
    req_session.verify = False
    req_session.proxies = session.proxy
    session.s = req_session
    session.connected = True
