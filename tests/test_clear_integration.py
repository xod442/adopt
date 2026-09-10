"""End-to-end test of the clear-registration rollback flow: real mock SSH
servers + app/clear_worker.py's orchestration (SSH -> clear mist
registration-info per switch), with no mocking of the paramiko layer
itself for any single switch's connection.

app/ssh_client.clear_mist_registration() takes a bare hostname and a
separate `port` (unlike cx_client's REST "host:port" convenience, since
paramiko's connect() wants them separate) — there's no port baked into
ClearSwitchResult.ip. To run several distinct real mock SSH servers (each
on its own ephemeral port) in one job without needing an actual DNS name
per switch, this test uses distinct fake hostnames ("mock-switch-1", ...)
and monkeypatches only the *port lookup* for those exact names via a thin
wrapper around the real function — the real connect/auth/shell logic still
runs unmodified underneath. A dict keyed by the exact ip string (not a
call-order counter) keeps this correct regardless of which order
ThreadPoolExecutor's worker threads happen to run in.
"""
import app.ssh_client as ssh_client
from app.clear_worker import run_clear_job
from app.db import SessionLocal
from app.models import ClearJob, ClearSwitchResult


def _make_job(db, switch_ips):
    job = ClearJob(switch_count=len(switch_ips), status="pending")
    db.add(job)
    db.flush()
    for i, ip in enumerate(switch_ips):
        db.add(ClearSwitchResult(job_id=job.id, order_index=i, ip=ip, status="pending"))
    db.commit()
    return job.id


def _run_with_patched_ports(job_id, port_by_ip, ssh_username="admin", ssh_password="admin"):
    real_clear = ssh_client.clear_mist_registration

    def _patched(ip, username, password, port=22, timeout=15.0):
        return real_clear("127.0.0.1", username, password, port=port_by_ip[ip], timeout=timeout)

    ssh_client.clear_mist_registration = _patched
    try:
        run_clear_job(job_id, ssh_username=ssh_username, ssh_password=ssh_password)
    finally:
        ssh_client.clear_mist_registration = real_clear


def test_full_clear_run_mixed_success_and_failure(make_mock_ssh_switch):
    good1 = make_mock_ssh_switch(username="admin", password="admin")
    good2 = make_mock_ssh_switch(username="admin", password="admin")
    bad = make_mock_ssh_switch(username="admin", password="different-password")

    port_by_ip = {
        "mock-switch-1": good1.port,
        "mock-switch-2": good2.port,
        "mock-switch-3": bad.port,
    }

    db = SessionLocal()
    job_id = _make_job(db, list(port_by_ip.keys()))
    db.close()

    _run_with_patched_ports(job_id, port_by_ip)

    db = SessionLocal()
    job = db.get(ClearJob, job_id)
    assert job.status == "complete_with_errors", job.error_message
    results = {s.ip: (s.status, s.message) for s in job.switches}
    assert results["mock-switch-1"][0] == "success"
    assert "cleared successfully" in results["mock-switch-1"][1].lower()
    assert results["mock-switch-2"][0] == "success"
    assert results["mock-switch-3"][0] == "failed"
    assert "authentication" in results["mock-switch-3"][1].lower()
    db.close()


def test_full_clear_run_all_succeed(make_mock_ssh_switch):
    switches = [make_mock_ssh_switch(username="admin", password="admin") for _ in range(3)]
    port_by_ip = {f"mock-switch-{i}": s.port for i, s in enumerate(switches)}

    db = SessionLocal()
    job_id = _make_job(db, list(port_by_ip.keys()))
    db.close()

    _run_with_patched_ports(job_id, port_by_ip)

    db = SessionLocal()
    job = db.get(ClearJob, job_id)
    assert job.status == "complete", job.error_message
    assert all(s.status == "success" for s in job.switches)
    db.close()
