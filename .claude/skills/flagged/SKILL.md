---
name: flagged
description: Work the user's flagged-vacancy queue (🚩 "позначено як помилка") — diagnose why each one got past scoring/hard_blocks, fix the cause at the shared root, measure against the live corpus, and clear the flags. Invoke for "зарепорчені вакансії", "прапорці", "перевір позначені", "чому це показується", or when the main list's "Приховано N вакансій, позначених як помилка" banner is non-empty.
---

# Flagged queue

The 🚩 flag is the user saying **"the software got this one wrong"** — not
"I'm not interested". It is the highest-signal input this project has:
every real scoring/hard-block bug found so far came from this queue, a
direct data measurement, or `/fullreview deep`'s live-corpus stage — never
from reading code.

The banner on `/` reads *"Приховано N вакансій, позначених як помилка — Клод
переглядає їх і виправляє причину"*. That promise is the contract: the queue
is worked until it is empty **or** until what remains has been explained to
the user as correct-by-design.

## The one rule that matters

**A flag is a symptom. Fix the class, not the row.** Never special-case the
flagged vacancy. If a fix wouldn't also catch the next twenty ads shaped
like it, it isn't the fix. Every round so far has turned 8-10 flagged rows
into 2-4 root causes.

---

## Step 1 — pull the queue with enough context to judge

```python
python -c "
import sys, db; sys.stdout.reconfigure(encoding='utf-8')
conn = db.connect()
for r in conn.execute('''
  SELECT uuid, title, employer_name, municipal, county, source, language,
         score, score_it, excluded, exclusion_reason, extent_percent,
         length(description) AS desc_len
  FROM vacancies WHERE flagged_at IS NOT NULL ORDER BY flagged_at'''):
    print(dict(r))
"
```

Group by shape before reading any code. Language, source, title pattern and
`excluded` together usually name the class on sight — six English trade ads
in one batch is a missing-English-patterns hole, not six bugs.

## Step 2 — reproduce with the real code, never by reading it

The single highest-yield habit here. Run the actual predicates on the actual
row and print the intermediate state — which clauses matched, whether each
sat in a required section, which verb/marker fired:

```python
body = strip_html(row["description"]).lower()
print(hb._has_unmet_truckforerbevis_requirement(title_l, body))
for clause, in_req in hb.iter_requirement_clauses(body):
    if MENTION_RE.search(clause):
        print(in_req, repr(clause[:110]),
              "VERB=", bool(hb.REQUIREMENT_VERB_RE.search(clause)),
              "OPT=", hb.has_optional_marker(clause))
```

This is what separates a real diagnosis from a plausible story. Reading the
regex will convince you of the wrong cause about half the time — 2026-09-02
it said "English ads are never excluded", and the corpus said English ads
are excluded at 41% vs Norwegian 43%; the actual hole was narrower and
elsewhere.

## Step 3 — decide honestly: bug, or working as designed?

Not every flag is a bug, and inventing a rule to make a flag go away is
worse than leaving it. Exclusion means **legally/practically impossible for
this user** (authorisation, fagbrev, clearance, a certificate they don't
hold), not **unappealing**.

2026-09-02: of ten flagged, eight were real bugs and two ("Countertop
Installer", "CAD Technician — training provided") had no certification gate
at all. Blocking those would have been over-blocking. They were reported to
the user with the reasoning and left flagged for the user to decide.

If a flag is really "this is irrelevant, why is it scoring 35?", that is a
**scoring** question — look at `score_breakdown` and find the component that
paid out, rather than reaching for `hard_blocks`.

```python
json.loads(row["score_breakdown"])  # which component actually paid out?
```

## Step 4 — measure the candidate fix on the live corpus BEFORE writing it

Non-negotiable, and it has repeatedly changed the design:

```python
rows = conn.execute("SELECT uuid,title,excluded FROM vacancies WHERE status='ACTIVE'").fetchall()
hits = [r for r in rows if re.search(pattern, r["title"] or "", re.I)]
print(len(hits), "hits,", sum(1 for r in hits if not r["excluded"]), "new")
for r in hits:
    if not r["excluded"]:
        print("  +", r["title"][:78])   # read EVERY new one
```

Read every newly-affected row, not a sample, while the count is small
enough to. Concrete saves from doing this: `mason` was dropped (added
nothing, risked matching a personal name); `frisør` kept its `\b` (pet
groomers are a different trade); `matproduksjon`/`varemottak` were dropped
outright for matching kitchen ads.

Then verify through the **real pipeline**, not the predicate alone —
`check_exclusion()` on its own doesn't know about the batch duplicate and
description-lending passes, and comparing against it produced 240 phantom
"regressions" on 2026-09-02:

```python
# snapshot -> scoring.rescore_all(conn) -> diff excluded/score per uuid
```
Expect duplicate-pass churn of a few rows in both directions; anything else
moving is a real regression to explain before committing.

## Step 5 — fix at the shared root

`hard_blocks.py` owns the shared vocabulary — `iter_requirement_clauses`,
`REQUIREMENT_HEADING_RE`, `OPTIONAL_HEADING_RE`, `REQUIREMENT_VERB_RE`,
`has_optional_marker`. `scoring.py` imports them. Extend the shared piece so
every consumer benefits at once; five copies of a soft/hard rule drift.

Live example: the trailing-qualifier fix ("har gyldig truckførerbevis,
gjerne T1–T4" is a hard requirement) generalised the existing
parenthesis-scoping rule ("Truckførerbevis T8 (T8.4 er en fordel)") into one
`has_optional_marker()` used by all five decision sites, instead of adding a
comma special-case to one of them.

**Known recurring classes** — check these first, they keep coming back:

| Class | Shape | Fix |
|---|---|---|
| Norwegian-only patterns | English ad slips past `elektriker` | English titles, matched on title regardless of detected language (langdetect calls one-word English titles Norwegian) |
| Compound words | `drosjesjåfør` misses `\bsjåfør` | drop the leading `\b` — then re-measure, some compounds are different professions |
| Bare ambiguous keyword | `troubleshooting` alone scores an electrician as IT | require co-occurrence with a second keyword of the same track |
| Softener scoping | a trailing/parenthesised qualifier disarms a hard requirement | `has_optional_marker()` strips qualifiers before testing |
| Missing heading | requirement reads as prose | add to `REQUIREMENT_HEADING_RE`, measure how many live ads use it |

## Step 6 — regression test each fix, then clear the flags

One test per fix, naming the live case and the date. Include the **mirror
case** — the thing that must NOT flip (a leading softener must still win
over a trailing qualifier; `\bmechanic\b` must not swallow "mechanical
engineer"). A fix without its mirror test is how the next widening
over-blocks.

Then clear the flags you actually fixed, and only those:

```python
for r in conn.execute("SELECT uuid FROM vacancies WHERE flagged_at IS NOT NULL AND excluded = 1"):
    db.set_flagged(conn, r["uuid"], False)
```

Rows still flagged after this are the correct-by-design ones — **name them
to the user with the reasoning**, don't silently unflag and don't invent a
rule for them.

## Step 7 — report and record

Report per flagged row: what it was, why it got through, what changed.
Give the corpus numbers (newly excluded / un-excluded / scores moved) — this
project's user reads them and has caught bad calls from them.

Then update memory (`jobsearch-norway-profile` for scoring/criteria,
`jobsearch-norway-sources` for pipeline) and mirror to Obsidian. A genuinely
new bug **class** also goes into `/fullreview`'s Stage 2 list; a new instance
of a known class goes in memory only.
