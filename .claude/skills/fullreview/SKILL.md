---
name: fullreview
description: >
  The single code-quality command for this project — mechanical grep sweep,
  per-file reasoning pass, security/privacy lens, and live-corpus divergence
  check, in one staged run. Invoke for "повний чек коду", "перевір все",
  "фул рев'ю", "аудит", or before a repo-visibility/push milestone. Supports
  `quick` (stage 1 only) and `deep` (all stages incl. live-corpus audit) —
  default runs stages 1-3. Manual only — see "Why manual" below.
---

# Full review

Jobsearch Norway: Python (FastAPI + SQLite, Jinja2/HTMX), ~7k LOC, single
local user, no auth/multi-tenant surface, five sync sources (NAV, Jobbnorge,
finn.no, LinkedIn, EasyCruit — the last three via Gmail digest parsing).
Project root `C:\Stuff\ClaudeCodeProjects\jobsearch`. Public GitHub repo
since 2026-08-21.

Replaces the former `/selfcheck` and `/full-review` skills, and routine
reliance on the built-in `/security-review` — same lessons, one entry point.

## Why manual (2026-08-26)

Reflexive full-review after every commit was costing tokens for near-zero
yield: across roughly a dozen invocations in one session, **zero** turned up
a real finding — this app's actual risk profile (local-only, no untrusted
web input, SQL parameterized since early on) means most commits (scoring
keyword tweaks, filter params, template additions) structurally have
nothing for a security pass to find. Every real bug that session actually
surfaced came from a direct data measurement, a user report, or the
flagged-vacancy queue — not from routine review. **Run this only when
explicitly invoked, not automatically after each commit.** The one thing
still worth doing reflexively is a plain `git status`/`git diff` glance
before committing — that's free and already normal practice, not this
skill.

## Modes

| Invocation | Stages | When |
|---|---|---|
| `/fullreview quick` | 1 | Cheap gut-check before a commit you're unsure about |
| `/fullreview` | 1 → 2 → 3 | Default. A real review pass |
| `/fullreview deep` | 1 → 2 → 3 → 4 | Before a repo-visibility change, a new data source, or periodically |

**The cheap→expensive gradient is the point.** Stage 1 is grep-only, never
reads a file in full. Stage 2 reads files but must actually reason, not
degrade into grep-and-report. Do the stages in order; later stages cover
fixes made by earlier ones.

---

# Stage 1 — Mechanical sweep (grep only)

**Token rule: Grep only.** Never Read a file unless a match genuinely needs
5+ lines of context to judge, and then read only that range.

**1. HTTP requests without a timeout**
```
pattern: requests\.(get|post|put|delete)\(
glob: **/*.py
```
Bug unless `timeout=` appears in the same call — a hung request blocks sync
indefinitely, no supervisor to kill it.

**2. HTTP requests without a status-check**
```
pattern: requests\.(get|post|put|delete)\(
glob: **/*.py
```
Check for `.raise_for_status()` or an explicit status check nearby. Missing
= a 4xx/5xx page silently parsed as real data.

**3. Bare or overly broad `except`**
```
pattern: except:|except Exception:
glob: **/*.py
```
Bare `except:` swallows `KeyboardInterrupt`/`SystemExit`, always a bug.
Broad `except Exception:` is a bug unless it re-raises, logs with real
detail, or is a top-level sync-loop guard that still logs (never silent).

**4. SQL built by string formatting**
```
pattern: execute\(f"|execute\(".*%s|execute\(".*\{|executescript\(f"
glob: **/*.py
```
Injection/correctness risk. Every value through `?` + a params tuple, never
f-string/`.format()`/`%` into the SQL text itself. Re-check `_vacancy_filters()`
in `db.py` specifically as it grows — it's the one WHERE-clause builder every
new filter param passes through.

**5. Secrets printed, logged, or in error messages**
```
pattern: print\(.*token|print\(.*password|print\(.*app_password
glob: **/*.py
```
`-i` flag on. Also check `raise`/exception text for an embedded credential.
The NAV bearer token and the Gmail app password must never appear in
stdout, logs, or error text — only in `credentials/`/`data/` (gitignored).

