"""Tests for reachability.py's caching and failure handling. Real Entur
network calls are isolated out via monkeypatch, same pattern as
test_jobbnorge_client.py — verified live against the real API manually
(Balestrand -> Bergen, 2026-07-17: ~5h25m via bus 820 + Fjordekspressen
coach, confirms the query shape works)."""

from datetime import datetime

import requests

import db
import reachability as rb


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def test_get_reachability_none_for_empty_municipal(tmp_path):
    conn = _make_conn(tmp_path)
    assert rb.get_reachability(conn, None) is None
    assert rb.get_reachability(conn, "") is None
    assert rb.get_reachability(conn, "   ") is None


def test_get_reachability_caches_result(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    calls = {"geocode": 0, "trip": 0}

    def fake_geocode(municipal):
        calls["geocode"] += 1
        return {"latitude": 61.0, "longitude": 6.0}

    def fake_trip(from_coords, to_coords):
        calls["trip"] += 1
        return {"duration": 3600, "legs": [{"mode": "bus", "line": {"publicCode": "1"}}]}

    monkeypatch.setattr(rb, "_geocode_municipal", fake_geocode)
    monkeypatch.setattr(rb, "_query_trip", fake_trip)

    first = rb.get_reachability(conn, "Sogndal")
    second = rb.get_reachability(conn, "sogndal")  # case-insensitive cache key

    assert first == {"duration_min": 60, "modes": ["автобус"]}
    assert second == first
    assert calls == {"geocode": 1, "trip": 1}  # second call hit the cache, not the network


def test_get_reachability_no_route_found(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    monkeypatch.setattr(rb, "_geocode_municipal", lambda m: {"latitude": 61.0, "longitude": 6.0})
    monkeypatch.setattr(rb, "_query_trip", lambda f, t: None)

    result = rb.get_reachability(conn, "Nowhereville")
    assert result == {"error": "no_route"}


def test_get_reachability_municipal_not_geocoded(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    monkeypatch.setattr(rb, "_geocode_municipal", lambda m: None)

    result = rb.get_reachability(conn, "Xyzabc")
    assert result == {"error": "not_found"}


def test_get_reachability_returns_none_on_network_failure(tmp_path, monkeypatch):
    """A slow/down Entur must not break the vacancy detail page — no cached
    result either, so the next view retries instead of caching a transient
    failure forever."""
    conn = _make_conn(tmp_path)

    def raise_error(municipal):
        raise requests.RequestException("boom")

    monkeypatch.setattr(rb, "_geocode_municipal", raise_error)

    assert rb.get_reachability(conn, "Bergen") is None
    assert db.get_state(conn, rb._cache_key("Bergen")) is None


def test_next_weekday_morning_is_future_weekday():
    """The whole bug this guards: a departure in the past makes Entur return
    zero tripPatterns, so every lookup silently becomes "no route"."""
    dt = datetime.fromisoformat(rb._next_weekday_morning())
    assert dt > datetime.now(dt.tzinfo)
    assert dt.weekday() < 5
    assert (dt.hour, dt.minute) == (8, 0)
