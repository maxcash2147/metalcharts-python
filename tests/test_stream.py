from __future__ import annotations

import json

import httpx
import pytest

from conftest import ChunkStream, make_client
from metalcharts import APIConnectionError, MetalChartsError, PlanRequiredError, RateLimitError
from metalcharts._sse import SSEDecoder, iter_events, iter_lines

SSE_HEADERS = {"content-type": "text/event-stream"}
XAU = {"symbol": "XAU", "price": 2650.5, "exchange": "SPOT", "marketType": "spot", "timestamp": "2026-11-12T10:00:00Z"}
XAG = {"symbol": "XAG", "price": 31.2, "exchange": "SPOT", "marketType": "spot", "timestamp": "2026-11-12T10:00:00Z"}


def snapshot(*quotes) -> bytes:
    data = {"data": {q["symbol"]: q for q in quotes}, "timestamp": "2026-11-12T10:00:00Z"}
    return f"event: snapshot\ndata: {json.dumps(data)}\n\n".encode()


def price(quote) -> bytes:
    return f"event: price\ndata: {json.dumps(quote)}\n\n".encode()


def sse_response(*chunks: bytes, stream: "ChunkStream | None" = None) -> httpx.Response:
    return httpx.Response(200, headers=SSE_HEADERS, stream=stream or ChunkStream(chunks))


# -- parser ------------------------------------------------------------------


def test_iter_lines_handles_every_line_ending_and_split_chunks():
    chunks = [b"a\r", b"\nb\n", b"c\rd", b"\r\n\r", b"\n"]
    assert list(iter_lines(chunks)) == ["a", "b", "c", "d", ""]


def test_iter_lines_handles_multibyte_characters_split_across_chunks():
    text = "data: été\n".encode()
    assert list(iter_lines([text[:7], text[7:]])) == ["data: été"]


def test_decoder_rules():
    stream = (
        b": ping\n\n"
        b"event: price\ndata: {\"a\":\ndata: 1}\nid: 7\nretry: 2500\n\n"
        b"data: plain\n\n"
        b"event: price\ndata: cut off at the end"
    )
    events = list(iter_events([stream]))
    assert len(events) == 2
    assert events[0].event == "price"
    assert events[0].data == '{"a":\n1}'
    assert events[0].id == "7"
    assert events[0].retry == 2500
    assert events[1].event == "message"
    assert events[1].id == "7"  # the last event id carries over


def test_decoder_ignores_blank_events_and_unknown_fields():
    decoder = SSEDecoder()
    for line in ["foo: bar", "event: price", ""]:
        assert decoder.decode(line) is None


# -- stream_prices -----------------------------------------------------------


def test_stream_yields_snapshot_then_price_and_skips_heartbeats():
    unknown = b"event: status\ndata: {\"note\": \"from a later API version\"}\n\n"
    client, rec = make_client(
        lambda r: sse_response(snapshot(XAU, XAG), b": ping\n\n", unknown, price({**XAU, "price": 2651.0}))
    )
    events = list(client.stream_prices(["XAU", "XAG"], currency="EUR", reconnect=False))

    assert [e.type for e in events] == ["snapshot", "price"]
    assert set(events[0].quotes) == {"XAU", "XAG"}
    assert events[1].quotes == {"XAU": {**XAU, "price": 2651.0}}
    assert events[1].data["symbol"] == "XAU"

    request = rec.requests[0]
    assert request.url.path == "/v1/stream/prices"
    assert dict(request.url.params) == {"symbols": "XAU,XAG", "currency": "EUR"}
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["authorization"].startswith("Bearer mc_live_")


def test_latest_prices_dict_pattern():
    client, _ = make_client(lambda r: sse_response(snapshot(XAU, XAG), price({**XAG, "price": 31.5})))
    latest = {}
    for event in client.stream_prices(reconnect=False):
        latest.update(event.quotes)
    assert latest["XAU"]["price"] == 2650.5
    assert latest["XAG"]["price"] == 31.5


def test_stream_event_split_across_chunks():
    body = snapshot(XAU)
    chunks = [body[i : i + 5] for i in range(0, len(body), 5)]
    client, _ = make_client(lambda r: sse_response(*chunks))
    events = list(client.stream_prices(reconnect=False))
    assert events[0].quotes["XAU"]["price"] == 2650.5


