"""Mist org client — mints one-time AOS-CX ("brownfield") registration codes.

Confirmed contract (2026-09-08, verified live against the real Mist staging
API — api.mistsys.com — plus the official HPE AOS-CX 10.18.xxxx Fundamentals
Guide, "Mist onboarding" / "Use case: brownfield onboarding" pages):

  GET https://{host}/api/v1/orgs/{org_id}/aoscx/register_cmd
  Header: Authorization: Token <api_token>
  Response: {"cli_commands": "mist registration-code <CODE>"}

`<CODE>` is a JWT (header.payload.signature). Its payload carries an org_id,
a random `challenge`, and `iat`/`exp` (observed ~30 days validity) — decoding
several calls in a row confirmed `challenge`/`iat` differ every time, i.e.
each GET mints a genuinely fresh, single code, not a cached/static value.
There is no bulk "give me N codes" call and no per-switch parameter (serial/
mac) accepted — you call this once per switch you intend to adopt, and the
code carries no association with any particular switch until it's used.

This replaces an earlier, incorrect assumption (bulk-fetching pre-existing
`claim_code` values from `GET /orgs/{org_id}/inventory`) that turned out to
describe *greenfield* onboarding (switches with a QR/claim code already
printed on the chassis) rather than *brownfield* onboarding (existing
switches with no such code) — see CLAUDE.md for the full investigation.
That also means the earlier EX (Juniper) -vs- CX device-model filtering is
gone: this endpoint is CX-only by construction, so there is nothing to
filter.

Two other endpoints were investigated and ruled out along the way:
  - GET /orgs/{org_id}/jsi/devices/outbound_ssh_cmd (documented in the
    official `mistapi` Python SDK) and its near-identical sibling
    /orgs/{org_id}/ocdevices/outbound_ssh_cmd (seen live, undocumented) both
    return a *static*, org-wide Junos-flavored outbound-ssh bootstrap
    script (repeated calls returned byte-identical output) — that's the
    Juniper EX zero-touch-provisioning mechanism, not a CX registration
    code, and not something that changes per call.
"""
from __future__ import annotations

import re

import requests

_CLI_CODE_RE = re.compile(r"mist registration-code\s+(\S+)")


class MistClientError(Exception):
    """Raised for any Mist API failure (auth, network, or unexpected shape)."""


def fetch_cx_registration_codes(
    host: str,
    org_id: str,
    api_token: str,
    needed: int,
    base_url_override: str | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """Return `needed` fresh, one-time AOS-CX registration codes, one per
    call to GET /orgs/{org_id}/aoscx/register_cmd (see module docstring —
    there is no bulk-fetch variant of this endpoint).

    Raises MistClientError on the first request/auth/shape failure. Unlike
    the old inventory-based approach, there's no concept of "fewer codes
    available than needed" here — every call either succeeds with a fresh
    code or fails outright — so a partial failure part-way through does not
    return a short list; it raises immediately.
    """
    codes: list[str] = []
    for _ in range(needed):
        codes.append(
            _fetch_one_registration_code(
                host=host,
                org_id=org_id,
                api_token=api_token,
                base_url_override=base_url_override,
                timeout=timeout,
            )
        )
    return codes


def _fetch_one_registration_code(
    host: str,
    org_id: str,
    api_token: str,
    base_url_override: str | None,
    timeout: float,
) -> str:
    base_url = base_url_override or f"https://{host}"
    url = f"{base_url}/api/v1/orgs/{org_id}/aoscx/register_cmd"
    headers = {
        "Authorization": f"Token {api_token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise MistClientError(f"Could not reach Mist API at {base_url}: {exc}") from exc

    if response.status_code != 200:
        raise MistClientError(
            f"Mist API error {response.status_code}: {response.text[:500]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise MistClientError(f"Mist API returned non-JSON response: {exc}") from exc

    cli_commands = body.get("cli_commands") if isinstance(body, dict) else None
    if not cli_commands:
        raise MistClientError(
            f"Mist API response had no 'cli_commands' field: {body!r}"
        )

    match = _CLI_CODE_RE.search(cli_commands)
    if not match:
        raise MistClientError(
            f"Could not find a registration code in cli_commands: {cli_commands!r}"
        )
    return match.group(1)
