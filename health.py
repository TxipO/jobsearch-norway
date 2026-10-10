"""Is the pipeline delivering what it should? Computed on every page load from
the database and the stored sync summary — nothing new is stored, so a problem
stays on screen until it is actually fixed instead of vanishing when the next
sync overwrites the summary.

Why this exists (2026-10-08): almost none of our real incidents raised an
error. They were syncs that *succeeded* and quietly brought less than they
should — NAV's cursor stuck on an ETag (2 days, "+0 new" looked like a slow
news day), LinkedIn's parser dropping 89% of the jobs (weeks), EasyCruit's
hand-kept id list going stale (2 months), a server still running yesterday's
code. try/except catches none of those; an invariant per source does.

Deliberately NOT a general anomaly detector: the per-day cadence differs too
much (NAV ~150 new/day, finn ≥10, LinkedIn ~5, EasyCruit 0 on 29 of 30 days),
and a baseline learned from history would have "learned" LinkedIn's broken
1/day as normal. So: explicit, per-source thresholds, each tied to a failure we
have actually seen.
"""

from datetime import datetime, timezone
from pathlib import Path

# (db source, label shown to the user, key of its stats dict in the stored sync summary)
SOURCES = (
    ("nav", "NAV", "stats"),
    ("jobbnorge", "Jobbnorge", "jobbnorge"),
    ("finn", "finn.no", "finn"),
    ("easycruit", "EasyCruit", "easycruit"),
    ("linkedin", "LinkedIn", "linkedin"),
)

SYNC_STALE_DAYS = 3

# (warn, bad): days between the last sync and the newest NEW row of the source.
# From the real cadence of the last 30 sync days (2026-10-08): NAV median 157
# new rows, and its only zero day was the cursor stall itself; finn never below
# 11; LinkedIn ~5/day once its parser worked; Jobbnorge zero on 5 weekend/holiday
# days. EasyCruit is absent on purpose — it is quiet by nature, and its own
# staleness check (the hand-kept id list) lives in easycruit_client.
FRESHNESS_DAYS = {"nav": (1.5, 3), "finn": (3, 7), "linkedin": (5, 10), "jobbnorge": (5, 10)}

_FRESHNESS_HINT = {
    "nav": "схоже на зависання курсора фіда (так виглядав збій 2026-08-31)",
    "finn": "перевір, чи ще приходять листи-дайджести finn.no і чи працює фільтр/пересилання в Gmail",
    "linkedin": "перевір, чи ще приходять листи job alerts і чи працює пересилання в Gmail",
    "jobbnorge": "перевір, чи відповідає API Jobbnorge",
}
_FRESHNESS_ACTION = {"nav": "Скажи Claude: перевірити курсор NAV"}

# A parse gap on a mail source is lost data (a card exists in the mail and never
# reaches the DB); any other warning (e.g. EasyCruit's stale list) is "needs a look".
_DATA_LOSS_SOURCES = {"finn", "linkedin"}
_MASS_DEACTIVATION_SHARE = 0.3


def _issue(source: str, level: str, message: str, action: str | None = None) -> dict:
    return {"source": source, "level": level, "message": message, "action": action}


def _utc(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _newest_row_age_days(conn, source: str, as_of: str) -> float | None:
    """Days between `as_of` (the last sync) and the newest row first seen for
    `source`; None when the source has no rows at all. Measured against the
    last SYNC, not against now: not having synced for a week is its own
    problem (reported separately) and must not smear onto every source."""
    return conn.execute(
        "SELECT julianday(?) - julianday(MAX(first_seen_at)) FROM vacancies WHERE source = ?",
        (as_of, source),
    ).fetchone()[0]


def evaluate_sources(conn, summary: dict | None, now: datetime | None = None) -> list[dict]:
    """Problems with the data pipeline, worst first: [{source, level
    ("warn"|"bad"), message, action}]. Empty list = everything is in order."""
    if not summary:
        return []
    now = now or datetime.now(timezone.utc)
    as_of = summary.get("watermark_utc")
    issues: list[dict] = []

    if as_of:
        days = (now - _utc(as_of)).total_seconds() / 86400
        if days >= SYNC_STALE_DAYS:
            issues.append(_issue("Sync", "warn", f"останній sync був {days:.0f} дн. тому — дані могли застаріти", "Натисни Sync now"))

    for source, label, key in SOURCES:
        stats = summary.get(key) or {}
        if stats.get("error"):
            issues.append(_issue(label, "bad", f"не синхронізувався цього разу — {str(stats['error'])[:160]}",
                                 "Натисни Sync now ще раз; якщо повторюється — скажи Claude"))
            continue

        if stats.get("warning"):
            level = "bad" if source in _DATA_LOSS_SOURCES else "warn"
            issues.append(_issue(label, level, str(stats["warning"])))

        if source == "nav" and stats.get("nav_degraded"):
            issues.append(_issue(label, "warn", "API NAV зараз повільне або недоступне (з їхнього боку) — імпорт на паузі, "
                                 "нічого не втрачено, наступний sync повторить цю сторінку"))
        elif source == "nav" and stats.get("detail_errors"):
            issues.append(_issue(label, "warn", f"{stats['detail_errors']} оголошень не завантажились — курсор фіда "
                                 "тримається на цій сторінці, імпорт призупинено до наступного sync"))

        if source == "jobbnorge" and "fetched" in stats:
            gone = stats.get("marked_inactive", 0)
            if stats["fetched"] == 0:
                issues.append(_issue(label, "bad", "API віддав 0 вакансій — знімок порожній, нічого не оновлено"))
            elif gone / (stats["fetched"] + gone) > _MASS_DEACTIVATION_SHARE:
                issues.append(_issue(label, "warn", f"деактивовано {gone} із {stats['fetched'] + gone} — підозріло багато для одного sync"))

        silent = False
        if source in _DATA_LOSS_SOURCES and stats.get("messages") == 0:
            silent = True
            issues.append(_issue(label, "bad", "у Gmail нема жодного листа-дайджесту в вікні пошуку — "
                                 "пересилання або фільтр, схоже, зламались"))

        if source in FRESHNESS_DAYS and as_of and not silent:
            age = _newest_row_age_days(conn, source, as_of)
            warn_at, bad_at = FRESHNESS_DAYS[source]
            if age is not None and age >= warn_at:
                issues.append(_issue(label, "bad" if age >= bad_at else "warn",
                                     f"нових вакансій не було {age:.0f} дн. до останнього sync — {_FRESHNESS_HINT[source]}",
                                     _FRESHNESS_ACTION.get(source)))

    issues.sort(key=lambda i: i["level"] != "bad")
    return issues


def stale_code(started_at: float, root: Path) -> str | None:
    """"HH:MM" of the newest Python source file when it is newer than the
    moment this server process started — i.e. the running code is older than
    the code on disk — else None. Templates are re-read per request, so only
    .py files count. Without `--reload` this is the single most common "why
    isn't my change showing" in this project; with it, the reloader restarts
    the process and this never fires."""
    newest = max((p.stat().st_mtime for p in [*root.glob("*.py"), *(root / "web").glob("*.py")]), default=0)
    if newest <= started_at + 2:
        return None
    return datetime.fromtimestamp(newest).strftime("%d.%m %H:%M")
