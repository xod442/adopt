"""Background orchestration for one 'clear registration' rollback run.

Simpler than app/worker.py's adoption flow: there's no Mist API involved
and no code to mint/assign — every switch gets the exact same action
(SSH in, run `clear mist registration-info`), so switches are just pushed
to concurrently (bounded by config.PUSH_CONCURRENCY), same as the
adoption flow, for the same reason (each switch is an independent
device).
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, ssh_client
from .db import SessionLocal
from .models import ClearJob, ClearSwitchResult

logger = logging.getLogger("adopt.clear_worker")


def start_clear_job_thread(job_id: int, **kwargs) -> None:
    thread = threading.Thread(
        target=run_clear_job, args=(job_id,), kwargs=kwargs, daemon=True
    )
    thread.start()


def run_clear_job(
    job_id: int,
    ssh_username: str,
    ssh_password: str,
) -> None:
    db = SessionLocal()
    try:
        job = db.get(ClearJob, job_id)
        if job is None:
            logger.error("Clear job %s vanished before it could start", job_id)
            return
        job.status = "running"
        db.commit()
    finally:
        db.close()

    with ThreadPoolExecutor(max_workers=max(1, config.PUSH_CONCURRENCY)) as pool:
        switch_ids = _switch_ids_for_job(job_id)
        futures = [pool.submit(_clear_one, switch_id, ssh_username, ssh_password) for switch_id in switch_ids]
        for future in as_completed(futures):
            future.result()  # re-raise unexpected bugs; per-switch errors are already caught

    _finalize_job(job_id)


def _switch_ids_for_job(job_id: int) -> list[int]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ClearSwitchResult.id)
            .filter(ClearSwitchResult.job_id == job_id)
            .order_by(ClearSwitchResult.order_index)
            .all()
        )
        return [row[0] for row in rows]
    finally:
        db.close()


def _clear_one(switch_result_id: int, ssh_username: str, ssh_password: str) -> None:
    db = SessionLocal()
    try:
        switch = db.get(ClearSwitchResult, switch_result_id)
        switch.status = "running"
        db.commit()

        try:
            output = ssh_client.clear_mist_registration(
                ip=switch.ip,
                username=ssh_username,
                password=ssh_password,
            )
        except ssh_client.SshClientError as exc:
            switch.status = "failed"
            switch.message = str(exc)
            db.commit()
            return

        switch.status = "success"
        switch.message = output or "(command sent — no output captured)"
        db.commit()
    except Exception as exc:  # belt-and-suspenders: never let a thread die silently
        logger.exception("Unexpected error clearing switch result %s", switch_result_id)
        switch = db.get(ClearSwitchResult, switch_result_id)
        if switch is not None:
            switch.status = "failed"
            switch.message = f"Unexpected error: {exc}"
            db.commit()
    finally:
        db.close()


def _finalize_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ClearJob, job_id)
        if job is None:
            return
        any_failed = (
            db.query(ClearSwitchResult)
            .filter(ClearSwitchResult.job_id == job_id, ClearSwitchResult.status == "failed")
            .first()
            is not None
        )
        job.status = "complete_with_errors" if any_failed else "complete"
        db.commit()
    finally:
        db.close()
