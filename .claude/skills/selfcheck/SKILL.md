# Selfcheck skill

Norwegian job-search aggregator (Python + SQLite). Started as sync scripts
against the NAV Arbeidsplassen feed and the Gmail API (finn.no alert
ingestion); planned to grow into a full web app.
Project root: `C:\Stuff\ClaudeCodeProjects\jobsearch`.

**Token rule:** use Grep only — never Read a file unless a grep match needs
5+ lines of context to judge. One Read = one specific file + specific line
range. No full-file reads.

**Why this skill exists in this shape:** adapted from the Budget project's
`/selfcheck` (`C:\Users\doter\Budget\.claude\skills\selfcheck\SKILL.md`),
which is grep-only and cheap enough to run after every change, unlike a full
reasoning pass. Same philosophy here, different bug shapes — this project has
no React/Prisma/Vercel surface (yet), but it does have external HTTP APIs,
OAuth tokens, and a growing SQLite schema, which fail in their own specific
ways. Extend this file with new checks as real bugs are found here, the same
way Budget's skill accumulated its list — don't let a lesson live only in
chat history.

## Run these grep checks in order

### 1. HTTP requests without a timeout
```
pattern: requests\.(get|post|put|delete)\(
glob: **/*.py
```
Any match where `timeout=` doesn't appear in that same call = bug. A hung
request blocks the sync indefinitely — there's no supervisor process to kill
it. Report file:line.

### 2. HTTP requests without status-check
```
pattern: requests\.(get|post|put|delete)\(
glob: **/*.py
```
For each match, check the surrounding lines for `.raise_for_status()` or an
explicit `resp.status_code` check before the response body is used. Missing
= a 4xx/5xx error page gets silently parsed as if it were real data (e.g.
`resp.json()` on an HTML error page raises a confusing `JSONDecodeError`
instead of a clear "NAV API returned 500" — or worse, on an endpoint that
returns valid-looking JSON on error, silently corrupts the sync).

### 3. Bare or overly broad `except`
```
pattern: except:|except Exception:
glob: **/*.py
```
Any bare `except:` is a bug — it also swallows `KeyboardInterrupt` and
`SystemExit`. A broad `except Exception:` is a bug unless it re-raises, logs
with enough detail to debug later, or is at the top-level entry point of a
long-running sync loop specifically to keep one bad item from killing the
whole run (that pattern must still log the exception, not discard it
silently).

### 4. SQL built by string formatting instead of parameterized queries
```
pattern: execute\(f"|execute\(".*%s|execute\(".*\{|executescript\(f"
glob: **/*.py
```
Any match = SQL injection risk (or, at minimum, correctness risk once a
value contains a quote). Every value must go through `?` placeholders and a
params tuple, never f-string/`.format()`/`%` interpolation into the SQL
string itself.

### 5. Secrets or tokens printed, logged, or returned in error messages
```
pattern: print\(.*token|print\(.*client_secret|print\(.*password
glob: **/*.py
```
-i flag on. Also check any `raise`/exception message that might embed a
credential value pulled from `os.environ` or a credentials file. The NAV
token and the Gmail OAuth token/refresh token must never appear in stdout,
logs, or error text — only in files under `credentials/` and `data/`
(already gitignored).

### 6. Credential/state files not actually excluded from git
```bash
git status --porcelain
git check-ignore -v credentials/client_secret.json data/gmail_token.json data/jobsearch.db
```
Run this once per pass (not per-file grep). If `git status` shows any of
`credentials/`, `data/*.db`, `data/*token*` as trackable, or `check-ignore`
fails to match them, treat as **Critical** — a leaked OAuth refresh token or
NAV bearer token is a real-world credential exposure, not just a code bug.

### 7. Debug leftovers
```
pattern: print\(
glob: **/*.py
```
Exclude `sync.py`'s and `test_gmail.py`'s intentional summary/status prints
(entry-point scripts are allowed to print progress) — flag `print(` left
inside library modules (`db.py`, `nav_client.py`, `gmail_client.py`) that
looks like leftover debugging rather than an intentional user-facing
message. Use judgment; report what's ambiguous rather than silently keeping
or removing it.

