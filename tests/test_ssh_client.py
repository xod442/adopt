"""Tests app/ssh_client.py against the real mock SSH server (tests/conftest.py),
exercising the actual paramiko interactive-shell flow end-to-end."""
import pytest

from app.ssh_client import SshClientError, clear_mist_registration


def test_clear_mist_registration_success(make_mock_ssh_switch):
    switch = make_mock_ssh_switch(username="admin", password="admin")

    output = clear_mist_registration(
        "127.0.0.1", "admin", "admin", port=switch.port, timeout=10
    )
    assert "cleared successfully" in output.lower()


def test_clear_mist_registration_auto_confirms_yn_prompt(make_mock_ssh_switch):
    switch = make_mock_ssh_switch(username="admin", password="admin", require_confirm=True)

    output = clear_mist_registration(
        "127.0.0.1", "admin", "admin", port=switch.port, timeout=10
    )
    assert "continue?" in output.lower()
    assert "cleared successfully" in output.lower()


def test_clear_mist_registration_raises_on_bad_credentials(make_mock_ssh_switch):
    switch = make_mock_ssh_switch(username="admin", password="admin")

    with pytest.raises(SshClientError):
        clear_mist_registration(
            "127.0.0.1", "admin", "wrong-password", port=switch.port, timeout=10
        )


def test_clear_mist_registration_raises_on_unreachable_host(make_mock_ssh_switch):
    # No server listening on this port at all.
    with pytest.raises(SshClientError):
        clear_mist_registration("127.0.0.1", "admin", "admin", port=1, timeout=2)
