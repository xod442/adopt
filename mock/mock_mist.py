"""Mock Mist org API, for exercising app/mist_client.py end-to-end without a
real Mist org.

Mirrors the real contract confirmed for this app (2026-09-08, verified live
against api.mistsys.com):
  GET https://{host}/api/v1/orgs/{org_id}/aoscx/register_cmd
  Header: Authorization: Token <api_token>
  Response: {"cli_commands": "mist registration-code <CODE>"}

A fresh, unique code is returned on every call (repeated calls against the
real API were confirmed to differ each time — see app/mist_client.py) — this
mock does the same via an incrementing counter, so tests can assert
uniqueness across multiple switches in one job.

Run standalone: `python mock_mist.py` (reads MOCK_MIST_PORT, default 9100).
"""
import itertools
import os

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Mist Org API Simulator (adopt mock)")

API_TOKEN = os.getenv("MOCK_MIST_TOKEN", "test-token")
ORG_ID = os.getenv("MOCK_MIST_ORG_ID", "test-org")

_counter = itertools.count(1)


@app.get("/api/v1/orgs/{org_id}/aoscx/register_cmd")
def register_cmd(org_id: str, authorization: str = Header(default="")):
    if authorization != f"Token {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid API token")
    if org_id != ORG_ID:
        raise HTTPException(status_code=404, detail="Org not found")
    code = f"MOCK-REG-CODE-{next(_counter):04d}"
    return {"cli_commands": f"mist registration-code {code}"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MOCK_MIST_PORT", "9100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
