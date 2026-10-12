"""Immutable configuration for sensor access, recording and plotting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .protocol import DEFAULT_BAUD, DEFAULT_SLAVE

RESOLUTION_MODES = ("high_rate", "high_res")


@dataclass(frozen=True)
class SensorConfig:
    """How to open and poll the sensor.

    Attributes:
        backend: "auto", "d2xx" or "serial". Windows auto-selects d2xx.
        port: Serial port ("COM4", "/dev/ttyUSB0"). None picks the first FTDI one.
        baud: Line rate. The sensor ships at 460800 8N1.
        slave: Modbus slave id (register 0x0602), 1 by default.
        latency_ms: FTDI latency timer. 16 ms (driver default) caps polling near
            60 Hz; 1 ms reaches ~500 Hz.
        timeout_s: Give up on a reply after this long.
        resolution: Applied on connect. "high_rate" (0.2 N / 0.01 Nm) or
            "high_res" (0.05 N / 0.002 Nm). None leaves the sensor as it is.
    """

    backend: str = "auto"
    port: Optional[str] = None
    baud: int = DEFAULT_BAUD
    slave: int = DEFAULT_SLAVE
    latency_ms: int = 1
    timeout_s: float = 0.05
    resolution: Optional[str] = None

    def __post_init__(self) -> None:
        if not 1 <= self.latency_ms <= 255:
            raise ValueError(f"latency_ms must be 1..255, got {self.latency_ms}")
        if not 1 <= self.slave <= 247:
            raise ValueError(f"slave must be 1..247, got {self.slave}")
        if self.resolution is not None and self.resolution not in RESOLUTION_MODES:
            raise ValueError(f"resolution must be one of {RESOLUTION_MODES} or None")

    def backend_kwargs(self) -> dict:
        """Arguments for :func:`qidian_ft.backends.open_backend`."""
        return {"port": self.port, "baud": self.baud,
                "latency_ms": self.latency_ms, "timeout_s": self.timeout_s}


@dataclass(frozen=True)
class RecordingConfig:
    """How a recording session behaves.

    Attributes:
        csv_path: Stream samples here as they arrive. None keeps them in memory.
        buffer_samples: Cap on the in-memory ring buffer used for plotting. The
            CSV is never truncated.
        tare_on_start: Zero the sensor before recording.
        settle_s: Pause after taring.
    """

    csv_path: Optional[str] = None
    buffer_samples: int = 600_000
    tare_on_start: bool = False
    settle_s: float = 0.2

    def __post_init__(self) -> None:
        if self.buffer_samples < 2:
            raise ValueError("buffer_samples must be at least 2")
