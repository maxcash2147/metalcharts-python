from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from conftest import FAKE_KEY, make_client
from metalcharts import (
    APIConnectionError,
    AuthenticationError,
    MetalCharts,
    MetalChartsError,
    PlanRequiredError,
    RateLimitError,
)

QUOTE = {
    "symbol": "XAU",
    "price": 2650.5,
    "change24h": 12.1,
    "changePercent24h": 0.46,
    "exchange": "SPOT",
    "marketType": "spot",
    "timestamp": "2026-11-12T10:00:00.000Z",
}


def json_response(body, status=200, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def test_key_from_environment(monkeypatch):
    monkeypatch.setenv("METALCHARTS_API_KEY", FAKE_KEY)
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return json_response({"success": True, "data": {}})

    client = MetalCharts(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.prices()
    assert seen["auth"] == f"Bearer {FAKE_KEY}"


def test_missing_key_raises():
    with pytest.raises(MetalChartsError, match="METALCHARTS_API_KEY"):
        MetalCharts()


def test_repr_does_not_show_the_key():
    client, _ = make_client(lambda r: json_response({}))
    assert "not_a_real_key" not in repr(client)


def test_prices_request():
    client, rec = make_client(lambda r: json_response({"success": True, "data": {"XAU": QUOTE}}))
    body = client.prices(["XAU", "XAG"], currency="EUR", unit="g")

    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/prices"
    assert dict(request.url.params) == {"symbols": "XAU,XAG", "currency": "EUR", "unit": "g"}
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["user-agent"].startswith("metalcharts-python/")
    assert body["data"]["XAU"]["price"] == 2650.5


def test_prices_without_arguments_sends_no_params():
    client, rec = make_client(lambda r: json_response({"success": True, "data": {}}))
    client.prices()
    assert str(rec.requests[0].url) == "https://api.metalcharts.org/v1/prices"


def test_history_request_with_dates():
    bars = [{"timestamp": "2026-10-01T00:00:00Z", "price": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
    client, rec = make_client(lambda r: json_response({"success": True, "data": bars, "symbol": "XAU"}))
    body = client.history("XAU", interval="1d", start=dt.date(2026, 10, 1), end="2026-10-31", karat=22)

    request = rec.requests[0]
    assert request.url.path == "/v1/history/XAU"
    assert dict(request.url.params) == {
        "interval": "1d",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "karat": "22",
    }
    assert body["data"][0]["close"] == 1


def test_history_symbol_is_path_escaped():
    client, rec = make_client(lambda r: json_response({"success": True, "data": []}))
    client.history("XAU/../usage")
    assert rec.requests[0].url.raw_path.decode().startswith("/v1/history/XAU%2F..%2Fusage")


def test_instruments_usage_metals_convert_paths():
    client, rec = make_client(lambda r: json_response({"success": True, "data": []}))
    client.instruments(asset_class="commodity", search="gold", limit=5)
    client.usage()
    client.metals()
    client.convert("XAU", "EUR", amount=10, unit="g")

    paths = [r.url.path for r in rec.requests]
    assert paths == ["/v1/instruments", "/v1/usage", "/v1/metals", "/v1/convert"]
    assert dict(rec.requests[0].url.params) == {"asset_class": "commodity", "search": "gold", "limit": "5"}
    assert dict(rec.requests[3].url.params) == {"from": "XAU", "to": "EUR", "amount": "10", "unit": "g"}


def test_get_merges_params_and_kwargs():
    client, rec = make_client(lambda r: json_response({"success": True, "data": []}))
    client.get("v1/cot", {"metal": "XAU"}, range="1Y", type=None, flag=True)
    request = rec.requests[0]
    assert request.url.path == "/v1/cot"
    assert dict(request.url.params) == {"metal": "XAU", "range": "1Y", "flag": "true"}


def test_get_returns_text_for_csv():
    csv = "timestamp,price\n2026-10-01T00:00:00Z,2650.5\n"
    client, _ = make_client(lambda r: httpx.Response(200, text=csv, headers={"content-type": "text/csv"}))
    assert client.get("/v1/history/XAU", format="csv") == csv


def test_get_rejects_full_urls():
    client, _ = make_client(lambda r: json_response({}))
    with pytest.raises(ValueError):
        client.get("https://example.com/v1/prices")


def test_401_raises_authentication_error():
    body = {"error": "API access requires an API key", "upgrade_url": "https://metalcharts.org/pricing?tab=api"}
    client, _ = make_client(lambda r: json_response(body, 401))
    with pytest.raises(AuthenticationError) as info:
        client.prices()
    assert info.value.status_code == 401
    assert "requires an API key" in str(info.value)


def test_403_raises_plan_required_error():
    body = {
        "error": "Your plan does not include this data class. Upgrade to pro or higher.",
        "required_plan": "pro",
        "upgrade_url": "https://metalcharts.org/pricing?tab=api",
    }
    client, _ = make_client(lambda r: json_response(body, 403))
    with pytest.raises(PlanRequiredError) as info:
        client.get("/v1/cot", metal="XAU")
    assert info.value.required_plan == "pro"
    assert info.value.upgrade_url == "https://metalcharts.org/pricing?tab=api"


def test_403_history_depth_has_no_required_plan():
    body = {"error": "Requested history exceeds your plan's limit.", "upgrade_url": "https://metalcharts.org/pricing?tab=api"}
    client, _ = make_client(lambda r: json_response(body, 403))
    with pytest.raises(PlanRequiredError) as info:
        client.history("XAU", range="5Y")
    assert info.value.required_plan is None


def test_429_uses_retry_after_header():
    body = {"error": "Rate limit exceeded", "limit": 10, "used": 11, "window": "1m", "retryAt": "2026-11-12T10:01:00.000Z"}
    client, rec = make_client(lambda r: json_response(body, 429, {"Retry-After": "17"}))
    with pytest.raises(RateLimitError) as info:
        client.prices()
    assert info.value.retry_after == 17
    assert info.value.window == "1m"
    assert info.value.limit == 10
    assert info.value.retry_at == dt.datetime(2026, 11, 12, 10, 1, tzinfo=dt.timezone.utc)
    assert len(rec.requests) == 1  # 429 is not retried automatically


def test_429_falls_back_to_retry_at():
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=120)).isoformat()
    client, _ = make_client(lambda r: json_response({"error": "Monthly quota exceeded", "retryAt": future, "window": "1mo"}, 429))
    with pytest.raises(RateLimitError) as info:
        client.prices()
    assert 100 < info.value.retry_after <= 120


def test_400_is_a_plain_metalcharts_error():
    client, _ = make_client(lambda r: json_response({"success": False, "error": "Unsupported unit: gram"}, 400))
    with pytest.raises(MetalChartsError) as info:
        client.prices(unit="gram")
    assert type(info.value) is MetalChartsError
    assert info.value.status_code == 400
    assert info.value.message == "Unsupported unit: gram"


def test_non_json_error_body():
    client, _ = make_client(lambda r: httpx.Response(404, text="NOT_FOUND"))
    with pytest.raises(MetalChartsError, match="NOT_FOUND"):
        client.get("/v1/nope")


def test_server_errors_are_retried():
    answers = [json_response({"error": "x", "reference": "ab12"}, 503), json_response({"success": True, "data": {}})]
    client, rec = make_client(lambda r: answers.pop(0))
    assert client.prices()["success"] is True
    assert len(rec.requests) == 2


def test_server_error_after_retries_keeps_reference():
    client, rec = make_client(lambda r: json_response({"success": False, "error": "Internal error", "reference": "ab12cd34"}, 500))
    with pytest.raises(MetalChartsError) as info:
        client.prices()
    assert info.value.reference == "ab12cd34"
    assert len(rec.requests) == 3  # first try plus max_retries=2


def test_connection_errors_become_api_connection_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client, rec = make_client(handler, max_retries=1)
    with pytest.raises(APIConnectionError):
        client.prices()
    assert len(rec.requests) == 2


def test_errors_share_a_base_class():
    for cls in (APIConnectionError, AuthenticationError, PlanRequiredError, RateLimitError):
        assert issubclass(cls, MetalChartsError)


def test_context_manager_closes_own_client():
    with MetalCharts(FAKE_KEY) as client:
        assert not client._http.is_closed
    assert client._http.is_closed


def test_json_body_round_trip():
    payload = {"success": True, "data": {"XAU": QUOTE}, "count": 1}
    client, _ = make_client(lambda r: httpx.Response(200, content=json.dumps(payload).encode(), headers={"content-type": "application/json; charset=utf-8"}))
    assert client.prices("XAU") == payload
