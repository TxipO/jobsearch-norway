"""Tests for generate_documents.py — søknad-only CLI bridge (CV generation
removed 2026-07-20, user: "прибери цю функцію" — the auto-copied/auto-built
CV kept resurfacing an outdated generic CV instead of the per-vacancy one
placed via place-cv). This script now only ever touches soknad.*, plus
converting an already-placed cv.docx to PDF if one exists."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import generate_documents as gd

# 2026-10-05 (/fullreview deep): these tests used to run the CLI as a
# subprocess against the REAL profile/generated/<slug> and clean up only
# after their asserts (a failure leaked folders into the user's data). They
# now run main() in-process with OUT_ROOT monkeypatched to tmp_path, and
# convert_to_pdf stubbed (no LibreOffice dependency).


class _FakeStdin:
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


@pytest.fixture
def out_root(tmp_path, monkeypatch):
    root = tmp_path / "generated"
    monkeypatch.setattr(gd, "OUT_ROOT", root)
    return root


@pytest.fixture
def converted(monkeypatch):
    """Records every convert_to_pdf call instead of running soffice."""
    calls = []
    monkeypatch.setattr(gd, "convert_to_pdf", lambda p: calls.append(Path(p).name))
    return calls


def _run(monkeypatch, uuid, tailoring, extra_args=(), raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(tailoring).encode("utf-8")
    monkeypatch.setattr(sys, "argv", ["generate_documents.py", uuid, *extra_args])
    monkeypatch.setattr(sys, "stdin", _FakeStdin(data))
    gd.main()


TAILORING = {"soknad": {"position_line": "Application for X, Y", "paragraphs": ["Para one.", "Para two."]}}


def test_cli_never_creates_a_cv_when_none_placed(out_root, converted, monkeypatch, capsys):
    """Core regression: this script must not fabricate/copy a generic CV —
    only a CV placed via place_cv.py should ever appear in the vacancy
    folder."""
    _run(monkeypatch, "test-slug-no-cv", TAILORING)
    assert "none placed yet" in capsys.readouterr().out

    out_dir = out_root / "test-slug-no-cv"
    assert not (out_dir / "cv.docx").exists()
    assert not (out_dir / "cv.pdf").exists()
    assert (out_dir / "soknad.docx").exists()


def test_cli_converts_already_placed_docx_cv_to_pdf(out_root, converted, monkeypatch):
    """If a .docx CV was placed (via place_cv.py) but not yet exported to
    PDF, this script's PDF pass should pick it up too."""
    from docx import Document

    out_dir = out_root / "test-slug-placed-cv"
    out_dir.mkdir(parents=True)
    Document().save(str(out_dir / "cv.docx"))

    _run(monkeypatch, "test-slug-placed-cv", TAILORING)

    assert "cv.docx" in converted and "soknad.docx" in converted


