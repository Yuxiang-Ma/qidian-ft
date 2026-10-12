"""Command-line interface: ``qidian-ft <command>``."""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from typing import List, Optional

from .backends import BackendError, describe_platform, list_ports, open_backend
from .config import RecordingConfig, SensorConfig
from .horizon import create_horizon
from .protocol import CHANNELS, UNITS, ProtocolError
from .recorder import record
from .sensor import FTSensor


def _sensor_config(args: argparse.Namespace) -> SensorConfig:
    return SensorConfig(backend=args.backend, port=args.port, slave=args.slave,
                        latency_ms=args.latency,
                        resolution=getattr(args, "resolution", None))


def cmd_info(args: argparse.Namespace) -> int:
    """Identify the sensor and measure the poll rate."""
    print(f"platform : {describe_platform()}")
    print(f"ports    : {', '.join(list_ports()) or 'none'}")
    with FTSensor(_sensor_config(args)) as ft:
        assert ft.info is not None  # set by connect() in __enter__
        print(f"backend  : {ft.backend_name} (latency timer {ft.latency_timer_ms} ms)")
        print(f"sensor   : {ft.info.describe()}")
        samples = list(ft.stream(max_samples=args.samples))
    span = samples[-1].t_recv - samples[0].t_send
    lat = sorted(s.latency_ms for s in samples)
    print(f"rate     : {len(samples) / span:.0f} Hz over {len(samples)} polls")
    print(f"latency  : median {lat[len(lat) // 2]:.2f} ms, max {lat[-1]:.2f} ms")
    for i, (name, unit) in enumerate(zip(CHANNELS, UNITS)):
        col = [s.wrench[i] for s in samples]
        print(f"  {name}: {statistics.fmean(col):+9.4f} {unit:2s}  "
              f"(sd {statistics.pstdev(col):.4f})")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Raw link diagnostics: sweep baud x slave and classify replies."""
    from .diagnose import VERDICT_ADVICE, probe
    ports = list_ports()
    port = args.port or (ports[0] if ports else None)
    print(f"Probing {port or 'auto'} (close any other program using it)")

    def open_at(baud: int):
        return open_backend(args.backend, port=port, baud=baud, latency_ms=args.latency)

    results = probe(open_at)
    best = next((r for r in results if r.verdict == "ok"), None)
    best = best or (results[0] if results else None)
    verdict = best.verdict if best else "silent"
    print(f"\nverdict: {verdict}\n  {VERDICT_ADVICE[verdict]}")
    if best and verdict == "ok":
        print(f"  use --baud {best.baud} --slave {best.slave}" if
              (best.baud, best.slave) != (460800, 1) else "  documented settings work")
    return 0 if verdict == "ok" else 1


def cmd_record(args: argparse.Namespace) -> int:
    """Record headlessly to CSV (and optionally PNG)."""
    horizon = (create_horizon("fixed", duration_s=args.duration) if args.duration > 0
               else create_horizon("infinite"))
    cfg = RecordingConfig(csv_path=args.csv, tare_on_start=args.tare)
    with FTSensor(_sensor_config(args)) as ft:
        assert ft.info is not None  # set by connect() in __enter__
        print(f"{ft.info.describe()}  ->  {args.csv}  ({horizon.describe()}; Ctrl-C stops)")
        trace = record(ft, horizon, cfg)
    s = trace.summary()
    print(f"{int(s.get('samples', 0))} samples, {s.get('rate_hz', 0):.0f} Hz")
    if args.plot:
        from .plotting import plot_trace
        plot_trace(trace, save_path=args.plot)
        print(f"plot -> {args.plot}")
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    """Render a recorded CSV to PNG."""
    from .plotting import plot_trace
    from .trace import Trace
    trace = Trace.load_csv(args.csv)
    out = args.out or args.csv.rsplit(".", 1)[0] + ".png"
    plot_trace(trace, save_path=out, show=args.show)
    print(f"plot -> {out}  ({len(trace)} samples, {trace.rate_hz:.0f} Hz)")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Launch the desktop GUI."""
    from .gui import main as gui_main
    gui_main(port=args.port, backend=args.backend)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qidian-ft",
        description="KEYDYN QS-Z4-4530Ti-1 six-axis F/T sensor over RS-485 Modbus-RTU.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--port", default=None, help="COM4, /dev/ttyUSB0 (default: first FTDI)")
    parser.add_argument("--backend", default="auto", choices=("auto", "d2xx", "serial"))
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave id (default 1)")
    parser.add_argument("--latency", type=int, default=1, metavar="MS",
                        help="FTDI latency timer (default 1; driver default 16 caps ~60 Hz)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="identify the sensor and measure the rate")
    p_info.add_argument("--samples", type=lambda v: max(2, int(v)), default=500)
    p_info.set_defaults(func=cmd_info)

    sub.add_parser("probe", help="raw link diagnostics").set_defaults(func=cmd_probe)

    p_rec = sub.add_parser("record", help="record to CSV without a GUI")
    p_rec.add_argument("--csv", default="ft.csv")
    p_rec.add_argument("--duration", type=float, default=10.0,
                       help="seconds; 0 records until Ctrl-C (default 10)")
    p_rec.add_argument("--tare", action="store_true", help="zero before recording")
    p_rec.add_argument("--resolution", choices=("high_rate", "high_res"), default=None)
    p_rec.add_argument("--plot", default=None, help="also save a PNG")
    p_rec.set_defaults(func=cmd_record)

    p_plot = sub.add_parser("plot", help="render a recorded CSV")
    p_plot.add_argument("csv")
    p_plot.add_argument("-o", "--out", default=None)
    p_plot.add_argument("--show", action="store_true")
    p_plot.set_defaults(func=cmd_plot)

    sub.add_parser("gui", help="launch the desktop GUI").set_defaults(func=cmd_gui)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except (BackendError, ProtocolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
