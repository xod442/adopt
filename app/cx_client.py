"""AOS-CX switch client — pushes a Mist registration code to a switch
("brownfield onboarding") and persists it ("write memory").

Uses pyaoscx (the official Aruba AOS-CX Python SDK):
  - pyaoscx.session.Session for connection/version state, but with a
    hand-rolled login (see `_login()` below) instead of Session.open() —
    .open() hardcodes scheme="https" into base_url at construction time
    (unusable for local http test mocks) and its post-login sanity check
    misidentifies any "host:port" address as IPv6 and then fails to account
    for the port when matching the login cookie's domain. `_login()` does
    the same POST-based cookie login Session.open()/Session.login() do,
    without either bug.
  - A raw PUT of `system/mist` (via session.request(), the same low-level
    call pyaoscx's own modules use) rather than pyaoscx.device.Device —
    Device uses a process-global Singleton metaclass, so the *first*
    Device(session) call in the process permanently wins: every later call,
    for any other switch's session, silently returns that same cached
    instance/session. That's fine for a single device but wrong here, where
    several switches are pushed to concurrently (see app/worker.py), so
    Device is not used at all.
  - pyaoscx.configuration.Configuration.create_checkpoint("running-config",
    "startup-config") for "write memory" (copy running-config to
    startup-config), pyaoscx's real equivalent of that CLI command
    (Configuration, unlike Device, is a plain — not singleton — class).

Confirmed contract (2026-09-09, verified live against real hardware — see
CLAUDE.md for the full investigation):

  PUT https://{IP}/rest/{version}/system/mist
  Content-Type: application/json
  { "registration_code": "<CODE>" }

(equivalent to the CLI command `mist registration-code <CODE>`). The
official HPE AOS-CX 10.18.xxxx Fundamentals Guide's own curl example shows
`-X POST` for this call — that was this module's original implementation,
but live testing against real hardware found POST returns HTTP 405 "Not
Allowed" **from nginx itself** (i.e. the path isn't wired up for POST at
all at the switch's reverse-proxy layer, not an AOS-CX-level rejection),
while PUT succeeds (200, with the written value reflected back on a
follow-up GET). This was confirmed on a switch already verified to be in a
clean, unregistered state (ruling out "already registered" as the 405's
cause) — so the doc's POST example appears to simply not match this
firmware's actual REST routing. If this needs revisiting on a different
firmware version, that's the first thing to re-check.

This also replaces an earlier, incorrect guess (writing into a
`system.aruba_central.activation_key` field via GET+PUT) that this module
used before the real field/endpoint was confirmed against official
documentation. Per that same doc, the registration code itself is *not*
persisted in running/startup-config and is automatically cleared from the
switch after successful use — the create_checkpoint() ("write memory")
call below is kept anyway to match this app's stated "saved to
startup-config" behavior for whatever else may be in running-config, but
is not what makes the registration code durable (the switch's own
persistent, post-registration state is what survives).
"""
from __future__ import annotations

import json
import logging

from pyaoscx.configuration import Configuration
from pyaoscx.session import Session

from . import config

logger = logging.getLogger("adopt.cx_client")


class CxClientError(Exception):
    """Raised for any AOS-CX switch failure (auth, network, push, or save)."""


def push_registration_code(
    ip: str,
    username: str,
    password: str,
    registration_code: str,
    api_version: str | None = None,
    scheme: str = "https",
) -> None:
    """Log into the switch at `ip`, PUT `registration_code` to
    system/mist, then persist via a checkpoint (write memory).

    `ip` may be "host" or "host:port" (the latter used by the local mock
    switch server in mock/mock_switch.py). `scheme` defaults to "https" for
    real switches; tests point it at the plain-http mock instead.

    Raises CxClientError on any failure. Always logs out of the switch
    session, even on failure.
    """
    api_version = api_version or config.DEFAULT_AOSCX_API_VERSION
    session = Session(ip, api_version)
    # pyaoscx.Session.__init__ hardcodes scheme="https" and bakes it into
    # base_url immediately. session.request() (GET/PUT/POST) reads .scheme
    # dynamically, but our own _login()/close() need base_url rebuilt too.
    session.scheme = scheme
    session.base_url = f"{scheme}://{ip}/{session.version_path}"

    try:
        _login(session, username, password)
    except Exception as exc:  # pyaoscx raises its own LoginError/ConnectTimeout etc.
        raise CxClientError(f"Login to {ip} failed: {exc}") from exc

    try:
        try:
            response = session.request(
                "PUT",
                "system/mist",
                data=json.dumps({config.MIST_REGISTRATION_FIELD: registration_code}),
            )
        except Exception as exc:
            raise CxClientError(
                f"Pushing registration code to {ip} failed: {exc}"
            ) from exc

        if response.status_code not in (200, 201, 204):
            raise CxClientError(
                f"Switch {ip} rejected the registration code "
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
