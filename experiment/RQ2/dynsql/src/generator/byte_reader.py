from __future__ import annotations

from pathlib import Path
from typing import Sequence, TypeVar

T = TypeVar("T")


class InputExhausted(EOFError):
    """Raised when deterministic input has no unread bytes."""


class ByteReader:
    def __init__(self, data: bytes) -> None:
        self._data = bytes(data)
        self._offset = 0

    @classmethod
    def from_file(cls, path: str | Path) -> "ByteReader":
        return cls(Path(path).read_bytes())

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def next_byte(self) -> int:
        if self.exhausted:
            raise InputExhausted("binary input exhausted")
        value = self._data[self._offset]
        self._offset += 1
        return value

    def choose(self, options: Sequence[T]) -> T:
        if not options:
            raise ValueError("choose() requires at least one option")
        return options[self.next_byte() % len(options)]

    def take(self, count: int) -> bytes:
        if count < 0:
            raise ValueError("count must be non-negative")
        if self.remaining < count:
            remaining = self.remaining
            self._offset = len(self._data)
            raise InputExhausted(f"need {count} bytes, only {remaining} remain")
        start = self._offset
        self._offset += count
        return self._data[start:self._offset]
