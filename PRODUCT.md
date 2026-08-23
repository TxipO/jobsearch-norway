# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack
Existing codebase: FastAPI + Jinja2 server-rendered templates + HTMX for partial updates, plain hand-written CSS (no framework, no build step) in `web/static/style.css`. Backend is Python (SQLite via `db.py`). Runs locally only (`uvicorn` on `127.0.0.1`), started and stopped manually by the user — not deployed or hosted anywhere.

## Users
A single user: a Ukrainian refugee (individual protection case pending, work permit granted) living near Balestrand, Vestland, Norway, ~1.5 years in-country. Norwegian A1, English B2, Ukrainian/Russian native. No driver's license — a hard constraint that rules out most on-site rural jobs. This is a personal tool built and used by this one person, run locally on their own machine — not a multi-tenant product, no accounts, no login.

## Product Purpose
An aggregator and workflow tool for the user's own Norwegian job search. It pulls vacancies from multiple public feeds (NAV, Jobbnorge, finn.no, Easycruit), scores each one against the user's specific profile and constraints, hides vacancies that are categorically impossible for them (missing licenses, regulated professions, security clearance requirements), and tracks the application pipeline (kanban: new → interesting → applied → interview → offer / rejected / ignored / archived). It also generates CV/cover-letter prompts tailored to each vacancy. Success = landing a job; the project is then archived, not iterated on further long-term.

## Positioning
Not a generic job board or aggregator — the value is a scoring and filtering model hand-tuned to one person's exact, unusual constraint set (no driver's license, degree-recognition status, A1 Norwegian, rural west-Norway location, entry-level requirement) that no generic job board encodes. Rule-based, evidence-driven scoring: every scoring signal is empirically checked against real vacancies before being trusted, not assumed.

## Operating Context
Used solo, on a laptop, likely in short sessions checking new vacancies after each sync. Primary workflows: browse/filter the vacancy list, drag/track vacancies through the kanban pipeline, open a vacancy to read its score breakdown and generate an application prompt, flag mis-scored vacancies back into a review queue. Norwegian and English source text; interface language is Ukrainian throughout (`lang="uk"`).

## Capabilities and Constraints
- Vacancy sources: NAV feed, Jobbnorge, finn.no (email-derived), Easycruit — no Webcruiter/Facebook (ruled out, see source notes).
- Rule-based scoring (`scoring.py`) plus hard categorical blocks (`hard_blocks.py`) for professions requiring credentials/authorization the user cannot obtain (medical, teaching, regulated fields, security clearance, driving).
- Kanban statuses: new, interesting, applied, interview, offer, rejected, ignored, archived — each with a distinct status color.
- Filters panel: score/salary/employment-% thresholds, sort, status, free text.
- Flag queue: user can flag a mis-scored vacancy; flags get root-caused and fixed in the scoring rules, not just dismissed per-vacancy.
- Notes field per vacancy (freeform, single mutable field, not a comment history).
- Public-transit reachability (via Entur) computed lazily per vacancy detail page, informational only, not part of scoring.
- CV/cover-letter prompt generator produces a prompt the user copies into a chat LLM manually — no LLM API key is wired in (explicit no-budget decision).
- DB is backed up on every sync after a prior data-loss incident; this must not regress.
- No authentication, no multi-user support, no hosting/deploy target — local-only by design.

## Brand Commitments
None beyond the working name "Jobsearch Norway" shown in the page header — not a binding brand identity, free to evolve.

**Visual direction (2026-07-31):** offered a fully replaced visual world for the main vacancy-list surface (via `/impeccable` new-work concept roll — bokføring ledger, then postal-sorting-station, plus catalog challengers each round); the user chose the category standard instead, both times it was offered. Standing preference: **execute the canon (a clean Operate-mode dashboard/list UI), not an invented visual world**, at a craft level benchmarked against **Todoist** (compact, clear list/task ergonomics) and **Vercel Dashboard** (precise, restrained, tabular-numeral data density). Apply this bar to future surface work here unless the user asks for a world-departure again.

## Evidence on Hand
Real, live production data: the user's actual job-search profile (education, work history, skills, location) in `profile_data.py`/`profile/`, and a live SQLite database of thousands of real vacancies pulled from real feeds. No fabricated testimonials, sample data, or placeholder content — every vacancy and score shown is real.

## Product Principles
1. Never silently hide or discard a vacancy — every exclusion (score, hard block, flag) must be visible and auditable, with a way to override and see it anyway.
2. Trust evidence over assumption — new scoring signals get checked against real vacancies (`score_breakdown`) before shipping, because generic single-keyword rules produce false positives.
3. Protect the data — this is the user's real, irreplaceable job-search history; destructive actions (bulk state changes, migrations) need a backup first.
4. Optimize for one intense, finite job search, not a long-lived multi-user product — no auth, no onboarding flows for other users, no premature generalization.
5. Root-cause fixes over per-instance patches — the flag queue exists specifically so mis-scoring gets fixed at the rule level, not dismissed one vacancy at a time.

## Accessibility & Inclusion
No formal requirement established. Interface language is Ukrainian throughout by the user's own choice/fluency; already supports light/dark via `prefers-color-scheme`.