def test_cli_preserves_non_ascii_characters_from_stdin(out_root, converted, monkeypatch):
    """Live bug caught 2026-07-20: sys.stdin.read() alone silently mangled
    Norwegian characters (ø/å/æ) into mojibake on Windows ("Høgskolen" ->
    "HÃ¸gskolen") in a real generated søknad — reading raw bytes and
    decoding as UTF-8 explicitly is the fix."""
    from docx import Document

    tailoring = {
        "soknad": {
            "position_line": "Søknad på stilling som IT-konsulent, NLA Høgskolen",
            "paragraphs": ["Jeg søker på stillingen ved NLA Høgskolen i Sandviken."],
        }
    }
    _run(monkeypatch, "test-slug-encoding", tailoring)

    doc = Document(str(out_root / "test-slug-encoding" / "soknad.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Høgskolen" in text
    assert "HÃ¸gskolen" not in text
    assert "Ã¸" not in text


def test_cli_never_overwrites_an_already_placed_pdf(out_root, converted, monkeypatch):
    """Critical regression, caught live 2026-07-20: a stale leftover
    cv.docx sitting next to a freshly place_cv'd cv.pdf got silently
    reconverted, overwriting the user's real placed PDF with the stale
    docx's content. A cv.pdf at least as new as the docx must never be
    touched."""
    out_dir = out_root / "test-slug-stale-docx"
    out_dir.mkdir(parents=True)
    (out_dir / "cv.docx").write_bytes(b"stale docx bytes from an old run")
    (out_dir / "cv.pdf").write_bytes(b"the real placed pdf bytes")
    # Pin mtimes: pdf strictly newer than docx.
    os.utime(out_dir / "cv.docx", (1_000_000, 1_000_000))
    os.utime(out_dir / "cv.pdf", (2_000_000, 2_000_000))

    _run(monkeypatch, "test-slug-stale-docx", TAILORING)

    assert (out_dir / "cv.pdf").read_bytes() == b"the real placed pdf bytes"
    assert "cv.docx" not in converted


def test_cli_reexports_pdf_when_docx_is_newer(out_root, converted, monkeypatch, capsys):
    """2026-10-05 second guard (stale-CV deliverable class): a cv.docx newer
    than cv.pdf means the PDF predates the latest CV — re-export it."""
    out_dir = out_root / "test-slug-newer-docx"
    out_dir.mkdir(parents=True)
    (out_dir / "cv.docx").write_bytes(b"edited docx")
    (out_dir / "cv.pdf").write_bytes(b"old pdf")
    os.utime(out_dir / "cv.pdf", (1_000_000, 1_000_000))
    os.utime(out_dir / "cv.docx", (2_000_000, 2_000_000))

    _run(monkeypatch, "test-slug-newer-docx", TAILORING)

    assert "cv.docx" in converted
    assert "newer than cv.pdf" in capsys.readouterr().out


def test_bad_json_input_exits_with_error(out_root, converted, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "test-slug-badjson", None, raw=b"not json")
    assert "not valid JSON" in str(exc.value)


def test_utf8_bom_from_powershell_is_accepted(out_root, converted, monkeypatch):
    """PowerShell pipes/Out-File -Encoding utf8 prepend a BOM; plain utf-8
    decoding left it in and json.loads failed (2026-10-05)."""
    raw = b"\xef\xbb\xbf" + json.dumps(TAILORING).encode("utf-8")
    _run(monkeypatch, "test-slug-bom", None, raw=raw)
    assert (out_root / "test-slug-bom" / "soknad.docx").exists()


def test_non_utf8_input_gives_clear_error(out_root, converted, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "test-slug-cp1252", None, raw='{"a": "Høgskolen"}'.encode("cp1252"))
    assert "not valid UTF-8" in str(exc.value)


def test_non_dict_json_gives_clear_error(out_root, converted, monkeypatch):
    for raw in (b"[1, 2]", b'"text"', b"42", b"null"):
        with pytest.raises(SystemExit) as exc:
            _run(monkeypatch, "test-slug-nondict", None, raw=raw)
        assert "must be a JSON object" in str(exc.value)


def test_unsupported_lang_is_rejected(out_root, converted, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "test-slug-lang", {**TAILORING, "lang": "sv"})
    assert "unsupported lang" in str(exc.value)


def test_file_argument_reads_json_without_stdin(out_root, converted, monkeypatch, tmp_path):
    """--file is the PowerShell/apostrophe-proof input path used by
    resume_prompt.py (2026-10-05)."""
    tailoring = {"soknad": {"position_line": "Application for X, Y",
                            "paragraphs": ["It's a søknad with an apostrophe."]}}
    f = tmp_path / "in.json"
    f.write_text(json.dumps(tailoring, ensure_ascii=False), encoding="utf-8")

    _run(monkeypatch, "test-slug-file", None, extra_args=("--file", str(f)), raw=b"")

    assert (out_root / "test-slug-file" / "soknad.docx").exists()


def test_cli_subprocess_smoke_bad_json_exits_nonzero():
    """One true end-to-end subprocess check of the entry point (no files are
    written for bad input, so this never touches real profile data)."""
    project_root = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(project_root / "generate_documents.py"), "test-slug-badjson"],
        input="not json", capture_output=True, text=True, cwd=str(project_root),
    )
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr
