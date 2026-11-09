"""Tests for console and windowless startup failure reporting."""

import main


def test_source_startup_failure_is_printed(monkeypatch, capsys):
    """Source execution still reports startup failures on the console."""
    monkeypatch.setattr(main, "_windowless_build", lambda: False)
    monkeypatch.setattr(main, "startup",
                        lambda: (_ for _ in ()).throw(main.StartupError("missing config")))
    assert main.main() == 1
    assert "Cannot start: missing config" in capsys.readouterr().out


def test_windowless_startup_failure_is_reported(monkeypatch):
    """A windowless startup failure is sent to the visible error path."""
    messages = []
    monkeypatch.setattr(main, "_windowless_build", lambda: True)
    monkeypatch.setattr(main, "_report_startup_error", messages.append)
    monkeypatch.setattr(main, "startup",
                        lambda: (_ for _ in ()).throw(main.StartupError("missing config")))
    assert main.main() == 1
    assert messages == ["missing config"]


def test_windowless_build_does_not_fall_back_to_invisible_console(monkeypatch):
    """A missing GUI in the packaged executable becomes a visible failure."""
    import builtins
    import pytest

    original_import = builtins.__import__

    def missing_gui(name, *args, **kwargs):
        if name == "ui.app":
            raise ImportError("GUI unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(main, "_windowless_build", lambda: True)
    monkeypatch.setattr(builtins, "__import__", missing_gui)
    with pytest.raises(RuntimeError, match="GUI is not available"):
        main.run_gui_mode(None, False)
