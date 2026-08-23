"""CLI bridge between the tailoring JSON (returned by the model) and the
søknad builder. Usage:

    py generate_documents.py <uuid> [--lang no] < tailoring.json
    echo '<json>' | py generate_documents.py <uuid> [--lang no]

Builds soknad.docx from the tailoring JSON and converts it to soknad.pdf via
LibreOffice headless — Norwegian applications are submitted as PDF, not
docx; the docx stays alongside for further edits.

CV generation removed 2026-07-20 (user: "прибери цю функцію" — the master-CV
copy/auto-build kept resurfacing an outdated generic CV instead of the
per-vacancy one the user actually wants). This script no longer touches
cv.docx/cv.pdf at all: place a CV for this vacancy yourself via the
place-cv skill (place_cv.py) before or after running this. If cv.docx/
cv.pdf already exists in the vacancy's folder, it's converted to PDF too
(covers the case where a freshly-placed .docx hasn't been exported yet);
otherwise this script only ever touches soknad.*.

--lang no selects the Norwegian søknad variant (cv_builder.build_soknad
(lang="no")) — also settable via a top-level "lang" key in the tailoring
JSON itself, which resume_prompt.py's NO variant tells the model to omit,
so this flag is the normal way to set it.
"""

import argparse
import json
import sys
from pathlib import Path

from cv_builder import build_soknad
from pdf_export import convert_to_pdf

OUT_ROOT = Path(__file__).parent / "profile" / "generated"


def main() -> None:
    # Same root cause as the stdin fix below, mirrored on the way out:
    # Windows' default console encoding for Python's sys.stdout is often a
    # legacy codepage (cp1252 here), not UTF-8 — print()ing "søknad" or an
    # em dash would otherwise write bytes that break a caller piping/
    # capturing this script's stdout expecting UTF-8 (caught via this
    # script's own tests failing to decode captured subprocess output).
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("uuid")
    parser.add_argument("--lang", choices=("en", "no"), default="en")
    args = parser.parse_args()
    uuid = args.uuid
    lang = args.lang
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in uuid.lower()).strip("-") or "job"

    # sys.stdin.read() alone picks up whatever encoding Python guessed for
    # the console (often not UTF-8 on Windows, even piped from PowerShell/
    # cmd's own UTF-8 output) — silently mangled Norwegian characters
    # (ø/å/æ) into mojibake ("Høgskolen" -> "HÃ¸gskolen") in a real
    # generated søknad, caught live 2026-07-20. Reading raw bytes and
    # decoding explicitly bypasses that guess entirely.
    raw = sys.stdin.buffer.read().decode("utf-8")
    try:
        tailoring = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"tailoring input is not valid JSON: {e}")
    lang = tailoring.get("lang", lang)

    out_dir = OUT_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # .pdf checked FIRST and short-circuits — a placed PDF is already the
    # final deliverable and must never be touched again. Checking .docx
    # first was a real bug (caught live 2026-07-20): if a stale leftover
    # cv.docx from an old run sat next to a freshly place_cv'd cv.pdf, this
    # silently re-converted the STALE docx and overwrote the user's actual
    # placed PDF with it.
    cv_pdf, cv_docx = out_dir / "cv.pdf", out_dir / "cv.docx"
    if cv_pdf.exists():
        pass  # already the final deliverable — nothing to do
    elif cv_docx.exists():
        convert_to_pdf(cv_docx)
    else:
        print(f"CV:     none placed yet for this vacancy — run place_cv.py {uuid} <file> first")

    sk = build_soknad(tailoring, out_dir / "soknad.docx", lang=lang)
    print(f"Søknad: {sk}")
    convert_to_pdf(sk)


if __name__ == "__main__":
    main()
