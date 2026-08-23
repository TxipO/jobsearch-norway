"""Builds the per-vacancy søknad-tailoring prompt.

What needs to vary per vacancy, per NAV/Academic Work's own guidance
researched for PLAN-BUILDER.md, is the søknad — "why THIS role", not the
CV. So the model's job is: return only the søknad JSON.

Update 2026-07-20 (user-requested): CV auto-generation/copying was removed
entirely from generate_documents.py — it kept resurfacing an outdated
generic CV instead of the per-vacancy one the user actually wants. CVs now
only ever come from the place-cv skill (place_cv.py), which the user drives
directly: attach a CV file in chat, name the vacancy, done. This prompt
tells a Claude Code session receiving it (see CLAUDE_CODE_NOTE) to place an
attached CV FIRST if one is present, since that produces
profile/generated/<uuid>/cv.txt, which _candidate_profile_section() below
then prefers over the generic profile.md — so the søknad never claims a
fact absent from the actual CV being sent alongside it. Falls back to
profile.md when no per-vacancy CV exists yet.
"""

from pathlib import Path

from db import strip_html

PROFILE_DIR = Path(__file__).parent / "profile"

RULES = """\
Ти пишеш søknad (супровідний лист) під ОДНУ конкретну вакансію, англійською. \
Уяви, що ти — сам кандидат, який сів написати листа роботодавцю, а не \
асистент, що "генерує документ". CV вже готовий окремо — не переписуй його й \
не переказуй; лист доповнює CV, а не дублює. Повертаєш ТІЛЬКИ JSON (формат \
нижче), нічого крім нього.

## Що робить лист сильним (finn.no + NAV, реальні норвезькі джерела)
- Відповідай ПРЯМО на оголошення. Візьми 2-3 головні речі, які вакансія \
просить, і закрий кожну конкретним фактом з досвіду. Не переказуй вимоги \
назад роботодавцю ("Я бачу, що ви шукаєте X") — просто покажи, що ти це маєш.
- Показуй, не заявляй. Замість "маю сильні комунікативні навички" — короткий \
реальний випадок, де це видно (напр. пояснював технічні речі не-технічним \
людям у платній спільноті). Один живий приклад вартий трьох прикметників.
- Перший абзац — одразу по суті: чому саме ця роль і чому ти підходиш. Без \
розлогого вступу, без переліку особистих даних.
- Останній абзац закриває лист по-норвезьки: коли можеш почати і що радо \
прийдеш на співбесіду. Це очікувана частина, не опційна.
- Максимум ~1 сторінка, радше ¾. 3-4 абзаци. Рекрутер читає перший прохід \
~20 секунд — кожне речення має щось додавати.

## Голос — щоб не звучало як ШІ
Норвезькі рекрутери одразу впізнають ШІ-тексти. Уникай саме цього:
- Формульних зачинів: "I am writing to apply for...", "I am excited about \
the opportunity to...", "I am thrilled to...". Почни як жива людина — з думки, \
не з шаблону.
- Правила трьох і порожніх прикметників: "passionate, driven and dedicated", \
"dynamic", "results-driven professional", "synergy", "truly/deeply/genuinely". \
Викинь підсилювачі — факт сильніший за прислівник.
- Конструкцій "not only... but also", ідеально паралельних речень, однакової \
довжини всіх абзаців. Пиши нерівно, як людина: десь коротке речення, десь \
довше. Різна довжина абзаців — це нормально й правильно.
- Фіналу, що переказує весь лист ("In summary, I believe I would be a great \
fit because..."). Просто закінчи — доречним рядком про готовність і співбесіду.
- Стриманість > самопіар (норвезький Janteloven). Впевненість так, хвастощі \
ні. Факти говорять самі.

## Чесність (жорстко)
- НІКОЛИ не згадуй слабкі сторони, брак досвіду чи мови, "не відповідаю \
вимозі", "хочу бути чесним що...". Роботодавець оцінить сам — не роби це за \
нього. Мовчання про прогалину ≠ брехня. (Виняток: якщо вакансія ПРЯМО просить \
прокоментувати щось на кшталт стресостійкості — не констатуй слабкість голо, \
а переформулюй у бік розвитку: не "I struggle under stress", а "I work well \
under pressure and keep improving how I handle it".)
- НІКОЛИ не пиши, що диплом "не визнаний" — він визнається (bachelorgrad за \
HK-dir).
- Не вигадуй жодного факту поза профілем/CV нижче. Кожне речення має спиратись \
на щось реальне звідти.
"""

