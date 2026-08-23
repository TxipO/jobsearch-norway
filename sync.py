import logging

import nav_client
import jobbnorge_client
import finn_client
import easycruit_client
import linkedin_client
from db import backup_db, connect, delete_archived, delete_expired_unreacted, delete_inactive
from scoring import rescore_all

if __name__ == "__main__":
    # nav_client logs via the `logging` module (not print) so its messages
    # stay controllable when called from the web app's /sync route — without
    # this, INFO-level messages (e.g. cursor bootstrap) would be silently
    # dropped here too, since Python's default log level is WARNING.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    print("backup:", backup_db())
    conn = connect()
    print("nav:", nav_client.sync(conn))
    print("jobbnorge:", jobbnorge_client.sync(conn))
    print("finn:", finn_client.sync(conn))
    print("easycruit:", easycruit_client.sync(conn))
    print("linkedin:", linkedin_client.sync(conn))
    scored = rescore_all(conn)
    print(f"rescored {scored}")
    print("deleted inactive:", delete_inactive(conn))
    print("deleted expired+unreacted:", delete_expired_unreacted(conn))
    print("deleted archived (Смітник):", delete_archived(conn))