**6. Credential files not actually excluded from git**
```bash
git status --porcelain
git check-ignore -v credentials/gmail_app_password.json data/jobsearch.db profile/personal.json
```
Once per pass. A tracked secret is Critical regardless of what else the
pass found.

**7. Debug leftovers**
```
pattern: print\(
glob: **/*.py
```
Exclude `sync.py`/`test_gmail.py`'s intentional progress prints. Flag
`print(` inside library modules (`db.py`, `nav_client.py`, `gmail_client.py`)
that reads like leftover debugging.

**8. Mutable default arguments**
```
pattern: def \w+\(.*=\[\]|def \w+\(.*=\{\}
glob: **/*.py
```
Shared across every call that doesn't override it. Any match = bug.

**9. Naive datetime comparisons against timezone-aware data**
```
pattern: datetime\.now\(\)|datetime\.today\(\)
glob: **/*.py
```
NAV timestamps carry an explicit UTC offset; SQLite's `datetime('now')`
columns are UTC. A naive `datetime.now()` compared against either is a bug
— sometimes a crash, sometimes (worse) a silent wrong-by-2-hours string
comparison. **Live case, 2026-07-17:** the "new since last sync" watermark
used local time against a UTC column, hiding genuinely-new vacancies for up
to 2 hours after every sync, no exception ever raised.

**10. External JSON parsed without defending against missing/renamed keys**
```
pattern: resp\.json\(\)\[|\.json\(\)\[
glob: **/*.py
```
Fine for fields an API's own schema marks required; a bug when the field is
optional/nullable, or the source is an undocumented/guessed structure (an
email digest parser). A crash mid-sync loses the whole batch, not just one
item.

**11. `sys.stdin`/`print()` without forcing UTF-8 on Windows**
```
pattern: sys\.stdin\.read\(\)|input\(\)
glob: **/*.py
```
**Live bug, 2026-07-20** (`generate_documents.py`): `sys.stdin.read()`
mojibake'd Norwegian characters via the guessed console encoding — no
exception, just wrong data. Fix: `sys.stdin.buffer.read().decode("utf-8")`
in, `sys.stdout.reconfigure(encoding="utf-8")` out. Both directions,
independently.

**12. Stale server process masking a code change**
```bash
Get-CimInstance Win32_Process -Filter "name='python.exe'" | Select-Object CommandLine
```
Once per pass, only if a just-shipped `web/app.py`/`db.py` change doesn't
seem to be taking effect. Jinja templates re-read from disk on every
request; Python module code does not without `--reload` or a restart.
**Live case, twice (2026-08 several times):** a new filter silently did
nothing because the running `uvicorn` process predated the code change —
looked like a bug in the new code, was a stale process.

Fix every Critical immediately without asking. Commit.

---

# Stage 2 — Reasoning pass (read files in full)

Read the touched files fully, trace actual data flow. Every item below
traces to a real incident in this project, not a hypothetical.

1. **Bare ambiguous keyword added to a scoring list.** Any single word
   added to `IT_SUPPORT_KEYWORDS`/`GENERAL_ENTRY_KEYWORDS`/`REMOTE_KEYWORDS`
   in `scoring.py` (or a title pattern in `hard_blocks.py`) without first
   measuring it against the live DB corpus is a false-positive risk by
   default, not an edge case. Repeat offenders: "hybrid"/"remote" alone
   matched pension products and PhD topics; "hjemmekontor"/"fjernarbeid"
   alone matched 218/220 generic benefits-paragraph mentions, not real
   remote roles; "matproduksjon"/"varemottak" were tried and dropped for
   matching kitchen/restaurant ads almost exclusively. **The check: grep
   the live `vacancies` table for the candidate word, sample the matches,
   and compute the false-positive rate before adding it — every time, not
   just the first time this lesson was learned.**
