"""Converts a generated .docx to .pdf via headless LibreOffice — Norwegian
job applications are submitted as PDF, not docx (user-requested 2026-07-20),
so every CV/søknad the pipeline produces should have a PDF sitting next to
it without a manual conversion step.

Best-effort: if LibreOffice isn't installed or the conversion fails, this
warns and returns None rather than raising — a missing PDF converter
shouldn't crash document generation, since the .docx itself is still usable
(can be opened and exported to PDF by hand).
"""

import shutil
import subprocess
from pathlib import Path

# Known Windows install location (this project's dev machine) — checked
# first since `shutil.which` alone won't find it if LibreOffice didn't add
# itself to PATH, which is the common case on Windows.
_KNOWN_SOFFICE_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_soffice() -> str | None:
    on_path = shutil.which("soffice")
    if on_path:
        return on_path
    for candidate in _KNOWN_SOFFICE_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def convert_to_pdf(docx_path: Path) -> Path | None:
    docx_path = Path(docx_path)
    soffice = _find_soffice()
    if soffice is None:
        print(f"PDF:    skipped for {docx_path.name} — LibreOffice (soffice) not found")
        return None

    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
            capture_output=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"PDF:    conversion failed for {docx_path.name} — {e}")
        return None

    pdf_path = docx_path.with_suffix(".pdf")
    if not pdf_path.exists():
        print(f"PDF:    soffice reported success but {pdf_path.name} wasn't found")
        return None
    print(f"PDF:    {pdf_path}")
    return pdf_path
