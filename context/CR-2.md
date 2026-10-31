# CR-2: Official Open WebUI image, no image of our own

The server runs Open WebUI's official image, unmodified, pulled from Open WebUI's own registry. Our files (the
functions, the startup scripts and the settings) come from this repo, fetched on every Up, instead of being baked
into a GHCR image of our own. This replaces CR-1's deployment: R12, §3.5 and §3.6. **State: planned, not
started.**

## 1. Requirements

| # | Requirement | Comes from |
|---|---|---|
| R1 | The server pulls `ghcr.io/open-webui/open-webui` itself, unmodified: Open WebUI supplies the 6.4 GB image, and `docker ps` shows that image | Owner |
| R2 | No Docker image of our own. The server takes our files from this public repo, and only the folders it needs | Owner |
| R3 | Upgrading Open WebUI is a version change in the compose file | Owner |
| R4 | Keep what the GHCR image gave: the server runs only code whose tests passed, an update can be rolled back, and it's clear which commit is running | Discussion of building on the server (§2) |
| R5 | CR-1's guardrails stay: secrets only in files, only Open WebUI's port published, the same Open WebUI settings | CLAUDE.md |

## 2. Problems with building on the server, and how the design handles them

The first idea was a compose `build:` step from the git repo. These are its problems, and they shape the design.

| Problem | How the design handles it |
|---|---|
| **Untested code reaches the server.** Merges are pushed to `main` before CI runs, so `main` can be failing | The server follows the branch `stable`, which CI moves to a commit only after its tests pass (D2) |
| **Up doesn't rebuild,** so the server quietly keeps running old code | Nothing is built. `init` fetches on every Up, and the routine is Down, then Up, so `webui` restarts and reloads the functions (D1, D8) |
| **No rollback, and no record of what's running** | `CHAT_REF` in the stack environment pins a tag or commit. `init` records the commit in `app/DEPLOYED`, and `webui` logs it at start |
| **Open WebUI's version and our functions drift apart:** the version now lives in OMV's pasted compose, and the functions in git | The repo's `deploy/compose.yaml` is the record of the tested version. `webui` refuses to start when the running Open WebUI differs from it (D7) |
| **GitHub is down during an Up** | `init` keeps the previous copy and warns (D5). A reboot fetches nothing: Docker restarts `webui` itself, and the files stay on the host |

## 3. Design

