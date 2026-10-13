"""Per-OS setup advice: port permissions and the FTDI latency timer.

On a fresh Ubuntu install two things stand between the adapter and ~470 Hz:

1. Access. ``/dev/ttyUSB*`` belongs to group ``dialout``, which a desktop user
   is not in by default.
2. The ``ftdi_sio`` latency timer defaults to 16 ms (~60 Hz). The serial
   backend asks for 1 ms through ``ASYNC_LOW_LATENCY``, which needs no root;
   the udev rule below makes it stick on every plug-in regardless.

``qidian-ft udev-rule`` prints a rule that fixes both.
"""
from __future__ import annotations

import platform

UDEV_RULE_PATH = "/etc/udev/rules.d/70-qidian-ft.rules"

UDEV_RULE = """\
# qidian-ft: FTDI USB-RS485 adapters (KEYDYN QS-Z4-4530Ti-1 F/T sensor).
# 1 ms latency timer: ~470 Hz polling instead of ~60 Hz.
ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"
# Let the logged-in desktop user open the port without joining dialout, and
# keep ModemManager from probing it. uaccess must run before
# 73-seat-late.rules, hence the 70- prefix.
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
"""

UDEV_INSTALL = f"""\
qidian-ft udev-rule | sudo tee {UDEV_RULE_PATH}
sudo udevadm control --reload-rules
# then unplug and replug the USB-RS485 adapter"""


def permission_hint(port: str) -> str:
    """How to get read/write access to ``port``."""
    if platform.system() != "Linux":
        return f"Another program may hold {port}; close it (e.g. the vendor SAFS_TOOL2)."
    return (f"No permission to open {port}. Either install the udev rule:\n"
            f"{UDEV_INSTALL}\n"
            "or join the dialout group: sudo usermod -aG dialout $USER (then log out and in).")


def latency_fix_hint(backend_name: str) -> str:
    """How to bring a 16 ms FTDI latency timer down to 1 ms."""
    if platform.system() == "Linux":
        return "Install the udev rule: run `qidian-ft udev-rule` for instructions."
    if backend_name == "serial":
        return ("Use --backend d2xx, or set Device Manager > Port > Advanced > "
                "Latency Timer = 1.")
    return "Set the latency timer to 1 ms in the FTDI driver settings."


def tk_missing_hint() -> str:
    """Install advice when tkinter cannot be imported."""
    if platform.system() == "Linux":
        return "Tkinter is missing: sudo apt install python3-tk"
    return "Tkinter is missing: reinstall Python with the Tcl/Tk component."
