---
name: score-review
description: Manual review of the top of the vacancy list for one scoring profile — flags false positives with a one-line reason so they can go through /flagged. Use when the user asks to check the scoring top, "перевір топ", or after a scoring change.
---

# Score review

Run only when invoked (`/score-review [warehouse|it]`, default: both). Read-only:
it never changes scores, statuses or code.

1. Take the top 30 visible vacancies (`status='ACTIVE' AND excluded=0 AND
   user_status IN ('new','interesting')`) ordered by `score` (warehouse) or
   `score_it` (it), straight from `data/jobsearch.db` opened read-only.
2. For each, read the title and the first ~600 characters of the plain
   description plus its `score_breakdown`/`score_it_breakdown` matches.
3. Output one table per profile: score | title — employer | verdict
   (`ok` / `сумнівна` / `хибнопозитив`) | why, in one line. A false positive
   names the keyword or bonus that wrongly fired (e.g. "'førstelinje' = first
   line of a law firm").
4. End with the 3–5 most common false-positive causes and which list/rule in
   `scoring.py` they come from. Do NOT edit anything; the user decides, and
   confirmed cases go through `/flagged` and the Stage 2 item-1 corpus check.

Also mention, per vacancy, `extent_percent < 80` ("<30 год") — informational
only, it never changes the score.