```
 push to main ─▶ GitHub Actions: pytest ─▶ passes: moves branch "stable" to that commit
                                                     │ fetched on every Up (functions/ and deploy/ only)
 OMV Compose stack "Chat"                            ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ init:  official image, one-shot, root ─▶ ${CHAT}/app = repo at ${CHAT_REF}   │
 │                                       ─▶ data/, secrets/, modes (init.sh)    │
 │ webui: ghcr.io/open-webui/open-webui:v0.12.5, unmodified                     │
 │   └─ runs ${CHAT}/app/deploy/entrypoint.sh (mounted read-only at /app/chat)  │
 │ kokoro: unchanged, internal only                                             │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Host folder

```
/StorageDrive/Chat/            (CHAT in OMV's global.env)
├── app/       our files at the deployed commit; init replaces them, webui mounts them read-only
│   ├── functions/, deploy/
│   └── DEPLOYED   the full commit hash, the ref and the time of the fetch
├── data/      unchanged
└── secrets/   unchanged
```

### 3.2 Services (`deploy/compose.yaml`)

- **`init`** is one one-shot service that fetches our files, then prepares the folders and secrets (D4). It runs
  the official Open WebUI image, which is already pulled and has git (2.47.3, checked on 2026-10-31), so there's
  no third image (D3). It runs as root with the network, mounts `${CHAT}` at `/chat-storage`, and gets `CHAT_REF`
  in its environment. Its script is inline in the compose file, because it can't come from the repo it fetches.
  Compose reads `$` as its own variables, so every `$` in the script is written `$$`:

  ```bash
  set -euo pipefail
  mkdir -p /tmp/src && cd /tmp/src && git init -q
  if git fetch -q --depth 1 https://github.com/selvalogesh/personal-llm-assistant.git "$$CHAT_REF"; then
    rm -rf /chat-storage/app.next && mkdir -p /chat-storage/app.next
    git archive FETCH_HEAD functions deploy | tar -x -C /chat-storage/app.next
    echo "$$(git rev-parse FETCH_HEAD) $$CHAT_REF $$(date -u +%FT%TZ)" > /chat-storage/app.next/DEPLOYED
    rm -rf /chat-storage/app && mv /chat-storage/app.next /chat-storage/app
    echo "init: fetched $$(cat /chat-storage/app/DEPLOYED)"
  elif [[ -f /chat-storage/app/deploy/init.sh ]]; then
    echo "init: couldn't fetch $$CHAT_REF; keeping $$(cat /chat-storage/app/DEPLOYED)" >&2
  else
    echo "init: couldn't fetch $$CHAT_REF and there's no earlier copy; check the network, then Up again" >&2
    exit 1
  fi
  exec bash /chat-storage/app/deploy/init.sh /chat-storage
  ```

  - **Fetch (D12):** a shallow fetch of the one commit `CHAT_REF` names (about 100 KB), then `git archive` of
    `functions/` and `deploy/` only. No sparse checkout: this is simpler, and only the needed folders land.
  - **Swap:** the new copy is built in `app.next` and renamed over `app` only once it's complete, so a failed
    fetch never leaves `app/` half-written.
  - **Fallback (D5):** if the fetch fails and `app/` holds a copy, it warns and keeps it. With no copy, it exits 1,
    because `webui` can't start without one.
  - **Then** it runs the fetched `deploy/init.sh`, unchanged from CR-1.
- **`webui`**: `ghcr.io/open-webui/open-webui:v0.12.5`, with `command: ["bash", "/app/chat/deploy/entrypoint.sh"]`
  and the extra mount `${CHAT}/app:/app/chat:ro`. Its environment and the other mounts are as in CR-1.
- **`kokoro`**: unchanged.
- **The Open WebUI tag is written literally in `init` and `webui`** (D13). A YAML anchor would write it once, but
  Dependabot may not follow anchors. A test fails if the two tags differ.
- `.env.example` gains `CHAT_REF=stable`. The comment above it says it takes `stable`, a tag or a **full** commit
  hash: GitHub serves a fetch by full hash, but not by a short one.

### 3.3 Startup (`deploy/entrypoint.sh`)

- **Settings:** the `ENV` block from `deploy/Dockerfile` moves verbatim to `deploy/settings.env`, without
  `WHISPER_MODEL`. The entrypoint loads it first, so it overrides the official image's defaults. **Rule:**
  `settings.env` never sets a variable that the compose environment sets, and a test checks this (D6).
- **Version guard:** the entrypoint reads the running version from `/app/package.json` (`"version": "0.12.5"`,
  checked) and the `open-webui` tag from `/app/chat/deploy/compose.yaml`. If they differ, it stops with "The stack
  runs Open WebUI X, but the repo at <commit> expects Y: paste deploy/compose.yaml into the stack, then Pull, Down,
  Up" (D7).
- **Log line at start**, from `DEPLOYED`: `running <commit> (<ref>), Open WebUI <version>`.
- Secrets and `load_functions.py` work as before, with the paths moved to `/app/chat/...`.

### 3.4 CI and updates

- `.github/workflows/image.yml` becomes `ci.yml`, with no Docker steps:
  - on pull requests: pytest;
  - on push to `main`: pytest, then `git push --force origin HEAD:refs/heads/stable` (`contents: write`).
    `--force` is needed because the owner amends and squashes. `stable` is a deploy pointer, not a work branch,
    and nobody commits to it. The existing concurrency group stays, so `stable` always ends on the newest green
    commit.
- `.github/dependabot.yml`, ecosystem `docker-compose` on `/deploy`: opens a pull request when Open WebUI or Kokoro
  releases a new version (O4).
- **Removed:** `deploy/Dockerfile`, `.dockerignore`, the Docker steps.
- **GHCR:** `ghcr.io/selvalogesh/personal-llm-assistant` is no longer pulled. Deleting it after the cutover is the
  owner's choice (D10).

### 3.5 Routines on the server (OMV Compose → Files → Chat)

| Change | Steps |
|---|---|
| Code, functions or `settings.env`, merged to `main` | Wait for CI to pass (it moves `stable`), then **Down, Up** |
| Open WebUI or Kokoro version (`compose.yaml`) | Local stack check, merge, paste `compose.yaml` into the stack, then **Pull, Down, Up** |
| Roll back the code | Set `CHAT_REF` to a tag or full commit hash in the stack environment, then **Down, Up**. Set it back to `stable` once fixed. Only within one Open WebUI version (D9) |

As before, Open WebUI saves most settings to its database on the first start, so a later change to `settings.env`
reaches those settings only through Admin → Settings.

### 3.6 Local stack check

A fetch from GitHub can't test uncommitted work. `deploy/compose.local.yaml` mounts the working tree read-only at
`/src` and replaces `init`'s command with a copy from it, followed by the same `init.sh`:
`docker compose -p chattest --env-file <file> -f deploy/compose.yaml -f deploy/compose.local.yaml up -d`.

## 4. Software

Only the changes from CR-1 §4:

| Software | Version | Role | Runs | Cost |
|---|---|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui), official image | v0.12.5 (pinned in `compose.yaml`) | As in CR-1, unmodified | `webui`, and the one-shot `init` | Free |
| git (bundled in Open WebUI's image) | 2.47.3 | Fetches our files | `init` | Free |
| GitHub Actions | n/a | Tests; moves `stable` | GitHub | Free tier |
| Dependabot | n/a | Pull requests for new Open WebUI and Kokoro versions | GitHub | Free |
| ~~GHCR~~ | | No longer used | | |

## 5. Open items (verified in phase 1)

| # | Question |
|---|---|
| O1 | The §3.2 script works in the official image: a shallow fetch of a branch, a tag and a full commit hash without credentials, then `git archive` piped to `tar` |
| O2 | OMV's Up re-runs the completed one-shot `init` and leaves a running, unchanged `webui` alone. That's why the routine is Down, then Up (D8) |
| O3 | `/app/package.json` carries the version in newer Open WebUI releases too; otherwise the guard needs another source |
| O4 | Dependabot's `docker-compose` ecosystem reads `ghcr.io/open-webui/open-webui:v0.12.5` and updates both tags in one pull request |
| O5 | With GitHub unreachable (`init` given a bad repo address), the previous `app/` is kept and `webui` starts |

## 6. Build order

Each phase ends in a commit (`CR-2: …`) on branch `story/main/CR-2-official-image`, which is fast-forward merged
into `main` once phase 6 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O5** on the laptop | Findings in §5; the design adjusted where needed | Not started |
| 2 | **Startup from files** | `deploy/settings.env`; `entrypoint.sh` loads it, guards the version, logs the commit, uses `/app/chat` paths. Tests: no overlap between `settings.env` and the compose environment, the version guard, the two Open WebUI tags exact and equal (`pyyaml` added to `requirements-dev.txt`) | Not started |
| 3 | **Compose** | `init` with the §3.2 script; `webui` on the official image; `compose.local.yaml`; `CHAT_REF` in `.env.example` | Not started |
| 4 | **CI** | `ci.yml` (pytest; on `main`, move `stable`); `dependabot.yml`; delete `deploy/Dockerfile`, `.dockerignore`, `image.yml` | Not started |
| 5 | **Docs** | `deploy/README.md` (diagram, routines, rollback, troubleshooting for the fetch and the guard; `init` now has the network), `ENVIRONMENT.md` (`settings.env`, `CHAT_REF`, local check), `CLAUDE.md` and `README.md` (no GHCR image), and a "superseded by CR-2" note on CR-1's R12, §3.5 and §3.6 | Not started |
| 6 | **Local stack check** | CR-1's local check through `compose.local.yaml`, plus a real fetch of `stable` from GitHub | Not started |
| 7 | **Cutover (owner)** | Paste `compose.yaml` and the environment (with `CHAT_REF=stable`) into the stack, then Pull, Down, Up, and the runbook's "Done when". `data/` and `secrets/` carry over. Then, optionally, delete the GHCR package | Not started |

## 7. Verification

| Check | How |
|---|---|
| Offline tests | `pytest`: CR-1's tests, plus the settings overlap, the version guard and the two tags |
| Official image | `docker ps` on the server shows `ghcr.io/open-webui/open-webui:v0.12.5` for `webui`; no `selvalogesh` image is on the server |
| Fetch | `app/` holds only `functions/`, `deploy/` and `DEPLOYED`; `DEPLOYED` and `webui`'s log name the commit `stable` points at |
| Test gate | A commit whose tests fail doesn't move `stable`, and a Down, Up keeps the previous commit |
| Rollback | `CHAT_REF=<older full commit hash>`, then Down, Up runs that commit; `stable` brings it back |
| Version guard | A stack running a different Open WebUI tag than the repo's `compose.yaml` refuses to start, with the message from §3.3 |
| GitHub unreachable | `init` warns and keeps `app/`; `webui` starts |
| Everything else | CR-1 §7 and the runbook's "Done when", unchanged |

## 8. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Fetch our files on every Up, instead of a compose `build:` from git | A build makes a locally named image, so `docker ps` wouldn't show the official one (R1). Up also doesn't rebuild, so the server could quietly run old code. A fetch is part of every Up, and the official image runs as-is |
| D2 | The server follows branch `stable`, which CI moves only after the tests pass | This restores the test gate the image push gave. `main` itself can be failing, because merges are pushed before CI runs |
| D3 | `init` uses the official Open WebUI image | It has git, and it adds no third image to trust or update |
| D4 | Fetching and preparing the folders are one service, `init`, which has the network | Owner's choice. A separate fetch service without access to `secrets/` protected little: `webui` runs the fetched code with the secrets readable and with the network anyway. One service is simpler, and the tag is written twice, not three times. The trade-off: the one container that can write `secrets/` now has the network, where CR-1's `init` had none |
| D5 | A failed fetch keeps the previous copy | A GitHub outage shouldn't take chat down; only the very first Up needs GitHub to answer |
| D6 | Settings live in `deploy/settings.env`, loaded by the entrypoint, and never set a variable the compose environment sets | Settings stay in git and pass CI, and changing one needs no compose paste. An overlap would let the file silently override the stack |
| D7 | The entrypoint refuses to start when the running Open WebUI version differs from the repo's `compose.yaml` | It keeps the Open WebUI version and our functions moving together, and catches a forgotten paste. The owner is there at every Up that can cause it; a reboot fetches nothing |
| D8 | The update routine is Down, then Up | Up leaves a running, unchanged `webui` alone, so the new functions wouldn't load |
| D9 | Rollback covers code only, within one Open WebUI version | Open WebUI migrates its database when it upgrades, and going back a version isn't supported. After a bad Open WebUI upgrade, restore `data/` from the backup with the old tag |
| D10 | Deleting the GHCR package after the cutover is the owner's choice | Nothing pulls it any more. It holds no secrets (CR-1 D14), only an old copy of the code |
| D11 | Rejected: a small GHCR image carrying only our files, beside the official image | It keeps an image of our own, which R2 rules out. It would have kept the plain Pull, Up routine and image tags for rollback |
| D12 | The fetch is a shallow fetch of one commit plus `git archive` of the two folders, not a sparse checkout | Simpler, and only the needed folders land on the server. The download is the whole commit, about 100 KB |
| D13 | The Open WebUI tag is written literally in each service, and a test checks they match | Dependabot can update literal tags; it may not follow a YAML anchor |
