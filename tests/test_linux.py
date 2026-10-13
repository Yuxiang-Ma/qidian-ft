"""Linux support: setup hints, and the real pyserial backend over a pty."""
import errno
import os
import platform
import select
import sys
import threading

import pytest
from fakes import SN, FakeSensorBackend

from qidian_ft import FTSensor, Recorder, SensorConfig, platform_help
from qidian_ft import protocol as p
from qidian_ft.backends import serial_vcp
from qidian_ft.cli import main as cli_main


def test_udev_rule_sets_latency_and_access():
    rule = platform_help.UDEV_RULE
    assert 'ATTR{latency_timer}="1"' in rule and 'DRIVER=="ftdi_sio"' in rule
    assert 'TAG+="uaccess"' in rule and "ID_MM_DEVICE_IGNORE" in rule
    # uaccess only works from rule files sorted before 73-seat-late.rules
    assert os.path.basename(platform_help.UDEV_RULE_PATH) < "73"


def test_udev_rule_command_keeps_stdout_clean_for_tee(capsys):
    assert cli_main(["udev-rule"]) == 0
    out, err = capsys.readouterr()
    assert out == platform_help.UDEV_RULE
    assert "sudo tee" in err


@pytest.mark.parametrize("exc, expect", [
    (OSError(errno.EACCES, "Permission denied"), "dialout"),
    (OSError(errno.EAGAIN, "Resource temporarily unavailable"), "another program"),
])
def test_open_errors_explain_the_fix(monkeypatch, exc, expect):
    serial = pytest.importorskip("serial")
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    def refuse(*args, **kwargs):
        raise serial.SerialException(exc.errno, f"could not open port: {exc}")

    monkeypatch.setattr(serial, "Serial", refuse)
    with pytest.raises(serial_vcp.SerialBackendError, match=expect):
        serial_vcp.SerialDevice(port="/dev/ttyUSB0")


def test_latency_hint_is_platform_specific(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert "udev" in platform_help.latency_fix_hint("serial")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert "d2xx" in platform_help.latency_fix_hint("serial")


# -- end to end over a pseudo-terminal ----------------------------------------

def _request_length(buf: bytes) -> int:
    if len(buf) < 7:
        return 8 if len(buf) >= 2 and buf[1] == p.FC_READ else 1 << 30
    return 8 if buf[1] == p.FC_READ else 9 + buf[6]


def _serve(master: int, sensor: FakeSensorBackend, stop: threading.Event) -> None:
    """Answer Modbus requests arriving on the pty like the real sensor would."""
    buf = b""
    while not stop.is_set():
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            continue
        try:
            buf += os.read(master, 256)
        except OSError:
            return
        while len(buf) >= 8 and len(buf) >= _request_length(buf):
            n = _request_length(buf)
            sensor.write(buf[:n])
            buf = buf[n:]
            os.write(master, sensor.read_available())


@pytest.fixture
def pty_port():
    if not sys.platform.startswith("linux"):
        pytest.skip("pty end-to-end runs on Linux")
    pytest.importorskip("serial")
    master, slave = os.openpty()
    path = os.ttyname(slave)
    sensor = FakeSensorBackend(wrench=(1.5, -2.5, 3.5, 0.1, -0.2, 0.3))
    stop = threading.Event()
    thread = threading.Thread(target=_serve, args=(master, sensor, stop), daemon=True)
    thread.start()
    yield path, sensor
    stop.set()
    thread.join(1.0)
    os.close(master)
    os.close(slave)


def test_serial_backend_end_to_end_over_pty(pty_port):
    path, fake = pty_port
    cfg = SensorConfig(backend="serial", port=path, timeout_s=1.0)  # slack for loaded CI
    with FTSensor(cfg) as ft:
        assert ft.info.serial_number == SN
        assert ft.read().wrench == pytest.approx((1.5, -2.5, 3.5, 0.1, -0.2, 0.3))
        ft.tare()
        assert fake.control_writes == [p.CTRL_TARE]
        with Recorder(ft) as rec:
            assert rec.wait(0.3) is False  # still running
        assert rec.error is None and rec.n_samples > 10


def test_serial_port_is_exclusive_on_posix(pty_port):
    path, _ = pty_port
    first = serial_vcp.SerialDevice(port=path)
    try:
        with pytest.raises(serial_vcp.SerialBackendError, match="another program"):
            serial_vcp.SerialDevice(port=path)
    finally:
        first.close()
