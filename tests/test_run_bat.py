from pathlib import Path


def test_run_bat_forces_utf8_python_mode() -> None:
    text = Path("run.bat").read_text(encoding="utf-8")

    assert 'set "PYTHONIOENCODING=utf-8"' in text
    assert 'set "PYTHONUTF8=1"' in text
