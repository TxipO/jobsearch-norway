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


def _headers(token: str, etag: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if etag:
        headers["If-None-Match"] = etag
    return headers


def _fetch_ad_detail(token: str, uuid: str) -> dict | None:
    resp = requests.get(
        f"{BASE_URL}/api/v1/feedentry/{uuid}", headers=_headers(token), timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("ad_content")


def sync(conn: sqlite3.Connection) -> dict:
    token = get_token()
    cursor_id = get_state(conn, CURSOR_KEY)
    etag = get_state(conn, ETAG_KEY)

    stats = {"pages": 0, "active_upserted": 0, "marked_inactive": 0, "detail_missing": 0}

    if not cursor_id:
        resp = requests.get(f"{BASE_URL}/api/v1/feed?last", headers=_headers(token), timeout=30)
        resp.raise_for_status()
        page = resp.json()
        set_state(conn, CURSOR_KEY, page["id"])
        set_state(conn, ETAG_KEY, resp.headers.get("ETag", ""))
        logger.info(f"Bootstrapped cursor at tip page {page['id']} (no history replayed).")
        return stats

    url = f"{BASE_URL}/api/v1/feed/{cursor_id}"
    while True:
        resp = requests.get(url, headers=_headers(token, etag), timeout=30)
        if resp.status_code == 304:
            break
        resp.raise_for_status()
        page = resp.json()
        stats["pages"] += 1

        active_uuids = []
        for item in page["items"]:
            entry = item["_feed_entry"]
            uuid, status = entry["uuid"], entry["status"]
            if status == "ACTIVE":
                active_uuids.append(uuid)
            else:
                mark_status(conn, uuid, status, entry["title"], entry["businessName"], entry["municipal"])
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
                    upsert_active_vacancy(conn, uuid, "ACTIVE", ad)
                    stats["active_upserted"] += 1

        this_page_id = page["id"]
        next_id = page.get("next_id")
        set_state(conn, CURSOR_KEY, this_page_id)
        set_state(conn, ETAG_KEY, resp.headers.get("ETag", ""))

        if not next_id:
            break
        url = f"{BASE_URL}/api/v1/feed/{next_id}"
        etag = None

    return stats
