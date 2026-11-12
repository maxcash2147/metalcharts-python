# metalcharts

Python client for the [MetalCharts API](https://metalcharts.org/metals-api): spot and historical prices for gold, silver, platinum, palladium, copper and base metals, plus COMEX inventory, LBMA vaults, Shanghai (SGE, SHFE), India MCX and CFTC Commitments of Traders data.

- Python 3.9+, one dependency ([httpx](https://www.python-httpx.org/))
- Typed responses and exceptions
- Live price streaming over Server-Sent Events, with reconnects handled for you

## Install

```bash
pip install git+https://github.com/maxcash2147/metalcharts-python
```

Get a free API key at [metalcharts.org/metals-api](https://metalcharts.org/metals-api). The free plan has 200 requests a month and 30 days of daily history, with no card.

## Quick start

```python
from metalcharts import MetalCharts

client = MetalCharts()  # reads the METALCHARTS_API_KEY environment variable

prices = client.prices()
gold = prices["data"]["XAU"]
print(gold["price"], gold["changePercent24h"])
```

You can also pass the key directly with `MetalCharts(api_key="mc_live_your_key_here")`, but keep it out of source control.

One `prices()` call returns every metal, keyed by symbol: `XAU` gold, `XAG` silver, `XPT` platinum, `XPD` palladium, `HG` copper, plus `ALI`, `NI`, `ZN`, `PB` and others. Ask for fewer with `symbols`:

```python
client.prices(["XAU", "XAG"])
client.prices("XAU,XAG")  # same thing
```

## Currency, unit and karat

Prices are in US dollars per each market's own unit (troy ounce for precious metals) unless you ask otherwise:

```python
# Gold in euros per gram
client.prices("XAU", currency="EUR", unit="g")

# 22 karat gold in Indian rupees per gram
client.prices("XAU", currency="INR", unit="g", karat=22)
```

- `currency`: 32 ISO codes, including USD, EUR, GBP, JPY, CHF, AUD, CAD, CNY, INR and AED. Converted at the ECB reference rate.
- `unit`: `toz`, `g`, `kg`, `lb`, `mt` or `tola`.
- `karat`: 24, 23, 22, 21, 20, 18, 16, 15, 14, 12, 10, 9 or 8 (gold only).

## Historical prices

`history()` returns OHLC bars, oldest first:

```python
bars = client.history("XAU", range="1M", interval="1d")["data"]
for bar in bars[-5:]:
    print(bar["timestamp"], bar["close"])
```

Pick the window with `range` (`1M`, `6M`, `1Y`, `5Y`, `ALL`) or with `start` and `end`, which take `datetime.date` objects or `"YYYY-MM-DD"` strings. `interval` is one of `15m`, `30m`, `45m`, `1h`, `2h`, `4h`, `8h`, `1d`, `1w`, `1M`. `currency`, `unit` and `karat` work here too, converted at each day's rate.

How far back you can go depends on the plan: the free plan reads the last 30 days of daily bars, Starter and Basic one year with intraday intervals, Pro five years, and Ultimate the full series (gold and silver daily bars go back to 1966).

### Into pandas

pandas is not a dependency of this package, so install it yourself:

```python
import pandas as pd
from metalcharts import MetalCharts

client = MetalCharts()
history = client.history("XAG", range="1M", interval="1d")

df = pd.DataFrame(history["data"])
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.set_index("timestamp")[["open", "high", "low", "close"]]

print(df.tail())
print("30-day change:", round((df.close.iloc[-1] / df.close.iloc[0] - 1) * 100, 2), "%")
```

## Other endpoints

The client has methods for the endpoints most people use:

| Method | Endpoint |
|---|---|
| `prices()` | `GET /v1/prices` |
| `history(symbol)` | `GET /v1/history/{symbol}` |
| `instruments()` | `GET /v1/instruments` |
| `metals()` | `GET /v1/metals` |
| `convert(from_, to)` | `GET /v1/convert` |
| `usage()` | `GET /v1/usage` (your plan and what you've used; costs nothing) |
| `stream_prices()` | `GET /v1/stream/prices` (see below) |

Everything else goes through `get()`, which takes the path and query parameters:

```python
# CFTC Commitments of Traders for gold (Pro plan)
cot = client.get("/v1/cot", metal="XAU", range="1Y")

# COMEX silver warehouse stocks (Pro plan)
comex = client.get("/v1/comex/inventory", symbol="XAG", range="3M")

# A parameter named like a Python keyword goes in a dict
client.get("/v1/convert", {"from": "XAU", "to": "EUR", "amount": 1})

# Any list endpoint as CSV text
csv_text = client.get("/v1/history/XAU", range="1M", format="csv")
```

The [API reference](https://metalcharts.org/metals-api/docs) lists every endpoint, parameter and response field, and which plan each one needs.

## Live prices (streaming)

On the Pro plan and above, `stream_prices()` holds one connection open and yields an event each time a price changes, so you don't spend requests polling:

```python
from metalcharts import MetalCharts

client = MetalCharts()
latest = {}

for event in client.stream_prices(["XAU", "XAG"]):
    latest.update(event.quotes)
    if event.type == "price":
        q = event.data
        print(q["symbol"], q["price"], q["timestamp"])
```

- The first event on every connection is a `snapshot` with all requested symbols. After that each `price` event carries one symbol's full quote, in the same shape as a quote from `prices()`.
- `event.quotes` gives the quotes keyed by symbol for both event types, so `latest.update(event.quotes)` keeps a current table.
- `currency` and `unit` work as they do for `prices()`.
- If the connection drops or the server closes it, the client reconnects with backoff and starts again from a fresh snapshot. Pass `reconnect=False` to handle that yourself, or `max_reconnect_attempts=10` to give up after ten failed attempts in a row.
- Breaking out of the loop closes the connection. Event types other than `snapshot` and `price` are skipped, so your code keeps working if the API adds new ones.
- Concurrent connections per account: Pro 1, Ultimate 10, Enterprise 50. Other plans get a `PlanRequiredError`, and opening one connection too many gets a `RateLimitError`.

## Errors

Every exception subclasses `MetalChartsError`, which has `status_code`, `message`, `body` and `upgrade_url`.

| Exception | When |
|---|---|
| `AuthenticationError` | 401: the key is missing or invalid |
| `PlanRequiredError` | 403: your plan doesn't include the data, or the history range reaches past your plan's limit. `required_plan` names the plan that does (it's `None` for history depth) |
| `RateLimitError` | 429: per-minute limit or monthly allowance used up. `retry_after` is the wait in seconds, `window` is `"1m"` or `"1mo"` |
| `APIConnectionError` | No response: DNS, TLS, timeout or a dropped connection |
| `MetalChartsError` | Anything else, such as a 400 for an invalid parameter |

```python
import time
from metalcharts import MetalCharts, PlanRequiredError, RateLimitError

client = MetalCharts()

try:
    bars = client.history("XAU", range="5Y")["data"]
except PlanRequiredError as exc:
    print(f"Needs a bigger plan ({exc.required_plan or 'more history'}): {exc.upgrade_url}")
except RateLimitError as exc:
    if exc.window == "1m":
        time.sleep(exc.retry_after or 60)
    else:
        print("Monthly allowance used up until", exc.retry_at)
```

Server errors (500, 502, 503, 504) and connection failures are retried twice with backoff before an exception is raised; set `MetalCharts(max_retries=0)` to turn that off. 429s are never retried automatically.

## Limits

Each request costs weight: most endpoints 1, history and news 2, news search and sentiment 3, `usage()` 0. The per-minute limit is per key and the monthly allowance is per account. Every response carries `X-RateLimit-*` headers, and `client.usage()` returns your plan, its limits and what you've used this month.

On the free plan, any public page or app that shows the data needs a visible link to https://metalcharts.org, such as "Metal prices by MetalCharts". Paid plans don't need it. Commercial use is allowed on every plan.

## Links

- [API reference](https://metalcharts.org/metals-api/docs)
- [Pricing](https://metalcharts.org/api-pricing)
- [OpenAPI 3.0 document](https://api.metalcharts.org/docs/json)
- [Examples in other languages](https://github.com/maxcash2147/metalcharts-api-examples)
- JavaScript and TypeScript client: `npm install metalcharts`

## License

MIT. Use of the API itself is covered by the [API terms](https://metalcharts.org/api-terms).