2. **Non-deterministic sort/tiebreak.** Any `.sort(key=...)` whose key can
   tie needs a final deterministic tiebreak (not "whatever order the SELECT
   happened to return"). **Live bug, 2026-08-15:** `_exclude_cross_source_duplicates`'s
   score-only sort flipped which duplicate was excluded between two
   back-to-back `rescore_all()` runs, because ties fell through to
   `iter_scorable_vacancies()`'s unordered SELECT.
3. **A fact about a real-world entity checked per-DB-row instead of per-entity.**
   The same job posting can exist as multiple rows (cross-source
   duplicates). A hard-block, a user_status, or any "is this reachable"
   fact must propagate across every row for that entity, not just the one
   whose scraped text happened to match a regex. **Live bug, 2026-08-10:**
   a sikkerhetsklarering-requiring posting was blocked on its NAV/Jobbnorge
   copies but stayed visible on its finn.no copy with different text.
4. **A structured signal already in the data, reimplemented as a fragile
   keyword proxy.** Before building/extending a regex-based classifier,
   check whether the DB already carries an official structured field for
   the same concept. **Live case, 2026-08-18:** NAV's own
   `occupation_categories` column was ~83% populated and unused while
   `scoring.py` guessed the same thing from keywords, catching only 23% of
   real titles in the target categories.
5. **A new filter/query param not threaded through every consumer.** Any
   new parameter added to `db._vacancy_filters()` must also reach
   `count_vacancies()`, `list_vacancies()`, the `index()` route's
   `filter_kwargs`, the template's filter form, **and** the pagination
   `base_qs` string in `index.html` — five places, and pagination silently
   drops a filter if the last one is missed. Grep `_vacancy_filters(` call
   sites and the `base_qs` assignment to check the new param made it to
   all of them.
6. **A manual/scripted call that bypasses a route's own side-effect state.**
   Calling `finn_client.sync()`/`linkedin_client.sync()` etc. directly
   (e.g. from a one-off Python script) updates the DB but not the
   `web_last_sync_summary` state the UI reads — the sync-error banner can
   stay stale even after the underlying problem is fixed. Prefer calling
   `web_app.trigger_sync()` itself when verifying a sync-path fix end to
   end, not the source client's `sync()` in isolation.
7. **A silently-expiring external credential with no monitoring.** Any
   external auth (API token, OAuth grant, app password) that can expire on
   its own schedule needs either a fix at the auth-method level or a
   deliberate note of the expiry mechanism — not just reactive re-auth
   every time it breaks. **Live case, three times (2026-08-18/25/26):**
   Gmail OAuth's refresh token died every 7 days (Testing-status + restricted-
   scope apps are capped by Google, full verification needs a paid audit) —
   fixed at the root by switching to an app password (`fd4cbb0`), not by
   re-authing again.
8. **Personal/identifying data landing in a tracked file.** Since the repo
   went public (2026-08-21): any new profile/soknad/reference file, or any
   hardcoded constant that looks like a real name, email, phone, exact
   coordinate, or document number, must be checked before it's committed —
   see Stage 3's PII checklist, not just this reasoning pass.
