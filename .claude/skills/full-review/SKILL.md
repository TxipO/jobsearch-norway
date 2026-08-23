---
name: full-review
description: >
  Run the complete code-quality pass for this project: /selfcheck, then
  /security-review, in sequence. Invoke when the user asks for a "full
  check", "повний чек коду", "перевір все", or a comprehensive audit
  spanning bugs and security in one go — instead of the user having to
  invoke each skill separately and remember the order.
---

# Full review skill

A thin sequencer, not a second review method. Adapted from the Budget
project's `/full-review`
(`C:\Users\doter\Budget\.claude\skills\full-review\SKILL.md`), which chains
three stages (`/selfcheck`, `/deep-review`, `/security-review`). This
project only has `/selfcheck` so far — no `/deep-review` exists here yet,
because that skill is itself specific to bugs found by actually reasoning
about a mature, already-buggy-in-practice codebase (timezone bugs, serverless
race conditions, etc.), and jobsearch doesn't have that history yet. If a
class of bug shows up here that grep can't catch but careful reading of the
code would — the way Budget's deep-review started — build that skill then,
with real examples, instead of copying Budget's checklist wholesale now.

## Order and why

1. **`/selfcheck`** first — cheap, mechanical, catches the "obvious" bug
   shapes fast (see `.claude/skills/selfcheck/SKILL.md` for the current
   checklist: HTTP timeouts, bare excepts, SQL injection, leaked
   credentials, etc.).
2. **`/security-review`** second (the built-in skill, not a project-specific
   one) — run over the current diff/branch state, so it also covers
   whatever selfcheck just fixed, not just the code as it looked before this
   pass started.

## What to do

1. Invoke `/selfcheck` via the Skill tool. Follow its own report format, fix
   every Critical immediately.
2. Invoke `/security-review` (built-in). Fix confirmed findings immediately.
   This project handles two real credentials worth extra attention here: the
   NAV feed bearer token and the Gmail OAuth refresh token — both must stay
   out of git, logs, and error messages (selfcheck check #5/#6 already cover
   this mechanically, but security-review's broader pass is the place to
   catch anything those greps missed, e.g. a new file that embeds a token
   directly instead of reading it from `credentials/`/`data/`).
3. If this is a git repo with existing commits: commit the fixes. Don't push
   unless the user has already established that pushes here don't need
   per-instance confirmation.
4. Give the user **one consolidated report**, not two separate ones — merge
   findings from both stages into a single Critical / Warnings /
   Reviewed-clean summary, noting which stage found what.
5. Update project memory
   (`C:\Users\doter\.claude\projects\C--Stuff-ClaudeCodeProjects-jobsearch\memory\`)
   with what this pass actually changed — see below. Do this even if the
   user didn't ask; a full-review pass is exactly the point where accumulated
   session work needs to land in memory before it's lost to context
   compaction.

## Updating memory after a pass

- **What qualifies**: new architecture or subsystems shipped since the last
  memory update (e.g. the web app framework, once it exists), real bugs
  found and fixed (the failure mode + fix, not the diff), and any
  security-relevant finding — confirmed or a pattern worth remembering even
  if this pass found it clean.
- **What doesn't**: anything already fully described by the code itself or
  by git history.
- **Where it goes**: update the existing memory file that already covers
  this area (`jobsearch-norway-sources.md` for anything about the NAV/finn.no
  data pipeline, `jobsearch-norway-profile.md` for anything about the user's
  own search criteria) rather than defaulting to a new file per pass. Create
  a new file only for a genuinely new subsystem with no existing home.
  Always update `MEMORY.md`'s index line for any file you touch or add.
- **Supersede, don't append**: rewrite/trim stale parts of an existing memory
  file rather than stacking a new paragraph on an outdated one.

## After finding new bug patterns

If a stage surfaces a genuinely new class of bug (not just a new instance of
an existing pattern), update `.claude/skills/selfcheck/SKILL.md` with the
lesson — a new numbered check, with the concrete example that motivated it,
following the same style as the existing checks. Once there's enough real
history of bugs that only careful reading (not grep) would catch, that's the
signal to write this project's own `/deep-review` skill rather than
continuing to bolt reasoning-pass lessons onto selfcheck's grep patterns.
