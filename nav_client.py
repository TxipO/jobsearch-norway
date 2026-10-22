import concurrent.futures
import json
import logging
import os
import sqlite3
import threading

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

# Same slowness hits the calls that are NOT per-ad, and those fail the whole
# NAV source ("не синхронізувався") instead of one ad: measured 2026-10-10 the
# public-token request took 22-30 s (one 500) and a feed page — up to 1000
# entries — 21-26 s (one 500), both against a 30 s read timeout. Page/token
# GETs are idempotent, so one retry on timeout/5xx is safe.
REQUEST_TIMEOUT = (10, 60)
REQUEST_ATTEMPTS = 2

# The public experimentation token is cached in feed_state and reused until
# NAV answers 401/403 — fetching it cost ~30 s on every sync and was itself
# the call that timed out. Ignored when NAV_FEED_TOKEN is set.
TOKEN_KEY = "nav_public_token"

# Ads of the cursor page whose detail was already handled, {"page": id,
# "done": {uuid: feed-entry sistEndret}}. A held page used to be re-fetched
# from its first ad on every sync, so with NAV slow the same already-imported
# ads ate each sync's budget and the not-yet-fetched ones never got a turn
# (2026-10-10 -> 10-12: 93 of 401 active ads on one 738-entry page stayed
# missing for two days). An ad is skipped only while its feed entry's
# sistEndret is unchanged; the record is dropped when the cursor moves on.
PROGRESS_KEY = "nav_page_progress"


def get_token() -> str:
    token = os.environ.get("NAV_FEED_TOKEN")
    if token:
        return token
    logger.warning(
        "NAV_FEED_TOKEN not set, falling back to the public experimentation token "
        "(https://pam-stilling-feed.nav.no/api/publicToken). This token rotates "
        "irregularly and should not be relied on long-term."
    )
    resp = _get_with_retry(f"{BASE_URL}/api/publicToken")
    return resp.text.strip().splitlines()[-1].strip()


def _get_with_retry(url: str, headers: dict | None = None) -> requests.Response:
    """GET with REQUEST_TIMEOUT; a timeout, connection error or 5xx is tried
    once more, then raised as before (raise_for_status on the last answer)."""
    for attempt in range(REQUEST_ATTEMPTS):
        last = attempt == REQUEST_ATTEMPTS - 1
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except (requests.Timeout, requests.ConnectionError):
            if last:
                raise
            continue
        if resp.status_code >= 500 and not last:
            continue
        resp.raise_for_status()
        return resp


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Token:
    """The bearer token, shared by the detail-fetch worker threads. NAV's
    public token rotates and an older one stays valid on only SOME backends:
    2026-10-12 a token cached two days earlier was accepted for the feed page
    and answered 401 for ~25% of the detail requests, every sync, and those
    ads were then counted toward "given up". On 401/403 the token is replaced
    once (under a lock, so N threads cause one request) and the call retried."""

    def __init__(self, value: str):
        self.value = value
        self.refreshed = False
        self._lock = threading.Lock()

    def refresh(self, stale: str) -> str:
        with self._lock:
            if self.value == stale:
                self.value = get_token()
                self.refreshed = True
            return self.value


def _get_authed(url: str, token: _Token) -> requests.Response:
    stale = token.value
    try:
        return _get_with_retry(url, _headers(stale))
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) not in (401, 403):
            raise
        return _get_with_retry(url, _headers(token.refresh(stale)))


def _fetch_ad_detail(token: _Token, uuid: str) -> dict | None:
    stale = token.value
    try:
        resp = requests.get(
            f"{BASE_URL}/api/v1/feedentry/{uuid}", headers=_headers(stale), timeout=DETAIL_TIMEOUT
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) not in (401, 403):
            raise
        resp = requests.get(
            f"{BASE_URL}/api/v1/feedentry/{uuid}", headers=_headers(token.refresh(stale)),
            timeout=DETAIL_TIMEOUT,
        )
        resp.raise_for_status()
    return resp.json().get("ad_content")


