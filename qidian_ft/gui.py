"""Desktop GUI: connect, watch six channels live, tare, and record to CSV.

Tkinter + embedded matplotlib, so the only extra dependency is matplotlib.
Acquisition runs on the :class:`~qidian_ft.recorder.Recorder` thread at the
sensor's full rate; the window redraws at ~25 fps from snapshots and never
gates polling.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import List, Optional, Tuple

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .backends import BackendError, list_ports
from .config import SensorConfig
from .plotting import AXIS_COLORS, FORCE_LABELS, TORQUE_LABELS, pad_ylim, style_axes
from .protocol import CHANNELS, UNITS, ProtocolError
from .recorder import Recorder
from .sensor import FTSensor

logger = logging.getLogger(__name__)

REFRESH_MS = 40
MAX_POINTS = 1500          # per line per frame; the buffer keeps everything
READOUT_AVG_S = 0.05       # numeric readout averages the last 50 ms
# Plot redraws hold the GIL; at the default 5 ms switch interval the poller
# waits behind them and drops from ~470 to ~260 Hz. 0.2 ms restores ~420 Hz.
GIL_SWITCH_INTERVAL_S = 0.0002


class FTApp:
    """Main window."""

    def __init__(self, root: tk.Tk, port: Optional[str] = None,
                 backend: str = "auto") -> None:
        self.root = root
        self.sensor: Optional[FTSensor] = None
        self.recorder: Optional[Recorder] = None
        root.title("QS-Z4-4530Ti-1 F/T sensor")
        root.geometry("1280x760")
        root.minsize(980, 600)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.port_var = tk.StringVar(value=port or "")
        self.backend_var = tk.StringVar(value=backend)
        self.status_var = tk.StringVar(value="Not connected")
        self.window_var = tk.DoubleVar(value=10.0)
        self.pause_var = tk.BooleanVar(value=False)
        self.res_var = tk.StringVar(value="")
        self.rec_var = tk.StringVar(value="")
        self.value_vars = [tk.StringVar(value="--") for _ in CHANNELS]
        self.stats_var = tk.StringVar(value="")

        self._build_toolbar()
        body = ttk.Frame(root, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        self._build_side_panel(body)
        self._build_plot(body)
        self.refresh_ports()
        self._set_connected(False)
        root.after(REFRESH_MS, self._tick)

    # -- layout --------------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Port").pack(side="left")
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=22)
        self.port_box.pack(side="left", padx=(4, 2))
        ttk.Button(bar, text="⟳", width=3, command=self.refresh_ports).pack(side="left")
        ttk.Label(bar, text="Backend").pack(side="left", padx=(12, 0))
        ttk.Combobox(bar, textvariable=self.backend_var, width=7, state="readonly",
                     values=("auto", "d2xx", "serial")).pack(side="left", padx=4)
        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left", padx=(8, 0))
        ttk.Label(bar, textvariable=self.status_var, foreground="#444").pack(
            side="left", padx=12)

    def _build_side_panel(self, parent: ttk.Frame) -> None:
        side = ttk.Frame(parent, width=260)
        side.pack(side="left", fill="y", padx=(0, 8))

        values = ttk.LabelFrame(side, text="Wrench", padding=8)
        values.pack(fill="x")
        big = _font("TkFixedFont", 18)
        for i, (name, unit) in enumerate(zip(CHANNELS, UNITS)):
            color = AXIS_COLORS[i % 3]
            ttk.Label(values, text=name.capitalize(), foreground=color,
                      font=_font("TkDefaultFont", 11, "bold"), width=3).grid(
                row=i, column=0, sticky="w")
            ttk.Label(values, textvariable=self.value_vars[i], font=big, width=9,
                      anchor="e").grid(row=i, column=1, sticky="e")
            ttk.Label(values, text=unit, width=4).grid(row=i, column=2, sticky="w", padx=(4, 0))
        ttk.Label(values, textvariable=self.stats_var, foreground="#555",
                  font=_font("TkFixedFont", 9), justify="left").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ctrl = ttk.LabelFrame(side, text="Sensor", padding=8)
        ctrl.pack(fill="x", pady=8)
        self.tare_btn = ttk.Button(ctrl, text="Tare (zero all)", command=self.tare)
        self.tare_btn.pack(fill="x")
        ttk.Label(ctrl, text="Resolution mode").pack(anchor="w", pady=(8, 0))
        self.res_buttons = [
            ttk.Radiobutton(ctrl, text="High rate  (0.2 N / 0.01 Nm)", value="high_rate",
                            variable=self.res_var, command=self.set_resolution),
            ttk.Radiobutton(ctrl, text="High res  (0.05 N / 0.002 Nm)", value="high_res",
                            variable=self.res_var, command=self.set_resolution),
        ]
        for rb in self.res_buttons:
            rb.pack(anchor="w")

        rec = ttk.LabelFrame(side, text="Recording", padding=8)
        rec.pack(fill="x")
        self.rec_btn = ttk.Button(rec, text="● Record to CSV…", command=self.toggle_record)
        self.rec_btn.pack(fill="x")
        ttk.Label(rec, textvariable=self.rec_var, foreground="#555", wraplength=230,
                  font=_font("TkDefaultFont", 8)).pack(anchor="w", pady=(4, 0))

        view = ttk.LabelFrame(side, text="View", padding=8)
        view.pack(fill="x", pady=8)
        row = ttk.Frame(view)
        row.pack(fill="x")
        ttk.Label(row, text="Window (s)").pack(side="left")
        ttk.Spinbox(row, from_=1, to=600, increment=1, width=6,
                    textvariable=self.window_var).pack(side="left", padx=6)
        ttk.Checkbutton(view, text="Pause plot", variable=self.pause_var).pack(anchor="w", pady=4)
        ttk.Button(view, text="Clear buffer", command=self.clear).pack(fill="x")
        ttk.Button(view, text="Save plot PNG…", command=self.save_png).pack(fill="x", pady=(4, 0))

    def _build_plot(self, parent: ttk.Frame) -> None:
        self.fig = Figure(figsize=(9, 6), dpi=100, constrained_layout=True)
        self.ax_f, self.ax_m = self.fig.subplots(2, 1, sharex=True)
        self.lines_f = [self.ax_f.plot([], [], color=c, lw=1.0, label=lbl)[0]
                        for c, lbl in zip(AXIS_COLORS, FORCE_LABELS)]
        self.lines_m = [self.ax_m.plot([], [], color=c, lw=1.0, label=lbl)[0]
                        for c, lbl in zip(AXIS_COLORS, TORQUE_LABELS)]
        self.ax_f.set_ylabel("force (N)")
        self.ax_m.set_ylabel("moment (Nm)")
        self.ax_m.set_xlabel("time (s)")
        for ax in (self.ax_f, self.ax_m):
            ax.legend(loc="upper left", ncol=3, fontsize=9, framealpha=0.85)
            ax.axhline(0, color="#999", lw=0.7, ls="--")
        style_axes((self.ax_f, self.ax_m))
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True)

    # -- connection ------------------------------------------------------------
    def refresh_ports(self) -> None:
        ports = list_ports()
        self.port_box["values"] = ports
        if not self.port_var.get() and ports:
            self.port_var.set(ports[0])

    def toggle_connect(self) -> None:
        if self.sensor is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        port = self.port_var.get().strip() or None
        cfg = SensorConfig(backend=self.backend_var.get(), port=port)
        self.status_var.set(f"Connecting to {port or 'auto'}…")
        self.root.update_idletasks()
        sensor = FTSensor(cfg)
        try:
            info = sensor.connect()
        except (BackendError, ProtocolError, KeyError, OSError) as exc:
            sensor.close()
            self.status_var.set("Not connected")
            messagebox.showerror("Connection failed", _connect_hint(str(exc)))
            return
        timer = sensor.latency_timer_ms  # before the poller owns the handle
        self.sensor = sensor
        self.recorder = Recorder(sensor)
        self.recorder.start()
        self.status_var.set(f"{info.describe()}   via {sensor.backend_name}"
                            f"   latency timer {timer if timer is not None else '?'} ms")
        self._set_connected(True)

    def disconnect(self, reason: str = "") -> None:
        if self.recorder is not None:
            self.recorder.stop()
        if self.sensor is not None:
            self.sensor.close()
        self.sensor = self.recorder = None
        self.status_var.set(f"Disconnected{': ' + reason if reason else ''}")
        self._set_connected(False)

    def _set_connected(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        self.connect_btn.configure(text="Disconnect" if on else "Connect")
        widgets: List[ttk.Widget] = [self.tare_btn, self.rec_btn, *self.res_buttons]
        for widget in widgets:
            widget.configure(state=state)  # type: ignore[call-arg]
        if not on:
            self.rec_btn.configure(text="● Record to CSV…")
            self.res_var.set("")

    # -- actions ---------------------------------------------------------------
    def _bus_action(self, fn, label: str) -> bool:
        try:
            fn()
            return True
        except (ProtocolError, BackendError) as exc:
            messagebox.showerror(f"{label} failed", str(exc))
            return False

    def tare(self) -> None:
        if self.sensor is not None:
            self._bus_action(self.sensor.tare, "Tare")

    def set_resolution(self) -> None:
        if self.sensor is not None:
            high = self.res_var.get() == "high_res"
            if not self._bus_action(lambda: self.sensor.set_high_resolution(high),
                                    "Mode change"):
                # Show what the sensor is really in, not what was clicked.
                self.res_var.set({None: "", True: "high_res",
                                  False: "high_rate"}[self.sensor.high_res])

    def toggle_record(self) -> None:
        rec = self.recorder
        if rec is None:
            return
        if rec.csv_path:
            path = rec.stop_csv()
            self.rec_btn.configure(text="● Record to CSV…")
            self.rec_var.set(f"Saved {os.path.basename(path or '')}")
            return
        default = time.strftime("ft_%Y%m%d_%H%M%S.csv")
        path = filedialog.asksaveasfilename(
            title="Record to", defaultextension=".csv", initialfile=default,
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if path:
            try:
                rec.start_csv(path)
            except OSError as exc:
                messagebox.showerror("Cannot record", str(exc))
                return
            self._rec_t0 = time.perf_counter()
            self.rec_btn.configure(text="■ Stop recording")

    def clear(self) -> None:
        if self.recorder is not None:
            self.recorder.clear()

    def save_png(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save plot", defaultextension=".png",
            initialfile=time.strftime("ft_%Y%m%d_%H%M%S.png"),
            filetypes=[("PNG", "*.png")])
        if path:
            try:
                self.fig.savefig(path, dpi=150)
            except OSError as exc:
                messagebox.showerror("Cannot save plot", str(exc))

    # -- refresh loop ------------------------------------------------------------
    def _tick(self) -> None:
        try:
            self._refresh()
        finally:
            self.root.after(REFRESH_MS, self._tick)

    def _refresh(self) -> None:
        rec = self.recorder
        if rec is None:
            return
        if rec.error is not None or not rec.is_running:
            self.disconnect(str(rec.error or "acquisition stopped"))
            return
        try:
            window = max(1.0, float(self.window_var.get()))
        except (tk.TclError, ValueError):
            window = 10.0  # spinbox empty or mid-edit
        if rec.csv_error and not rec.csv_path and self.rec_btn["text"].startswith("■"):
            self.rec_btn.configure(text="● Record to CSV…")
            self.rec_var.set("Recording stopped")
            messagebox.showerror("Recording stopped", rec.csv_error)
        rows = rec.snapshot(window)
        if not rows:
            return
        data = np.asarray(rows)
        self._update_readout(rec, data)
        if rec.csv_path:
            secs = time.perf_counter() - getattr(self, "_rec_t0", time.perf_counter())
            self.rec_var.set(f"{os.path.basename(rec.csv_path)}\n"
                             f"{rec.csv_rows} rows, {secs:.1f} s")
        if not self.pause_var.get():
            self._update_plot(data, window)

    def _update_readout(self, rec: Recorder, data: np.ndarray) -> None:
        recent = data[data[:, 0] >= data[-1, 0] - READOUT_AVG_S]
        avg = recent[:, 1:7].mean(axis=0)
        for i, var in enumerate(self.value_vars):
            var.set(f"{avg[i]:+.3f}" if i < 3 else f"{avg[i]:+.4f}")
        span = data[-1, 0] - data[0, 0]
        rate = (len(data) - 1) / span if span > 0 else 0.0
        self.stats_var.set(
            f"rate    {rate:6.0f} Hz\n"
            f"poll    {np.median(data[:, 7]):6.2f} ms\n"
            f"samples {rec.n_samples:>8d}\n"
            f"errors  {rec.n_errors:>8d}")

    def _update_plot(self, data: np.ndarray, window: float) -> None:
        step = max(1, len(data) // MAX_POINTS)
        d = data[::step]
        t = d[:, 0]
        for i, line in enumerate(self.lines_f):
            line.set_data(t, d[:, 1 + i])
        for i, line in enumerate(self.lines_m):
            line.set_data(t, d[:, 4 + i])
        hi = max(window, float(t[-1]))
        self.ax_m.set_xlim(hi - window, hi)
        self.ax_f.set_ylim(*pad_ylim(d[:, 1:4], 0.25))
        self.ax_m.set_ylim(*pad_ylim(d[:, 4:7], 0.005))
        self.canvas.draw_idle()

    def on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def _font(base: str, size: int, weight: str = "normal") -> Tuple[str, int, str]:
    """The platform's own family for a named Tk font (Consolas, DejaVu Sans Mono, ...).

    A plain tuple, not a ``tkfont.Font``: Tk deletes a Font once Python
    garbage-collects it, and the widget would silently fall back.
    """
    return (tkfont.nametofont(base).actual("family"), size, weight)


def _pick_theme(root: tk.Tk) -> None:
    """Native look on Windows/macOS; "clam" over Tk's dated default on Linux."""
    style = ttk.Style(root)
    for theme in ("vista", "aqua", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            return


def _connect_hint(err: str) -> str:
    hints: List[str] = [err, ""]
    if "bad CRC" in err or "short reply" in err:
        hints.append("The sensor answers but the reply is corrupted: check the RS-485 "
                     "signal (A/B, GND, adapter). See README > Troubleshooting.")
    elif "timeout" in err or "no reply" in err:
        hints.append("No reply: check 18-72 V power, that the sensor is in RS-485 mode "
                     "(not CAN), 460800 baud, and slave id 1.")
    elif "permission" not in err.lower() and "another program" not in err:
        hints.append("Close other programs using the port (e.g. the vendor SAFS_TOOL2).")
    return "\n".join(hints)


def main(port: Optional[str] = None, backend: str = "auto") -> None:
    """Launch the GUI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.setswitchinterval(GIL_SWITCH_INTERVAL_S)
    root = tk.Tk()
    _pick_theme(root)
    FTApp(root, port=port, backend=backend)
    root.mainloop()


if __name__ == "__main__":
    main()
