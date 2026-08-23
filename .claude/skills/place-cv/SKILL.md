# Place-CV skill

The user hand-crafts/redesigns CVs outside this repo (Claude Design exports,
manual edits) and will keep doing this **per vacancy**, not just
occasionally for one master CV — user-requested 2026-07-20. This skill is
the repeatable hand-off: user attaches a file and names the vacancy, this
skill files it in the right place so `generate_documents.py`'s søknad step
picks up its facts (see `resume_prompt.py`'s per-vacancy `cv.txt` lookup).

**Trigger:** the user attaches/references a CV file (PDF or docx) and tells
you which vacancy it's for — a link, a uuid, or a title/company they name in
the same message. Per the user's own choice, vacancy identification is
always given explicitly this way; don't try to guess it from a filename or
"the currently open vacancy" convention. Two ways this trigger shows up:
- Directly: the user attaches a CV and names a vacancy in a message to you.
- Via the resume-prompt flow: the user pastes the prompt from
  `/vacancy/<uuid>/resume-prompt` into a fresh Claude Code chat AND attaches
  a CV in that same message — the prompt's own `CLAUDE_CODE_NOTE` section
  (see `resume_prompt.py`) tells you to run this skill first, before
  writing the søknad JSON, so the søknad is grounded in that specific CV.

**There is no "master/default CV" fallback anymore** (removed 2026-07-20,
user: "прибери цю функцію" — the old auto-copy kept resurfacing an outdated
generic CV). Every CV in `profile/generated/<uuid>/` comes from this skill,
placed deliberately, or doesn't exist yet.

## Steps

1. **Resolve the vacancy uuid.** If the user gave a uuid directly, use it.
   If they gave a NAV/Jobbnorge/finn.no link or a title/company, look it up:
   ```bash
   py -c "import db; c = db.connect(); rows = c.execute(\"SELECT uuid, title, business_name FROM vacancies WHERE title LIKE ? OR business_name LIKE ?\", ('%<search>%', '%<search>%')).fetchall(); print(rows)"
   ```
   If more than one row matches, ask the user which one rather than guessing.

2. **Place the file:**
   ```bash
   py place_cv.py <uuid> <path-to-file>
   ```
   This copies the file to `profile/generated/<uuid>/cv.<ext>` **as-is** (PDF
   stays PDF, docx stays docx — no forced conversion, per the user's own
   call that Norwegian applications go out as PDF but the source format
   shouldn't be forced) and extracts its text into `cv.txt` alongside it.

3. **If place_cv.py prints a WARNING about near-empty extraction** — the
   user's design-tool PDFs (confirmed live 2026-07-20 on their Claude
   Design/Canva-style exports) often have NO machine-readable text layer at
   all: the page is images/vector outlines, so pypdf AND pdfplumber both
   extract 0 chars. Don't leave cv.txt empty (resume_prompt.py would fall
   back to generic profile.md and the søknad wouldn't match this CV). Fix
   it yourself: **Read the placed PDF with the Read tool (it renders pages
   visually), transcribe the full CV content — headings, jobs, dates,
   skills, languages — and Write it to `profile/generated/<uuid>/cv.txt`**.
   Plain text, keep the CV's own wording and section order. This is the
   expected path for this user's CVs, not an edge case.

4. **Confirm to the user** what was filed and where — quote `place_cv.py`'s
   own stdout (`CV placed: ...`, `Text extracted: ...`), plus whether you
   had to transcribe manually.

5. If the user also wants a søknad generated for this same vacancy now,
   continue with the normal flow: `/vacancy/<uuid>/resume-prompt` in the web
   UI (or `resume_prompt.build_resume_prompt(..., uuid=uuid)`), which
   automatically prefers `profile/generated/<uuid>/cv.txt` over `profile.md`
   when it has real content (≥200 chars — near-empty extraction falls back
   to profile.md) — see `resume_prompt.py`'s docstring.

## Why cv.txt, not just the file itself

The søknad-generation prompt needs the CV's content as plain text to hand
to the model. Extracting once at placement time (rather than re-parsing the
PDF/docx on every prompt build) keeps `resume_prompt.py` simple and fast,
and means `place_cv.py`'s own text-extraction logic (pypdf for PDF,
python-docx for docx, including table cells) only has to be gotten right in
one place.

## generate_documents.py never touches cv.*

`generate_documents.py` only ever builds `soknad.docx`/`.pdf` — it doesn't
create, copy, or overwrite a CV. If `cv.docx`/`cv.pdf` already exists for
the vacancy (placed via this skill) it gets converted to PDF too as a
convenience; if none exists, `generate_documents.py` just prints a reminder
to run `place_cv.py` first. Either way, a CV placed via this skill is never
silently replaced.
