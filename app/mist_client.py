"""Mist org-inventory client — fetches CX switch adoption ("claim") codes.

Confirmed contract (see conversation record / user-supplied snippet):
  GET https://{host}/api/v1/orgs/{org_id}/inventory
  Header: Authorization: Token <api_token>
  Response: JSON list of device dicts. Mist inventory uses the `claim_code`
  field for device adoption/claiming.

Mist's org inventory can return both EX (Juniper) and CX (Aruba) switches
under the same `type=switch` filter now that both live in one org. An EX
switch's claim_code will not adopt a CX switch, so devices are filtered by
model prefix before their codes are used (see config.EX_MODEL_PREFIXES).
"""
from __future__ import annotations

import requests

from . import config


class MistClientError(Exception):
    """Raised for any Mist API failure (auth, network, unexpected shape)."""


def _is_cx_model(model: str | None) -> bool:
    if not model:
        return False
    model_upper = model.strip().upper()
    return not model_upper.startswith(config.EX_MODEL_PREFIXES)


def fetch_cx_adoption_codes(
    host: str,
    org_id: str,
    api_token: str,
    needed: int,
    base_url_override: str | None = None,
    timeout: float = 20.0,
) -> list[dict]:
    """Return up to `needed` CX-eligible inventory devices that carry a
    claim_code, each as {"claim_code", "mac", "serial", "model"}.

    Raises MistClientError on any request/auth failure. Does not raise if
    fewer than `needed` codes are available — the caller (worker.py) decides
    how to treat a shortfall, since that's a job-level policy decision.
    """
    base_url = base_url_override or f"https://{host}"
    url = f"{base_url}/api/v1/orgs/{org_id}/inventory"
    headers = {
        "Authorization": f"Token {api_token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, params={"type": "switch"}, timeout=timeout)
    except requests.RequestException as exc:
        raise MistClientError(f"Could not reach Mist API at {base_url}: {exc}") from exc

    if response.status_code != 200:
        raise MistClientError(
            f"Mist API error {response.status_code}: {response.text[:500]}"
        )

    try:
        inventory = response.json()
    except ValueError as exc:
        raise MistClientError(f"Mist API returned non-JSON response: {exc}") from exc

    if not isinstance(inventory, list):
        raise MistClientError("Mist API response was not a list of devices as expected")

    codes: list[dict] = []
    for device in inventory:
        if len(codes) >= needed:
            break
        claim_code = device.get("claim_code")
        model = device.get("model")
        if not claim_code:
            continue
        if not _is_cx_model(model):
            continue
        codes.append(
            {
                "claim_code": claim_code,
                "mac": device.get("mac"),
                "serial": device.get("serial"),
                "model": model,
            }
        )
    return codes