# lang="no" (2026-07-20, user-requested): the candidate's real Norwegian
# level is A1 — RULES above explicitly forbids writing free NO text FOR
# THAT REASON when the target is English. This variant is for when the
# posting itself is Norwegian and the user has deliberately chosen to send
# a Norwegian-language søknad anyway (their call, not a claim about their
# own fluency — the letter is AI-drafted from real facts either way, same
# as the English one). Same honesty rules, same forbidden filler, just
# instructing Norwegian output and citing cv-reference.md's actual NO
# conventions (no "Dear ___," opener, "Med vennlig hilsen," close —
# cv_builder.build_soknad's lang="no" branch already handles the
# structural parts; this only controls what the model writes in between).
RULES_NO = """\
Ти пишеш søknad (супровідний лист) під ОДНУ конкретну вакансію, норвезькою \
(bokmål). Уяви, що ти — сам кандидат, який сів написати листа роботодавцю, \
а не асистент, що "генерує документ". CV вже готовий окремо — не переписуй \
його й не переказуй. Повертаєш ТІЛЬКИ JSON (формат нижче), нічого крім нього.

## Що робить лист сильним (finn.no + NAV, реальні норвезькі джерела)
- Відповідай ПРЯМО на оголошення ("Les annonsen nøye og svar direkte på \
stillingsannonsen" — NAV). Візьми 2-3 головні речі, які вакансія просить, і \
закрий кожну конкретним фактом з досвіду. Не переказуй вимоги назад.
- Показуй, не заявляй ("Fokusér på hva du kan og begrunn med eksempler" — \
NAV). Замість "har gode kommunikasjonsevner" — короткий реальний випадок, де \
це видно. Один живий приклад вартий трьох прикметників.
- Перший абзац — одразу по суті: чому саме ця роль і чому ти підходиш. Без \
розлогого вступу, без переліку особистих даних.
- Останній абзац закриває лист: коли можеш почати і що радо прийдеш на \
співбесіду ("stiller gjerne til intervju"). Це очікувана частина, не опційна.
- Максимум ~1 сторінка, радше ¾ ("bør helst ikke være lenger enn en side" — \
NAV). 3-4 абзаци, охайно, без помилок.

## Голос — щоб не звучало як ШІ
Norske rekrutterere gjenkjenner AI-generert tekst умить. Уникай саме цього:
- Formульних зачинів: "Jeg søker med dette på stillingen som...", "Jeg er \
svært motivert for...". Почни як жива людина, з думки, не з шаблону.
- Svulstig språk і порожніх прикметників: "engasjert og resultatorientert", \
"brenner for bransjen", "dynamisk", "synergi", "svært/virkelig/dypt". Викинь \
підсилювачі — факт сильніший.
- Ідеально паралельних речень і однакової довжини всіх абзаців. Пиши нерівно, \
як людина: десь коротке речення, десь довше.
- Фіналу, що переказує весь лист. Просто закінчи доречним рядком про \
готовність і співбесіду.
- Стриманість > самопіар (Janteloven). Впевненість так, хвастощі ні.

## Чесність (жорстко)
- НІКОЛИ не згадуй слабкі сторони, брак досвіду чи мови, "не відповідаю \
вимозі". Роботодавець оцінить сам. Мовчання про прогалину ≠ брехня. (Виняток: \
якщо вакансія ПРЯМО просить прокоментувати щось — переформулюй у бік розвитку, \
не констатуй слабкість голо.)
- НІКОЛИ не пиши, що диплом "не визнаний" — він визнається (bachelorgrad за \
HK-dir).
- Не вигадуй жодного факту поза профілем/CV нижче.
- НЕ додавай привітання ("Kjære...") чи підпис ("Med vennlig hilsen") у \
paragraphs — лист іде одразу від заголовка до тексту, і код додає закриття сам.
"""

# 2026-07-20 (user-requested): the user attaches a CV file in the SAME chat
# message where they paste this prompt — this note tells a Claude Code
# session receiving it to place that file BEFORE writing the søknad JSON, so
# _candidate_profile_section() (via the cv.txt it produces) governs the
# facts instead of whatever fallback profile.md snapshot got baked into the
# "ПРОФІЛЬ КАНДИДАТА" section below at prompt-generation time. Written for
# Claude Code specifically (the user's own stated workflow), not a generic
# "if you're an agent" hedge.
CLAUDE_CODE_NOTE = """\
Якщо ти працюєш у Claude Code і користувач прикріпив у цьому ж повідомленні \
файл CV (PDF або docx) — це саме той CV, під який треба написати søknad. \
Перш ніж писати текст:
1. Розмісти прикріплений файл: py place_cv.py {uuid} <шлях до прикріпленого файлу>
2. Якщо place_cv.py видав WARNING про порожню екстракцію (PDF цього \
користувача часто БЕЗ текстового шару — сторінки це зображення, pypdf \
витягує 0 символів): прочитай розміщений PDF інструментом Read (він рендерить \
сторінки візуально), транскрибуй ПОВНИЙ зміст CV (секції, посади, дати, \
скіли, мови) і запиши плоским текстом у profile/generated/{slug}/cv.txt. \
НЕ лишай cv.txt порожнім.
3. cv.txt МАЄ ПРІОРИТЕТ над розділом "ПРОФІЛЬ КАНДИДАТА" нижче (той зібраний \
ДО розміщення CV і є лише fallback-знімком). Пиши søknad за фактами й \
акцентами саме з щойно розміщеного CV.
Якщо файл не прикріплено — просто працюй з розділом "ПРОФІЛЬ КАНДИДАТА" \
нижче як завжди.
"""

