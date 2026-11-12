from __future__ import annotations

import email.utils
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Union

__all__ = [
    "MetalChartsError",
    "APIConnectionError",
    "AuthenticationError",
    "PlanRequiredError",
    "RateLimitError",
]


class MetalChartsError(Exception):
    """Base class for every error raised by this package.

    ``status_code`` is the HTTP status, or ``None`` when no response arrived.
    ``body`` is the decoded JSON error body (an empty dict if there was none).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body: Dict[str, Any] = dict(body) if body else {}
        #: Link to the pricing page, present on plan and quota errors.
        self.upgrade_url: Optional[str] = self.body.get("upgrade_url")
        #: Support reference, present on 500 errors. Quote it when asking for help.
        self.reference: Optional[str] = self.body.get("reference")

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.status_code}: {self.message}"


class APIConnectionError(MetalChartsError):
    """The request failed before an HTTP response arrived (DNS, TLS, timeout, reset)."""


class AuthenticationError(MetalChartsError):
    """401: the API key is missing, malformed, revoked or unknown."""


class PlanRequiredError(MetalChartsError):
    """403: the request needs a higher plan.

    ``required_plan`` names the lowest plan that allows it (for example
    ``"pro"``). It is ``None`` when the plan has the data but not this much of
    it, such as a history range that reaches further back than the plan allows.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 403,
        body: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.required_plan: Optional[str] = self.body.get("required_plan")


class RateLimitError(MetalChartsError):
    """429: the per-minute or monthly limit is used up.

    ``retry_after`` is the number of seconds to wait, from the ``Retry-After``
    header or the ``retryAt`` field. ``window`` is ``"1m"`` for the per-minute
    limit and ``"1mo"`` for the monthly quota.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 429,
        body: Optional[Mapping[str, Any]] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.retry_at: Optional[datetime] = _parse_iso(self.body.get("retryAt"))
        if retry_after is None and self.retry_at is not None:
            retry_after = max(0.0, (self.retry_at - datetime.now(timezone.utc)).total_seconds())
        self.retry_after: Optional[float] = retry_after
        self.limit: Optional[int] = self.body.get("limit")
        self.window: Optional[str] = self.body.get("window")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds from a Retry-After header, which is either seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def error_from_response(
    status_code: int,
    body: Union[Mapping[str, Any], str, None],
    retry_after_header: Optional[str] = None,
) -> MetalChartsError:
    """Build the right exception for an error response."""
    data: Dict[str, Any] = dict(body) if isinstance(body, Mapping) else {}
    message = data.get("error") or data.get("message")
    if not message:
        text = body.strip() if isinstance(body, str) else ""
        message = text[:200] if text else f"HTTP {status_code}"
    message = str(message)

    if status_code == 401:
        return AuthenticationError(message, status_code=status_code, body=data)
    if status_code == 403:
        return PlanRequiredError(message, status_code=status_code, body=data)
    if status_code == 429:
        return RateLimitError(
            message,
            status_code=status_code,
            body=data,
            retry_after=parse_retry_after(retry_after_header),
        )
    return MetalChartsError(message, status_code=status_code, body=data)
