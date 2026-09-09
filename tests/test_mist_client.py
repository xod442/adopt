"""Tests app/mist_client.py against the real mock Mist server (tests/conftest.py)."""
from app.mist_client import MistClientError, fetch_cx_registration_codes


def test_fetch_returns_needed_number_of_codes(mock_mist):
    import mock_mist as mist_module

    codes = fetch_cx_registration_codes(
        host="unused",
        org_id=mist_module.ORG_ID,
        api_token=mist_module.API_TOKEN,
        needed=3,
        base_url_override=mock_mist.base_url,
    )
    assert len(codes) == 3
    assert all(c.startswith("MOCK-REG-CODE-") for c in codes)


def test_fetch_returns_a_fresh_code_every_call(mock_mist):
    """Confirmed live against the real Mist API (2026-09-08): repeated calls
    to GET .../aoscx/register_cmd return a different code every time."""
    import mock_mist as mist_module

    codes = fetch_cx_registration_codes(
        host="unused",
        org_id=mist_module.ORG_ID,
        api_token=mist_module.API_TOKEN,
        needed=5,
        base_url_override=mock_mist.base_url,
    )
    assert len(set(codes)) == 5


def test_fetch_raises_on_bad_token(mock_mist):
    import mock_mist as mist_module

    try:
        fetch_cx_registration_codes(
            host="unused",
            org_id=mist_module.ORG_ID,
            api_token="wrong-token",
            needed=1,
            base_url_override=mock_mist.base_url,
        )
        assert False, "expected MistClientError"
    except MistClientError as exc:
        assert "401" in str(exc)


def test_fetch_raises_on_bad_org(mock_mist):
    import mock_mist as mist_module

    try:
        fetch_cx_registration_codes(
            host="unused",
            org_id="wrong-org",
            api_token=mist_module.API_TOKEN,
            needed=1,
            base_url_override=mock_mist.base_url,
        )
        assert False, "expected MistClientError"
    except MistClientError as exc:
        assert "404" in str(exc)
