"""Response shapes for the main endpoints.

These are TypedDicts, so responses stay plain dicts at runtime and editors
still know the field names. Endpoints reached through ``MetalCharts.get``
return untyped JSON.
"""

from __future__ import annotations

from typing import Dict, List, Optional, TypedDict

__all__ = [
    "Quote",
    "PricesResponse",
    "Bar",
    "HistoryResponse",
    "Instrument",
    "InstrumentsResponse",
    "UsageResponse",
]


class _QuoteRequired(TypedDict):
    symbol: str
    price: float
    exchange: str
    marketType: str
    timestamp: str


class Quote(_QuoteRequired, total=False):
    """One symbol's latest price, as found under ``data`` in /v1/prices."""

    change24h: Optional[float]
    changePercent24h: Optional[float]
    open24h: Optional[float]
    high24h: Optional[float]
    low24h: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    stale: bool
    # Present when the request set currency, unit or karat.
    currency: str
    unit: str
    karat: int
    purity: float


class _PricesRequired(TypedDict):
    success: bool
    data: Dict[str, Quote]
    count: int
    source: str
    isStale: bool
    timestamp: str


class PricesResponse(_PricesRequired, total=False):
    cacheAge: Optional[float]
    currency: str
    fx: Dict[str, object]
    _rid: str


class Bar(TypedDict):
    """One OHLC bar. ``price`` equals ``close``."""

    timestamp: str
    price: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class _HistoryRequired(TypedDict):
    success: bool
    data: List[Bar]
    symbol: str
    range: str
    startDate: str
    endDate: str
    count: int
    source: str


class HistoryResponse(_HistoryRequired, total=False):
    exchange: Optional[str]
    currency: str
    unit: str
    _rid: str


class _InstrumentRequired(TypedDict):
    symbol: str
    name: str


class Instrument(_InstrumentRequired, total=False):
    assetClass: Optional[str]
    exchange: Optional[str]
    marketType: Optional[str]
    currency: Optional[str]
    unit: Optional[str]


class InstrumentsResponse(TypedDict):
    success: bool
    count: int
    data: List[Instrument]


class UsageLimits(TypedDict):
    weightPerMinute: int
    monthlyWeight: Optional[int]
    maxKeys: int
    maxHistoryYears: Optional[int]
    maxHistoryDays: Optional[int]
    intradayHistory: bool


class UsageCounts(TypedDict):
    today: int
    month: int
    keysActive: int


class UsageDay(TypedDict):
    date: str
    requests: int
    weight: int


class UsageResponse(TypedDict, total=False):
    tier: str
    creditCode: str
    limits: UsageLimits
    usage: UsageCounts
    dailyHistory: List[UsageDay]