JSON_SPEC = """\
Формат відповіді — рівно цей JSON, нічого крім нього:
{{
  "soknad": {{
    "position_line": "Application for the position of X, {employer}",
    "paragraphs": ["абзац 1", "абзац 2", "абзац 3"]
  }}
}}
НЕ додавай привітання й підпис — код додасть "Dear Hiring Team" і "Kind regards" сам.

Останній крок (якщо ти в Claude Code): збережи JSON і запусти —
    echo '<JSON>' | py generate_documents.py {slug}
Це побудує лише soknad.docx/.pdf у profile/generated/{slug}/. CV сюди НЕ \
копіюється автоматично — якщо ще не розмістив його (крок вище), зроби це \
окремо через place_cv.py.
"""

JSON_SPEC_NO = """\
Формат відповіді — рівно цей JSON, нічого крім нього:
{{
  "soknad": {{
    "position_line": "Søknad på stilling som X, {employer}",
    "paragraphs": ["absnitt 1 (norsk)", "absnitt 2 (norsk)", "absnitt 3 (norsk)"]
  }}
}}
НЕ додавай "Kjære..." чи "Med vennlig hilsen" у paragraphs — код додасть \
закриття сам.

Останній крок (якщо ти в Claude Code): збережи JSON і запусти —
    echo '<JSON>' | py generate_documents.py {slug} --lang no
Це побудує лише soknad.docx/.pdf (норвезькою) у profile/generated/{slug}/. \
CV сюди НЕ копіюється автоматично — якщо ще не розмістив його (крок вище), \
зроби це окремо через place_cv.py.
"""


def _candidate_profile_section(slug: str) -> str:
    """Prefers the specific CV placed for this vacancy (via the place-cv
    skill/place_cv.py) over the generic profile.md — user-requested
    2026-07-20: once CVs vary per vacancy, the søknad must mirror facts and
    emphasis actually on THAT CV, not a canonical list that might include a
    job or skill this particular tailored CV dropped.

    Takes the already-sanitized slug, not the raw uuid — security-review
    2026-07-21 flagged that this was the one file-path-building spot in the
    codebase still using the raw uuid instead of the slug every sibling
    (place_cv.py, generate_documents.py, download_document()) already
    applies, since uuid is sourced from external feeds. Not exploitable
    today (the web route 404s before this runs unless the uuid already
    exists as a DB row, and no source can produce traversal-shaped uuids),
    but there's no reason for this one spot to be the odd one out."""
    per_vacancy_cv_text = PROFILE_DIR / "generated" / slug / "cv.txt"
    if per_vacancy_cv_text.exists():
        cv_text = per_vacancy_cv_text.read_text(encoding="utf-8").strip()
        # place_cv.py's pypdf-based extraction can return next to nothing
        # for a PDF whose text isn't a real selectable layer (vector-outlined
        # fonts, flattened text) — live bug caught 2026-07-20: a near-empty
        # cv.txt was still treated as "the CV's facts", handing the model an
        # empty profile instead of falling back, so the søknad had nothing
        # real to draw from. A genuine CV's extracted text is realistically
        # hundreds of characters at minimum; treat anything drastically
        # shorter as a failed extraction, not "the candidate has no facts".
        if len(cv_text) >= 200:
            return (
                "=== ПРОФІЛЬ КАНДИДАТА (текст CV, розміщеного саме під цю вакансію — "
                "єдине джерело фактів; НЕ згадуй нічого поза цим текстом) ===\n"
                f"{cv_text}"
            )
    profile = (PROFILE_DIR / "profile.md").read_text(encoding="utf-8")
    return f"=== ПРОФІЛЬ КАНДИДАТА (єдине джерело фактів) ===\n{profile}"


def build_resume_prompt(
    title: str, description_html: str, employer: str, municipal: str, county: str | None,
    uuid: str = "job", lang: str = "en",
) -> str:
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in uuid.lower()).strip("-") or "job"
    candidate_profile = _candidate_profile_section(slug)
    description = strip_html(description_html).strip()

    rules = RULES_NO if lang == "no" else RULES
    json_spec_template = JSON_SPEC_NO if lang == "no" else JSON_SPEC
    json_spec = json_spec_template.format(employer=employer or "the company", slug=slug)
    claude_code_note = CLAUDE_CODE_NOTE.format(uuid=uuid, slug=slug)

    return (
        f"=== ПРАВИЛА ===\n{rules}\n\n"
        f"=== ЯКЩО ТИ CLAUDE CODE ===\n{claude_code_note}\n\n"
        f"{candidate_profile}\n\n"
        f"=== ВАКАНСІЯ ===\n"
        f"Роботодавець: {employer or '—'}\n"
        f"Локація: {municipal or '—'}{f', {county}' if county else ''}\n"
        f"Назва позиції: {title or '—'}\n\n"
        f"Опис:\n{description}\n\n"
        f"=== ФОРМАТ ВІДПОВІДІ ===\n{json_spec}"
    )
