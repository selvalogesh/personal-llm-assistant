# Server runbook (OMV Compose stack "Chat", image from GHCR)

```
push to main ─▶ GitHub Actions: tests, build ─▶ ghcr.io/selvalogesh/personal-llm-assistant:latest (private)
                                                               │ pulled by
openai.selvalogesh.com ─▶ your proxy / tunnel ─▶ host :CHAT_UI_PORT ─▶ webui container (OMV stack "Chat")
                                                                         │ kokoro (speech, internal only)
                                                                         ▼
                                                                    OpenRouter

/StorageDrive/Chat/            (CHAT in OMV's global.env)
├── data/      Open WebUI: users, chats, files, document vectors, the Whisper model cache
└── secrets/   chmod 700, read-only in the containers; each file 600 and holding only its value
    ├── openrouter-api-key     the chat-server OpenRouter key                 (you)
    ├── webui-admin-password   the admin account's password                   (you)
    └── webui-secret-key       random session key; init generates it
```

The stack's one-shot `init` service builds this tree. It runs as root, with no network, before `webui` on every
start. It creates missing folders, placeholder secrets and the session key, and sets modes. It never overwrites a
file. `webui` refuses to start while a secret is missing or still a placeholder, and its log says which.

The repo isn't cloned on the server. The server gets the image from GHCR and two files from this folder,
[compose.yaml](compose.yaml) and [.env.example](.env.example), pasted into the OMV Compose stack.

Steps marked **(you)** need the owner: a browser, a dashboard or a password. An agent can do everything else, and can
prepare each (you) step and verify it afterwards.

## First-time setup

1. **Image:**
   - Push or merge into `main`. The workflow [image.yml](../.github/workflows/image.yml) runs the tests, then pushes
     `ghcr.io/selvalogesh/personal-llm-assistant:latest`. Actions → Image → "Run workflow" rebuilds by hand.
   - The package is **private**. The server's root is already logged in to `ghcr.io` for finance-analysis, with a
     classic token that has only `read:packages`; that login reads every private package of the account, so
     nothing new is needed.
   - Check: `docker manifest inspect ghcr.io/selvalogesh/personal-llm-assistant:latest` works on the server. If it
     says `denied`, log in again as in finance-analysis's runbook (step 1).
2. **Stack:**
   - Add `CHAT=/StorageDrive/Chat` to OMV's global environment (Compose → Files → Global environment), next to
     `FINANCE`.
   - In OMV, go to Services → Compose → Files and add a stack named **Chat**. Its file is
     [compose.yaml](compose.yaml), and its environment is [.env.example](.env.example) with the values filled in.
     Pick a `CHAT_UI_PORT` nothing else uses (finance's UI is on 8010). OMV commits both to its compose git repo,
     so no passwords go there.
   - Run **Up** once. `init` creates `/StorageDrive/Chat` with its folders and secrets, and `webui` then stops with
     "still holds the placeholder". That's expected until step 3.
3. **Secrets (you):** as root, edit each file in place, so it keeps its mode. Never paste a key or password into a
   chat: these files are the only place they live on the server.
   - `/StorageDrive/Chat/secrets/openrouter-api-key`: the **chat-server** key from your password manager (on the
     laptop it's also in the `OPEN_ROUTER_SERVER` user variable), alone on one line.
   - `/StorageDrive/Chat/secrets/webui-admin-password`: a strong password for the admin account, alone on one
     line. Save it in your password manager too.
   - Run **Up** again. On this first real start Open WebUI creates the admin account (`CHAT_ADMIN_EMAIL` with this
     password), so nobody can claim admin by signing up first.
   - Check: the webui container's log (OMV Compose → Files → Chat → Logs) shows `[load_functions] first start:
     sign-ups on, new accounts wait for approval`, four functions installed, and `[load_functions] done`.
4. **Address (you):** point `openai.selvalogesh.com` at `CHAT_UI_PORT` on this host with your proxy or tunnel. It
   must pass WebSockets (live replies and call mode use them); with Nginx, also allow uploads of 50 MB or more
   (`client_max_body_size`).
5. **Sign in (you):** open https://openai.selvalogesh.com and sign in with `CHAT_ADMIN_EMAIL` and the password.
   Then remove the server key from the laptop, so it lives in one place only:
   `[Environment]::SetEnvironmentVariable("OPEN_ROUTER_SERVER", $null, "User")`.

**Done when:**
- the model picker shows exactly **Auto, Creative, Smart, Free**, and a chat on Auto answers, with a status line
  under the reply naming the model, tokens and cost;
- the chat input's toggles work: **Web search** (an answer about today's news with sources), **Create image** (an
  image in the reply), **Voice reply** (a voice-note player under the reply that plays on your phone);
- the microphone dictates a message, and call mode answers aloud (the first dictation downloads Whisper's model,
  about a minute);
- an uploaded PDF can be asked about;
- a test sign-up from another browser shows "pending" until you approve it in Admin → Users;
- `docker ps` shows the kokoro container with no published port.

## Day to day

- **New people** sign up at the address and wait as pending. Approve them in Admin → Users (role "user").
- **Models:** Admin → Functions → Router → Valves holds each option's chain (comma-separated OpenRouter model ids,
  up to any length; they're asked in groups of three), the image models, the task model and the reply cap. Changes
  apply at once and survive updates.
- **Spending:** every request carries the person's Open WebUI user id, so OpenRouter's Activity page can split
  usage by person. The workspace budget ($10 a month) is the hard cap; when it or the credits run out, the Router
  answers from the Free chain and says so under the reply.
- **Updating the code:**
  - Push or merge into `main`, and the workflow pushes a new `:latest`.
  - Then in OMV Compose → Files, run **Pull** and then **Up** on the Chat stack. On start the functions update
    themselves; Valves you changed are kept.
  - If Pull answers `denied`, the GHCR token was revoked or expired: log in again (step 1).
  - A change to `compose.yaml` or `.env.example` must also be copied into the stack.
- **Upgrading Open WebUI:** change the version in [Dockerfile](Dockerfile), run the tests and a local stack check
  (ENVIRONMENT.md), then push. Check the "Done when" list after the server's Pull and Up.
- **Rotating the OpenRouter key:** create the new key on OpenRouter, replace `secrets/openrouter-api-key`, then
  delete the old key on OpenRouter. The Router reads the file on every message. Document search keeps its own copy
  (Open WebUI saved it on the first start), so also paste the new key in Admin → Settings → Documents → API key.
- **Changing the admin password:** change it in the app (Settings → Account) **and** in
  `secrets/webui-admin-password`. The startup loader signs in with the file's password; if they differ, the log
  shows the functions failing to update until both match.
- **Backups:** `data/` holds everything people made (accounts, chats, files). Include `/StorageDrive/Chat` in the
  server's backup; `secrets/` can be recreated, but keep the admin password in your password manager.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| webui keeps restarting; log says "missing" or "placeholder" | Step 3: fill in that file, then Up |
| A reply says the key was rejected | `secrets/openrouter-api-key` is wrong or the key was deleted on OpenRouter |
| Replies end with "free fallback" in the status line | Credits or the monthly budget ran out: top up on OpenRouter, or wait for the reset |
| Replies say "logging provider" | No private provider could answer that chain, so a logging one did (R6 allows this as a last resort) |
| "Voice note unavailable right now" | The kokoro container is down or still starting: check it in OMV |
| `[load_functions] … FAILED` in the log | Usually the admin password differs between the app and the file (see above) |
