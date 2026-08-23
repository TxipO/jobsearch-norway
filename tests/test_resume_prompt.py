"""Tests for the søknad-tailoring prompt builder's lang="no" variant
(2026-07-20, user-requested) — locks in that the Norwegian prompt actually
instructs Norwegian output and the NO-specific JSON_SPEC/generate_documents
invocation, not just a copy of the English one."""

import resume_prompt
from resume_prompt import build_resume_prompt


def _prompt(lang="en"):
    return build_resume_prompt(
        title="IT-konsulent", description_html="<p>Beskrivelse</p>", employer="Test AS",
        municipal="Bergen", county="Vestland", uuid="abc-123", lang=lang,
    )


def test_default_lang_is_english_rules():
    text = _prompt()
    assert "англійською" in text
    assert "bokmål" not in text


def test_no_lang_instructs_norwegian_output():
    text = _prompt("no")
    assert "bokmål" in text
    assert "Søknad på stilling som" in text
    assert "py generate_documents.py abc-123 --lang no" in text


def test_no_lang_forbids_dear_and_signoff_in_paragraphs():
    text = _prompt("no")
    assert 'НЕ додавай "Kjære..." чи "Med vennlig hilsen"' in text


def test_en_lang_json_spec_unchanged():
    text = _prompt("en")
    assert "py generate_documents.py abc-123" in text
    assert "--lang no" not in text


def test_both_langs_carry_finn_nav_and_anti_ai_guidance():
    """2026-07-20, user-requested rewrite: the prompt must carry the
    finn.no/NAV søknad structure advice (answer the ad directly, close with
    availability + interview interest) and explicit anti-AI-voice guidance,
    in both language variants — not just the old bare rule list."""
    for lang in ("en", "no"):
        text = _prompt(lang)
        # finn/NAV structure
        assert "співбесіду" in text  # closing must invite an interview
        assert "оголошення" in text  # answer the ad directly
        # anti-AI voice
        assert "не звучало як ШІ" in text
        assert "жива людина" in text
        # honesty guardrails survived the rewrite
        assert "bachelorgrad" in text
        assert "Мовчання про прогалину" in text


def test_falls_back_to_profile_md_when_no_per_vacancy_cv(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_prompt, "PROFILE_DIR", tmp_path)
    (tmp_path / "profile.md").write_text("Generic profile facts.", encoding="utf-8")
    text = _prompt("en")
    assert "Generic profile facts." in text
    assert "розміщеного саме під цю вакансію" not in text


def test_uses_per_vacancy_cv_text_when_placed(tmp_path, monkeypatch):
    """place_cv.py extracts a placed CV into profile/generated/<uuid>/cv.txt
    (2026-07-20) — when it exists (and has real content), the prompt must
    source facts from it instead of the generic profile.md, so the søknad
    matches what's actually on the CV paired with this vacancy."""
    monkeypatch.setattr(resume_prompt, "PROFILE_DIR", tmp_path)
    (tmp_path / "profile.md").write_text("Generic profile facts.", encoding="utf-8")
    cv_dir = tmp_path / "generated" / "abc-123"
    cv_dir.mkdir(parents=True)
    # 200+ chars — realistic length for actually-extracted CV text, not
    # just above the fallback threshold by accident.
    cv_text = "A fact only on this vacancy's specific CV. " * 6
    (cv_dir / "cv.txt").write_text(cv_text, encoding="utf-8")

    text = _prompt("en")
    assert "A fact only on this vacancy's specific CV." in text
    assert "Generic profile facts." not in text
    assert "розміщеного саме під цю вакансію" in text


def test_falls_back_to_profile_md_when_cv_txt_extraction_was_near_empty(tmp_path, monkeypatch):
    """Live bug caught 2026-07-20: place_cv.py's pypdf extraction returned
    almost nothing for a PDF with no real selectable text layer, but the
    near-empty cv.txt still got treated as "the CV's facts" — the model
    got an empty profile instead of falling back, and the søknad had
    nothing real to draw from. A near-empty cv.txt must fall back to
    profile.md exactly like a missing one."""
    monkeypatch.setattr(resume_prompt, "PROFILE_DIR", tmp_path)
    (tmp_path / "profile.md").write_text("Generic profile facts.", encoding="utf-8")
    cv_dir = tmp_path / "generated" / "abc-123"
    cv_dir.mkdir(parents=True)
    (cv_dir / "cv.txt").write_text("\r\n", encoding="utf-8")

    text = _prompt("en")
    assert "Generic profile facts." in text
    assert "розміщеного саме під цю вакансію" not in text


def test_claude_code_note_tells_it_to_place_attached_cv_first():
    """User-requested 2026-07-20: the prompt is often pasted into a fresh
    Claude Code chat with a CV attached in the SAME message — the prompt
    must tell that session to place it (via place_cv.py) before writing
    the søknad, so the freshly-placed CV's cv.txt governs the facts
    instead of whatever profile.md snapshot got baked in at prompt-build
    time."""
    text = _prompt("en")
    assert "py place_cv.py abc-123" in text
    assert "МАЄ ПРІОРИТЕТ" in text


def test_json_spec_no_longer_mentions_copying_a_master_cv():
    """generate_documents.py stopped auto-copying/building a CV
    (2026-07-20, user: "прибери цю функцію") — the prompt's own closing
    instructions must not still claim it copies master-cv.docx."""
    text = _prompt("en")
    assert "master-cv" not in text
    assert "скопіює" not in text
