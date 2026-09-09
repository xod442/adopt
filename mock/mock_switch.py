"""Mock AOS-CX switch REST API, for exercising app/cx_client.py end-to-end
without real hardware.

Adapted from the login/session pattern in ../../aos-cx-lab/sim/main.py (same
cookie + optional X-Csrf-Token flow pyaoscx's Session class expects), extended
with:
  - a `system/mist` resource (confirmed via live testing against real
    hardware, 2026-09-09: PUT /rest/{version}/system/mist
    {"registration_code": "..."} is how a switch is given a Mist
    registration code — see app/cx_client.py. The official HPE AOS-CX
    10.18.xxxx Fundamentals Guide's own curl example shows POST for this,
    but that returned HTTP 405 from nginx on real hardware; PUT is what
    actually works)
  - `fullconfigs/{name}` GET/PUT, enough to support pyaoscx's
    Configuration.create_checkpoint("running-config", "startup-config"),
    i.e. the "write memory" step.

Run standalone: `python mock_switch.py` (reads MOCK_SWITCH_PORT, default 9101).
Multiple instances on different ports simulate multiple switch IPs
(127.0.0.1:9101, 127.0.0.1:9102, ...) for local testing. `create_app()` is
also used directly by tests/test_integration.py to run several independent
switches in one process.
"""
from __future__ import annotations

import os
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse


def create_app(
    api_version: str = "10.09",
    username: str = "admin",
    password: str = "admin",
    switch_name: str = "aos-cx-sim",
) -> FastAPI:
    prefix = f"/rest/v{api_version}"
    valid_users = {username: password}

    app = FastAPI(title="AOS-CX Switch Simulator (adopt mock)", version=api_version)

    # ── Session store ────────────────────────────────────────────────────
    sessions: dict = {}
    session_ttl = 3600

    def require_session(request: Request):
        token = request.cookies.get("id") or request.headers.get("x-auth-token")
        if not token or token not in sessions:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if time.time() - sessions[token] > session_ttl:
            del sessions[token]
            raise HTTPException(status_code=401, detail="Session expired")
        return token

    # ── Initial state ────────────────────────────────────────────────────
    system_info = {
        "hostname": switch_name,
        "platform_name": "X86-64",
        "software_version": "FL.10.09.1000",
        "software_info": {"build_id": "FL.10.09.1000"},
        "mgmt_intf_status": {"ip": "0.0.0.0", "default_gateway": ""},
        "capacities": {"max_vlans": 4094},
    }

    # `system/mist` — modeled on the real `show mist` / GET .../system/mist
    # output shown in the official HPE doc (registration_code, agent_status,
    # connectivity_status, etc.); only the fields app/cx_client.py actually
    # touches (registration_code) are meaningfully exercised here.
    mist_info = {
        "registration_code": None,
        "agent_status": {"status": "ready", "reason": "agent is fully operational"},
        "connectivity_status": {"status": "disconnected", "reason": "not registered"},
    }

    # fullconfigs store, used by the write-memory (checkpoint) step.
    fullconfigs = {
        "running-config": {"hostname": switch_name},
        "startup-config": {"hostname": switch_name},
    }

    # ═══════════════════════════════════════════════════════════════
    # AUTH
    # ═══════════════════════════════════════════════════════════════
    @app.post(f"{prefix}/login")
    async def login(request: Request):
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        if valid_users.get(body.get("username")) != body.get("password"):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = str(uuid.uuid4())
        sessions[token] = time.time()
        response = JSONResponse({"status": "OK", "message": "Logged in"})
        response.set_cookie("id", token, httponly=True)
        response.headers["x-auth-token"] = token
        response.headers["X-Csrf-Token"] = token
        return response

    @app.post(f"{prefix}/logout")
    async def logout(token: str = Depends(require_session)):
        sessions.pop(token, None)
        response = JSONResponse({"status": "OK", "message": "Logged out"})
        response.delete_cookie("id")
        return response

    # ═══════════════════════════════════════════════════════════════
    # SYSTEM
    # ═══════════════════════════════════════════════════════════════
    @app.get(f"{prefix}/system")
    async def get_system(attributes: str | None = None, _: str = Depends(require_session)):
        if attributes:
            keys = [a.strip() for a in attributes.split(",")]
            return {k: system_info.get(k) for k in keys}
        return system_info

    @app.put(f"{prefix}/system")
    async def update_system(request: Request, _: str = Depends(require_session)):
        body = await request.json()
        system_info.update({k: v for k, v in body.items() if k in system_info})
        return system_info

    # ═══════════════════════════════════════════════════════════════
    # SYSTEM/MIST — Mist registration (see module docstring)
    # ═══════════════════════════════════════════════════════════════
    @app.get(f"{prefix}/system/mist")
    async def get_system_mist(_: str = Depends(require_session)):
        return mist_info

    @app.put(f"{prefix}/system/mist")
    async def set_system_mist(request: Request, _: str = Depends(require_session)):
        body = await request.json()
        if "registration_code" in body:
            mist_info["registration_code"] = body["registration_code"]
            mist_info["connectivity_status"] = {
                "status": "connected",
                "reason": "connected to the Mist Cloud",
            }
        return mist_info

    # pyaoscx.Device() calls this on construction to set self.firmware_version.
    @app.get(f"{prefix}/firmware")
    async def get_firmware(_: str = Depends(require_session)):
        return {
            "current_version": system_info["software_version"],
            "primary_version": system_info["software_version"],
            "secondary_version": system_info["software_version"],
        }

    # ═══════════════════════════════════════════════════════════════
    # FULLCONFIGS (running-config / startup-config) — write-memory support
    # ═══════════════════════════════════════════════════════════════
    @app.get(f"{prefix}/fullconfigs/{{config_name}}")
    async def get_full_config(config_name: str, _: str = Depends(require_session)):
        if config_name not in fullconfigs:
            raise HTTPException(status_code=404, detail=f"{config_name} not found")
        return fullconfigs[config_name]

    @app.put(f"{prefix}/fullconfigs/{{config_name}}")
    async def set_full_config(
        config_name: str,
        request: Request,
        from_: str | None = Query(default=None, alias="from"),
        _: str = Depends(require_session),
    ):
        # pyaoscx's create_checkpoint() sends `from=<full source uri>`, e.g.
        # /rest/v10.09/fullconfigs/running-config — copy that source's content.
        if from_:
            source_name = from_.rstrip("/").rsplit("/", 1)[-1]
            if source_name not in fullconfigs:
                raise HTTPException(status_code=404, detail=f"source {source_name} not found")
            fullconfigs[config_name] = dict(fullconfigs[source_name])
        else:
            body = await request.json()
            fullconfigs[config_name] = body
        return fullconfigs[config_name]

    # Expose mutable state for white-box test assertions.
    app.state.system_info = system_info
    app.state.mist_info = mist_info
    app.state.fullconfigs = fullconfigs

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MOCK_SWITCH_PORT", "9101"))
    standalone_app = create_app(
        api_version=os.getenv("MOCK_SWITCH_API_VERSION", "10.09"),
        username=os.getenv("MOCK_SWITCH_USER", "admin"),
        password=os.getenv("MOCK_SWITCH_PASS", "admin"),
        switch_name=os.getenv("MOCK_SWITCH_NAME", "aos-cx-sim"),
    )
    uvicorn.run(standalone_app, host="0.0.0.0", port=port)
