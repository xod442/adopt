"""Mock Mist org-inventory API, for exercising app/mist_client.py end-to-end
without a real Mist org.

Mirrors the real contract confirmed for this app:
  GET https://{host}/api/v1/orgs/{org_id}/inventory
  Header: Authorization: Token <api_token>
  Response: JSON list of device dicts, each optionally carrying a
  `claim_code` (the adoption code) for devices not yet claimed into the org.

Serves a configurable mix of CX (Aruba) and EX (Juniper) switch models so
app/mist_client.py's CX-vs-EX filtering can be exercised.

Run standalone: `python mock_mist.py` (reads MOCK_MIST_PORT, default 9100).
"""
import os

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Mist Org Inventory Simulator (adopt mock)")

API_TOKEN = os.getenv("MOCK_MIST_TOKEN", "test-token")
ORG_ID = os.getenv("MOCK_MIST_ORG_ID", "test-org")

# A deliberately mixed inventory: CX switches with claim codes (adoptable),
# an EX (Juniper) switch with a claim code (must be filtered out — EX codes
# don't work on a CX switch), and one CX switch already claimed (no code).
INVENTORY = [
    {
        "mac": "aa:bb:cc:00:00:01",
        "serial": "CX0001",
        "model": "6300M-24",
        "type": "switch",
        "claim_code": "CX-CLAIM-0001",
    },
    {
        "mac": "aa:bb:cc:00:00:02",
        "serial": "CX0002",
        "model": "6300M-24",
        "type": "switch",
        "claim_code": "CX-CLAIM-0002",
    },
    {
        "mac": "aa:bb:cc:00:00:03",
        "serial": "CX0003",
        "model": "8325-32C",
        "type": "switch",
        "claim_code": "CX-CLAIM-0003",
    },
    {
        "mac": "ee:ee:ee:00:00:01",
        "serial": "EX0001",
        "model": "EX4400-24P",
        "type": "switch",
        "claim_code": "EX-CLAIM-0001",
    },
    {
        "mac": "aa:bb:cc:00:00:04",
        "serial": "CX0004",
        "model": "6300M-24",
        "type": "switch",
        "claim_code": None,
    },
]


@app.get("/api/v1/orgs/{org_id}/inventory")
def get_inventory(org_id: str, authorization: str = Header(default="")):
    if authorization != f"Token {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid API token")
    if org_id != ORG_ID:
        raise HTTPException(status_code=404, detail="Org not found")
    return INVENTORY


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MOCK_MIST_PORT", "9100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
