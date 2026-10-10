import concurrent.futures
import json
import logging
import os
import sqlite3

import requests

from db import get_state, set_state, upsert_active_vacancy, mark_status

logger = logging.getLogger(__name__)

DETAIL_FETCH_WORKERS = 8

BASE_URL = "https://pam-stilling-feed.nav.no"
CURSOR_KEY = "nav_feed_cursor_id"

# Deliberately unused since 2026-09-02 — kept only so the meaning of the
# leftover feed_state row is discoverable. NAV's ETag is not a content hash:
# it is the id of the page that follows, handed out before that page is
# published. Measured on the live feed — every page's ETag equals its own
# next_id, and a page's ETag never changes as entries are appended to it. So
# `If-None-Match` returns 304 while genuinely new ads sit on the other side
# of it, which is exactly how this feed silently stopped importing for two
# days (2026-08-31 → 09-02). Do not reintroduce conditional requests here.
ETAG_KEY = "nav_feed_cursor_etag"

# Per-uuid count of consecutive syncs that were HELD on a page because this
# ad's detail fetch failed transiently (JSON dict {uuid: count} in
# feed_state). Without a cap, one ad that keeps 5xx-ing / timing out / answering
# with invalid JSON holds the cursor on its page forever and halts ALL NAV
# imports — the same silent-stall shape as the 2026-08-31 incident (review
# 2026-10-05). After MAX_DETAIL_HOLDS held syncs the ad is given up on
# (counted as detail_missing) so the cursor can move on.
FAILURES_KEY = "nav_detail_failures"
MAX_DETAIL_HOLDS = 3

# NAV's feed backend is sometimes just slow: 2026-10-10 single detail requests
# took 13-30 s and ~1 in 5 timed out, with no load from us. The hold counter
# above must not treat that as "this ad is poisoned" — three slow syncs would
# otherwise write off a whole page of real vacancies. Successful answers came
# back as late as 30.5 s, so the read timeout needs headroom; and after
# BREAKER_FAILURES transient failures in a row the rest of the page is
# abandoned instead of burning ~10 minutes on requests that will time out.
DETAIL_TIMEOUT = (10, 60)  # (connect, read) seconds
BREAKER_FAILURES = 5


