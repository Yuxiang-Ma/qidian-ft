"""A recorded wrench trace: timestamps, six channels, CSV round-tripping."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence

import numpy as np

from .protocol import CHANNELS

CSV_HEADER = ["t_s", "fx_N", "fy_N", "fz_N", "mx_Nm", "my_Nm", "mz_Nm", "latency_ms"]


@dataclass
class Trace:
    """Wrench samples on a time base starting at zero.

    Attributes:
        t: Sample times [s], shape (N,).
        wrench: Fx Fy Fz [N], Mx My Mz [Nm], shape (N, 6).
        latency_ms: Per-sample round trip, shape (N,), or None.
        metadata: Provenance (sensor SN, backend, resolution, time).
    """

    t: np.ndarray
    wrench: np.ndarray
    latency_ms: Optional[np.ndarray] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.wrench = np.asarray(self.wrench, dtype=float).reshape(-1, 6)
        if self.wrench.shape[0] != self.t.shape[0]:
            raise ValueError(f"t and wrench disagree: {self.t.shape} vs {self.wrench.shape}")
        if self.latency_ms is not None:
            self.latency_ms = np.asarray(self.latency_ms, dtype=float)

    def __len__(self) -> int:
        return int(self.t.size)

    def __getitem__(self, channel: str) -> np.ndarray:
        """trace["fz"] -> that channel's samples."""
        return self.wrench[:, CHANNELS.index(channel)]

    @property
    def force(self) -> np.ndarray:
        return self.wrench[:, :3]

    @property
    def torque(self) -> np.ndarray:
        return self.wrench[:, 3:]

    @property
    def duration_s(self) -> float:
        return float(self.t[-1] - self.t[0]) if len(self) > 1 else 0.0

    @property
    def rate_hz(self) -> float:
        return (len(self) - 1) / self.duration_s if self.duration_s > 0 else 0.0

    def summary(self) -> Dict[str, float]:
        """Headline statistics: rate, and mean/std/min/max per channel."""
        if not len(self):
            return {}
        out = {"samples": float(len(self)), "duration_s": self.duration_s,
               "rate_hz": self.rate_hz}
        for i, name in enumerate(CHANNELS):
            col = self.wrench[:, i]
            out[f"{name}_mean"] = float(col.mean())
            out[f"{name}_std"] = float(col.std())
            out[f"{name}_min"] = float(col.min())
            out[f"{name}_max"] = float(col.max())
        if self.latency_ms is not None and self.latency_ms.size:
            out["latency_median_ms"] = float(np.median(self.latency_ms))
        return out

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[float]],
                  metadata: Optional[Dict[str, str]] = None) -> Trace:
        """From recorder rows ``(t, fx, fy, fz, mx, my, mz, latency_ms)``."""
        arr = np.asarray(rows, dtype=float).reshape(-1, 8)
        return cls(t=arr[:, 0], wrench=arr[:, 1:7], latency_ms=arr[:, 7],
                   metadata=dict(metadata or {}))

    def save_csv(self, path: str) -> None:
        """Write with metadata as leading ``#`` lines."""
        lat = self.latency_ms if self.latency_ms is not None else np.full(len(self), np.nan)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            for key, value in self.metadata.items():
                handle.write(f"# {key}: {value}\n")
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADER)
            for ts, w, latency in zip(self.t, self.wrench, lat):
                writer.writerow([f"{ts:.6f}", *(f"{v:.5f}" for v in w),
                                 "" if np.isnan(latency) else f"{latency:.3f}"])

    @classmethod
    def load_csv(cls, path: str) -> Trace:
        """Read a CSV written by the recorder or :meth:`save_csv`.

        Raises:
            ValueError: If the file has no usable rows.
        """
        metadata: Dict[str, str] = {}
        lines = []
        with open(path, newline="", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    key, _, value = line[1:].partition(":")
                    metadata[key.strip()] = value.strip()
                else:
                    lines.append(line)
        rows = []
        for rec in csv.DictReader(lines):
            try:
                vals = [float(rec[k]) for k in CSV_HEADER[:7]]
            except (KeyError, TypeError, ValueError):
                continue
            lat = (rec.get("latency_ms") or "").strip()
            rows.append(vals + [float(lat) if lat else np.nan])
        if not rows:
            raise ValueError(f"No parseable samples in {path}")
        trace = cls.from_rows(rows, metadata)
        if trace.latency_ms is not None and np.all(np.isnan(trace.latency_ms)):
            trace.latency_ms = None
        return trace
