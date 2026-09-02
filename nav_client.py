import concurrent.futures
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
        f"{BASE_URL}/api/v1/feedentry/{uuid}", headers=_headers(token), timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("ad_content")


def sync(conn: sqlite3.Connection) -> dict:
    token = get_token()
    cursor_id = get_state(conn, CURSOR_KEY)

    stats = {"pages": 0, "new": 0, "updated": 0, "marked_inactive": 0, "detail_missing": 0}

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
                mark_status(conn, uuid, entry["status"], entry["title"], entry["businessName"], entry["municipal"])
                stats["marked_inactive"] += 1

        # Detail fetches are one HTTP call per vacancy — fan them out concurrently
        # (I/O-bound, not CPU-bound) instead of one at a time, which made a
        # multi-hundred-item catch-up sync take minutes. DB writes stay on this
        # thread; worker threads only ever touch the network, never `conn`.
        if active_uuids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
                future_to_uuid = {pool.submit(_fetch_ad_detail, token, u): u for u in active_uuids}
                for future in concurrent.futures.as_completed(future_to_uuid):
                    uuid = future_to_uuid[future]
                    try:
                        ad = future.result()
                    except requests.RequestException as e:
                        logger.warning(f"Skipping {uuid}: detail fetch failed ({e})")
                        stats["detail_missing"] += 1
                        continue
                    if ad is None:
                        stats["detail_missing"] += 1
                        continue
                    if upsert_active_vacancy(conn, uuid, "ACTIVE", ad):
                        stats["new"] += 1
                    else:
                        stats["updated"] += 1

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
        url = f"{BASE_URL}/api/v1/feed/{next_id}"

    return stats