def get_token() -> str:
    token = os.environ.get("NAV_FEED_TOKEN")
    if token:
        return token
    logger.warning(
        "NAV_FEED_TOKEN not set, falling back to the public experimentation token "
        "(https://pam-stilling-feed.nav.no/api/publicToken). This token rotates "
        "irregularly and should not be relied on long-term."
    )
    resp = requests.get(f"{BASE_URL}/api/publicToken", timeout=30)
    resp.raise_for_status()
    return resp.text.strip().splitlines()[-1].strip()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _fetch_ad_detail(token: str, uuid: str) -> dict | None:
    resp = requests.get(
        f"{BASE_URL}/api/v1/feedentry/{uuid}", headers=_headers(token), timeout=DETAIL_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json().get("ad_content")


def _service_answers(conn: sqlite3.Connection, token: str) -> bool:
    """Probe with an ad we already hold, to tell "the NAV feed is down/slow"
    from "this one ad is broken" when a whole page failed with no success to
    compare against. A 4xx still counts as an answer. No stored ad to probe
    with -> assume healthy (the per-ad cap then behaves as it always did)."""
    row = conn.execute(
        "SELECT uuid FROM vacancies WHERE source = 'nav' ORDER BY first_seen_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return True
    try:
        _fetch_ad_detail(token, row[0])
    except requests.RequestException as e:
        return _is_permanent_detail_error(e)
    except ValueError:
        return False
    return True


def _is_permanent_detail_error(exc: requests.RequestException) -> bool:
    """A 4xx on the detail endpoint (404/410: ad withdrawn between the feed
    listing and the fetch) will never succeed on retry — treating it as
    transient would pin the cursor on that page forever (2026-10-05). 408/429
    are the 4xx that DO clear up on their own, so they stay transient."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status is not None and 400 <= status < 500 and status not in (408, 429)


def _load_failures(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        data = json.loads(get_state(conn, FAILURES_KEY) or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, int)}


def _save_failures(conn: sqlite3.Connection, failures: dict[str, int]) -> None:
    set_state(conn, FAILURES_KEY, json.dumps(failures, sort_keys=True))


def sync(conn: sqlite3.Connection) -> dict:
    token = get_token()
    cursor_id = get_state(conn, CURSOR_KEY)
    failures = _load_failures(conn)

    # "updated" = the stored ad content really changed; "unchanged" = the feed
    # re-sent an ad byte-identical to what we hold. The tip page is re-read
    # every sync (see below), so before 2026-10-05 every idle sync reported
    # updated=N forever. "detail_errors" = transient detail-fetch failures on
    # a page — the cursor is held on that page so the next sync retries it.
    stats = {"pages": 0, "new": 0, "updated": 0, "unchanged": 0, "marked_inactive": 0,
             "detail_missing": 0, "detail_errors": 0}

    if not cursor_id:
        resp = requests.get(f"{BASE_URL}/api/v1/feed?last", headers=_headers(token), timeout=30)
        resp.raise_for_status()
        page = resp.json()
        set_state(conn, CURSOR_KEY, page["id"])
        logger.info(f"Bootstrapped cursor at tip page {page['id']} (no history replayed).")
        return stats

    # Always read the page the cursor points at — no conditional request. See
    # ETAG_KEY above for why NAV's ETag cannot answer "is there anything new".
    # The cost is re-reading the tip page each sync and re-fetching details for
    # the ads on it; that page only ever holds ads published since the last
    # page seal, so it stays small (2-4 entries in practice) and the redundant
    # work is bounded by real publishing volume.
    url = f"{BASE_URL}/api/v1/feed/{cursor_id}"
    while True:
        resp = requests.get(url, headers=_headers(token), timeout=30)
        resp.raise_for_status()
        page = resp.json()
        stats["pages"] += 1

        # One entry per uuid, last occurrence on the page wins. The same
        # vacancy legitimately appears several times in one page (published,
        # edited, then withdrawn) and only its final state there is true.
        # The old code applied every occurrence, and worse, applied all the
        # ACTIVE ones *after* all the inactive ones regardless of the real
        # order — so a [ACTIVE X, INACTIVE X] page left X active, the exact
        # opposite of what the feed said.
        latest = {}
        for item in page["items"]:
            entry = item["_feed_entry"]
            latest[entry["uuid"]] = entry

        active_uuids = []
        for uuid, entry in latest.items():
            if entry["status"] == "ACTIVE":
                active_uuids.append(uuid)
            else:
                # .get(): an INACTIVE entry missing one of these keys must not
                # raise KeyError before the cursor advances — that would wedge
                # the cursor on this page forever (2026-10-05 audit).
                mark_status(conn, uuid, entry["status"], entry.get("title"),
                            entry.get("businessName"), entry.get("municipal"))
                stats["marked_inactive"] += 1

        # Detail fetches are one HTTP call per vacancy — fan them out concurrently
        # (I/O-bound, not CPU-bound) instead of one at a time, which made a
        # multi-hundred-item catch-up sync take minutes. DB writes stay on this
        # thread; worker threads only ever touch the network, never `conn`.
        page_detail_errors = 0
        failures_before = dict(failures)
        transient: list[str] = []  # uuids whose detail failed transiently on this page
        successes = 0
        in_a_row = 0
        tripped = False
        if active_uuids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
                future_to_uuid = {pool.submit(_fetch_ad_detail, token, u): u for u in active_uuids}
                for future in concurrent.futures.as_completed(future_to_uuid):
                    uuid = future_to_uuid[future]
                    try:
                        ad = future.result()
                    except (requests.RequestException, ValueError) as e:
                        # ValueError: a 200 whose body is not valid JSON —
                        # transient like a 5xx (and capped the same way).
                        logger.debug(f"Detail fetch failed for {uuid} ({e})")
                        if _is_permanent_detail_error(e):
                            stats["detail_missing"] += 1
                            continue
                        transient.append(uuid)
                        in_a_row += 1
                        if in_a_row >= BREAKER_FAILURES:
                            tripped = True
                            pool.shutdown(wait=False, cancel_futures=True)
                            break
                        continue
                    in_a_row = 0
                    successes += 1
                    failures.pop(uuid, None)
                    if ad is None:
                        # No content is a stable answer, not a transient
                        # failure: skip it, retrying would never change it.
                        stats["detail_missing"] += 1
                        continue
                    # Read the stored content before the upsert so we can tell
                    # "changed" from "re-sent identical" (db.py's upsert only
                    # returns is_new). Compared against json.dumps(ad), which is
                    # exactly what upsert_active_vacancy stores in raw_json.
                    prev = conn.execute(
                        "SELECT raw_json FROM vacancies WHERE uuid = ?", (uuid,)
                    ).fetchone()
                    if upsert_active_vacancy(conn, uuid, "ACTIVE", ad):
                        stats["new"] += 1
                    elif prev is None:
                        pass  # dismissed uuid: nothing was written, nothing to count
                    elif prev[0] == json.dumps(ad):
                        stats["unchanged"] += 1
                    else:
                        stats["updated"] += 1

        if transient:
            # Outage vs one broken ad: a breaker trip is an outage by
            # definition; a page with no success at all is checked with a probe.
            # During an outage nothing is charged to the ads — only a failure
            # next to successes (or with the service answering) counts toward
            # MAX_DETAIL_HOLDS.
            if tripped or (successes == 0 and not _service_answers(conn, token)):
                stats["nav_degraded"] = True
                page_detail_errors = len(transient)
            else:
                for uuid in transient:
                    if failures.get(uuid, 0) >= MAX_DETAIL_HOLDS:
                        logger.warning(
                            f"Giving up on {uuid}: detail failed on {MAX_DETAIL_HOLDS} "
                            f"consecutive held syncs; counting it as missing so the cursor can advance."
                        )
                        stats["detail_missing"] += 1
                    else:
                        failures[uuid] = failures.get(uuid, 0) + 1
                        page_detail_errors += 1

        if failures != failures_before:
            _save_failures(conn, failures)

        if page_detail_errors:
            # A transient detail failure used to only bump detail_missing, and
            # then the cursor moved past the sealed page below — that ad was
            # never fetched again (reproduced 2026-10-05). Keep the cursor on
            # THIS page (it still names the page to be read, so the
            # "cursor = page still to read" invariant holds) and stop; the
            # next sync re-reads it. Re-processing is idempotent.
            stats["detail_errors"] += page_detail_errors
            logger.warning(
                f"{page_detail_errors} of {len(active_uuids)} detail fetch(es) failed on page "
                f"{page.get('id')}"
                + (" — NAV feed slow/unreachable, abandoned the rest of the page" if tripped else "")
                + "; cursor held there so the next sync retries it."
            )
            break

        next_id = page.get("next_id")
        if not next_id:
            # Tip page: nothing published after it yet, so this is as far as
            # the feed goes. Leave the cursor here and re-read it next sync.
            break

        # Page is sealed (something follows it) and we have consumed it in
        # full, so move the cursor PAST it. The cursor must always name the
        # page still to be read, never the last one read — parking it on a
        # finished page is what let a single failed fetch of the next page
        # strand the whole feed for two days (2026-08-31 → 09-02, 12 pages /
        # 10 375 entries queued up while every sync reported "+0 new").
        set_state(conn, CURSOR_KEY, next_id)
        # Pruned once the page is passed (given-up uuids included) so the
        # counter dict can't grow without bound.
        if any(u in failures for u in active_uuids):
            for u in active_uuids:
                failures.pop(u, None)
            _save_failures(conn, failures)
        url = f"{BASE_URL}/api/v1/feed/{next_id}"

    return stats
