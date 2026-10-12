"""FTDI D2XX backend (Windows default).

D2XX exposes ``FT_SetLatencyTimer``. The VCP driver's 16 ms default caps a
request/response loop near 60 Hz; 1 ms lifts this sensor to ~500 Hz, with no
registry edit and no admin rights. The library is loaded lazily so importing
the package never fails on machines without it.
"""
from __future__ import annotations

import ctypes as ct
import logging
import platform
import threading
from typing import List, Optional

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

OK = 0
BITS_8, STOP_BITS_1, PARITY_NONE = 8, 0, 0
PURGE_RX, PURGE_TX = 1, 2
FLOW_NONE = 0x0000
OPEN_BY_SERIAL_NUMBER = 1

_STATUS = {
    0: "OK", 1: "INVALID_HANDLE", 2: "DEVICE_NOT_FOUND", 3: "DEVICE_NOT_OPENED",
    4: "IO_ERROR", 5: "INSUFFICIENT_RESOURCES", 6: "INVALID_PARAMETER",
    7: "INVALID_BAUD_RATE", 18: "OTHER_ERROR",
}

_LIB_NAMES = {
    "Windows": ["ftd2xx.dll", "ftd2xx64.dll"],
    "Darwin": ["libftd2xx.dylib"],
    "Linux": ["libftd2xx.so", "libftd2xx.so.1"],
}

_dll = None
_dll_lock = threading.Lock()


class D2xxError(BackendError):
    """A D2XX call failed, or the library is unavailable."""


def _load() -> ct.CDLL:
    """Load the D2XX shared library once and cache the handle."""
    global _dll
    with _dll_lock:
        if _dll is not None:
            return _dll
        system = platform.system()
        loader = getattr(ct, "WinDLL", ct.CDLL) if system == "Windows" else ct.CDLL
        errors = []
        for name in _LIB_NAMES.get(system, []):
            try:
                _dll = loader(name)
                return _dll
            except OSError as exc:
                errors.append(f"{name}: {exc}")
        raise D2xxError(f"No D2XX library on {system}. Tried: {'; '.join(errors)}")


def is_available() -> bool:
    """Whether a D2XX library can be loaded on this machine."""
    try:
        _load()
        return True
    except D2xxError:
        return False


def _check(status: int, fn: str) -> None:
    if status != OK:
        raise D2xxError(f"{fn} failed: {_STATUS.get(status, status)} ({status})")


def list_serials() -> List[str]:
    """USB serial numbers of the FTDI devices D2XX can see."""
    dll = _load()
    n = ct.c_ulong()
    _check(dll.FT_CreateDeviceInfoList(ct.byref(n)), "FT_CreateDeviceInfoList")
    serials = []
    for i in range(n.value):
        flags, typ, dev_id, loc = ct.c_ulong(), ct.c_ulong(), ct.c_ulong(), ct.c_ulong()
        sn, desc = ct.create_string_buffer(64), ct.create_string_buffer(64)
        handle = ct.c_void_p()
        status = dll.FT_GetDeviceInfoDetail(
            i, ct.byref(flags), ct.byref(typ), ct.byref(dev_id), ct.byref(loc),
            sn, desc, ct.byref(handle))
        if status == OK:
            serials.append(sn.value.decode("ascii", "replace"))
    return serials


def serial_for_port(port: str) -> Optional[str]:
    """Map a COM port name to the USB serial number D2XX opens by."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    vcp_serial = next((info.serial_number for info in list_ports.comports()
                       if info.device.upper() == port.upper()), None)
    if not vcp_serial:
        return None
    # Windows appends the interface letter ("B001VI22" -> "B001VI22A").
    for sn in list_serials():
        if sn and (vcp_serial == sn or vcp_serial.startswith(sn)):
            return sn
    return None


class D2xxDevice(Backend):
    """An FTDI adapter opened through D2XX with a settable latency timer.

    Args:
        port: COM port name; mapped to the adapter's USB serial number.
        serial_number: Open this adapter directly. Takes precedence over port.
        index: Device index, used when neither port nor serial is given.
        baud: Line rate (the sensor uses 460800).
        latency_ms: FTDI latency timer, 1..255.
        timeout_s: Read timeout.
    """

    name = "d2xx"

    def __init__(self, port: Optional[str] = None, serial_number: Optional[str] = None,
                 index: int = 0, baud: int = 460800, latency_ms: int = 1,
                 timeout_s: float = 0.1) -> None:
        self._dll = _load()
        self.h = ct.c_void_p()
        if serial_number is None and port is not None:
            serial_number = serial_for_port(port)
            if serial_number is None:
                raise D2xxError(f"{port} is not an FTDI adapter D2XX can open")
        if serial_number is not None:
            self._label = f"d2xx[{port or serial_number}]"
            _check(self._dll.FT_OpenEx(serial_number.encode("ascii"),
                                       OPEN_BY_SERIAL_NUMBER, ct.byref(self.h)),
                   "FT_OpenEx (is the port open in another program?)")
        else:
            self._label = f"d2xx[{index}]"
            _check(self._dll.FT_Open(index, ct.byref(self.h)), "FT_Open")
        self._timeout_ms = -1
        try:
            _check(self._dll.FT_SetBaudRate(self.h, baud), "FT_SetBaudRate")
            _check(self._dll.FT_SetDataCharacteristics(
                self.h, BITS_8, STOP_BITS_1, PARITY_NONE), "FT_SetDataCharacteristics")
            _check(self._dll.FT_SetFlowControl(self.h, FLOW_NONE, 0, 0),
                   "FT_SetFlowControl")
            _check(self._dll.FT_SetLatencyTimer(self.h, latency_ms), "FT_SetLatencyTimer")
            self._set_timeout(timeout_s)
            self.purge()
        except Exception:
            self.close()
            raise

    @property
    def description(self) -> str:
        return self._label

    def _set_timeout(self, timeout_s: float) -> None:
        ms = max(1, int(round(timeout_s * 1000)))
        if ms != self._timeout_ms:
            _check(self._dll.FT_SetTimeouts(self.h, ms, ms), "FT_SetTimeouts")
            self._timeout_ms = ms

    def get_latency(self) -> Optional[int]:
        v = ct.c_ubyte()
        _check(self._dll.FT_GetLatencyTimer(self.h, ct.byref(v)), "FT_GetLatencyTimer")
        return v.value

    def purge(self) -> None:
        _check(self._dll.FT_Purge(self.h, PURGE_RX | PURGE_TX), "FT_Purge")

    def write(self, data: bytes) -> int:
        written = ct.c_ulong()
        buf = ct.create_string_buffer(data, len(data))
        _check(self._dll.FT_Write(self.h, buf, len(data), ct.byref(written)), "FT_Write")
        return written.value

    def read_available(self) -> bytes:
        n = ct.c_ulong()
        _check(self._dll.FT_GetQueueStatus(self.h, ct.byref(n)), "FT_GetQueueStatus")
        return self._read(n.value) if n.value else b""

    def read_exact(self, n: int, timeout_s: float) -> bytes:
        """Block in the driver until n bytes arrive or the timeout fires."""
        self._set_timeout(timeout_s)
        return self._read(n)

    def _read(self, n: int) -> bytes:
        got = ct.c_ulong()
        buf = ct.create_string_buffer(n)
        _check(self._dll.FT_Read(self.h, buf, n, ct.byref(got)), "FT_Read")
        return buf.raw[:got.value]

    def close(self) -> None:
        if self.h:
            self._dll.FT_Close(self.h)
            self.h = ct.c_void_p()
