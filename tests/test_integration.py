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
    # Codes are handed out in the order Mist returned them (CX-CLAIM-0001..03),
    # to switches in dashboard list order.
    assert [r.claim_code_used for r in results] == [
        "CX-CLAIM-0001",
        "CX-CLAIM-0002",
        "CX-CLAIM-0003",
    ]
    db.close()

    for switch, expected_code in zip(switches, ["CX-CLAIM-0001", "CX-CLAIM-0002", "CX-CLAIM-0003"]):
        info = switch.app.state.system_info
        assert info["aruba_central"]["activation_key"] == expected_code
        fullconfigs = switch.app.state.fullconfigs
        assert fullconfigs["startup-config"] == fullconfigs["running-config"]


def test_insufficient_mist_codes_fails_without_pushing_anything(mock_mist, make_mock_switch):
    import mock_mist as mist_module

    # Mock inventory only has 3 eligible CX codes; ask for 4 switches.
    switches = [make_mock_switch() for _ in range(4)]
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
    assert job.status == "failed"
    assert "3" in job.error_message and "4" in job.error_message
    assert all(r.status == "pending" for r in job.switches)
    db.close()

    for switch in switches:
        info = switch.app.state.system_info
        assert info["aruba_central"]["activation_key"] is None
