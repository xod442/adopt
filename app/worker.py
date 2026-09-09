"""Background orchestration for one adoption run.

Flow (matches the requested design):
  1. Collect exactly `len(ips)` fresh CX registration codes from Mist (one
     GET per code — see app/mist_client.py), store them in the temp db
     (AdoptionCode rows). If Mist fails to supply any one of them, the job
     fails here — nothing is pushed to any switch.
  2. For each switch IP (in dashboard list order), pop the oldest unconsumed
     registration code from the db and push it to that switch via pyaoscx,
     then write memory. Switches are pushed concurrently (bounded by
     config.PUSH_CONCURRENCY) since each is an independent device.

Credentials (Mist API token, AOS-CX username/password) are passed straight
through as function arguments and never written to the temp db.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, cx_client, mist_client
from .db import SessionLocal
from .models import AdoptionCode, AdoptionJob, SwitchResult

logger = logging.getLogger("adopt.worker")

# Guards the "claim the oldest unconsumed adoption code" read-then-write —
# without this, two switch-push threads can both read the same code before
# either commits consumed=True. Only held briefly (a couple of small db
# queries), not around the slow network calls to the switch itself.
_code_assignment_lock = threading.Lock()


def start_job_thread(job_id: int, **kwargs) -> None:
    thread = threading.Thread(
        target=run_adoption_job, args=(job_id,), kwargs=kwargs, daemon=True
    )
    thread.start()


def run_adoption_job(
    job_id: int,
    mist_host: str,
    org_id: str,
    mist_token: str,
    cx_username: str,
    cx_password: str,
    mist_base_url_override: str | None = None,
    cx_api_version: str | None = None,
    cx_scheme: str = "https",
) -> None:
    db = SessionLocal()
    try:
        job = db.get(AdoptionJob, job_id)
        if job is None:
            logger.error("Job %s vanished before it could start", job_id)
            return

        switch_count = job.switch_count
        job.status = "collecting_codes"
        db.commit()

        try:
            codes = mist_client.fetch_cx_registration_codes(
                host=mist_host,
                org_id=org_id,
                api_token=mist_token,
                needed=switch_count,
                base_url_override=mist_base_url_override,
            )
        except mist_client.MistClientError as exc:
            job.status = "failed"
            job.error_message = f"Mist API error: {exc}"
            db.commit()
            return

        for code in codes:
            db.add(AdoptionCode(job_id=job_id, registration_code=code))
        job.status = "pushing"
        db.commit()
    finally:
        db.close()

    # Push to switches concurrently; each worker uses its own db session.
    with ThreadPoolExecutor(max_workers=max(1, config.PUSH_CONCURRENCY)) as pool:
        switch_ids = _switch_ids_for_job(job_id)
        futures = [
            pool.submit(
                _push_one,
                job_id,
                switch_id,
                cx_username,
                cx_password,
                cx_api_version,
                cx_scheme,
            )
            for switch_id in switch_ids
        ]
        for future in as_completed(futures):
            future.result()  # re-raise unexpected bugs; per-switch errors are already caught

    _finalize_job(job_id)


def _switch_ids_for_job(job_id: int) -> list[int]:
    db = SessionLocal()
    try:
        rows = (
            db.query(SwitchResult.id)
            .filter(SwitchResult.job_id == job_id)
            .order_by(SwitchResult.order_index)
            .all()
        )
        return [row[0] for row in rows]
    finally:
        db.close()


def _push_one(
    job_id: int,
    switch_result_id: int,
    cx_username: str,
    cx_password: str,
    cx_api_version: str | None,
    cx_scheme: str,
) -> None:
    db = SessionLocal()
    try:
        switch = db.get(SwitchResult, switch_result_id)
        switch.status = "adopting"
        db.commit()

        with _code_assignment_lock:
            code_row = (
                db.query(AdoptionCode)
                .filter(AdoptionCode.job_id == job_id, AdoptionCode.consumed == False)  # noqa: E712
                .order_by(AdoptionCode.id)
                .first()
            )
            if code_row is None:
                switch.status = "failed"
                switch.message = "No registration code left to assign (this should not happen)"
                db.commit()
                return

            code_row.consumed = True
            switch.registration_code_used = code_row.registration_code
            db.commit()

        try:
            cx_client.push_registration_code(
                ip=switch.ip,
                username=cx_username,
                password=cx_password,
                registration_code=code_row.registration_code,
                api_version=cx_api_version,
                scheme=cx_scheme,
            )
        except cx_client.CxClientError as exc:
            switch.status = "failed"
            switch.message = str(exc)
            db.commit()
            return

        switch.status = "success"
        switch.message = "Registration code written and saved to startup-config"
        db.commit()
    except Exception as exc:  # belt-and-suspenders: never let a thread die silently
        logger.exception("Unexpected error adopting switch result %s", switch_result_id)
        switch = db.get(SwitchResult, switch_result_id)
        if switch is not None:
            switch.status = "failed"
            switch.message = f"Unexpected error: {exc}"
            db.commit()
    finally:
        db.close()


def _finalize_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(AdoptionJob, job_id)
        if job is None or job.status == "failed":
            return
        any_failed = (
            db.query(SwitchResult)
            .filter(SwitchResult.job_id == job_id, SwitchResult.status == "failed")
            .first()
            is not None
        )
        job.status = "complete_with_errors" if any_failed else "complete"
        db.commit()
    finally:
        db.close()
