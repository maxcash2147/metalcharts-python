from __future__ import annotations

import datetime as _dt
import logging
import os
import random
import time
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Union, cast

import httpx

from ._errors import (
    APIConnectionError,
    MetalChartsError,
    RateLimitError,
    error_from_response,
    parse_retry_after,
)
from ._sse import SSEDecoder, StreamEvent, iter_lines
from ._types import HistoryResponse, InstrumentsResponse, PricesResponse, UsageResponse
from ._version import __version__

__all__ = ["MetalCharts", "DEFAULT_BASE_URL", "ENV_VAR"]

DEFAULT_BASE_URL = "https://api.metalcharts.org"
ENV_VAR = "METALCHARTS_API_KEY"

logger = logging.getLogger("metalcharts")

ParamValue = Union[str, int, float, bool, _dt.date, Iterable[str], None]
Symbols = Union[str, Iterable[str], None]

_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_STREAM_EVENTS = frozenset({"snapshot", "price"})
# The stream sends a heartbeat about every 15 seconds, so 45 seconds of
# silence means the connection is dead even if the socket has not noticed.
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
_MAX_BACKOFF = 30.0


class MetalCharts:
    """Client for the MetalCharts API.

    ``api_key`` defaults to the ``METALCHARTS_API_KEY`` environment variable.
    Get a free key at https://metalcharts.org/metals-api.

    Every method returns the decoded JSON body. Errors raise a subclass of
    :class:`metalcharts.MetalChartsError`.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        key = api_key or os.environ.get(ENV_VAR)
        if not key:
            raise MetalChartsError(
                "No API key. Pass MetalCharts(api_key=...) or set the METALCHARTS_API_KEY "
                "environment variable. Free keys: https://metalcharts.org/metals-api"
            )
        self._api_key = key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._sleep = time.sleep

    def __repr__(self) -> str:
        return f"MetalCharts(base_url={self.base_url!r}, api_key='{self._api_key[:8]}...')"

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool (only if this client created it)."""
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "MetalCharts":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- endpoints ---------------------------------------------------------

    def prices(
        self,
        symbols: Symbols = None,
        *,
        currency: Optional[str] = None,
        unit: Optional[str] = None,
        karat: Union[int, str, None] = None,
    ) -> PricesResponse:
        """Latest prices, keyed by symbol under ``data``. One request covers every metal.

        ``symbols``: "XAU,XAG" or ["XAU", "XAG"]; omit for all metals.
        ``currency``: ISO code such as "EUR" (default USD).
        ``unit``: toz, g, kg, lb, mt or tola (default: each market's own unit).
        ``karat``: gold only, e.g. 22.
        """
        params = {"symbols": symbols, "currency": currency, "unit": unit, "karat": karat}
        return cast(PricesResponse, self._get("/v1/prices", params))

    def history(
        self,
        symbol: str,
        *,
        range: Optional[str] = None,
        interval: Optional[str] = None,
        start: Union[str, _dt.date, None] = None,
        end: Union[str, _dt.date, None] = None,
        currency: Optional[str] = None,
        unit: Optional[str] = None,
        karat: Union[int, str, None] = None,
    ) -> HistoryResponse:
        """OHLC bars for a metal, oldest first, under ``data``.

        ``symbol`` is a metal code: XAU, XAG, XPT, XPD, HG, ALI, NI, ZN, PB...
        ``range``: 1M, 6M, 1Y, 5Y, ALL. ``start``/``end``: dates or "YYYY-MM-DD";
        ``start`` overrides ``range``. ``interval``: 15m, 30m, 45m, 1h, 2h, 4h,
        8h, 1d, 1w or 1M. The free plan reads 30 days of daily bars.
        """
        params = {
            "range": range,
            "interval": interval,
            "start": start,
            "end": end,
            "currency": currency,
            "unit": unit,
            "karat": karat,
        }
        path = "/v1/history/" + _quote_segment(symbol)
        return cast(HistoryResponse, self._get(path, params))

    def instruments(
        self,
        *,
        asset_class: Optional[str] = None,
        exchange: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> InstrumentsResponse:
        """Every tracked instrument with its symbol, exchange and asset class."""
        params = {"asset_class": asset_class, "exchange": exchange, "search": search, "limit": limit}
        return cast(InstrumentsResponse, self._get("/v1/instruments", params))

    def usage(self) -> UsageResponse:
        """Your plan, its limits and what you have used. Does not count against your quota."""
        return cast(UsageResponse, self._get("/v1/usage", {}))

    def metals(self) -> Any:
        """The supported metals."""
        return self._get("/v1/metals", {})

    def convert(
        self,
        from_: str,
        to: str,
        *,
        amount: Union[int, float, str, None] = None,
        unit: Optional[str] = None,
        karat: Union[int, str, None] = None,
        date: Union[str, _dt.date, None] = None,
    ) -> Any:
        """Convert between a metal, a currency and a mass unit, e.g. ``convert("XAU", "EUR", amount=10, unit="g")``."""
        params = {"from": from_, "to": to, "amount": amount, "unit": unit, "karat": karat, "date": date}
        return self._get("/v1/convert", params)

    def get(self, path: str, params: Optional[Mapping[str, ParamValue]] = None, **kwargs: ParamValue) -> Any:
        """GET any endpoint, for the ones without a method here.

        ``client.get("/v1/cot", metal="XAU", range="1Y")``. Pass parameters
        whose names are Python keywords through ``params``. ``None`` values
        are dropped and lists are joined with commas. A ``format="csv"``
        response comes back as a string.
        """
        merged: Dict[str, ParamValue] = dict(params or {})
        merged.update(kwargs)
        return self._get(path, merged)

    def stream_prices(
        self,
        symbols: Symbols = None,
        *,
        currency: Optional[str] = None,
        unit: Optional[str] = None,
        reconnect: bool = True,
        max_reconnect_attempts: Optional[int] = None,
    ) -> Iterator[StreamEvent]:
        """Live prices over Server-Sent Events. Needs the Pro plan or above.

        Yields a ``snapshot`` event with every requested symbol first, then a
        ``price`` event each time a quote changes. ``event.quotes`` holds the
        quotes keyed by symbol for either type.

        The server may close the stream at any time. With ``reconnect=True``
        the client reconnects with exponential backoff (capped at 30 seconds)
        and the new connection starts with a fresh snapshot.
        ``max_reconnect_attempts`` caps consecutive failed attempts; ``None``
        keeps trying. Authentication and plan errors are raised straight away.
        Break out of the loop to close the connection.
        """
        params = _clean_params({"symbols": symbols, "currency": currency, "unit": unit})
        url = self._url("/v1/stream/prices")
        headers = self._headers(accept="text/event-stream")
        headers["Cache-Control"] = "no-cache"

        decoder = SSEDecoder()
        failures = 0
        connected_before = False
        retry_ms: Optional[int] = None

        while True:
            received = False
            wait: Optional[float] = None
            try:
                if decoder.last_event_id:
                    headers["Last-Event-ID"] = decoder.last_event_id
                with self._http.stream(
                    "GET", url, params=params, headers=headers, timeout=_STREAM_TIMEOUT
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        raise self._error(response)
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type:
                        raise MetalChartsError(
                            f"Expected text/event-stream, got {content_type or 'no content type'}",
                            status_code=response.status_code,
                        )
                    for line in iter_lines(response.iter_bytes()):
                        sse = decoder.decode(line)
                        if sse is None:
                            continue
                        if sse.retry is not None:
                            retry_ms = sse.retry
                        received = True
                        connected_before = True
                        failures = 0
                        # Event types this client doesn't know are skipped, so
                        # code written today keeps working if the API adds some.
                        if sse.event in _STREAM_EVENTS:
                            yield StreamEvent.from_sse(sse)
                if not reconnect:
                    return
                error: MetalChartsError = APIConnectionError("Stream closed by the server")
            except httpx.TransportError as exc:
                error = APIConnectionError(f"Stream connection failed: {exc}")
                if not reconnect:
                    raise error from exc
            except RateLimitError as exc:
                # Right after a drop the server can still count the old
                # connection for a moment, so a 429 on a reconnect is worth
                # one more wait. On the first connection it means the plan's
                # connection limit is reached, and waiting will not help.
                if not reconnect or not connected_before:
                    raise
                if exc.retry_after is not None and exc.retry_after > 300:
                    raise
                error = exc
                wait = exc.retry_after
            except MetalChartsError as exc:
                if not reconnect or exc.status_code is None or exc.status_code not in _RETRY_STATUSES:
                    raise
                error = exc

            if not received:
                failures += 1
            if max_reconnect_attempts is not None and failures > max_reconnect_attempts:
                raise error
            delay = _backoff(failures, retry_ms)
            if wait is not None:
                delay = max(delay, wait)
            logger.warning("MetalCharts stream: %s. Reconnecting in %.1fs", error, delay)
            self._sleep(delay)

    # -- internals ---------------------------------------------------------

    def _headers(self, accept: str = "application/json") -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": accept,
            "User-Agent": f"metalcharts-python/{__version__}",
        }

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            raise ValueError("Pass a path such as '/v1/prices', not a full URL")
        return self.base_url + "/" + path.lstrip("/")

    def _error(self, response: httpx.Response) -> MetalChartsError:
        body: Union[Dict[str, Any], str, None]
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return error_from_response(
            response.status_code, body, response.headers.get("retry-after")
        )

    def _get(self, path: str, params: Mapping[str, ParamValue]) -> Any:
        url = self._url(path)
        query = _clean_params(params)
        attempt = 0
        while True:
            try:
                response = self._http.get(url, params=query, headers=self._headers())
            except httpx.TransportError as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    self._sleep(_backoff(attempt, 500))
                    continue
                raise APIConnectionError(f"Request to {path} failed: {exc}") from exc

            if response.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                wait = parse_retry_after(response.headers.get("retry-after"))
                delay = _backoff(attempt, 500)
                self._sleep(min(max(delay, wait or 0.0), _MAX_BACKOFF))
                continue

            if response.status_code >= 400:
                raise self._error(response)

            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            return response.text


def _backoff(attempt: int, base_ms: Optional[int] = None) -> float:
    base = (base_ms if base_ms is not None else 1000) / 1000.0
    delay: float = min(_MAX_BACKOFF, base * 2.0 ** max(0, attempt - 1))
    # Jitter keeps many clients from reconnecting in lockstep after an outage.
    return delay * random.uniform(0.5, 1.0)


def _quote_segment(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value).strip(), safe="")


def _clean_params(params: Mapping[str, ParamValue]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[name] = "true" if value else "false"
        elif isinstance(value, _dt.date):
            out[name] = value.strftime("%Y-%m-%d")
        elif isinstance(value, (str, int, float)):
            out[name] = str(value)
        else:
            out[name] = ",".join(str(v) for v in value)
    return out
