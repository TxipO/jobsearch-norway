import json
import logging
import sys

if __name__ == "__main__":
    # Same stdout fix as generate_documents.py: the summary below carries
    # Cyrillic/Norwegian text, and on Windows a redirected stdout is a legacy
    # codepage -> UnicodeEncodeError mid-run (2026-10-05 audit).
    sys.stdout.reconfigure(encoding="utf-8")

    # nav_client logs via the `logging` module (not print) so its messages
    # stay controllable when called from the web app's /sync route — without
    # this, INFO-level messages (e.g. cursor bootstrap) would be silently
    # dropped here too, since Python's default log level is WARNING.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    # 2026-10-05 (/fullreview deep): the CLI used to run its own copy of the
    # sync pipeline with no per-source guard (a Gmail auth failure killed
    # easycruit/linkedin/rescore/deletes), never ran
    # auto_ignore_stale_applications and never wrote the web_last_sync_summary
    # state the UI reads. The web app's trigger_sync() is the one real
    # pipeline (backup, per-source guards, rescore, deletes, auto-ignore,
    # summary state) — delegate to it so CLI and button can't drift again.
    # Imported here, not at module top, so importing sync stays side-effect free.
    from web.app import trigger_sync

    summary = trigger_sync()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
