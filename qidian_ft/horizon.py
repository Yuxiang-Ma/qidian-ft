"""Recording horizons: how long to record and which time window to display.

* ``fixed``    -- record for ``duration_s``; the x-axis is pinned to the span.
* ``infinite`` -- record until stopped; the x-axis scrolls over ``window_s``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Type

HORIZON_REGISTRY: Dict[str, Type[Horizon]] = {}

DEFAULT_FIXED_DURATION_S = 10.0
DEFAULT_WINDOW_S = 10.0


def register_horizon(name: str):
    """Register a Horizon subclass under a lookup name."""

    def decorator(cls: Type[Horizon]) -> Type[Horizon]:
        if name in HORIZON_REGISTRY:
            raise ValueError(f"Horizon {name!r} is already registered")
        HORIZON_REGISTRY[name] = cls
        cls.name = name
        return cls

    return decorator


class Horizon(ABC):
    """Decides when recording stops and which time window a plot shows."""

    name: str = "base"

    @abstractmethod
    def should_continue(self, elapsed_s: float) -> bool:
        """Whether acquisition should keep running."""

    @abstractmethod
    def xlim(self, latest_t: float) -> Tuple[float, float]:
        """X-axis limits given the newest sample time."""

    @property
    def duration_s(self) -> Optional[float]:
        """Total run length if bounded, else None."""
        return None

    def describe(self) -> str:
        return self.name


@register_horizon("fixed")
class FixedHorizon(Horizon):
    """Record for a fixed duration, then stop."""

    def __init__(self, duration_s: float = DEFAULT_FIXED_DURATION_S) -> None:
        if duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {duration_s}")
        self._duration_s = float(duration_s)

    @property
    def duration_s(self) -> Optional[float]:
        return self._duration_s

    def should_continue(self, elapsed_s: float) -> bool:
        return elapsed_s < self._duration_s

    def xlim(self, latest_t: float) -> Tuple[float, float]:
        return 0.0, self._duration_s

    def describe(self) -> str:
        return f"fixed {self._duration_s:g} s"


@register_horizon("infinite")
class InfiniteHorizon(Horizon):
    """Record until stopped, showing a scrolling window (None = everything)."""

    def __init__(self, window_s: Optional[float] = DEFAULT_WINDOW_S) -> None:
        if window_s is not None and window_s <= 0:
            raise ValueError(f"window_s must be positive or None, got {window_s}")
        self.window_s = None if window_s is None else float(window_s)

    def should_continue(self, elapsed_s: float) -> bool:
        return True

    def xlim(self, latest_t: float) -> Tuple[float, float]:
        if self.window_s is None:
            return 0.0, max(1.0, latest_t)
        if latest_t <= self.window_s:
            return 0.0, self.window_s
        return latest_t - self.window_s, latest_t

    def describe(self) -> str:
        return "infinite" + ("" if self.window_s is None else f", {self.window_s:g} s window")


def create_horizon(mode: str, **kwargs) -> Horizon:
    """Build a horizon by registered name ("fixed" or "infinite").

    Raises:
        KeyError: If the mode is not registered.
    """
    try:
        cls = HORIZON_REGISTRY[mode]
    except KeyError:
        raise KeyError(f"Unknown horizon {mode!r}; available: {sorted(HORIZON_REGISTRY)}") from None
    return cls(**kwargs)
