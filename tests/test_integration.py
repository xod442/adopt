"""End-to-end test of the whole adoption flow: real mock Mist server + real
mock AOS-CX switch servers + app/worker.py's orchestration (Mist code
collection -> temp db -> pyaoscx push -> write memory), with no mocking of
the HTTP/pyaoscx layer itself.
"""
from app.db import SessionLocal
from app.models import AdoptionJob, SwitchResult
from app.worker import run_adoption_job


def _make_job(db, switch_ips):
    job = AdoptionJob(mist_host="unused", org_id="unused", switch_count=len(switch_ips), status="pending")
    db.add(job)
    db.flush()
    for i, ip in enumerate(switch_ips):
        db.add(SwitchResult(job_id=job.id, order_index=i, ip=ip, status="pending"))
    db.commit()
    return job.id


def test_full_adoption_run_succeeds_in_order(mock_mist, make_mock_switch):
    import mock_mist as mist_module

    switches = [make_mock_switch() for _ in range(3)]
    switch_ips = [f"127.0.0.1:{s.port}" for s in switches]

    db = SessionLocal()
    job_id = _make_job(db, switch_ips)
    db.close()

    run_adoption_job(
        job_id,
        mist_host="unused",
        org_id=mist_module.ORG_ID,
        mist_token=mist_module.API_TOKEN,
        cx_username="admin",
        cx_password="admin",
        mist_base_url_override=mock_mist.base_url,
        cx_scheme="http",
    )

    db = SessionLocal()
    job = db.get(AdoptionJob, job_id)
    assert job.status == "complete", job.error_message
    results = sorted(job.switches, key=lambda s: s.order_index)
    assert [r.status for r in results] == ["success", "success", "success"]
    # Each switch got a distinct, freshly-minted registration code.
    codes_used = [r.registration_code_used for r in results]
    assert len(set(codes_used)) == 3
    db.close()

    for switch, code in zip(switches, codes_used):
        mist_info = switch.app.state.mist_info
        assert mist_info["registration_code"] == code
        fullconfigs = switch.app.state.fullconfigs
        assert fullconfigs["startup-config"] == fullconfigs["running-config"]


def test_mist_failure_fails_job_without_pushing_anything(mock_mist, make_mock_switch):
    switches = [make_mock_switch() for _ in range(2)]
    switch_ips = [f"127.0.0.1:{s.port}" for s in switches]

    db = SessionLocal()
    job_id = _make_job(db, switch_ips)
    db.close()

    run_adoption_job(
        job_id,
        mist_host="unused",
        org_id="wrong-org",
        mist_token="wrong-token",
        cx_username="admin",
        cx_password="admin",
        mist_base_url_override=mock_mist.base_url,
        cx_scheme="http",
    )

    db = SessionLocal()
    job = db.get(AdoptionJob, job_id)
    assert job.status == "failed"
    assert "Mist API error" in job.error_message
    assert all(r.status == "pending" for r in job.switches)
    db.close()

    for switch in switches:
        mist_info = switch.app.state.mist_info
        assert mist_info["registration_code"] is None
