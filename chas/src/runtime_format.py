"""Cross-version formatting helpers for Chas runtime values."""

from __future__ import annotations


_DECIMAL_CHUNK_DIGITS = 9
_DECIMAL_CHUNK_BASE = 10**_DECIMAL_CHUNK_DIGITS


def format_integer(value: int) -> str:
    """Return an arbitrary-size integer in decimal without Python's digit cap.

    Python 3.11 added a process-wide limit for converting large integers to
    decimal strings. Chas keeps arbitrary-precision arithmetic, so formatting
    works in small decimal chunks instead of changing that global interpreter
    setting (which would be unsafe in the threaded Studio server).
    """

    if type(value) is not int:
        raise TypeError("format_integer requires an int")
    if value == 0:
        return "0"

    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    chunks: list[int] = []
    while magnitude:
        magnitude, chunk = divmod(magnitude, _DECIMAL_CHUNK_BASE)
        chunks.append(chunk)

    head = str(chunks.pop())
    tail = "".join(
        f"{chunk:0{_DECIMAL_CHUNK_DIGITS}d}" for chunk in reversed(chunks)
    )
    return sign + head + tail
