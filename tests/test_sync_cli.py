"""sync.py is a thin CLI over web.app.trigger_sync() (2026-10-05, /fullreview
deep: it used to run its own unguarded copy of the pipeline)."""

import json
import runpy
from pathlib import Path

import web.app

SYNC_PY = str(Path(__file__).parent.parent / "sync.py")


def test_cli_delegates_to_trigger_sync_and_prints_utf8_json(monkeypatch, capsys):
    calls = []
    summary = {"at": "05.10.2026 10:00", "finn": {"parsed": 0, "warning": "Привіт ø"}}
    monkeypatch.setattr(web.app, "trigger_sync", lambda: calls.append(1) or summary)

    runpy.run_path(SYNC_PY, run_name="__main__")

    out = capsys.readouterr().out
    assert calls == [1]
    assert json.loads(out) == summary
    # ensure_ascii=False: the readable text is printed, not \u escapes.
    assert "Привіт ø" in out
