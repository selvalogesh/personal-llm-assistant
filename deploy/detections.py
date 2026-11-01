"""Lists and deletes the Router's classifier misses (CR-4 §3.7). Run it inside the webui container:

    docker exec <webui container> python /app/chat/deploy/detections.py list [--user EMAIL]
    docker exec <webui container> python /app/chat/deploy/detections.py delete ID
    docker exec <webui container> python /app/chat/deploy/detections.py clear (--user EMAIL | --all)

A strong miss (the user corrected the format, or the model used a tool the classifier didn't pick) is one of that
user's learned examples; a weak one (a thumbs-down) is only listed. People show by email, from Open WebUI's database.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing

STORE = "/app/backend/data/router/router.sqlite"
WEBUI_DB = "/app/backend/data/webui.db"


def people(webui_db: str) -> dict[str, str]:
    """User id → email, from Open WebUI's database; empty when it can't be read."""
    if not os.path.exists(webui_db):
        return {}
    try:
        with closing(sqlite3.connect(webui_db)) as con:
            return dict(con.execute('SELECT id, email FROM "user"'))
    except sqlite3.Error:
        return {}


def describe(labels: dict) -> str:
    parts = [name for name in ("image", "web", "code") if labels.get(name)]
    if labels.get("voice", "none") != "none":
        parts.append(f"voice {labels['voice']}")
    return ", ".join(parts) or "chat"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("list", "delete", "clear"))
    parser.add_argument("id", nargs="?", type=int, help="the miss to delete (from list)")
    parser.add_argument("--user", help="one person's misses, by email or user id")
    parser.add_argument("--all", action="store_true", help="with clear: everyone's misses")
    parser.add_argument("--store", default=STORE)
    parser.add_argument("--webui-db", default=WEBUI_DB)
    args = parser.parse_args(argv)

    if not os.path.exists(args.store):
        print("No misses yet: the Router hasn't written its file.")
        return 0
    emails = people(args.webui_db)
    user_id = next((uid for uid, email in emails.items() if email == args.user), args.user)
    with closing(sqlite3.connect(args.store)) as con, con:
        if args.action == "delete":
            if args.id is None:
                parser.error("delete needs the miss's ID, from list")
            gone = con.execute("DELETE FROM detections WHERE id = ?", (args.id,)).rowcount
            print(f"Deleted miss {args.id}." if gone else f"There's no miss {args.id}.")
            return 0 if gone else 1
        if args.action == "clear":
            if not (args.user or args.all):
                parser.error("clear needs --user EMAIL, or --all for everyone's")
            where, params = ("WHERE user_id = ?", (user_id,)) if args.user else ("", ())
            gone = con.execute(f"DELETE FROM detections {where}", params).rowcount
            print(f"Deleted {gone} misses.")
            return 0
        where, params = ("WHERE user_id = ?", (user_id,)) if args.user else ("", ())
        rows = con.execute(
            f"SELECT id, at, user_id, labels, signal, strong, text FROM detections {where} ORDER BY at DESC", params
        ).fetchall()
    if not rows:
        print("No misses.")
    for miss_id, at, uid, labels, signal, strong, text in rows:
        when = time.strftime("%Y-%m-%d", time.localtime(at))
        kind = "learned" if strong else "listed only"
        print(f"{miss_id:>5}  {when}  {emails.get(uid, uid)}  {signal} ({kind})  → {describe(json.loads(labels))}")
        print(f"       {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