### 8. Mutable default arguments
```
pattern: def \w+\(.*=\[\]|def \w+\(.*=\{\}
glob: **/*.py
```
Classic Python bug — the default is created once at function-definition time
and shared across every call that doesn't override it. Any match = bug.

### 9. Naive datetime comparisons against NAV's timezone-aware timestamps
```
pattern: datetime\.now\(\)|datetime\.today\(\)
glob: **/*.py
```
NAV feed timestamps (`applicationDue`, `expires`, `updated`, `sistEndret`)
arrive as ISO 8601 strings with an explicit `+02:00`/`+01:00` offset. Any
comparison against a naive `datetime.now()` (no `tzinfo`) will raise on
mixed-aware/naive comparison, or worse, silently compare wall-clock values
across different UTC offsets if someone strips the offset first. Any
date/time math introduced for filtering ("still open", "closes soon") must
go through timezone-aware datetimes throughout — this is the same failure
shape as the Budget project's timezone lesson (works fine in dev, wrong once
the assumption that "everything is one timezone" breaks).

**Live instance caught 2026-07-17 — same check, different data source:**
`db.py`'s `first_seen_at`/`last_synced_at` columns default to SQLite's
`datetime('now')`, which is **UTC**, not local time. `web/app.py`'s
"new since last sync" watermark was built from Python's local
`datetime.now()` and compared against `first_seen_at` as plain strings —
in Norway's summer UTC+2, that silently hid genuinely-new vacancies from
the "new" badge for up to 2 hours after every sync. No exception was ever
raised (it's a string comparison, not a real datetime comparison), which
is exactly the "or worse, silently..." branch above — don't assume this
check only fires on a crash. Fix: `datetime.now(timezone.utc)` for
anything compared against a SQLite-`datetime('now')` column; keep a
separate local-time value only for what's actually displayed to the user.

### 10. External JSON parsed without defending against missing/renamed keys
```
pattern: resp\.json\(\)\[|\.json\(\)\[
glob: **/*.py
```
Direct bracket access straight off a freshly parsed external response
(`resp.json()["items"]`) is fine for fields the API's own schema marks
required — check the field against
[jobsearch-norway-sources](reference — NAV OpenAPI spec) before deciding.
It's a bug when the field is actually optional/nullable in that schema, or
when the source isn't NAV's documented feed at all (e.g. a future finn.no
email parser guessing at HTML structure) — external formats drift, and a
crash mid-sync loses the whole batch instead of just the one malformed item.

### 11. `sys.stdin.read()`/`print()` without forcing UTF-8 on Windows
```
pattern: sys\.stdin\.read\(\)|input\(\)
glob: **/*.py
```
Live bug caught 2026-07-20 in `generate_documents.py`: `sys.stdin.read()`
picked up whatever encoding Python guessed for the Windows console (not
UTF-8, even when the pipe's source — PowerShell's own `echo` — was UTF-8),
silently turning Norwegian characters into mojibake (`"Høgskolen"` ->
`"HÃ¸gskolen"`) in the generated søknad text. No exception, no crash — just
wrong data, exactly the "or worse, silently..." failure shape check #9
warns about. **Fix:** `sys.stdin.buffer.read().decode("utf-8")` — read raw
bytes and decode explicitly, never rely on the guessed text-mode encoding.
The same failure hits `print()` symmetrically on the way out (any
non-ASCII — "søknad", em dashes — can break a caller piping/capturing this
script's stdout expecting UTF-8); fix there is
`sys.stdout.reconfigure(encoding="utf-8")` at the top of `main()`. Any CLI
script in this project that reads or writes non-ASCII text through stdin/
stdout needs both fixes, not just one — they're independent failure points.

---

## Report format

```
## Selfcheck — <date>

### Critical (fix now)
- file.py:42 — description

### Warnings (review)
- file.py:17 — description

### Clean ✓
Checks that found nothing: #4, #8
```

After reporting: **fix every Critical immediately** (this includes check #6
— a tracked secret is Critical regardless of what else the pass found).
For Warnings — fix if clearly wrong, otherwise list for human review. Do not
ask permission to fix Criticals.

After all fixes, if this is a git repo with commits already in it:
`git add -A && git commit -m "Selfcheck fixes <date>"`. Do not push unless
the user has already established that pushes in this project don't need
per-instance confirmation. If the project isn't a git repo yet, skip this
step and say so rather than failing on it.
