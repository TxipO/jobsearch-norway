"""Stage 4 (live-corpus divergence) measurement harness for /fullreview deep.

Measures every candidate pattern from a static audit against the live
`vacancies` table BEFORE anything is adopted (SKILL.md Stage 2 item 1,
Stage 4). It changes nothing: the DB is opened read-only, and the report
lists match counts plus a random sample per candidate, for someone to mark
TP/FP and compute precision.

    python .claude/skills/fullreview/stage4_measure.py
    python .claude/skills/fullreview/stage4_measure.py --active --sample 20

Writes _check/stage4-<date>.md (gitignored) and prints a one-line summary
per candidate. Edit CANDIDATES below for the next audit's list.
"""

import argparse
import ast
import datetime
import random
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import hard_blocks  # noqa: E402
import scoring  # noqa: E402
from db import strip_html  # noqa: E402

# --- Title-level hard_blocks candidates ------------------------------------
# (label, candidate regex, optional "current" regex to diff against). Every
# title match is split into "already blocked by a title pattern" vs "would
# be NEWLY blocked"; only the newly-blocked set needs a precision judgment.
TITLE_CANDIDATES = [
    # doctors
    ("overlege unanchored", r"overlege", r"\boverlege\b"),
    ("assistentlege", r"assistentlege", None),
    ("bedriftslege", r"bedriftslege", None),
    ("vikarlege", r"vikarlege", None),
    ("sykehuslege", r"sykehuslege", None),
    ("legevaktlege", r"legevakts?lege", None),
    ("lege unanchored (expected FP: college/kollege)", r"lege", r"\blege\b"),
    # teachers / pedagogues
    ("lærer unanchored", r"lærer", r"\blærer\b"),
    ("lærar unanchored", r"lærar", r"\blærar\b"),
    ("kontaktlærer", r"kontaktlærer", None),
    ("førskolelærer", r"førskolelærer", None),
    ("faglærer", r"faglærer", None),
    ("norsklærer", r"norsklærer", None),
    ("pedagog unanchored, not -isk", r"pedagog(?!isk)", r"\bpedagog\b"),
    ("barnehagepedagog", r"barnehagepedagog", None),
    ("sosialpedagog", r"sosialpedagog", None),
    # others
    ("forsker unanchored", r"forsker", r"\bforsker\b"),
    ("forskar unanchored", r"forskar", r"\bforskar\b"),
    ("fysioterapeut unanchored", r"fysioterapeut", r"\bfysioterapeut"),
    ("ergoterapeut unanchored", r"ergoterapeut", r"\bergoterapeut"),
    ("revisor unanchored", r"revisor", r"\brevisor"),
    ("tannlege unanchored", r"tannlege", r"\btannlege"),
    ("frisør unanchored (expected FP: hundefrisør)", r"frisør", r"\bfrisør"),
    ("hår/herre/dame-frisør", r"(?:hår|herre|dame)frisør", None),
    ("barber", r"\bbarber", None),
    ("maskinfører unanchored", r"maskinfører", r"\banleggsmaskinfører"),
    ("gravemaskinfører", r"gravemaskinfører", None),
    ("hjullasterfører", r"hjullaster(?:fører)?", None),
    ("lokfører", r"lok(?:omotiv)?fører", None),
    # English
    ("welders? (current: welder\\b)", r"\bwelders?\b", r"\bwelder\b"),
    ("mechanics? (current: mechanic\\b)", r"\bmechanics?\b", r"\bmechanic\b"),
    ("X drivers?", r"\b(?:truck|delivery|bus|taxi|van|lorry|hgv) drivers?\b", None),
    ("doctoral research fellow", r"doctoral research fellow|\bphd (?:research )?fellow", None),
    # professions in no list (owner's policy call, measure regardless)
    ("murer", r"murer", None),
    ("maler bare", r"maler", None),
    ("maler anchored", r"\bmalere?\b", None),
    ("blikkenslager", r"blikkenslager", None),
    ("el-/elektromontør", r"(?:\bel|elektro)[- ]?montør", None),
    ("montør (any, scoring +6 today)", r"montør", None),
    ("dekksoffiser", r"dekksoffiser", None),
    ("maskinoffiser", r"maskinoffiser", None),
    ("helsesekretær", r"helsesekretær", None),
    ("apotektekniker", r"apotektekniker", None),
    # ASCII fallbacks: does the corpus carry de-diacritised spellings at all?
    ("ASCII sjafor", r"sjafor", None),
    ("ASCII laerer/laerar", r"laer[ea]r", None),
    ("ASCII rorlegger", r"rorlegger", None),
    ("ASCII tomrer", r"tomrer", None),
    ("ASCII frisor", r"frisor\b", None),
    ("ASCII -forer (kran/truck/maskin)", r"(?:kran|truck|maskin|lok)forer", None),
    ("ASCII farmasoyt", r"farmasoyt", None),
    ("ASCII bioingenior", r"bioingenior", None),
]

