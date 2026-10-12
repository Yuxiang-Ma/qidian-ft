"""Static rendering of a recorded wrench trace."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from .trace import Trace

# x / y / z share a hue across force and moment axes so Fx and Mx read as a pair.
AXIS_COLORS = ("#d62728", "#2ca02c", "#1f77b4")
FORCE_LABELS = ("Fx", "Fy", "Fz")
TORQUE_LABELS = ("Mx", "My", "Mz")


def pad_ylim(values: np.ndarray, floor: float, pad_frac: float = 0.12) -> Tuple[float, float]:
    """Y limits with headroom and a minimum half-span so noise is not magnified."""
    if values.size == 0:
        return -floor, floor
    lo, hi = float(np.min(values)), float(np.max(values))
    mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo) * (1 + 2 * pad_frac)
    half = max(half, floor)
    return mid - half, mid + half


def style_axes(axes: Sequence) -> None:
    for ax in axes:
        ax.grid(alpha=0.25, linewidth=0.6)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)


def plot_trace(trace: Trace, save_path: Optional[str] = None, show: bool = False,
               title: Optional[str] = None):
    """Render forces, moments and poll latency on a shared time axis.

    Returns:
        The matplotlib Figure.
    """
    import matplotlib
    if not show:
        matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    fig, (ax_f, ax_m, ax_l) = plt.subplots(
        3, 1, figsize=(11, 7.5), sharex=True, constrained_layout=True,
        gridspec_kw={"height_ratios": [3, 3, 1]})
    for i in range(3):
        ax_f.plot(trace.t, trace.force[:, i], color=AXIS_COLORS[i], lw=1.0,
                  label=FORCE_LABELS[i])
        ax_m.plot(trace.t, trace.torque[:, i], color=AXIS_COLORS[i], lw=1.0,
                  label=TORQUE_LABELS[i])
    ax_f.set_ylabel("force (N)")
    ax_m.set_ylabel("moment (Nm)")
    ax_f.set_ylim(*pad_ylim(trace.force, 0.5))
    ax_m.set_ylim(*pad_ylim(trace.torque, 0.01))
    ax_f.legend(loc="upper right", ncol=3, fontsize=9, framealpha=0.85)
    ax_m.legend(loc="upper right", ncol=3, fontsize=9, framealpha=0.85)
    if trace.latency_ms is not None and len(trace):
        ax_l.plot(trace.t, trace.latency_ms, color="#7f7f7f", lw=0.6)
        ax_l.axhline(float(np.median(trace.latency_ms)), color="#d62728", lw=0.8, ls=":")
    ax_l.set_ylabel("poll (ms)")
    ax_l.set_xlabel("time (s)")
    if len(trace) > 1:
        ax_l.set_xlim(float(trace.t[0]), float(trace.t[-1]))
    style_axes((ax_f, ax_m, ax_l))
    sensor = trace.metadata.get("sensor", "")
    head = title or f"QS-Z4-4530Ti-1  {sensor}".strip()
    ax_f.set_title(f"{head}   {len(trace)} samples, {trace.rate_hz:.0f} Hz",
                   loc="left", fontsize=10)
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    return fig
