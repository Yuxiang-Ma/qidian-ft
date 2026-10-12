"""The byte-transport contract the sensor driver depends on.

The Modbus protocol is identical everywhere; only how the host reaches the USB
RS-485 adapter differs. On Windows, FTDI's D2XX API can set the adapter's
latency timer directly. The VCP driver keeps that timer in the registry (admin
only) and defaults it to 16 ms, which caps polling near 60 Hz. Linux exposes
the same knob through ftdi_sio. Both reduce to: move bytes, and set the timer.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional


class BackendError(RuntimeError):
    """A transport could not be opened, configured, or used."""


class Backend(ABC):
    """Byte transport to the RS-485 adapter."""

    name: str = "base"

    @abstractmethod
    def purge(self) -> None:
        """Discard any buffered input and output."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Send bytes. Returns the number written."""

    @abstractmethod
    def read_available(self) -> bytes:
        """Return whatever bytes are buffered now, without blocking."""

    @abstractmethod
    def close(self) -> None:
        """Release the device."""

    @abstractmethod
    def get_latency(self) -> Optional[int]:
        """Current FTDI latency timer in ms, or None if it cannot be read."""

    def read_exact(self, n: int, timeout_s: float) -> bytes:
        """Read until ``n`` bytes arrive or ``timeout_s`` elapses.

        The default polls :meth:`read_available`. Backends that can block in
        the OS should override it.
        """
        buf = bytearray()
        deadline = time.perf_counter() + timeout_s
        while len(buf) < n and time.perf_counter() < deadline:
            buf += self.read_available()
        return bytes(buf)

    @property
    def description(self) -> str:
        """Identifier for logs, e.g. the port name or device index."""
        return self.name

    def __enter__(self) -> Backend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
