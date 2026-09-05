"""Tests app/cx_client.py against the real mock switch server (tests/conftest.py),
exercising the actual pyaoscx login/GET/PUT/checkpoint flow end-to-end."""
import pytest

from app.cx_client import CxClientError, push_adoption_code


def test_push_adoption_code_writes_field_and_checkpoints(make_mock_switch):
    switch = make_mock_switch(username="admin", password="admin")

    push_adoption_code(
        ip=f"127.0.0.1:{switch.port}",
        username="admin",
        password="admin",
        claim_code="CX-CLAIM-0001",
        scheme="http",
    )

    system_info = switch.app.state.system_info
    assert system_info["aruba_central"]["activation_key"] == "CX-CLAIM-0001"
    assert system_info["aruba_central"]["enabled"] is True

    fullconfigs = switch.app.state.fullconfigs
    assert fullconfigs["startup-config"] == fullconfigs["running-config"]


def test_push_adoption_code_raises_on_bad_credentials(make_mock_switch):
    switch = make_mock_switch(username="admin", password="admin")

    with pytest.raises(CxClientError):
        push_adoption_code(
            ip=f"127.0.0.1:{switch.port}",
            username="admin",
            password="wrong-password",
            claim_code="CX-CLAIM-0001",
            scheme="http",
        )
