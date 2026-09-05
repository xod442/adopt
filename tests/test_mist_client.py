"""Tests app/mist_client.py against the real mock Mist server (tests/conftest.py)."""
from app.mist_client import MistClientError, fetch_cx_adoption_codes


def test_fetch_returns_only_cx_codes_up_to_needed(mock_mist):
    import mock_mist as mist_module

    codes = fetch_cx_adoption_codes(
        host="unused",
        org_id=mist_module.ORG_ID,
        api_token=mist_module.API_TOKEN,
        needed=2,
        base_url_override=mock_mist.base_url,
    )
    assert len(codes) == 2
    assert all(c["claim_code"].startswith("CX-CLAIM-") for c in codes)


def test_fetch_excludes_ex_and_already_claimed_devices(mock_mist):
    import mock_mist as mist_module

    codes = fetch_cx_adoption_codes(
        host="unused",
        org_id=mist_module.ORG_ID,
        api_token=mist_module.API_TOKEN,
        needed=100,  # ask for more than exist, to see the full eligible set
        base_url_override=mock_mist.base_url,
    )
    claim_codes = {c["claim_code"] for c in codes}
    assert "EX-CLAIM-0001" not in claim_codes
    assert None not in claim_codes
    assert claim_codes == {"CX-CLAIM-0001", "CX-CLAIM-0002", "CX-CLAIM-0003"}


def test_fetch_raises_on_bad_token(mock_mist):
    import mock_mist as mist_module

    try:
        fetch_cx_adoption_codes(
            host="unused",
            org_id=mist_module.ORG_ID,
            api_token="wrong-token",
            needed=1,
            base_url_override=mock_mist.base_url,
        )
        assert False, "expected MistClientError"
    except MistClientError as exc:
        assert "401" in str(exc)
