"""Tests app/cx_client.py against the real mock switch server (tests/conftest.py),
exercising the actual pyaoscx login/POST/checkpoint flow end-to-end."""
import pytest

from app.cx_client import CxClientError, push_registration_code


def test_push_registration_code_writes_field_and_checkpoints(make_mock_switch):
    switch = make_mock_switch(username="admin", password="admin")

    push_registration_code(
        ip=f"127.0.0.1:{switch.port}",
        username="admin",
        password="admin",
        registration_code="MOCK-REG-CODE-0001",
        scheme="http",
    )

    mist_info = switch.app.state.mist_info
    assert mist_info["registration_code"] == "MOCK-REG-CODE-0001"
    assert mist_info["connectivity_status"]["status"] == "connected"

    fullconfigs = switch.app.state.fullconfigs
    assert fullconfigs["startup-config"] == fullconfigs["running-config"]


def test_push_registration_code_raises_on_bad_credentials(make_mock_switch):
    switch = make_mock_switch(username="admin", password="admin")

    with pytest.raises(CxClientError):
        push_registration_code(
            ip=f"127.0.0.1:{switch.port}",
            username="admin",
            password="wrong-password",
            registration_code="MOCK-REG-CODE-0001",
            scheme="http",
        )
