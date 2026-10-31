# personal-llm-assistant

A private chat service for the owner, family and friends: Open WebUI on the owner's OMV home server, with model
traffic going to OpenRouter through our own routing pipe, local voice (Whisper, Kokoro), and a GHCR image built by
GitHub Actions. The owner made every design decision deliberately; [context/CR-1.md](context/CR-1.md) records them
with reasons, and [context/Discovery.md](context/Discovery.md) the ideation before it. Settings and secrets:
[ENVIRONMENT.md](ENVIRONMENT.md).

## Guardrails

These are the owner's decisions. Change one only when the owner asks.

- **Secrets live only in files** in `/StorageDrive/Chat/secrets/` on the server (each holding only its value,
  mounted read-only). Never in the stack environment (OMV commits it to git), the image, git, logs or chats.
- **Only Open WebUI's port is published.** Kokoro and anything else stay internal to the stack.
- **Users see only the pipe's options** (Auto, Creative, Smart, Free). The direct OpenRouter model list stays off,
  so spending stays predictable inside the shared $10/month workspace budget.
- **No refusal detector and no rerouting on refusal** (CR-1 decision D3). Model order is the lever.
- **Privacy order:** each turn asks for non-logging providers first and allows logging providers only when none
  can serve the chain.
- **Docs name commits by message, never by hash**, because the owner amends and squashes.

## Setting up the server

[deploy/README.md](deploy/README.md) is the runbook. The repo isn't cloned on the server: it runs the GHCR image
(pushed by `.github/workflows/image.yml` on every push to `main`) from the OMV Compose stack "Chat". Steps marked
**(you)** need the owner: prepare everything around them, hand the owner the exact action, then verify it with the
check the runbook gives.

## Working on the code

- **Every new feature request gets the next CR number:** the highest number in [context/](context/README.md) plus
  one. Write its plan as `context/CR-<n>.md` and add it to that index before writing code. Fixes and docs that
  belong to a request keep its number.
- **Merging:** fast-forward only. Merge locally: `git checkout main && git pull && git merge --ff-only <branch> &&
  git push origin main`. If `main` has moved on, rebase the branch onto it first.
- **Branches:** `<kind>/<target branch>/CR-<n>-<short-desc>`, kind `story` (feature) or `bug` (bug fix).
- **Commit messages:** `CR-<n>: <commit message>`.
- `python -m pytest` runs the offline tests; they never call OpenRouter.
- Functions in `functions/` are single-file Open WebUI functions: keep each self-contained (no imports from this
  repo), because Open WebUI stores and runs them on their own.