def test_stream_reconnects_after_server_close_and_resends_last_event_id():
    bodies = [
        [snapshot(XAU), b"id: 41\nevent: price\ndata: " + json.dumps(XAU).encode() + b"\n\n"],
        [snapshot(XAU)],
    ]
    client, rec = make_client(lambda r: sse_response(*bodies.pop(0)))
    stream = client.stream_prices()
    events = [next(stream) for _ in range(3)]
    stream.close()

    assert [e.type for e in events] == ["snapshot", "price", "snapshot"]
    assert len(rec.requests) == 2
    assert "last-event-id" not in rec.requests[0].headers
    assert rec.requests[1].headers["last-event-id"] == "41"


def test_closing_the_generator_closes_the_response():
    body = ChunkStream([snapshot(XAU), price(XAU), price(XAU)])
    client, _ = make_client(lambda r: sse_response(stream=body))
    stream = client.stream_prices()
    assert next(stream).type == "snapshot"
    assert not body.closed
    stream.close()
    assert body.closed


def test_stream_plan_error_is_raised_without_retry():
    body = {"error": "Streaming needs the Pro plan or above.", "required_plan": "pro", "upgrade_url": "https://metalcharts.org/pricing?tab=api"}
    client, rec = make_client(lambda r: httpx.Response(403, json=body))
    with pytest.raises(PlanRequiredError) as info:
        next(client.stream_prices())
    assert info.value.required_plan == "pro"
    assert len(rec.requests) == 1


def test_stream_429_on_first_connection_is_raised():
    client, rec = make_client(lambda r: httpx.Response(429, json={"error": "Too many concurrent streams"}, headers={"Retry-After": "5"}))
    with pytest.raises(RateLimitError):
        next(client.stream_prices())
    assert len(rec.requests) == 1


def test_stream_429_on_reconnect_waits_and_retries():
    answers = [
        sse_response(snapshot(XAU)),
        httpx.Response(429, json={"error": "Too many concurrent streams"}, headers={"Retry-After": "7"}),
        sse_response(snapshot(XAG)),
    ]
    client, rec = make_client(lambda r: answers.pop(0))
    waits = []
    client._sleep = waits.append
    stream = client.stream_prices()
    first, second = next(stream), next(stream)
    stream.close()

    assert list(first.quotes) == ["XAU"]
    assert list(second.quotes) == ["XAG"]
    assert waits[-1] >= 7


def test_stream_retries_server_errors_and_connection_failures():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down", request=request)
        if calls["n"] == 2:
            return httpx.Response(502, text="Bad Gateway")
        return sse_response(snapshot(XAU))

    client, _ = make_client(handler)
    waits = []
    client._sleep = waits.append
    stream = client.stream_prices()
    assert next(stream).type == "snapshot"
    stream.close()
    assert len(waits) == 2
    assert waits[1] > waits[0] * 0.5  # backoff grows (with jitter)


def test_stream_gives_up_after_max_reconnect_attempts():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    client, rec = make_client(handler)
    with pytest.raises(APIConnectionError):
        next(client.stream_prices(max_reconnect_attempts=3))
    assert len(rec.requests) == 4


def test_stream_without_reconnect_raises_connection_errors():
    def handler(request):
        raise httpx.ReadTimeout("silent", request=request)

    client, rec = make_client(handler)
    with pytest.raises(APIConnectionError):
        next(client.stream_prices(reconnect=False))
    assert len(rec.requests) == 1


def test_stream_rejects_non_event_stream_response():
    client, _ = make_client(lambda r: httpx.Response(200, json={"success": True}))
    with pytest.raises(MetalChartsError, match="text/event-stream"):
        next(client.stream_prices())


def test_server_retry_field_sets_backoff_base():
    answers = [sse_response(b"retry: 100\n" + snapshot(XAU)), sse_response(snapshot(XAU))]
    client, _ = make_client(lambda r: answers.pop(0))
    waits = []
    client._sleep = waits.append
    stream = client.stream_prices()
    next(stream)
    next(stream)
    stream.close()
    assert waits and waits[0] <= 0.1
