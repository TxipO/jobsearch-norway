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