9. **A bare-keyword "requirement" check with no soft/hard distinction.**
   Any check of the shape `any(kw in text for kw in SOME_KEYWORDS)` feeding
   a hard_blocks exclusion or a scoring penalty is a false-positive risk
   whenever the concept itself can be phrased as optional ("er en fordel,
   men ikke et krav") or negated ("trenger ikke X") — the keyword being
   unambiguous (unlike item 1's hybrid/remote problem) doesn't save it.
   **Live bug, 2026-08-30:** `car_penalty` gave the full -20 to every
   "førerkort" mention alike — 280 of 1425 non-excluded matches were
   explicitly soft/negated, penalized identically to a hard requirement.
   The fix: run it through hard_blocks.py's shared
   `iter_requirement_clauses`/`REQUIREMENT_VERB_RE`/`OPTIONAL_MARKER_RE`
   machinery (already built for truckførerbevis/forklift) instead of a
   bare substring check — don't reimplement the distinction per keyword.

---

# Stage 3 — Security & privacy lens

Project-specific surface — this app has no auth/session/multi-tenant layer
to review (single local user, `127.0.0.1`-bound), so that entire class of
Budget-style check is N/A here. What actually applies:

- **Credentials.** `credentials/gmail_app_password.json` (full-mailbox IMAP
  access — broader than the OAuth `gmail.readonly` scope it replaced, a
  deliberate tradeoff, see jobsearch-norway-sources memory) and the NAV
  feed bearer token. Mechanical checks #5/#6 above catch the obvious
  leaks; this pass is for anything they'd miss — a new file that embeds a
  credential directly instead of reading it from `credentials/`/`data/`.
- **PII in tracked files — the highest-stakes item now that the repo is
  public.** Before every push, and especially before touching
  `profile/*.md`, `profile_data.py`, or any test fixture built from a real
  email, grep tracked files for the real name/email/phone/street from
  `profile/personal.json` (gitignored) — build the pattern from those field
  values at review time, e.g.:
  ```bash
  python -c "import json,subprocess; p=json.load(open('profile/personal.json')); terms=[p['name'],p['email'],p['phone'],p['street_address']]; subprocess.run(['git','grep','-inF']+sum([['-e',t] for t in terms],[]))"
  ```
  **Never hardcode the actual identifier strings into this tracked file** —
  a real leak, found and fixed 2026-08-30: this exact checklist item used
  to embed the real name/email-prefix/phone/address fragments in plaintext,
  live on the public repo, as its own example pattern. Also check: any new
  third-party name (a referral contact, not the user) landing in
  `profile/*.md` — generalize to "a contact/referral" instead, don't record
  another real person's identity even when the user names them in chat.
  Also check any new hardcoded lat/long constant (home coordinates
  belong in gitignored `profile/personal.json`, not source — see
  `reachability.py`'s `_load_home_coords()`), and that `.gitignore`
  patterns for personal files use a wildcard suffix (`profile/photo.jpg*`,
  not the bare filename — a `.bak`/copy variant slipped past the exact-name
  version once, 2026-08-21). Before any git-history rewrite or
  visibility change specifically: also check the git author identity
  (`git config user.email`) that will be baked into commit metadata, not
  just file contents.
  **This grep is not enough on its own — this project has other Claude
  Code sessions/skills committing to the same repo concurrently (the
  `soknad` skill's own logging, `place-cv`, etc.), and those can introduce
  a real identifier the fixed pattern list has never seen.** Live case,
  2026-08-30: a concurrent session's `soknad`-log activity committed (and
  pushed) the user's real name+local path in `.claude/skills/place-cv/
  SKILL.md`, and a real third-party referral contact's full name in
  `profile/cv-reference.md`/`profile/soknad-log.md` — none of that matched
  the fixed grep pattern above (a NEW identifier, not the known set) and
  sat live on the public repo until this pass found it by eye while
  reading `git log`/diffing recent commits, not by grep. On a `deep` pass:
  skim every file any OTHER skill has written to `profile/*.md` and
  `.claude/skills/*/SKILL.md` since the last review (`git log --stat` over
  the range) specifically for named individuals — the user's own name,
  and anyone else's (referral contacts, recruiters mentioned in a log) —
  not just the fixed pattern list.
- **SQL.** Re-verify `_vacancy_filters()` stays fully parameterized as it
  grows (mechanical check #4 covers new `execute(f"...)` patterns, this is
  the "did the LIKE-clause construction stay safe" re-check for the
  category/status filters specifically).
- **Untrusted-content parsing.** finn.no/LinkedIn/EasyCruit digest emails
  are sender-trusted (own mailbox, known senders) but still externally
  formatted — a parser regex change must not let one malformed message
  crash the whole sync batch (same principle as mechanical check #10).
- **Dependencies.** `pip list --outdated` occasionally; this project has
  few and low-churn dependencies, not worth a standing heavy audit.

---

# Stage 4 — Live-corpus divergence (`deep` mode)

**The highest-yield technique in this project, structurally — every real
scoring/hard-block bug found so far was found this way, not by reading
code.** Code review proves the logic is internally consistent; it can't
prove the keyword list matches reality, because "reality" is ~9000 rows of
externally-sourced Norwegian text that keeps changing.

For every keyword list and title/body regex in `scoring.py` and
`hard_blocks.py`, on a `deep` pass:

1. Grep the live `vacancies` table (`description`/`title`) for the pattern.
2. Sample the matches (10-20 is usually enough to see a real pattern of
   noise vs. signal) — read the surrounding context, not just the matched
   substring.
3. Compute a rough false-positive rate. Anything under ~90% "actually means
   what the keyword assumes" is a candidate to narrow into an explicit
   phrase (the fix shape used for hybrid/remote/hjemmekontor) rather than a
   bare word.

Also re-check these specific **hardcoded "enumerate everything" lists**,
which go stale silently as the project grows (same failure shape as an
expired precondition — true when written, not re-verified since):

- `scoring._SOURCE_TIE_BREAK_PRIORITY` — explicit for every live source as
  of 2026-08-30 (`nav`/`jobbnorge`/`easycruit`/`finn`/`linkedin`/`work.ua`/
  `manual`). Re-check for a forgotten new source each time one is added —
  a silent `.get(c["source"], 99)` fallback is low-severity but easy to
  miss.
- `web/app.py`'s `OCCUPATION_CATEGORIES` — a fixed copy of NAV's level1
  taxonomy, now including the "Uoppgitt/ ikke identifiserbare" catch-all
  (added 2026-08-30, was silently unreachable via the filter dropdown).
  Compare against a fresh `SELECT DISTINCT` over live
  `occupation_categories` JSON periodically — NAV can still add/rename a
  category without notice.
- `scoring.TIER_1_MUNICIPALS` — assumes the user's current location. Revisit
  if that ever changes (see jobsearch-norway-profile memory).

**New class, 2026-08-30 — Norwegian compound-word gap in `hard_blocks.py`
title patterns.** A pattern with a leading `\b` (e.g. `\bsjåfør`) only
matches when the word starts a token — Norwegian compounds these words
together with no boundary ("drosjesjåfør", "kommunepsykolog",
"anleggsrørlegger"), so the leading `\b` silently misses every compound
form. This is the SAME lesson `HEALTH_TITLE_PATTERNS`' own sykepleier/lege
comment already documents, just never swept across the *other* categories
in the file. On a `deep` pass, for every `\bWORD` pattern in
`hard_blocks.py`: `re.compile(WORD)` (bare) vs `re.compile(r"\b"+WORD)`
(current) against live titles, diff the two, and read what's only caught
by the bare version. **Live sweep, 2026-08-30, found real gaps in nearly
every category checked:** sjåfør (46 missed — drosjesjåfør, taxisjåfør,
betongbilsjåfør...), mekaniker (12 — tungvognmekaniker, båtmekaniker,
lastebilmekaniker...), elektriker/rørlegger/sveiser (compounds each),
jordmor/psykolog/farmasøyt/bioingeniør (avdelingsjordmor,
kommunepsykolog, sykehusfarmasøyt, spesialbioingeniør), stipendiat
("doktorgradsstipendiat" — missed on literally every live posting),
kranfører, matros, advokat/jurist. **Not every word is safe to widen
this way** — always sample the bare-match set for a DIFFERENT-profession
false positive before dropping the `\b`: `frisør` stayed `\b`-anchored
because "hunde- og kattefrisør" (pet groomer) is a real different
profession, not a hairdresser compound, and would have been wrongly
blocked.

---

# Report format

```
## Full review — <date>   [mode: quick | default | deep]

### Critical (fixed immediately)
- <stage> file.py:42 — <what's wrong> — <the proof>

### Warnings (review, not auto-fixed)
- file.py:17 — <what's fragile> — <what would break it>

### Live-corpus findings (deep mode)
- <keyword/list> — <false-positive rate measured> — <fix or "left as-is because...">

### Reviewed clean
<name the checks/stages that found nothing>
```

After reporting: fix every Critical immediately without asking (this
includes Stage 1 check #6 — a tracked secret is Critical regardless of
what else the pass found). Warnings — fix if clearly wrong, otherwise
surface for the user. Commit the fixes; don't push unless pushing here is
already established as not needing per-instance confirmation.

## Updating memory after a pass

Same discipline as before: update the existing memory file that already
covers the area (`jobsearch-norway-sources.md` for the data pipeline,
`jobsearch-norway-profile.md` for scoring/search-criteria, a `feedback`-type
entry for anything about *how* to work on this project) rather than
defaulting to a new file. Supersede stale content, don't stack a new
paragraph on top of it. Update `MEMORY.md`'s index line for anything
touched, and mirror to Obsidian.

## Feeding lessons back

A genuinely new bug **class** found during a pass goes into this file
immediately — grep-able → Stage 1, reasoning → Stage 2, a new
project-specific security concern → Stage 3, a new stale-list class →
Stage 4. A new **instance** of an existing class goes in memory only, not
here — don't let this file grow into a changelog.
