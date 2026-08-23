"""Public-transport reachability from the user's home location, via Entur's
open Journey Planner v3 + Geocoder APIs (developer.entur.org) — free, no API
key, just an identifying ET-Client-Name header.

Deliberately NOT a batch precompute across every municipal seen in the DB:
measured 2026-07-17, active non-excluded vacancies span ~290 distinct
municipals nationwide (the search is intentionally nationwide, see
jobsearch-norway-sources memory) — hundreds of geocode+journey calls per
sync against an API with unknown rate limits is a real risk of silently
slowing or breaking every sync. Instead this is computed lazily, one
municipal at a time, the first time a vacancy in that municipal is opened
on the detail page, and cached indefinitely afterwards (public-transport
travel time from a fixed home point doesn't meaningfully change week to
week — no TTL needed).

This is informational only — it does NOT feed into scoring.py's score.
Travel time doesn't have an empirically-grounded point value the way the
existing scoring signals do (see scoring.py's "measure before building"
pattern), and bolting an unvalidated weight onto the tuned score risks
distorting it. A badge on the detail page is the honest scope.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import db

ET_CLIENT_NAME = "jobsearch-norway-personal-project"
GEOCODER_URL = "https://api.entur.io/geocoder/v1/autocomplete"
JOURNEY_URL = "https://api.entur.io/journey-planner/v3/graphql"

_PERSONAL_PATH = Path(__file__).parent / "profile" / "personal.json"


def _load_home_coords() -> dict:
    """Geocoded once via the API, 2026-07-17 — home doesn't move, no reason
    to re-query Entur for it on every lookup. Read from the gitignored
    personal.json (same file cv_builder.py reads contact details from) so
    the repo itself never carries anyone's real coordinates — a placeholder
    (Oslo city centre) is used until personal.json supplies real ones, which
    just makes every reachability badge look like a long trip from Oslo
    rather than crashing."""
    if _PERSONAL_PATH.exists():
        data = json.loads(_PERSONAL_PATH.read_text(encoding="utf-8"))
        lat, lon = data.get("home_latitude"), data.get("home_longitude")
        if lat is not None and lon is not None:
            return {"latitude": lat, "longitude": lon}
    return {"latitude": 59.911491, "longitude": 10.757933}  # placeholder: Oslo


HOME_COORDS = _load_home_coords()

_STATE_PREFIX = "reachability:"

# searchWindow spans a full day (minutes): rural Vestland departures are
# sparse enough that Entur's default dynamic window finds nothing at all for
# neighbouring municipals — Sogndal and Voss both came back "no route" at
# 08:00 until this was widened.
TRIP_QUERY = """
query($from: Location!, $to: Location!, $dateTime: DateTime!) {
  trip(from: $from, to: $to, dateTime: $dateTime, numTripPatterns: 1, searchWindow: 1440) {
    tripPatterns {
      duration
      legs { mode line { publicCode name } }
    }
  }
}
"""


# The rest of the app's UI is Ukrainian (see web/app.py's STATUS_LABELS /
# BREAKDOWN_LABELS) — mode labels match that, not the vacancy's own
# language.
_MODE_LABELS_UK = {
    "foot": "пішки", "bus": "автобус", "coach": "автобус далекого сполучення",
    "rail": "потяг", "water": "пором", "metro": "метро", "tram": "трамвай", "air": "літак",
}


def _cache_key(municipal: str) -> str:
    return _STATE_PREFIX + municipal.strip().upper()


def _geocode_municipal(conn, municipal: str) -> dict | None:
    """Coordinates for a municipal's town centre. Uses the 'locality' or
    'address' layer result Entur's Photon-based geocoder returns for a bare
    place name — good enough for a travel-time estimate, not turn-by-turn
    precision."""
    resp = requests.get(
        GEOCODER_URL,
        params={"text": municipal, "size": 1, "lang": "no"},
        headers={"ET-Client-Name": ET_CLIENT_NAME},
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features") or []
    if not features:
        return None
    lon, lat = features[0]["geometry"]["coordinates"]
    return {"latitude": lat, "longitude": lon}


def _next_weekday_morning() -> str:
    """Next upcoming weekday at 08:00 Norwegian local time — a reasonable
    "can I get there for a workday" proxy. Not trying to model the specific
    vacancy's actual shift times.

    Computed per call, never hardcoded: Entur's journey-planner returns zero
    tripPatterns for a departure in the past, so a fixed date silently turns
    every lookup into "no route" the moment it rolls by (it did — see the
    no_route rows purged from the cache 2026-08-19)."""
    dt = datetime.now(ZoneInfo("Europe/Oslo")).replace(
        hour=8, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.isoformat()


def _query_trip(from_coords: dict, to_coords: dict) -> dict | None:
    resp = requests.post(
        JOURNEY_URL,
        json={
            "query": TRIP_QUERY,
            "variables": {
                "from": {"coordinates": from_coords},
                "to": {"coordinates": to_coords},
                "dateTime": _next_weekday_morning(),
            },
        },
        headers={"ET-Client-Name": ET_CLIENT_NAME, "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    patterns = (data.get("trip") or {}).get("tripPatterns") or []
    return patterns[0] if patterns else None


def _summarize(pattern: dict) -> dict:
    duration_min = round(pattern["duration"] / 60)
    modes = [leg["mode"] for leg in pattern["legs"] if leg["mode"] != "foot"]
    mode_labels = [_MODE_LABELS_UK.get(m, m) for m in modes] or ["пішки"]
    return {"duration_min": duration_min, "modes": mode_labels}


def get_reachability(conn, municipal: str | None) -> dict | None:
    """Cached public-transport travel time from home to `municipal`.
    Returns {"duration_min": int, "modes": [str, ...]} on a found route,
    {"error": "no_route"} if Entur found none, or None if municipal is
    missing/unrecognized or the API call itself failed (network hiccups
    must never break the detail page — caller treats None as "no badge",
    not an error to surface)."""
    if not municipal or not municipal.strip():
        return None

    cached = db.get_state(conn, _cache_key(municipal))
    if cached:
        return json.loads(cached)

    try:
        coords = _geocode_municipal(conn, municipal)
        if coords is None:
            result = {"error": "not_found"}
        else:
            pattern = _query_trip(HOME_COORDS, coords)
            result = _summarize(pattern) if pattern else {"error": "no_route"}
    except (requests.RequestException, KeyError, ValueError, IndexError):
        # Entur being slow/down/malformed must not break the detail page —
        # just skip caching so the next view retries instead of caching a
        # transient failure forever.
        return None

    db.set_state(conn, _cache_key(municipal), json.dumps(result, ensure_ascii=False))
    return result
