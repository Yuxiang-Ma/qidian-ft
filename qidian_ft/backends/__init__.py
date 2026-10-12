"""Transport backends, selected by name or auto-detected per platform.

``auto`` prefers D2XX where it can open the adapter (Windows), because it sets
the 1 ms latency timer through an API call. Otherwise it falls back to the
pyserial VCP backend, the normal path on Linux and for non-FTDI adapters.
"""
from __future__ import annotations

import logging
import platform
from typing import Callable, Dict, List

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

BACKEND_REGISTRY: Dict[str, Callable[..., Backend]] = {}

__all__ = [
    "BACKEND_REGISTRY",
    "Backend",
    "BackendError",
    "available_backends",
    "describe_platform",
    "list_ports",
    "open_backend",
    "register_backend",
]


def register_backend(name: str):
    """Register a Backend factory under a lookup name."""

    def decorator(factory: Callable[..., Backend]) -> Callable[..., Backend]:
        if name in BACKEND_REGISTRY:
            raise ValueError(f"Backend {name!r} is already registered")
        BACKEND_REGISTRY[name] = factory
        return factory

    return decorator


@register_backend("d2xx")
def _make_d2xx(**kwargs) -> Backend:
    from .d2xx import D2xxDevice
    return D2xxDevice(port=kwargs.get("port"), baud=kwargs.get("baud", 460800),
                      latency_ms=kwargs.get("latency_ms", 1),
                      timeout_s=kwargs.get("timeout_s", 0.1))


@register_backend("serial")
def _make_serial(**kwargs) -> Backend:
    from .serial_vcp import SerialDevice
    return SerialDevice(port=kwargs.get("port"), baud=kwargs.get("baud", 460800),
                        latency_ms=kwargs.get("latency_ms", 1),
                        timeout_s=kwargs.get("timeout_s", 0.1))


def available_backends() -> List[str]:
    """Names of backends whose dependencies are importable right now."""
    from . import d2xx, serial_vcp
    found = []
    if d2xx.is_available():
        found.append("d2xx")
    if serial_vcp.is_available():
        found.append("serial")
    return found


def _auto_order() -> List[str]:
    if platform.system() == "Linux":
        return ["serial", "d2xx"]
    return ["d2xx", "serial"]


def open_backend(name: str = "auto", **kwargs) -> Backend:
    """Open a transport.

    Args:
        name: A registered backend name, or "auto".
        **kwargs: port, baud, latency_ms, timeout_s.

    Raises:
        BackendError: If the named backend failed, or no backend worked.
        KeyError: If the name is not registered.
    """
    if name != "auto":
        if name not in BACKEND_REGISTRY:
            raise KeyError(f"Unknown backend {name!r}; available: {sorted(BACKEND_REGISTRY)}")
        return BACKEND_REGISTRY[name](**kwargs)
    failures = []
    for candidate in _auto_order():
        try:
            return BACKEND_REGISTRY[candidate](**kwargs)
        except (BackendError, OSError) as exc:
            failures.append(f"{candidate}: {exc}")
            logger.debug("Backend %s unavailable: %s", candidate, exc)
    raise BackendError("No usable backend.\n  " + "\n  ".join(failures))


def list_ports() -> List[str]:
    """Serial ports worth offering in a picker, FTDI adapters first."""
    from .serial_vcp import find_ports, is_available
    return find_ports() if is_available() else []


def describe_platform() -> str:
    """One-line summary of platform and backend availability."""
    return (f"{platform.system()} {platform.machine()} | "
            f"backends: {', '.join(available_backends()) or 'none'} | "
            f"auto order: {', '.join(_auto_order())}")
