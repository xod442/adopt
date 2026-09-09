"""Application factory and entrypoint for ADOPT — a no-login dashboard that
collects Mist/AOS-CX adoption codes and pushes them out to CX switches."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .db import SessionLocal, get_db, init_db
from .models import AdoptionJob, SwitchResult
from .worker import start_job_thread


def _parse_ips(raw: str) -> list[str]:
    """Split the switch-IP textarea on newlines/commas/whitespace, dedupe
    while preserving order, drop blanks."""
    seen: set[str] = set()
    ips: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        ip = chunk.strip()
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def create_app() -> FastAPI:
    app = FastAPI(title="ADOPT", docs_url=None, redoc_url=None, openapi_url=None)

    app.mount(
        "/assets",
        StaticFiles(directory=str(config.BASE_DIR / "app" / "static")),
        name="assets",
    )
    templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))

    @app.on_event("startup")
    def _startup():
        init_db()

    def _tmpl_ctx(request: Request, **extra):
        return {"request": request, "rp": config.ROOT_PATH, "config": config, **extra}

    @app.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            _tmpl_ctx(
                request,
                mist_host=config.DEFAULT_MIST_HOST,
                error=None,
            ),
        )

    @app.post("/adopt")
    def adopt(
        request: Request,
        mist_host: str = Form(...),
        org_id: str = Form(...),
        mist_token: str = Form(...),
        cx_username: str = Form(...),
        cx_password: str = Form(...),
        switch_ips: str = Form(...),
        db=Depends(get_db),
    ):
        ips = _parse_ips(switch_ips)
        if not ips:
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                _tmpl_ctx(
                    request,
                    mist_host=mist_host,
                    org_id=org_id,
                    cx_username=cx_username,
                    error="Enter at least one switch IP address.",
                ),
                status_code=400,
            )

        job = AdoptionJob(
            mist_host=mist_host.strip(),
            org_id=org_id.strip(),
            switch_count=len(ips),
            status="pending",
        )
        db.add(job)
        db.flush()  # assign job.id

        for i, ip in enumerate(ips):
            db.add(SwitchResult(job_id=job.id, order_index=i, ip=ip, status="pending"))
        db.commit()

        start_job_thread(
            job.id,
            mist_host=job.mist_host,
            org_id=job.org_id,
            mist_token=mist_token,
            cx_username=cx_username,
            cx_password=cx_password,
            mist_base_url_override=config.MIST_BASE_URL_OVERRIDE,
            cx_api_version=config.DEFAULT_AOSCX_API_VERSION,
            cx_scheme=config.CX_SCHEME,
        )

        return RedirectResponse(f"{config.ROOT_PATH}/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}")
    def job_status(request: Request, job_id: int, db=Depends(get_db)):
        job = db.get(AdoptionJob, job_id)
        if job is None:
            return RedirectResponse(f"{config.ROOT_PATH}/", status_code=303)
        return templates.TemplateResponse(
            request, "job_status.html", _tmpl_ctx(request, job=job)
        )

    @app.get("/jobs/{job_id}/data")
    def job_data(job_id: int, db=Depends(get_db)):
        job = db.get(AdoptionJob, job_id)
        if job is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {
            "id": job.id,
            "status": job.status,
            "error_message": job.error_message,
            "switch_count": job.switch_count,
            "switches": [
                {
                    "ip": s.ip,
                    "status": s.status,
                    "message": s.message,
                    "registration_code_used": s.registration_code_used,
                }
                for s in sorted(job.switches, key=lambda s: s.order_index)
            ],
        }

    return app


app = create_app()