# --- Body/title scoring candidates ------------------------------------------
LINJE_KW = ["1. linje", "1.linje", "førstelinje", "2. linje", "2.linje", "andrelinje"]
NEGATION_NEAR_REMOTE_RE = re.compile(
    r"(?:not|no|ikke|uten|never)\b[^.]{0,40}$|^[^.]{0,40}\b(?:not (?:possible|available|an option)|"
    r"ikke (?:mulig|aktuelt)|is not offered|er ikke)"
)
FLUENCY_CANDIDATE_RE = re.compile(
    r"gode norskkunnskaper|norskkunnskaper (?:er )?(?:et )?krav|norsk (?:språk)?(?:nivå )?b2|"
    r"\bb2[- ]nivå|fluent norwegian|norwegian (?:is )?(?:required|a requirement|a must)|"
    r"(?:god|gode|svært gode) norsk(?:e)? språkkunnskaper|beherske norsk"
)
CURRENT_FLUENCY_RE = re.compile(
    r"flytende norsk|norsk (skriftlig og muntlig|muntlig og skriftlig)"
    r"|(written and oral|oral and written|verbal and written|spoken and written) "
    r"(communication )?(skills )?in norwegian"
    r"|fluent(ly)? in norwegian"
)


def parse_occupation_categories_const() -> list[str]:
    """Read web/app.py's OCCUPATION_CATEGORIES by AST, without importing the
    FastAPI app (import has side effects: templates, DB path, routes)."""
    tree = ast.parse((ROOT / "web" / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "OCCUPATION_CATEGORIES" for t in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise RuntimeError("OCCUPATION_CATEGORIES not found in web/app.py")


def title_blocked_now(title_l: str) -> str | None:
    for key, patterns, _reason in hard_blocks.BLOCK_CATEGORIES:
        if any(re.search(p, title_l) for p in patterns):
            return key
    return None


def level1s(raw: str | None) -> list[str]:
    return sorted(scoring._parse_occupation_category_level1(raw))


def snippet(text: str, m: re.Match, width: int = 90) -> str:
    s = text[max(0, m.start() - width):m.end() + width].replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "jobsearch.db"))
    ap.add_argument("--active", action="store_true", help="only status='ACTIVE' rows")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(args.seed)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    where = "WHERE status = 'ACTIVE'" if args.active else ""
    rows = conn.execute(
        f"SELECT uuid, status, title, description, source, excluded, exclusion_reason, "
        f"occupation_categories, language, municipal, county, extent_percent, engagement_type "
        f"FROM vacancies {where}"
    ).fetchall()

    out: list[str] = []
    summary: list[str] = []
    today = datetime.date.today().isoformat()
    out.append(f"# Stage 4 measurement — {today}\n")
    out.append(f"DB: `{db_path}` · rows: {len(rows)} ({'ACTIVE only' if args.active else 'all statuses'}) · "
               f"sample={args.sample} seed={args.seed}\n")
    out.append("Mark each sample line TP/FP; precision = TP / sampled. Adopt only ≥90%.\n")

    def sample(items):
        items = list(items)
        return rng.sample(items, min(args.sample, len(items)))

    # Pre-compute lowercased title/text once.
    prepared = []
    for r in rows:
        title_l = (r["title"] or "").lower()
        text = f"{title_l} {strip_html(r['description'] or '')}".lower()
        prepared.append((r, title_l, text))

    # ---- 1. Title candidates ----
    out.append("\n## 1. hard_blocks title candidates\n")
    for label, cand, current in TITLE_CANDIDATES:
        cand_re, cur_re = re.compile(cand), re.compile(current) if current else None
        matched = [(r, t) for r, t, _ in prepared if cand_re.search(t)]
        new = [(r, t) for r, t in matched if not title_blocked_now(t)]
        only_bare = [(r, t) for r, t in matched if cur_re and not cur_re.search(t)]
        distinct_new = Counter(r["title"] for r, _ in new)
        summary.append(f"{label:52s} match={len(matched):5d}  newly-blocked rows={len(new):5d} "
                       f"distinct titles={len(distinct_new):4d}")
        out.append(f"\n### `{cand}` — {label}\n")
        out.append(f"- title matches: {len(matched)}; already title-blocked: {len(matched) - len(new)}; "
                   f"**newly blocked: {len(new)} rows / {len(distinct_new)} distinct titles**")
        if cur_re:
            out.append(f"- caught by candidate but not by current `{current}`: {len(only_bare)}")
        tokens = Counter(
            tok for _, t in new for tok in re.findall(r"\w*" + cand.removeprefix(r"\b") + r"\w*", t)
        ) if not cand.startswith("(") else Counter()
        if tokens:
            out.append("- matched word forms (newly blocked): "
                       + ", ".join(f"{w} ×{n}" for w, n in tokens.most_common(30)))
        if new:
            out.append(f"- sample of distinct newly-blocked titles ({min(args.sample, len(distinct_new))}):")
            for title in sample(distinct_new):
                r = next(r for r, _ in new if r["title"] == title)
                out.append(f"  - [ ] {title!r} · {r['source']} · {', '.join(level1s(r['occupation_categories'])) or '—'}"
                           f"{' · already excluded: ' + r['exclusion_reason'] if r['excluded'] else ''}")

    # ---- 2. Scoring candidates ----
    out.append("\n## 2. scoring.py candidates\n")

    def body_section(label, rows_with_match, note=""):
        out.append(f"\n### {label}\n")
        if note:
            out.append(note)
        out.append(f"- rows: {len(rows_with_match)}")
        summary.append(f"{label:52s} rows={len(rows_with_match):5d}")
        for r, snip in sample(rows_with_match):
            out.append(f"  - [ ] {r['title']!r} · {', '.join(level1s(r['occupation_categories'])) or '—'} · …{snip}…")

    # 2a. 1./2. linje (and førstelinje/andrelinje) as the ONLY IT-support hit
    other_it = [k for k in scoring.IT_SUPPORT_KEYWORDS if k not in LINJE_KW + ["3. linje", "3.linje", "tredjelinje"]]
    hits = []
    by_cat = Counter()
    for r, t, text in prepared:
        found = [k for k in LINJE_KW if k in text]
        if found and not any(k in text for k in other_it):
            m = re.search(re.escape(found[0]), text)
            hits.append((r, snippet(text, m)))
            by_cat.update(level1s(r["occupation_categories"]) or ["—"])
    body_section("linje-terms as ONLY IT-support signal (+8 each, up to +16)", hits,
                 "- by NAV level1: " + ", ".join(f"{c} ×{n}" for c, n in by_cat.most_common()))

    for kw in ("windows", "ticketing"):
        hits = []
        for r, t, text in prepared:
            if kw in text:
                others = [k for k in scoring.IT_SUPPORT_KEYWORDS if k != kw and k in text]
                if not others:
                    hits.append((r, snippet(text, re.search(kw, text))))
        total = sum(1 for _, _, text in prepared if kw in text)
        body_section(f"`{kw}` as ONLY IT-support signal", hits, f"- all rows containing it: {total}")

    hits = [(r, snippet(text, m)) for r, t, text in prepared
            if (m := re.search(r"data ?-?warehouse|datavarehus", text)) and "warehouse" in text]
    total = sum(1 for _, _, text in prepared if "warehouse" in text)
    body_section("`warehouse` via 'data warehouse'", hits, f"- all rows containing `warehouse`: {total}")

    hits = [(r, snippet(text, m)) for r, t, text in prepared if (m := re.search(r"programmer(?!er|ing)", text))]
    body_section("`programmer` (DEV_SECURITY) — NO plural 'programs' vs EN 'programmer'", hits)

    utv = [(r, t) for r, t, _ in prepared if "utvikler" in t]
    forms = Counter(tok for _, t in utv for tok in re.findall(r"\w*utvikler\w*", t))
    out.append("\n### `utvikler` in title (DEV_TITLE -40) — word forms\n")
    out.append(f"- rows: {len(utv)}; forms: " + ", ".join(f"{w} ×{n}" for w, n in forms.most_common(40)))
    summary.append(f"{'utvikler title forms':52s} rows={len(utv):5d} forms={len(forms)}")

    sen = [(r, t) for r, t, _ in prepared if "senior" in t]
    forms = Counter(tok for _, t in sen for tok in re.findall(r"\w*senior\w*", t))
    out.append("\n### `senior` in title (-15) — word forms\n")
    out.append(f"- rows: {len(sen)}; forms: " + ", ".join(f"{w} ×{n}" for w, n in forms.most_common(40)))
    summary.append(f"{'senior title forms':52s} rows={len(sen):5d} forms={len(forms)}")

    vik = [(r, t) for r, t, _ in prepared if "vikar" in t]
    forms = Counter(tok for _, t in vik for tok in re.findall(r"\w*vikar\w*", t))
    out.append("\n### `vikar` in title (+6) — word forms\n")
    out.append(f"- rows: {len(vik)}; forms: " + ", ".join(f"{w} ×{n}" for w, n in forms.most_common(40)))
    summary.append(f"{'vikar title forms':52s} rows={len(vik):5d} forms={len(forms)}")

    # REMOTE: rows scoring is_remote today, with a negation within ~40 chars
    hits = []
    for r, t, text in prepared:
        if not scoring._keyword_present_unnegated(text, scoring.REMOTE_KEYWORDS):
            continue
        for kw in scoring.REMOTE_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                before, after = text[max(0, m.start() - 45):m.start()], text[m.end():m.end() + 45]
                if NEGATION_NEAR_REMOTE_RE.search(before) or NEGATION_NEAR_REMOTE_RE.search(after):
                    hits.append((r, snippet(text, m)))
                    break
            else:
                continue
            break
    total_remote = sum(1 for _, _, text in prepared
                       if scoring._keyword_present_unnegated(text, scoring.REMOTE_KEYWORDS))
    body_section("REMOTE +15 with a negation within 45 chars (6-char window misses it)", hits,
                 f"- rows getting remote +15 today: {total_remote}")

    # DEGREE stacking: degree -10 AND formal_qualification -35; and degree firing
    # on an IT-field clause that formal_qualification exempts.
    stack, it_field = [], []
    for r, t, text in prepared:
        degree = [p for p in scoring.DEGREE_REQUIRED_PATTERNS if re.search(p, text)]
        if not degree:
            continue
        m = re.search(degree[0], text)
        if scoring._has_unmet_formal_qualification(text):
            stack.append((r, snippet(text, m)))
        window = text[max(0, m.start() - 120):m.end() + 120]
        if scoring.FORMAL_QUALIFICATION_IT_FIELD_RE.search(window):
            it_field.append((r, snippet(text, m)))
    body_section("DEGREE -10 stacked with formal_qualification -35", stack)
    body_section("DEGREE -10 fired with an IT-field term within 120 chars (no exemption)", it_field)

    hits = [(r, snippet(text, m)) for r, t, text in prepared
            if (m := FLUENCY_CANDIDATE_RE.search(text)) and not CURRENT_FLUENCY_RE.search(text)]
    body_section("Norwegian-fluency candidate phrases missed by current regex", hits,
                 "- read each: is Norwegian a HARD requirement here? (FP = soft/'en fordel')")

    # ---- 3. Hardcoded lists ----
    out.append("\n## 3. Hardcoded lists\n")
    live = Counter()
    for (raw,) in conn.execute(f"SELECT occupation_categories FROM vacancies {where}"):
        for c in level1s(raw):
            live[c] += 1
    const = parse_occupation_categories_const()
    missing, stale = sorted(set(live) - set(const)), sorted(set(const) - set(live))
    out.append("- live level1 counts: " + ", ".join(f"{c} ×{n}" for c, n in live.most_common()))
    out.append(f"- **in DB, missing from OCCUPATION_CATEGORIES:** {missing or 'none'}")
    out.append(f"- **in OCCUPATION_CATEGORIES, absent from DB:** {stale or 'none'}")
    summary.append(f"{'OCCUPATION_CATEGORIES diff':52s} missing={missing} stale={stale}")

    sources = Counter(s for (s,) in conn.execute(f"SELECT source FROM vacancies {where}"))
    out.append("- sources: " + ", ".join(f"{s} ×{n}" for s, n in sources.most_common()))
    unlisted = sorted(set(sources) - set(scoring._SOURCE_TIE_BREAK_PRIORITY))
    unused = sorted(set(scoring._SOURCE_TIE_BREAK_PRIORITY) - set(sources))
    out.append(f"- **sources missing from _SOURCE_TIE_BREAK_PRIORITY:** {unlisted or 'none'}")
    out.append(f"- **_SOURCE_TIE_BREAK_PRIORITY keys with 0 rows:** {unused or 'none'}")
    summary.append(f"{'_SOURCE_TIE_BREAK_PRIORITY':52s} unlisted={unlisted} zero-rows={unused}")

    out_path = Path(args.out) if args.out else ROOT / "_check" / f"stage4-{today}.md"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
