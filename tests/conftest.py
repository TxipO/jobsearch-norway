import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Synthetic identity used by every test (2026-10-05, /fullreview deep: three
# test_cv_builder tests used to read the gitignored profile/personal.json, so
# they failed on a clean checkout/CI and the tracked test file carried the
# real user's hobbies). Nothing here is real data.
SYNTHETIC_PERSONAL = {
    "name": "Test Testesen",
    "phone": "+47 000 00 000",
    "email": "test.testesen@example.invalid",
    "address_line": "0000 Testby, Norway",
    "linkedin": "",
    "hobbies": ["Test chess", "Test hiking"],
    "hobbies_no": ["Testsjakk", "Testturer"],
    "home_latitude": 59.911491,
    "home_longitude": 10.757933,
}


def _tiny_png(path: Path) -> None:
    """Smallest valid 1x1 RGB PNG, built by hand so tests need no Pillow."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


@pytest.fixture(autouse=True)
def fresh_county_map_cache():
    """jobbnorge_client caches the county map for a few minutes; tests that
    patch the fetchers must not see (or leave) a map built by another test."""
    import jobbnorge_client
    jobbnorge_client._county_map_cache.clear()
    yield
    jobbnorge_client._county_map_cache.clear()


@pytest.fixture(autouse=True)
def synthetic_personal(tmp_path_factory, monkeypatch):
    """Point cv_builder/reachability at a synthetic personal.json in tmp so
    tests never depend on (or leak) the real, gitignored profile/personal.json.
    Tests that need a different file simply monkeypatch PERSONAL_PATH again —
    a later monkeypatch.setattr wins over this one."""
    d = tmp_path_factory.mktemp("personal")
    photo = d / "photo.png"
    _tiny_png(photo)
    personal = d / "personal.json"
    personal.write_text(
        json.dumps({**SYNTHETIC_PERSONAL, "photo_path": str(photo)}, ensure_ascii=False),
        encoding="utf-8",
    )
    import cv_builder
    import reachability
    monkeypatch.setattr(cv_builder, "PERSONAL_PATH", personal)
    monkeypatch.setattr(reachability, "_PERSONAL_PATH", personal)
    return personal


@pytest.fixture(autouse=True)
def never_touch_the_live_database(monkeypatch):
    """A test that reaches data/jobsearch.db fails loudly instead of quietly
    running real sync stages on the user's data. 2026-10-22: one test reverted
    its tmp-db patch (monkeypatch.undo()) and every full test run since
    2026-10-20 overwrote the real "last sync" summary and ran the real
    delete/retire/auto-ignore stages. Applies to db.connect and db.backup_db
    when called with their DEFAULT (live) paths; tests that pass a tmp path,
    or patch these functions themselves, are unaffected."""
    import db
    live = db.DB_PATH.resolve()
    real_connect, real_backup = db.connect, db.backup_db

    def guarded_connect(db_path=db.DB_PATH, *a, **kw):
        if Path(db_path).resolve() == live:
            pytest.fail("a test tried to open the live data/jobsearch.db")
        return real_connect(db_path, *a, **kw)

    def guarded_backup(db_path=db.DB_PATH, *a, **kw):
        if Path(db_path).resolve() == live:
            pytest.fail("a test tried to back up the live data/jobsearch.db")
        return real_backup(db_path, *a, **kw)

    monkeypatch.setattr(db, "connect", guarded_connect)
    monkeypatch.setattr(db, "backup_db", guarded_backup)


def _live_db_stamp():
    import db
    try:
        st = db.DB_PATH.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def pytest_sessionstart(session):
    session.config._live_db_stamp_at_start = _live_db_stamp()


def pytest_sessionfinish(session, exitstatus):
    """Backstop for the autouse guard above (which cannot see a test that
    reverts its own patches with monkeypatch.undo()): if the live database
    file changed while the suite ran, fail the run. A real sync/server write
    during the run would also trigger this - re-run in that case."""
    before = getattr(session.config, "_live_db_stamp_at_start", None)
    after = _live_db_stamp()
    if before is not None and after != before:
        session.exitstatus = 1
        print(
            "\n!!! data/jobsearch.db changed while the tests ran - a test touched the live database "
            "(or a real sync wrote to it meanwhile)."
        )
