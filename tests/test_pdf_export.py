"""Unit tests for pdf_export.py's conversion logic, with subprocess/which
mocked out so these don't depend on LibreOffice actually being installed —
generate_documents.py's own tests cover the real end-to-end conversion."""

import subprocess

import pdf_export


def test_missing_soffice_returns_none_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: None)
    monkeypatch.setattr(pdf_export.Path, "exists", lambda self: False)
    docx = tmp_path / "cv.docx"
    docx.write_text("x")
    assert pdf_export.convert_to_pdf(docx) is None


def test_successful_conversion_returns_pdf_path(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: "soffice")

    def fake_run(cmd, **kwargs):
        # Simulate LibreOffice actually writing the output file.
        out_pdf = tmp_path / "cv.pdf"
        out_pdf.write_text("fake pdf")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pdf_export.subprocess, "run", fake_run)
    docx = tmp_path / "cv.docx"
    docx.write_text("x")

    result = pdf_export.convert_to_pdf(docx)
    assert result == tmp_path / "cv.pdf"


def test_conversion_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: "soffice")

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(pdf_export.subprocess, "run", fake_run)
    docx = tmp_path / "cv.docx"
    docx.write_text("x")

    assert pdf_export.convert_to_pdf(docx) is None


def test_conversion_timeout_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: "soffice")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(pdf_export.subprocess, "run", fake_run)
    docx = tmp_path / "cv.docx"
    docx.write_text("x")

    assert pdf_export.convert_to_pdf(docx) is None
