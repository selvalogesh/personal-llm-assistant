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
| **Up doesn't rebuild,** so the server quietly keeps running old code | Nothing is built. The `fetch` service runs on every Up, and the routine is Down, then Up, so `webui` restarts and reloads the functions (D1, D8) |
| **No rollback, and no record of what's running** | `CHAT_REF` in the stack environment pins a tag or commit. `fetch` records the commit in `app/DEPLOYED`, and `webui` logs it at start |
| **Open WebUI's version and our functions drift apart:** the version now lives in OMV's pasted compose, and the functions in git | The repo's `deploy/compose.yaml` is the record of the tested version. `webui` refuses to start when the running Open WebUI differs from it (D7) |
| **GitHub is down during an Up** | `fetch` keeps the previous copy and warns (D5). A reboot fetches nothing: Docker restarts `webui` itself, and the files stay on the host |

## 3. Design

```
 push to main ─▶ GitHub Actions: pytest ─▶ passes: moves branch "stable" to that commit
                                                     │ fetched on every Up (functions/ and deploy/ only)
 OMV Compose stack "Chat"                            ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ fetch: official image, network, root   ─▶ ${CHAT}/app = repo at ${CHAT_REF}  │
 │ init:  official image, no network, root ─▶ data/, secrets/, modes            │
 │ webui: ghcr.io/open-webui/open-webui:v0.12.5, unmodified                     │
 │   └─ runs ${CHAT}/app/deploy/entrypoint.sh (mounted read-only at /app/chat)  │
 │ kokoro: unchanged, internal only                                             │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Host folder

```
/StorageDrive/Chat/            (CHAT in OMV's global.env)
├── app/       our files at the deployed commit; fetch replaces them, webui mounts them read-only
│   └── DEPLOYED   the commit, the ref and the time of the fetch
├── data/      unchanged
└── secrets/   unchanged; fetch never sees it
```

### 3.2 Services (`deploy/compose.yaml`)

- **`fetch`** runs the official Open WebUI image, which is already pulled and has git (2.47.3, checked on
  2026-10-31), so there's no third image. It runs as root with the network and mounts only `${CHAT}/app`. Its
  script is inline in the compose file, because it can't come from the repo it's fetching. The script:
  - makes a shallow, sparse fetch of `functions/` and `deploy/` at `${CHAT_REF}`. A branch, a tag or a full commit
    hash all work, through `git fetch --depth 1 origin "$CHAT_REF"`, then checking out `FETCH_HEAD`;
  - replaces the contents of `app/` with that tree and writes `DEPLOYED`. `webui` reads these files only at start;
  - if the fetch fails and `app/` already holds a copy, warns and keeps it (exit 0). With no copy, it exits 1,
    because `webui` can't start without one.
- **`init`**: the same one-shot as CR-1, now on the official image and running `app/deploy/init.sh`. It still has
  no network. It starts after `fetch` completes.
- **`webui`**: `ghcr.io/open-webui/open-webui:v0.12.5`, with `command: ["bash", "/app/chat/deploy/entrypoint.sh"]`
  and the extra mount `${CHAT}/app:/app/chat:ro`. Its environment and the other mounts are as in CR-1.
- **`kokoro`**: unchanged.
- `.env.example` gains `CHAT_REF=stable`.

### 3.3 Startup (`deploy/entrypoint.sh`)

- **Settings:** the `ENV` block from `deploy/Dockerfile` moves verbatim to `deploy/settings.env`, without
  `WHISPER_MODEL`. The entrypoint loads it first, so it overrides the official image's defaults. **Rule:**
  `settings.env` never sets a variable that the compose environment sets, and a test checks this (D6).
- **Version guard:** the entrypoint reads the running version from `/app/package.json` (`"version": "0.12.5"`,
  checked) and the `open-webui` tag from `/app/chat/deploy/compose.yaml`. If they differ, it stops with "The stack
  runs Open WebUI X, but the repo at <commit> expects Y: paste deploy/compose.yaml into the stack, then Pull, Down,
  Up" (D7).
- **Log line at start:** `running <commit> (<ref>), Open WebUI <version>`.
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
| Roll back the code | Set `CHAT_REF` to a commit or tag in the stack environment, then **Down, Up**. Set it back to `stable` once fixed. Only within one Open WebUI version (D9) |

As before, Open WebUI saves most settings to its database on the first start, so a later change to `settings.env`
reaches those settings only through Admin → Settings.

### 3.6 Local stack check

A fetch from GitHub can't test uncommitted work. `deploy/compose.local.yaml` overrides `fetch` to copy from the
working tree, mounted read-only:
`docker compose -p chattest --env-file <file> -f deploy/compose.yaml -f deploy/compose.local.yaml up -d`.

## 4. Software

Only the changes from CR-1 §4:

| Software | Version | Role | Runs | Cost |
|---|---|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui), official image | v0.12.5 (pinned in `compose.yaml`) | As in CR-1, unmodified | `webui`, and `fetch` and `init` (one-shots) | Free |
| git (bundled in Open WebUI's image) | 2.47.3 | Fetches our files | `fetch` | Free |
| GitHub Actions | n/a | Tests; moves `stable` | GitHub | Free tier |
| Dependabot | n/a | Pull requests for new Open WebUI and Kokoro versions | GitHub | Free |
| ~~GHCR~~ | | No longer used | | |

## 5. Open items (verified in phase 1)

| # | Question |
|---|---|
| O1 | A shallow, sparse fetch of a branch, a tag and a full commit hash works without credentials, with the official image's git |
| O2 | OMV's Up re-runs the completed one-shots (`fetch`, `init`) and leaves a running, unchanged `webui` alone. That's why the routine is Down, then Up (D8) |
| O3 | `/app/package.json` carries the version in newer Open WebUI releases too; otherwise the guard needs another source |
| O4 | Dependabot's `docker-compose` ecosystem reads `ghcr.io/open-webui/open-webui:v0.12.5` and proposes the next tag |
| O5 | With GitHub unreachable (`fetch` given a bad repo address), the previous `app/` is kept and `webui` starts |

## 6. Build order

Each phase ends in a commit (`CR-2: …`) on branch `story/main/CR-2-official-image`, which is fast-forward merged
into `main` once phase 6 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O5** on the laptop | Findings in §5; the design adjusted where needed | Not started |
| 2 | **Startup from files** | `deploy/settings.env`; `entrypoint.sh` loads it, guards the version, logs the commit, uses `/app/chat` paths. Tests: no overlap between `settings.env` and the compose environment, the version guard, and `compose.yaml` pinning an exact Open WebUI tag (`pyyaml` added to `requirements-dev.txt`) | Not started |
| 3 | **Compose** | `fetch` service; `init` and `webui` on the official image; `compose.local.yaml`; `CHAT_REF` in `.env.example` | Not started |
| 4 | **CI** | `ci.yml` (pytest; on `main`, move `stable`); `dependabot.yml`; delete `deploy/Dockerfile`, `.dockerignore`, `image.yml` | Not started |
| 5 | **Docs** | `deploy/README.md` (diagram, routines, rollback, troubleshooting for `fetch` and the guard), `ENVIRONMENT.md` (`settings.env`, `CHAT_REF`, local check), `CLAUDE.md` and `README.md` (no GHCR image), and a "superseded by CR-2" note on CR-1's R12, §3.5 and §3.6 | Not started |
| 6 | **Local stack check** | CR-1's local check through `compose.local.yaml`, plus a real fetch of `stable` from GitHub | Not started |
| 7 | **Cutover (owner)** | Paste `compose.yaml` and the environment (with `CHAT_REF=stable`) into the stack, then Pull, Down, Up, and the runbook's "Done when". `data/` and `secrets/` carry over. Then, optionally, delete the GHCR package | Not started |

## 7. Verification

| Check | How |
|---|---|
| Offline tests | `pytest`: CR-1's tests, plus the settings overlap, the version guard and the pinned tag |
| Official image | `docker ps` on the server shows `ghcr.io/open-webui/open-webui:v0.12.5` for `webui`; no `selvalogesh` image is on the server |
| Fetch | `app/` holds only `functions/`, `deploy/` and `DEPLOYED`; `DEPLOYED` and `webui`'s log name the commit `stable` points at |
| Test gate | A commit whose tests fail doesn't move `stable`, and a Down, Up keeps the previous commit |
| Rollback | `CHAT_REF=<older commit>`, then Down, Up runs that commit; `stable` brings it back |
| Version guard | A stack running a different Open WebUI tag than the repo's `compose.yaml` refuses to start, with the message from §3.3 |
| GitHub unreachable | `fetch` warns and keeps `app/`; `webui` starts |
| Everything else | CR-1 §7 and the runbook's "Done when", unchanged |

## 8. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Fetch our files on every Up, instead of a compose `build:` from git | A build makes a locally named image, so `docker ps` wouldn't show the official one (R1). Up also doesn't rebuild, so the server could quietly run old code. A fetch is part of every Up, and the official image runs as-is |
| D2 | The server follows branch `stable`, which CI moves only after the tests pass | This restores the test gate the image push gave. `main` itself can be failing, because merges are pushed before CI runs |
| D3 | `fetch` uses the official Open WebUI image | It has git, and it adds no third image to trust or update |
| D4 | `fetch` mounts only `${CHAT}/app`, and `init` keeps no network | The one container with network access and write access never sees the secrets or the data |
| D5 | A failed fetch keeps the previous copy | A GitHub outage shouldn't take chat down; only the very first Up needs GitHub to answer |
| D6 | Settings live in `deploy/settings.env`, loaded by the entrypoint, and never set a variable the compose environment sets | Settings stay in git and pass CI, and changing one needs no compose paste. An overlap would let the file silently override the stack |
| D7 | The entrypoint refuses to start when the running Open WebUI version differs from the repo's `compose.yaml` | It keeps the Open WebUI version and our functions moving together, and catches a forgotten paste. The owner is there at every Up that can cause it; a reboot fetches nothing |
| D8 | The update routine is Down, then Up | Up leaves a running, unchanged `webui` alone, so the new functions wouldn't load |
| D9 | Rollback covers code only, within one Open WebUI version | Open WebUI migrates its database when it upgrades, and going back a version isn't supported. After a bad Open WebUI upgrade, restore `data/` from the backup with the old tag |
| D10 | Deleting the GHCR package after the cutover is the owner's choice | Nothing pulls it any more. It holds no secrets (CR-1 D14), only an old copy of the code |
| D11 | Rejected: a small GHCR image carrying only our files, beside the official image | It keeps an image of our own, which R2 rules out. It would have kept the plain Pull, Up routine and image tags for rollback |