def _service_answers(conn: sqlite3.Connection, token: _Token, default: bool = True) -> bool:
    """Probe with an ad we already hold, to tell "the NAV feed is down/slow"
    from "this one ad is broken" when a whole page failed with no success to
    compare against. A 4xx still counts as an answer. No stored ad to probe
    with (fresh database) -> `default`."""
    row = conn.execute(
        "SELECT uuid FROM vacancies WHERE source = 'nav' ORDER BY first_seen_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return default
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
    are the 4xx that DO clear up on their own, so they stay transient (as do 401/403)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    # 401/403: an expired/rotated token says nothing about the ad itself.
    return status is not None and 400 <= status < 500 and status not in (401, 403, 408, 429)


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


def _persist_token(conn: sqlite3.Connection, token: _Token, use_cache: bool) -> None:
    if use_cache and token.refreshed:
        set_state(conn, TOKEN_KEY, token.value)


def _load_progress(conn: sqlite3.Connection, page_id: str | None) -> dict[str, str]:
    try:
        data = json.loads(get_state(conn, PROGRESS_KEY) or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("page") != page_id or not isinstance(data.get("done"), dict):
        return {}
    return {k: v for k, v in data["done"].items() if isinstance(k, str) and isinstance(v, str)}


def sync(conn: sqlite3.Connection) -> dict:
    use_cache = not os.environ.get("NAV_FEED_TOKEN")
    cached = get_state(conn, TOKEN_KEY) if use_cache else None
    token = _Token(cached or get_token())
    if use_cache and not cached:
        set_state(conn, TOKEN_KEY, token.value)
    cursor_id = get_state(conn, CURSOR_KEY)
    failures = _load_failures(conn)

    # "updated" = the stored ad content really changed; "unchanged" = the feed
    # re-sent an ad byte-identical to what we hold. The tip page is re-read
    # every sync (see below), so before 2026-10-05 every idle sync reported
    # updated=N forever. "detail_errors" = transient detail-fetch failures on
    # a page — the cursor is held on that page so the next sync retries it.
    stats = {"pages": 0, "new": 0, "updated": 0, "unchanged": 0, "marked_inactive": 0,
             "detail_missing": 0, "detail_errors": 0}

    def fetch_feed(url: str) -> dict:
        return _get_authed(url, token).json()

    if not cursor_id:
        page = fetch_feed(f"{BASE_URL}/api/v1/feed?last")
        set_state(conn, CURSOR_KEY, page["id"])
        logger.info(f"Bootstrapped cursor at tip page {page['id']} (no history replayed).")
        _persist_token(conn, token, use_cache)
        return stats

    # Always read the page the cursor points at — no conditional request. See
    # ETAG_KEY above for why NAV's ETag cannot answer "is there anything new".
    # The cost is re-reading the tip page each sync and re-fetching details for
    # the ads on it; that page only ever holds ads published since the last
    # page seal, so it stays small (2-4 entries in practice) and the redundant
    # work is bounded by real publishing volume.
    url = f"{BASE_URL}/api/v1/feed/{cursor_id}"
    while True:
        page = fetch_feed(url)
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
            entry = item.get("_feed_entry") if isinstance(item, dict) else None
            if not isinstance(entry, dict) or not isinstance(entry.get("uuid"), str)                     or not isinstance(entry.get("status"), str):
                # A malformed entry must not raise before the cursor advances -
                # that wedges the cursor on this page forever (same shape as
                # the INACTIVE .get() case below). Skipped, visible in stats.
                stats["malformed"] = stats.get("malformed", 0) + 1
                logger.warning(f"Skipping malformed feed entry on page {page.get('id')}: {str(item)[:200]}")
                continue
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
        done = _load_progress(conn, page.get("id"))
        done_before = dict(done)
        # Ads that already failed MAX_DETAIL_HOLDS held syncs are given up on
        # HERE, before the fetch, and recorded in `done`. Doing it after a
        # further failed fetch (as before) left them out of `done`, so an ad
        # whose feed entry has no sistEndret was fetched, failed and tripped
        # the breaker again on every sync - and the cursor logic below now
        # holds a page whose ads were not all attempted.
        for u in active_uuids:
            if failures.get(u, 0) >= MAX_DETAIL_HOLDS:
                logger.warning(
                    f"Giving up on {u}: detail failed on {MAX_DETAIL_HOLDS} "
                    f"consecutive held syncs; counting it as missing so the cursor can advance."
                )
                stats["detail_missing"] += 1
                done[u] = latest[u].get("sistEndret") or ""
        todo = [u for u in active_uuids
                if not (latest[u].get("sistEndret") and done.get(u) == latest[u]["sistEndret"])
                and failures.get(u, 0) < MAX_DETAIL_HOLDS]
        if len(todo) != len(active_uuids):
            stats["skipped"] = stats.get("skipped", 0) + len(active_uuids) - len(todo)
        page_detail_errors = 0
        failures_before = dict(failures)
        transient: list[str] = []  # uuids whose detail failed transiently on this page
        settled: set[str] = set()  # todo uuids with a final answer this sync (imported/skipped/withdrawn)
        successes = 0
        in_a_row = 0
        tripped = False
        if todo:
            with concurrent.futures.ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
                future_to_uuid = {pool.submit(_fetch_ad_detail, token, u): u for u in todo}
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
                            done[uuid] = latest[uuid].get("sistEndret") or ""
                            settled.add(uuid)
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
                    done[uuid] = latest[uuid].get("sistEndret") or ""
                    settled.add(uuid)
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
            # Outage vs broken ads: a breaker trip or a page with no success at
            # all is checked with a probe (an ad we already hold). Probe fails
            # -> outage, nothing is charged to the ads. Probe answers -> the ads
            # themselves are the problem and count toward MAX_DETAIL_HOLDS (a
            # breaker trip alone used to be treated as an outage forever, which
            # would pin the cursor on >= 5 permanently failing ads).
            if (tripped or successes == 0) and not _service_answers(conn, token, default=not tripped):
                stats["nav_degraded"] = True
                page_detail_errors = len(transient)
            else:
                for uuid in transient:
                    failures[uuid] = failures.get(uuid, 0) + 1
                    page_detail_errors += 1

        # Ads never attempted: a breaker trip cancels the rest of the pool. The
        # cursor used to advance past them as soon as the failing ads were
        # given up (page_detail_errors == 0) - up to a whole sealed page lost
        # (reproduced: 5 failing + 300 good ads, all 300 dropped). Hold the
        # page; the next sync skips what is in `done` and continues, and the
        # failing ads keep being charged until they are given up, so each
        # sync makes progress and the hold cannot last forever.
        transient_set = set(transient)
        unattempted = [u for u in todo if u not in settled and u not in transient_set]
        page_detail_errors += len(unattempted)

        if failures != failures_before:
            _save_failures(conn, failures)
        if done != done_before:
            set_state(conn, PROGRESS_KEY, json.dumps({"page": page.get("id"), "done": done}))

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
        if done:
            set_state(conn, PROGRESS_KEY, "{}")
        # Pruned once the page is passed (given-up uuids included) so the
        # counter dict can't grow without bound.
        if any(u in failures for u in active_uuids):
            for u in active_uuids:
                failures.pop(u, None)
            _save_failures(conn, failures)
        url = f"{BASE_URL}/api/v1/feed/{next_id}"

    _persist_token(conn, token, use_cache)
    return stats
