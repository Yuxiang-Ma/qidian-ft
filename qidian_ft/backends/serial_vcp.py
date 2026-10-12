"""Virtual COM port backend (pyserial).

The default on Linux and the fallback on Windows. On Windows the VCP latency
timer lives in the registry, so this backend runs at whatever is configured
there (16 ms by default, ~60 Hz). On Linux it tries the ftdi_sio sysfs knob,
then the unprivileged ``ASYNC_LOW_LATENCY`` flag, which ftdi_sio maps to 1 ms.
"""
from __future__ import annotations

import glob
import logging
import os
import platform
from typing import List, Optional

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

FTDI_VID = 0x0403


class SerialBackendError(BackendError):
    """The serial port could not be opened or configured."""


def _import_serial():
    try:
        import serial
        import serial.tools.list_ports  # noqa: F401
        return serial
    except ImportError as exc:
        raise SerialBackendError("pyserial is required: pip install pyserial") from exc


def is_available() -> bool:
    """Whether pyserial is importable."""
    try:
        _import_serial()
        return True
    except SerialBackendError:
        return False


def find_ports() -> List[str]:
    """Candidate serial ports, FTDI adapters first."""
    serial = _import_serial()
    ftdi, others = [], []
    for port in serial.tools.list_ports.comports():
        if getattr(port, "vid", None) == FTDI_VID:
            ftdi.append(port.device)
        elif getattr(port, "vid", None) is not None:
            others.append(port.device)
    return ftdi + others


def _sysfs_latency_path(port: str) -> Optional[str]:
    if platform.system() != "Linux":
        return None
    leaf = os.path.basename(port)
    for candidate in glob.glob(f"/sys/bus/usb-serial/devices/{leaf}*/latency_timer"):
        return candidate
    return None


class SerialDevice(Backend):
    """The sensor reached through a virtual COM port."""

    name = "serial"

    def __init__(self, port: Optional[str] = None, baud: int = 460800,
                 latency_ms: int = 1, timeout_s: float = 0.1) -> None:
        serial = _import_serial()
        if port is None:
            candidates = find_ports()
            if not candidates:
                raise SerialBackendError("No USB serial port found")
            port = candidates[0]
        self._port = port
        try:
            self._ser = serial.Serial(
                port=port, baudrate=baud, bytesize=8, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=timeout_s,
                write_timeout=timeout_s)
        except (OSError, serial.SerialException) as exc:
            raise SerialBackendError(f"Could not open {port}: {exc}") from exc
        self._apply_latency(latency_ms)
        self.purge()

    @property
    def description(self) -> str:
        return f"serial[{self._port}]"

    def _apply_latency(self, ms: int) -> None:
        """Best effort; a working 60 Hz link beats a hard error."""
        path = _sysfs_latency_path(self._port)
        if path is not None:
            try:
                with open(path, "w", encoding="ascii") as handle:
                    handle.write(str(ms))
                return
            except OSError:
                logger.debug("Cannot write %s; trying ASYNC_LOW_LATENCY", path)
        setter = getattr(self._ser, "set_low_latency_mode", None)
        if ms == 1 and setter is not None:
            try:
                setter(True)
            except (OSError, ValueError) as exc:
                logger.debug("ASYNC_LOW_LATENCY refused: %s", exc)

    def get_latency(self) -> Optional[int]:
        path = _sysfs_latency_path(self._port)
        if path is not None:
            try:
                with open(path, encoding="ascii") as handle:
                    return int(handle.read().strip())
            except (OSError, ValueError):
                return None
        if platform.system() == "Windows":
            return _windows_registry_latency(self._port)
        return None

    def purge(self) -> None:
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def write(self, data: bytes) -> int:
        return self._ser.write(data) or 0

    def read_available(self) -> bytes:
        n = self._ser.in_waiting
        return self._ser.read(n) if n else b""

    def read_exact(self, n: int, timeout_s: float) -> bytes:
        if self._ser.timeout != timeout_s:
            self._ser.timeout = timeout_s
        return self._ser.read(n)

    def close(self) -> None:
        ser = getattr(self, "_ser", None)
        if ser is not None and ser.is_open:
            ser.close()


def _windows_registry_latency(port: str) -> Optional[int]:
    """Read the FTDI VCP latency timer for ``port`` from the registry."""
    try:
        import winreg
    except ImportError:
        return None
    root = r"SYSTEM\CurrentControlSet\Enum\FTDIBUS"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as ftdibus:
            for i in range(winreg.QueryInfoKey(ftdibus)[0]):
                dev = winreg.EnumKey(ftdibus, i)
                params = rf"{root}\{dev}\0000\Device Parameters"
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, params) as key:
                        if winreg.QueryValueEx(key, "PortName")[0].upper() == port.upper():
                            return int(winreg.QueryValueEx(key, "LatencyTimer")[0])
                except OSError:
                    continue
    except OSError:
        return None
    return None
