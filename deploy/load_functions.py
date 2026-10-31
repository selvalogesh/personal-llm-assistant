"""Installs or updates our Open WebUI functions through its API, then turns them on (CR-1 §3.5).

Run by entrypoint.sh in the background on every start, once Open WebUI answers /health. It signs in as the admin
that Open WebUI created from WEBUI_ADMIN_EMAIL and the admin password file, so no API key is needed. Only a
function's code is updated: Valves changed in Admin → Functions are kept.

On the very first start it also turns sign-ups back on, as pending (R3): Open WebUI turns them off whenever it
creates the admin from the environment. A marker file in the data folder makes sure that happens only once, so a
later choice in Admin → Settings sticks.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("LOADER_BASE_URL", "http://127.0.0.1:8080")
FUNCTIONS_DIR = Path(os.environ.get("LOADER_FUNCTIONS_DIR", "/app/chat-functions"))
MARKER = Path(os.environ.get("LOADER_MARKER", "/app/backend/data/.chat-first-start-done"))


def log(message: str) -> None:
    print(f"[load_functions] {message}", flush=True)


def call(method: str, path: str, token: str = "", body: dict | None = None):
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def wait_for_health(timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if call("GET", "/health"):
                return
        except Exception:
            pass
        time.sleep(3)
    raise TimeoutError(f"Open WebUI didn't answer /health within {timeout}s")


def frontmatter(content: str) -> dict:
    match = re.match(r'\s*"""(.*?)"""', content, re.S)
    fields = {}
    for line in (match.group(1) if match else "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def sync_function(token: str, path: Path) -> None:
    function_id = path.stem
    content = path.read_text(encoding="utf-8")
    meta = frontmatter(content)
    form = {
        "id": function_id,
        "name": meta.get("title", function_id),
        "content": content,
        "meta": {"description": meta.get("description", "")},
    }
    try:
        existing = call("GET", f"/api/v1/functions/id/{function_id}", token)
    except urllib.error.HTTPError as e:
        if e.code not in (401, 404):
            raise
        existing = None

    if existing is None:
        existing = call("POST", "/api/v1/functions/create", token, form)
        log(f"{function_id}: installed")
    elif existing.get("content") != content:
        existing = call("POST", f"/api/v1/functions/id/{function_id}/update", token, form)
        log(f"{function_id}: updated")
    else:
        log(f"{function_id}: unchanged")

    if not existing.get("is_active"):
        call("POST", f"/api/v1/functions/id/{function_id}/toggle", token)
        log(f"{function_id}: turned on")
    # Filters apply to every model (the Router's options): the toggles then show in every chat.
    if existing.get("type") == "filter" and not existing.get("is_global"):
        call("POST", f"/api/v1/functions/id/{function_id}/toggle/global", token)
        log(f"{function_id}: made global")


def first_start(token: str) -> None:
    if MARKER.exists():
        return
    config = call("GET", "/api/v1/auths/admin/config", token)
    config.update({"ENABLE_SIGNUP": True, "DEFAULT_USER_ROLE": "pending"})
    call("POST", "/api/v1/auths/admin/config", token, config)
    MARKER.write_text("sign-ups turned on as pending on the first start\n")
    log("first start: sign-ups on, new accounts wait for approval")


def main() -> int:
    email = os.environ["WEBUI_ADMIN_EMAIL"]
    password = os.environ["WEBUI_ADMIN_PASSWORD"]
    wait_for_health(int(os.environ.get("LOADER_HEALTH_TIMEOUT", "600")))
    token = call("POST", "/api/v1/auths/signin", body={"email": email, "password": password})["token"]
    first_start(token)
    failed = 0
    for path in sorted(FUNCTIONS_DIR.glob("*.py")):
        try:
            sync_function(token, path)
        except Exception as e:  # one broken function shouldn't block the others
            failed += 1
            detail = e.read().decode(errors="replace")[:300] if isinstance(e, urllib.error.HTTPError) else e
            log(f"{path.stem}: FAILED: {detail}")
    log("done" if not failed else f"done, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
