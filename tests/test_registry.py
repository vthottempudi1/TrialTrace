##############################################################################
# tests/test_registry.py
#
# PURPOSE: Prove every outcome of a fetch maps to the right status.
#          A fake network replaces the internet, so tests are fast and offline.
##############################################################################

import httpx
import pytest

from trialtrace.config import Settings
from trialtrace.sources.registry import fetch_study

NCT = "NCT04644237"


def fetch_with(handler):
    """Run fetch_study against a fake network that answers with `handler`."""
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return fetch_study(http, Settings(), NCT)


def test_200_is_ok_with_body():
    result = fetch_with(lambda request: httpx.Response(200, content=b'{"a": 1}'))
    assert result.status == "ok"
    assert result.body == b'{"a": 1}'


def test_404_is_not_found():
    result = fetch_with(lambda request: httpx.Response(404))
    assert result.status == "not_found"
    assert result.body is None


def test_server_error_is_unavailable_not_not_found():
    # A broken server must never read as "this trial does not exist"
    for code in (429, 500, 503):
        result = fetch_with(lambda request, code=code: httpx.Response(code))
        assert result.status == "unavailable"
        assert result.error_code == f"http_{code}"


def test_timeout_is_unavailable():
    def slow(request):
        raise httpx.ReadTimeout("too slow")

    result = fetch_with(slow)
    assert result.status == "unavailable"
    assert result.error_code == "timeout"


def test_network_error_is_unavailable():
    def down(request):
        raise httpx.ConnectError("no connection")

    result = fetch_with(down)
    assert result.status == "unavailable"
    assert result.error_code == "network_error"


def test_bad_nct_id_is_rejected_before_any_request():
    def must_not_be_called(request):
        raise AssertionError("no request should be sent")

    with httpx.Client(transport=httpx.MockTransport(must_not_be_called)) as http:
        with pytest.raises(ValueError):
            fetch_study(http, Settings(), "NCT123/../x")
