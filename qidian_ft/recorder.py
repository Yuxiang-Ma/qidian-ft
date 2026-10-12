"""Acquisition on a background thread, independent of drawing.

The sensor answers ~500 times a second; a GUI redraws ~30 times a second. The
recorder polls as fast as the sensor allows into a bounded deque, and readers
take snapshots whenever they like. CSV logging can be switched on and off while
acquisition keeps running, which is what the GUI's Record button does.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from collections import deque
from typing import IO, TYPE_CHECKING, Any, List, Optional, Tuple

from .config import RecordingConfig
from .horizon import Horizon, InfiniteHorizon
from .protocol import ProtocolError
from .sensor import FTSensor

if TYPE_CHECKING:
    from .trace import Trace

logger = logging.getLogger(__name__)

# Duplicated in trace.py: this module must import without numpy.
CSV_HEADER = ["t_s", "fx_N", "fy_N", "fz_N", "mx_Nm", "my_Nm", "mz_Nm", "latency_ms"]

Row = Tuple[float, float, float, float, float, float, float, float]


class Recorder:
    """Polls a sensor on a background thread under the control of a horizon.

    Rows are ``(t, fx, fy, fz, mx, my, mz, latency_ms)`` with ``t`` in seconds
    since the first sample.
    """

    def __init__(self, sensor: FTSensor, horizon: Optional[Horizon] = None,
                 config: Optional[RecordingConfig] = None) -> None:
        self.sensor = sensor
        self.horizon = horizon or InfiniteHorizon()
        self.config = config or RecordingConfig()
        self._buf: deque = deque(maxlen=self.config.buffer_samples)
        self._lock = threading.Lock()
        self._csv_lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0: Optional[float] = None
        self._n_total = 0
        self._n_errors = 0
        self._last_error = ""
        self._csv_handle: Optional[IO[str]] = None
        self._csv_writer: Optional[Any] = None  # csv.writer has no public type
        self._csv_rows = 0
        self._csv_path: Optional[str] = None
        self._csv_error = ""
        self._error: Optional[BaseException] = None

    # -- state -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def n_samples(self) -> int:
        return self._n_total

    @property
    def n_errors(self) -> int:
        """Polls that timed out or failed CRC (skipped, not fatal)."""
        return self._n_errors

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self._t0 is None else time.perf_counter() - self._t0

    @property
    def error(self) -> Optional[BaseException]:
        """Exception that ended the acquisition thread, if any."""
        return self._error

    @property
    def csv_path(self) -> Optional[str]:
        return self._csv_path

    @property
    def csv_rows(self) -> int:
        return self._csv_rows

    @property
    def csv_error(self) -> str:
        """Why CSV logging stopped on its own (e.g. disk full), else empty."""
        return self._csv_error

    # -- control -----------------------------------------------------------
    def start(self) -> None:
        """Begin acquiring. Returns immediately.

        Raises:
            RuntimeError: If already running.
        """
        if self.is_running:
            raise RuntimeError("Recorder is already running")
        with self._lock:  # a restarted recorder begins a fresh run
            self._buf.clear()
            self._t0 = None
            self._n_total = self._n_errors = 0
        self._error = None
        if self.config.tare_on_start:
            self.sensor.tare()
            time.sleep(self.config.settle_s)
        if self.config.csv_path:
            self.start_csv(self.config.csv_path)
        self._stop.clear()
        self._done.clear()
        self._thread = threading.Thread(target=self._run, name="ft-acq", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop acquisition and close any open CSV."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.stop_csv()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the horizon ends the recording."""
        return self._done.wait(timeout)

    def clear(self) -> None:
        """Drop buffered samples and reset the counters.

        The time base is kept, so a CSV being written stays monotonic in t_s.
        """
        with self._lock:
            self._buf.clear()
            self._n_total = 0
            self._n_errors = 0

    def start_csv(self, path: str) -> None:
        """Start streaming every new sample to ``path``."""
        handle = open(path, "w", newline="", encoding="utf-8")  # noqa: SIM115
        for key, value in self._metadata().items():
            handle.write(f"# {key}: {value}\n")
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        with self._csv_lock:
            self._close_csv_locked()
            self._csv_handle, self._csv_writer = handle, writer
            self._csv_rows, self._csv_path = 0, path
            self._csv_error = ""
        logger.info("Logging to %s", path)

    def stop_csv(self) -> Optional[str]:
        """Stop CSV logging. Returns the path that was written, if any."""
        with self._csv_lock:
            path = self._csv_path
            self._close_csv_locked()
        return path

    def _close_csv_locked(self) -> None:
        handle, path = self._csv_handle, self._csv_path
        self._csv_handle = self._csv_writer = None
        self._csv_path = None
        if handle is not None:
            try:
                handle.close()
                logger.info("Wrote %d rows to %s", self._csv_rows, path)
            except OSError as exc:
                self._csv_error = f"closing {path}: {exc}"
                logger.error("CSV %s", self._csv_error)

    def _write_csv(self, row: Row) -> None:
        """Append one row; a write failure stops logging, not acquisition."""
        with self._csv_lock:
            if self._csv_writer is None:
                return
            try:
                self._csv_writer.writerow(
                    [f"{row[0]:.6f}", *(f"{v:.5f}" for v in row[1:7]), f"{row[7]:.3f}"])
                self._csv_rows += 1
            except OSError as exc:
                self._csv_error = f"writing {self._csv_path}: {exc}"
                logger.error("CSV logging stopped: %s", exc)
                self._close_csv_locked()

    def _metadata(self) -> dict:
        info = self.sensor.info
        return {
            "device": "KEYDYN QS-Z4-4530Ti-1",
            "sensor": info.describe() if info else "unknown",
            "backend": self.sensor.backend_name,
            "resolution": {None: "unchanged", True: "high_res",
                           False: "high_rate"}[self.sensor.high_res],
            "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # -- acquisition ---------------------------------------------------------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    s = self.sensor.read()
                except ProtocolError as exc:
                    self._n_errors += 1
                    self._last_error = str(exc)
                    continue
                with self._lock:
                    if self._t0 is None:
                        self._t0 = s.t_mid
                    row: Row = (s.t_mid - self._t0, *s.wrench, s.latency_ms)
                    self._buf.append(row)
                    self._n_total += 1
                self._write_csv(row)
                if not self.horizon.should_continue(row[0]):
                    break
        except Exception as exc:  # surfaced to the caller via .error
            self._error = exc
            logger.error("Acquisition stopped: %s", exc)
        finally:
            try:
                self.stop_csv()
            finally:
                self._done.set()

    # -- readers -------------------------------------------------------------
    def latest(self) -> Optional[Row]:
        with self._lock:
            return self._buf[-1] if self._buf else None

    def snapshot(self, window_s: Optional[float] = None) -> List[Row]:
        """Copy buffered rows, optionally only the last ``window_s`` seconds.

        Walks from the newest row backwards, so a short window is cheap even
        when the buffer holds minutes of data.
        """
        with self._lock:
            if not self._buf:
                return []
            if window_s is None:
                return list(self._buf)
            cutoff = self._buf[-1][0] - window_s
            out = []
            for row in reversed(self._buf):
                if row[0] < cutoff:
                    break
                out.append(row)
        out.reverse()
        return out

    def trace(self) -> Trace:
        """Build a Trace (needs numpy) from the buffer."""
        from .trace import Trace
        return Trace.from_rows(self.snapshot(), metadata=self._metadata())

    def __enter__(self) -> Recorder:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def record(sensor: FTSensor, horizon: Horizon,
           config: Optional[RecordingConfig] = None) -> Trace:
    """Record until the horizon ends (or Ctrl-C) and return the trace."""
    recorder = Recorder(sensor, horizon, config)
    recorder.start()
    try:
        while recorder.is_running:
            recorder.wait(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        recorder.stop()
    if recorder.error is not None:
        raise recorder.error
    return recorder.trace()
