"""Stops the start when the running Open WebUI isn't the version the repo's compose.yaml pins (CR-2 D7), and logs
which commit is running.

Run by entrypoint.sh before Open WebUI starts. The repo's compose.yaml, fetched with our files, records the version
our functions were checked against. The stack runs OMV's pasted copy, so a forgotten paste makes the two differ.
"""

import json
import os
import re
import sys
from pathlib import Path

APP = Path(os.environ.get("CHECK_APP_DIR", "/app/chat"))
PACKAGE_JSON = Path(os.environ.get("CHECK_PACKAGE_JSON", "/app/package.json"))
IMAGE = re.compile(r"image:\s*ghcr\.io/open-webui/open-webui:v?(\S+)")


def pinned_versions(compose: str) -> set[str]:
    return set(IMAGE.findall(compose))


def deployed(text: str) -> tuple[str, str]:
    """The commit and the ref from DEPLOYED ("<commit> <ref> <time>")."""
    fields = text.split()
    return (fields[0], fields[1]) if len(fields) >= 2 else ("unknown", "unknown")


def problem(running: str, pinned: set[str], commit: str) -> str | None:
    if len(pinned) != 1:
        return f"deploy/compose.yaml at {commit} should pin one Open WebUI version, found {sorted(pinned) or 'none'}"
    expected = next(iter(pinned))
    if running != expected:
        return (
            f"The stack runs Open WebUI {running}, but the repo at {commit} expects {expected}: "
            "paste deploy/compose.yaml into the stack, then Pull, Down, Up"
        )
    return None


def main() -> int:
    running = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
    deployed_file = APP / "DEPLOYED"
    commit, ref = deployed(deployed_file.read_text(encoding="utf-8") if deployed_file.exists() else "")
    message = problem(running, pinned_versions((APP / "deploy/compose.yaml").read_text(encoding="utf-8")), commit)
    if message:
        print(f"entrypoint: {message}", file=sys.stderr, flush=True)
        return 1
    print(f"entrypoint: running {commit} ({ref}), Open WebUI {running}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
