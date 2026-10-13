"""Driver for the KEYDYN QS-Z4-4530Ti-1 six-axis force/torque sensor.

The sensor speaks Modbus-RTU over RS-485 and only answers when polled, so the
achievable rate is one request/response round trip per sample: ~0.7 ms on the
wire at 460800 baud, plus whatever the USB adapter adds. With the FTDI latency
timer at 1 ms that is ~500 Hz; at the 16 ms driver default it is ~60 Hz.

Every bus transaction holds a lock, so a GUI thread can tare while a recorder
thread polls without interleaving frames.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from . import protocol as p
from .backends import Backend, BackendError, open_backend
from .config import SensorConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    """One wrench reading with host timestamps taken around the round trip."""

    t_send: float
    t_recv: float
    wrench: Tuple[float, float, float, float, float, float]

    @property
    def t_mid(self) -> float:
        """Best single estimate of the acquisition instant."""
        return 0.5 * (self.t_send + self.t_recv)

    @property
    def latency_ms(self) -> float:
        """Round-trip time for this poll."""
        return (self.t_recv - self.t_send) * 1e3

    @property
    def force(self) -> Tuple[float, float, float]:
        """(Fx, Fy, Fz) in newtons."""
        return self.wrench[0], self.wrench[1], self.wrench[2]

    @property
    def torque(self) -> Tuple[float, float, float]:
        """(Mx, My, Mz) in newton-metres."""
        return self.wrench[3], self.wrench[4], self.wrench[5]

    def as_dict(self) -> Dict[str, float]:
        """Channel name -> value."""
        return dict(zip(p.CHANNELS, self.wrench))


@dataclass(frozen=True)
class DeviceInfo:
    """Identification read on connect."""

    serial_number: str
    software_version: int
    hardware_version: int

    def describe(self) -> str:
        return (f"SN {self.serial_number}  sw {p.format_version(self.software_version)}"
                f"  hw {p.format_version(self.hardware_version)}")


class FTSensor:
    """A QS-Z4-4530Ti-1 polled over Modbus-RTU.

    Example:
        >>> with FTSensor(SensorConfig(port="COM4")) as ft:
        ...     ft.tare()
        ...     for s in ft.stream(duration_s=1.0):
        ...         print(s.t_mid, s.force, s.torque)
    """

    def __init__(self, config: Optional[SensorConfig] = None,
                 backend: Optional[Backend] = None) -> None:
        self.config = config or SensorConfig()
        self._dev = backend
        self._lock = threading.Lock()
        self.info: Optional[DeviceInfo] = None
        self.high_res: Optional[bool] = None

    @property
    def is_connected(self) -> bool:
        return self._dev is not None

    @property
    def backend_name(self) -> str:
        return self._dev.description if self._dev is not None else "none"

    @property
    def latency_timer_ms(self) -> Optional[int]:
        with self._lock:  # never query the handle while another thread reads it
            return self._dev.get_latency() if self._dev is not None else None

    def connect(self, retries: int = 3) -> DeviceInfo:
        """Open the transport and read the sensor's identification.

        Raises:
            BackendError: If no transport could be opened.
            ProtocolError: If the sensor never gave a valid reply. A reply of
                the right length with a bad CRC points at the RS-485 signal,
                not at these settings.
        """
        if self._dev is None:
            self._dev = open_backend(self.config.backend, **self.config.backend_kwargs())
        try:
            self.info = self._identify(retries)
            if self.config.resolution is not None:
                self.set_high_resolution(self.config.resolution == "high_res")
            latency = self.latency_timer_ms
        except BaseException:
            self.close()  # never leave the port held after a failed connect
            raise
        if latency is not None and latency > 2:
            from .platform_help import latency_fix_hint
            logger.warning("FTDI latency timer is %d ms: polling capped near %.0f Hz. %s",
                           latency, 1000.0 / latency, latency_fix_hint(self.backend_name))
        logger.info("Connected %s via %s", self.info.describe(), self.backend_name)
        return self.info

    def _identify(self, retries: int) -> DeviceInfo:
        last: Optional[Exception] = None
        for _ in range(retries):
            try:
                sn = p.decode_sn(self.read_registers(p.REG_SN, p.REG_SN_COUNT))
                sw, hw = p.decode_versions(self.read_registers(p.REG_SW_VERSION, 4))
                return DeviceInfo(sn, sw, hw)
            except p.ProtocolError as exc:
                last = exc
                time.sleep(0.05)
        raise p.ProtocolError(f"sensor did not answer on {self.config.port}: {last}")

    def _transact(self, request: bytes, function: int, reply_len: int) -> bytes:
        with self._lock:
            dev = self._dev  # read under the lock: close() may run on another thread
            if dev is None:
                raise BackendError("Sensor is not connected")
            dev.purge()
            dev.write(request)
            reply = dev.read_exact(reply_len, self.config.timeout_s)
        logger.debug("TX %s | RX %s", request.hex(" "), reply.hex(" "))
        return p.check_reply(reply, self.config.slave, function, reply_len)

    def read_registers(self, address: int, count: int) -> bytes:
        """Raw 0x03 read; returns the register bytes."""
        return self._transact(p.read_request(self.config.slave, address, count),
                              p.FC_READ, p.read_reply_length(count))

    def write_registers(self, address: int, values: Sequence[int]) -> None:
        """Raw 0x10 write."""
        self._transact(p.write_request(self.config.slave, address, values),
                       p.FC_WRITE, p.WRITE_REPLY_LENGTH)

    def read(self) -> Sample:
        """Take one wrench reading.

        Raises:
            ProtocolError: On timeout or a corrupt reply.
        """
        t0 = time.perf_counter()
        payload = self.read_registers(p.REG_WRENCH, p.REG_WRENCH_COUNT)
        t1 = time.perf_counter()
        return Sample(t0, t1, p.decode_wrench(payload))

    def stream(self, duration_s: Optional[float] = None,
               max_samples: Optional[int] = None,
               skip_errors: bool = True,
               max_consecutive_errors: int = 50) -> Iterator[Sample]:
        """Yield samples as fast as the sensor answers.

        Args:
            duration_s: Stop after this long.
            max_samples: Stop after this many good samples.
            skip_errors: Skip polls that time out or fail CRC.
            max_consecutive_errors: Give up if this many polls fail in a row,
                so a sensor that stops answering cannot hang the caller.

        Raises:
            ValueError: If neither bound is given.
            ProtocolError: On a failed poll (skip_errors=False), or after
                max_consecutive_errors failures in a row.
        """
        if duration_s is None and max_samples is None:
            raise ValueError("Provide duration_s or max_samples to bound the stream")
        end = time.perf_counter() + duration_s if duration_s is not None else float("inf")
        count = failures = 0
        while time.perf_counter() < end and (max_samples is None or count < max_samples):
            try:
                sample = self.read()
            except p.ProtocolError:
                failures += 1
                if not skip_errors or failures >= max_consecutive_errors:
                    raise
                continue
            failures = 0
            count += 1
            yield sample

    def _control(self, tare: bool, high_res: bool) -> None:
        word = (p.CTRL_TARE if tare else 0) | (p.CTRL_HIGH_RES if high_res else 0)
        self.write_registers(p.REG_CONTROL, (word,))
        self.high_res = high_res  # only after the sensor acknowledged it

    def tare(self) -> None:
        """Zero all six channels at the current load.

        The control word is write-only and every write carries the resolution
        bit, so a tare re-sends the mode last set through this driver. If no
        mode was set yet, the tare selects high-rate mode (bit 2 = 0).
        """
        self._control(tare=True, high_res=bool(self.high_res))

    def set_high_resolution(self, enabled: bool) -> None:
        """Select high-resolution (True) or high-rate (False) mode."""
        self._control(tare=False, high_res=bool(enabled))

    def close(self) -> None:
        """Release the port so other programs can open it.

        Waits for any in-flight transaction, so it is safe to call while a
        Recorder thread is polling (its next poll raises BackendError).
        """
        with self._lock:
            dev, self._dev = self._dev, None
        if dev is not None:
            dev.close()

    def __enter__(self) -> FTSensor:
        if self.info is None:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
