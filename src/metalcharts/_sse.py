"""A small Server-Sent Events parser.

Follows the WHATWG event stream rules: lines end in CRLF, LF or CR; a line
starting with a colon is a comment (the API's ``: ping`` heartbeat); a blank
line dispatches the event built so far; an event cut off by the end of the
stream is dropped.
"""

from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional

__all__ = ["ServerSentEvent", "StreamEvent", "SSEDecoder", "iter_lines", "iter_events"]

_LINE_END = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class ServerSentEvent:
    event: str
    data: str
    id: Optional[str] = None
    retry: Optional[int] = None


@dataclass(frozen=True)
class StreamEvent:
    """One event from ``MetalCharts.stream_prices``.

    ``type`` is ``"snapshot"`` (every requested symbol, sent first on each
    connection) or ``"price"`` (one symbol whose quote changed). ``data`` is
    the decoded JSON payload exactly as the API sent it.
    """

    type: str
    data: Any
    id: Optional[str] = None
    quotes: Dict[str, Dict[str, Any]] = field(default_factory=dict, compare=False)

    @classmethod
    def from_sse(cls, sse: ServerSentEvent) -> "StreamEvent":
        try:
            data: Any = json.loads(sse.data) if sse.data else None
        except ValueError:
            data = sse.data
        return cls(type=sse.event, data=data, id=sse.id, quotes=_quotes(sse.event, data))


def _quotes(event: str, data: Any) -> Dict[str, Dict[str, Any]]:
    """Quotes keyed by symbol, so ``latest.update(event.quotes)`` works for both event types."""
    if not isinstance(data, dict):
        return {}
    if event == "snapshot" and isinstance(data.get("data"), dict):
        return dict(data["data"])
    if event == "price" and isinstance(data.get("symbol"), str):
        return {data["symbol"]: data}
    return {}


class SSEDecoder:
    """Turns lines into events. Feed it every line, including blank ones."""

    def __init__(self) -> None:
        self._event = ""
        self._data: List[str] = []
        self._retry: Optional[int] = None
        self.last_event_id: Optional[str] = None

    def decode(self, line: str) -> Optional[ServerSentEvent]:
        if line == "":
            if not self._data:
                self._event = ""
                self._retry = None
                return None
            sse = ServerSentEvent(
                event=self._event or "message",
                data="\n".join(self._data),
                id=self.last_event_id,
                retry=self._retry,
            )
            self._event = ""
            self._data = []
            self._retry = None
            return sse

        if line.startswith(":"):
            return None

        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]

        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif name == "retry":
            if value.isdigit():
                self._retry = int(value)
        return None


def iter_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Split a byte stream into lines, holding back a trailing CR in case LF follows."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    first = True
    for chunk in chunks:
        text = decoder.decode(chunk)
        if first and text:
            first = False
            if text.startswith("﻿"):
                text = text[1:]
        buffer += text
        if buffer.endswith("\r"):
            head, tail = buffer[:-1], "\r"
        else:
            head, tail = buffer, ""
        parts = _LINE_END.split(head)
        buffer = parts.pop() + tail
        for part in parts:
            yield part
    buffer += decoder.decode(b"", final=True)
    if buffer.endswith("\r"):
        # A bare CR at the very end still ends its line.
        yield from _LINE_END.split(buffer[:-1])


def iter_events(chunks: Iterable[bytes], decoder: Optional[SSEDecoder] = None) -> Iterator[ServerSentEvent]:
    decoder = decoder or SSEDecoder()
    for line in iter_lines(chunks):
        sse = decoder.decode(line)
        if sse is not None:
            yield sse
