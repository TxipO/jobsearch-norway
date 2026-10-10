"""Scoring regression check against the live DB (read-only, never writes).

    python scripts/score_regression.py --snapshot   # record current code's scores
    python scripts/score_regression.py              # compare working code to the snapshot

Control set = the vacancies the user acted on (applied/interesting/offer/
rejected). Each is ranked among ACTIVE, non-excluded vacancies, but only in
the profile it belongs to: an IT-keyword vacancy in "it", a warehouse-signal
vacancy WITHOUT IT keywords in "wh" (an IT role is SUPPOSED to leave the warehouse top since
2026-10-10). A control vacancy in the top 5% before and below it after is a
regression unless its title is in EXPECTED_DROPS (deliberate).
The snapshot and report live in _check/ (gitignored: real vacancy data).
"""

import argparse
import bisect
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scoring import score_vacancy  # noqa: E402

DB = ROOT / "data" / "jobsearch.db"
SNAPSHOT = ROOT / "_check" / "score-baseline.json"
CONTROL_STATUSES = ("applied", "interesting", "offer", "rejected")
TOP_PERCENTILE = 95
# Title substrings (lowercase) allowed to fall out of the top 5% — one line
# each, with the reason, so a drop here is a decision and not an accident.
EXPECTED_DROPS: dict[str, str] = {
    # 2026-10-10 relevance gate: wh signal was only NAV's broad "Industri og
    # produksjon" tag on an engineering title; these live in the IT profile.
    "graduate 2025 - solution engineer": "engineering title, tag-only warehouse signal",
    "testingeniør/ testtekniker": "engineering title, tag-only warehouse signal",
}


def _scores(conn) -> dict[str, dict]:
    out = {}
    rows = conn.execute(
        "SELECT uuid, title, description, municipal, county, language, occupation_categories, "
        "extent_percent, engagement_type, status, excluded, user_status FROM vacancies"
    )
    for r in rows:
        wh, wh_bd = score_vacancy(r["title"], r["description"], r["municipal"], r["county"], r["language"],
                              r["occupation_categories"], "warehouse", r["extent_percent"], r["engagement_type"])
        it, it_bd = score_vacancy(r["title"], r["description"], r["municipal"], r["county"], r["language"],
                              r["occupation_categories"], "it", r["extent_percent"], r["engagement_type"])
        out[r["uuid"]] = {
            "title": r["title"] or "", "wh": wh, "it": it,
            "it_signal": bool(it_bd["track_it_support"]["matched"]),
            "wh_signal": bool(wh_bd["track_general_entry_level"]["matched"])
            or wh_bd["occupation_category_bonus"]["points"] > 0,
            "visible": r["status"] == "ACTIVE" and not r["excluded"],
            "user_status": r["user_status"],
        }
    return out


def _percentiles(scores: dict[str, dict], key: str) -> dict[str, float]:
    pop = sorted(v[key] for v in scores.values() if v["visible"])
    return {u: 100 * bisect.bisect_left(pop, v[key]) / max(len(pop), 1) for u, v in scores.items()}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = _scores(conn)

    if args.snapshot:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now), encoding="utf-8")
        print(f"snapshot: {len(now)} vacancies -> {SNAPSHOT}")
        return 0

    before = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    failures = []
    print(f"{'status':11} {'profile':4} {'before':>13} {'after':>13}  title")
    for key, label in (("wh", "wh"), ("it", "it")):
        p_before, p_after = _percentiles(before, key), _percentiles(now, key)
        for uuid, v in sorted(now.items(), key=lambda kv: kv[1]["title"]):
            if v["user_status"] not in CONTROL_STATUSES or uuid not in before:
                continue
            sig = before[uuid]
            if key == "it" and not sig.get("it_signal"):
                continue
            if key == "wh" and (not sig.get("wh_signal") or sig.get("it_signal")):
                continue  # an IT-keyword role leaving the warehouse top is the point
            b, a = p_before[uuid], p_after[uuid]
            if abs(a - b) < 1 and b < TOP_PERCENTILE:
                continue
            flag = ""
            if b >= TOP_PERCENTILE and a < TOP_PERCENTILE:
                if any(s in v["title"].lower() for s in EXPECTED_DROPS):
                    flag = " (expected)"
                else:
                    flag = "  <-- REGRESSION"
                    failures.append((label, v["title"], b, a))
            print(f"{v['user_status']:11} {label:4} {before[uuid][key]:3} p{b:5.1f} {now[uuid][key]:3} p{a:5.1f}  {v['title'][:55]}{flag}")

    for key, label in (("wh", "warehouse"), ("it", "it")):
        top = sorted((v for v in now.values() if v["visible"] and v["user_status"] in ("new", "interesting")),
                     key=lambda v: -v[key])[:30]
        print(f"\n=== top 30 {label} (after) ===")
        for v in top:
            print(f"{v[key]:3}  {v['title'][:70]}")

    print(f"\nregressions: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
