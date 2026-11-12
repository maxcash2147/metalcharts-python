"""Python client for the MetalCharts metals price API.

    from metalcharts import MetalCharts

    client = MetalCharts()  # reads METALCHARTS_API_KEY
    gold = client.prices("XAU")["data"]["XAU"]["price"]

Docs: https://metalcharts.org/metals-api/docs
"""

from ._client import DEFAULT_BASE_URL, ENV_VAR, MetalCharts
from ._errors import (
    APIConnectionError,
    AuthenticationError,
    MetalChartsError,
    PlanRequiredError,
    RateLimitError,
)
from ._sse import StreamEvent
from ._types import (
    Bar,
    HistoryResponse,
    Instrument,
    InstrumentsResponse,
    PricesResponse,
    Quote,
    UsageResponse,
)
from ._version import __version__

__all__ = [
    "MetalCharts",
    "DEFAULT_BASE_URL",
    "ENV_VAR",
    "MetalChartsError",
    "APIConnectionError",
    "AuthenticationError",
    "PlanRequiredError",
    "RateLimitError",
    "StreamEvent",
    "Quote",
    "PricesResponse",
    "Bar",
    "HistoryResponse",
    "Instrument",
    "InstrumentsResponse",
    "UsageResponse",
    "__version__",
]
