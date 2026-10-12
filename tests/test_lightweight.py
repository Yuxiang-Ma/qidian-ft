import gc
import subprocess
import sys

from qidian_ft.cli import build_parser


def test_core_import_pulls_no_numpy_or_matplotlib():
    code = ("import sys, qidian_ft; "
            "assert 'numpy' not in sys.modules, 'numpy'; "
            "assert 'matplotlib' not in sys.modules, 'matplotlib'")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_cli_parses_all_commands():
    parser = build_parser()
    for argv in (["info"], ["probe"], ["record", "--duration", "1"], ["plot", "x.csv"],
                 ["gui"], ["--port", "COM4", "--backend", "serial", "info"]):
        assert parser.parse_args(argv).func


def test_gui_builds_without_sensor():
    import pytest
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        from qidian_ft.gui import FTApp
        app = FTApp(root)
        root.update()
        assert str(app.tare_btn["state"]) == "disabled"
    finally:
        root.destroy()
        del app
        gc.collect()  # free Tk images on the main thread
