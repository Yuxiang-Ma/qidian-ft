"""Driver, recorder and GUI for the KEYDYN QS-Z4-4530Ti-1 six-axis F/T sensor.

The sensor speaks Modbus-RTU over RS-485 at 460800 baud and only answers when
polled. With the USB adapter's FTDI latency timer at 1 ms (set automatically
through D2XX on Windows) polling reaches ~500 Hz; at the 16 ms driver default
it is ~60 Hz.

Importing this package pulls only the standard library (plus pyserial when a
port is opened). numpy and matplotlib load lazily for traces, plots and the GUI.

    >>> from qidian_ft import FTSensor, SensorConfig
    >>> with FTSensor(SensorConfig(port="COM4")) as ft:
    ...     ft.tare()
    ...     for s in ft.stream(duration_s=1.0):
    ...         print(s.t_mid, s.force, s.torque)
"""
from typing import TYPE_CHECKING, Any

from .backends import (
    BACKEND_REGISTRY,
    Backend,
    BackendError,
    available_backends,
    describe_platform,
    list_ports,
    open_backend,
    register_backend,
)
from .config import RecordingConfig, SensorConfig
from .horizon import (
    HORIZON_REGISTRY,
    FixedHorizon,
    Horizon,
    InfiniteHorizon,
    create_horizon,
    register_horizon,
)
from .protocol import CHANNELS, UNITS, ProtocolError
from .recorder import Recorder, record
from .sensor import DeviceInfo, FTSensor, Sample

if TYPE_CHECKING:
    from .plotting import plot_trace
    from .trace import Trace

__version__ = "0.1.0"

_LAZY = {
    "Trace": (".trace", "Trace"),
    "plot_trace": (".plotting", "plot_trace"),
}

__all__ = [
    "BACKEND_REGISTRY",
    "CHANNELS",
    "HORIZON_REGISTRY",
    "UNITS",
    "Backend",
    "BackendError",
    "DeviceInfo",
    "FTSensor",
    "FixedHorizon",
    "Horizon",
    "InfiniteHorizon",
    "ProtocolError",
    "Recorder",
    "RecordingConfig",
    "Sample",
    "SensorConfig",
    "Trace",
    "__version__",
    "available_backends",
    "create_horizon",
    "describe_platform",
    "list_ports",
    "open_backend",
    "plot_trace",
    "record",
    "register_backend",
    "register_horizon",
]


def __getattr__(name: str) -> Any:
    """Resolve numpy/matplotlib-backed names on first use."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    try:
        module = import_module(target[0], __package__)
    except ImportError as exc:
        raise ImportError(f"{name} needs numpy/matplotlib: "
                          f"pip install 'qidian-ft[viz]' ({exc})") from exc
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(__all__)
