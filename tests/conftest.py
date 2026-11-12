from __future__ import annotations

from typing import Callable, Iterable, Iterator, List

import httpx
import pytest

from metalcharts import MetalCharts

FAKE_KEY = "mc_live_test_not_a_real_key"


class Recorder:
    """Collects requests and answers them from a handler."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


class ChunkStream(httpx.SyncByteStream):
    """A response body delivered in the given chunks, to test split lines."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self.chunks = list(chunks)
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> "tuple[MetalCharts, Recorder]":
    recorder = Recorder(handler)
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    client = MetalCharts(FAKE_KEY, http_client=http, **kwargs)
    client._sleep = lambda seconds: None  # no real waiting in tests
    return client, recorder


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METALCHARTS_API_KEY", raising=False)
