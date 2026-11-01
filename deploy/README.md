# Server runbook (OMV Compose stack "Chat", official Open WebUI image)

```
push to main ─▶ GitHub Actions: tests ─▶ pass: branch "stable" moves to that commit
                                                    │ fetched by init on every Up (functions/ and deploy/ only)
openai.selvalogesh.com ─▶ your proxy / tunnel ─▶ host :CHAT_UI_PORT ─▶ webui container (OMV stack "Chat")
                                                                         │ kokoro (speech, internal only)
                                                                         ▼
                                                                    OpenRouter

/StorageDrive/Chat/            (CHAT in OMV's global.env)
├── app/       our files at the deployed commit (init replaces them on every Up; read-only in webui)
│   └── DEPLOYED   the commit, the ref and the time of the fetch
├── data/      Open WebUI: users, chats, files, document vectors, the Whisper model cache;
│              router/router.sqlite: the Router's voice modes and classifier misses (CR-4)
└── secrets/   chmod 700, read-only in webui; each file 600 and holding only its value
    ├── openrouter-api-key     the chat-server OpenRouter key                 (you)
    ├── webui-admin-password   the admin account's password                   (you)
    └── webui-secret-key       random session key; init generates it
```

Both Open WebUI services run Open WebUI's official image, `ghcr.io/open-webui/open-webui`, unmodified (CR-2). The
stack's one-shot `init` service runs first on every Up, as root:
- it fetches `functions/` and `deploy/` from this repo at `CHAT_REF` (normally `stable`) into `app/`. If GitHub
  can't be reached, it keeps the copy from the last Up;
- then it runs `deploy/init.sh` from that copy, which creates missing folders, placeholder secrets and the session
  key, and sets modes. It never overwrites a file.

`webui` starts from `app/deploy/entrypoint.sh`. It refuses to start while a secret is missing or still a
placeholder, or when its Open WebUI version differs from the one the repo's `compose.yaml` pins. Its log says
which.

The repo isn't cloned on the server, and there's no image of our own. The server gets Open WebUI's image from Open
WebUI, our files from GitHub, and two files from this folder, [compose.yaml](compose.yaml) and
[.env.example](.env.example), pasted into the OMV Compose stack.

Steps marked **(you)** need the owner: a browser, a dashboard or a password. An agent can do everything else, and can
prepare each (you) step and verify it afterwards.

## First-time setup

1. **Code:** push or merge into `main`. The workflow [ci.yml](../.github/workflows/ci.yml) runs the tests, then
   moves the branch `stable` to that commit. Check: `stable` exists on GitHub and points at the newest commit of
   `main`.
2. **Stack:**
   - Add `CHAT=/StorageDrive/Chat` to OMV's global environment (Compose → Files → Global environment), next to
     `FINANCE`.
   - In OMV, go to Services → Compose → Files and add a stack named **Chat**. Its file is
     [compose.yaml](compose.yaml), and its environment is [.env.example](.env.example) with the values filled in
     (`CHAT_REF=stable`). Pick a `CHAT_UI_PORT` nothing else uses (finance's UI is on 8010). OMV commits both to its
     compose git repo, so no passwords go there.
   - Run **Up** once. `init` fetches our files and creates `/StorageDrive/Chat` with its folders and secrets, and
     `webui` then stops with "still holds the placeholder". That's expected until step 3.
3. **Secrets (you):** as root, edit each file in place, so it keeps its mode. Never paste a key or password into a
   chat: these files are the only place they live on the server.
   - `/StorageDrive/Chat/secrets/openrouter-api-key`: the **chat-server** key from your password manager (on the
     laptop it's also in the `OPEN_ROUTER_SERVER` user variable), alone on one line.
   - `/StorageDrive/Chat/secrets/webui-admin-password`: a strong password for the admin account, alone on one
     line. Save it in your password manager too.
   - Run **Down**, then **Up**. On this first real start Open WebUI creates the admin account (`CHAT_ADMIN_EMAIL`
     with this password), so nobody can claim admin by signing up first.
   - Check: the webui container's log (OMV Compose → Files → Chat → Logs) shows `entrypoint: running <commit>
     (stable), Open WebUI <version>`, `[load_functions] first start: sign-ups on, new accounts wait for approval`,
     two functions installed (`router`, `voice_note`), and `[load_functions] done`. The first start also downloads the document embedding
     model (about 380 MB, into `data/cache`), so document search is ready a minute or two later.
4. **Address (you):** point `openai.selvalogesh.com` at `CHAT_UI_PORT` on this host with your proxy or tunnel. It
   must pass WebSockets (live replies and call mode use them); with Nginx, also allow uploads of 50 MB or more
   (`client_max_body_size`).
5. **Sign in (you):** open https://openai.selvalogesh.com and sign in with `CHAT_ADMIN_EMAIL` and the password.
   Then remove the server key from the laptop, so it lives in one place only:
   `[Environment]::SetEnvironmentVariable("OPEN_ROUTER_SERVER", $null, "User")`.

**Done when:**
- `docker ps` shows `ghcr.io/open-webui/open-webui:<version>` for webui, and the kokoro container with no
  published port;
- the model picker shows exactly **Auto, Creative, Smart, Free**, and a chat on Auto answers, with a status line
  under the reply naming the model, tokens and cost;
- the chat input has **no Integrations button**, and each format comes by itself (CR-4): "draw a lighthouse at
  dusk" gives an image, and "same, but in winter" a matching one; "what's in today's news?" answers with sources;
  "plot sin(x) from 0 to 10" runs Python and shows the chart; "reply with a voice note" puts a player under that
  reply that plays on your phone; "from now on, voice notes" voices every reply until "stop the voice notes". The
  status line under each says what was chosen, e.g. `auto: web search`;
- the microphone dictates a message, and call mode answers aloud (the first dictation downloads Whisper's model,
  about a minute);
- an uploaded PDF can be asked about;
- a test sign-up from another browser shows "pending" until you approve it in Admin → Users.

## Day to day

- **New people** sign up at the address and wait as pending. Approve them in Admin → Users (role "user").
- **Models:** Admin → Functions → Router → Valves holds each option's chain (comma-separated OpenRouter model ids,
  up to any length; they're asked in groups of three), the image models, the task model, the classifier's model
  and the thinking time limit. Changes apply at once and survive updates.
- **Wrong formats** (an image when you wanted words, no search when it needed one): say so in the next message
  ("no, I wanted a picture"). That reply comes in the right format, and the miss becomes an example for that
  person's later messages; so does a format the model chose itself with a tool. To see or delete misses, run in the
  webui container (`docker ps` names it):
  - `docker exec <webui> python /app/chat/deploy/detections.py list` (add `--user EMAIL` for one person);
  - `… detections.py delete ID`, or `… detections.py clear --user EMAIL` (or `--all`).

  A thumbs-down is listed too, but never learned from. Good misses can become seed examples for everyone in
  `router.py` (CR-4 §3.7).
- **Spending:** every request carries the person's Open WebUI user id, so OpenRouter's Activity page can split
  usage by person. The workspace budget ($10 a month) is the hard cap; when it or the credits run out, the Router
  answers from the Free chain and says so under the reply.
- **Updating the code** (functions, startup scripts, `settings.env`):
  - Push or merge into `main`, and wait for CI to pass: it moves `stable`.
  - Then in OMV Compose → Files, run **Down** and then **Up** on the Chat stack. A plain Up re-fetches but leaves
    the running webui alone, so the new code wouldn't load. On start the functions update themselves; Valves you
    changed are kept.
  - A change to `compose.yaml` or `.env.example` must also be copied into the stack.
- **Upgrading Open WebUI:** Dependabot opens a pull request when a new version comes out (or change both tags in
  [compose.yaml](compose.yaml) by hand). Run the tests and a local stack check (ENVIRONMENT.md), then merge. Paste
  the new `compose.yaml` into the stack, then run **Pull**, **Down**, **Up**, and check the "Done when" list. If
  you forget the paste, webui refuses to start and its log says so.
- **Rolling back the code:** set `CHAT_REF` in the stack environment to a tag or a **full** commit hash (a short
  hash won't fetch), then **Down**, **Up**. The log line `entrypoint: running …` confirms the commit. Set it back
  to `stable` once the fix is on `main`. This works only within one Open WebUI version: Open WebUI migrates its
  database when it upgrades, so undoing an upgrade means restoring `data/` from the backup with the old tag.
- **Rotating the OpenRouter key:** create the new key on OpenRouter, replace `secrets/openrouter-api-key`, then
  delete the old key on OpenRouter. The Router reads the file on every message; nothing else holds the key (CR-3).
- **Document search runs on the server** with `BAAI/bge-small-en-v1.5` (CR-3); no document text goes to
  OpenRouter. Embedding a long PDF takes the N100 a minute or more. Changing the model later (Admin → Settings →
  Documents) means clicking **Reindex knowledge base vectors** afterwards; until then old documents silently find
  nothing, and files attached straight to a chat must be uploaded again.
- **Changing the admin password:** change it in the app (Settings → Account) **and** in
  `secrets/webui-admin-password`. The startup loader signs in with the file's password; if they differ, the log
  shows the functions failing to update until both match.
- **Backups:** `data/` holds everything people made (accounts, chats, files). Include `/StorageDrive/Chat` in the
  server's backup; `secrets/` can be recreated, but keep the admin password in your password manager. `app/` is
  fetched again on every Up.

## Once: switch the running server to on-server embeddings (CR-3, you)

A server set up before CR-3 has OpenRouter embeddings saved in its database, and the new `settings.env` doesn't
change saved settings. After the CR-3 update (Down, Up), in Admin → Settings → Documents, in this order:

1. With the engine still on OpenAI, **clear the API key** field and save. That deletes Open WebUI's copy of the
   OpenRouter key; switching the engine alone would leave it in the database. Open WebUI saves the key field only
   while the engine is OpenAI, and hides it on Default. If you already switched, set the engine back to OpenAI,
   clear the key, save, then set Default and the model again and save (the model loads from disk, no download).
2. Set the embedding engine to **Default (SentenceTransformers)** and the model to `BAAI/bge-small-en-v1.5`, then
   save. It downloads the model (about 380 MB); wait until the save finishes.
3. Click **Reindex knowledge base vectors**.
4. Tell people that files they attached straight to a chat before today must be uploaded again: those find nothing
   now, without an error.

Check: ask a question about a document in a knowledge base and get an answer from it; Admin → Settings → Documents
shows the engine as Default and no API key.

## Once: turn off the Code Interpreter (CR-4, you)

A server set up before CR-4 has the Code Interpreter saved as on in its database, and the new `settings.env`
doesn't change saved settings. While it's on, the chat input keeps its **Integrations** button (CR-4 O1). After the
CR-4 update (Down, Up):

1. In Admin → Settings → Code Execution, turn **Code Interpreter** off and save. Leave **Code Execution** (the Run
   button on code blocks) as it is.
2. Check: the webui log shows `web_search: removed (retired)` and `image_mode: removed (retired)` (the old toggles);
   Admin → Functions lists only Router and Voice reply; the chat input has no Integrations button, on a phone too.

A fresh install needs none of this.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| webui keeps restarting; log says "missing" or "placeholder" | Step 3: fill in that file, then Down, Up |
| webui keeps restarting; log says "The stack runs Open WebUI X, but the repo … expects Y" | The stack's `compose.yaml` is older or newer than the repo's: paste the repo's, then Pull, Down, Up |
| Up fails; init's log says "GitHub has no …" | `CHAT_REF` is mistyped or a short hash: set it to `stable`, a tag or a full commit hash |
| init's log says "couldn't reach GitHub; keeping …" | GitHub or the network was down: the previous code runs. Down, Up again later to update |
| The new code doesn't show after an update | CI hadn't passed yet (so `stable` hadn't moved), or only Up was run: check `app/DEPLOYED`, then Down, Up |
| Questions about a document get answers that ignore it | The embedding model changed since it was added (the CR-3 switch, or a later model change): Reindex knowledge base vectors, or upload a chat's file again |
| A reply says the key was rejected | `secrets/openrouter-api-key` is wrong or the key was deleted on OpenRouter |
| Replies end with "free fallback" in the status line | Credits or the monthly budget ran out: top up on OpenRouter, or wait for the reset |
| Replies say "logging provider" | No private provider could answer that chain, so a logging one did (R6 allows this as a last resort) |
| "Voice note unavailable right now" | The kokoro container is down or still starting: check it in OMV |
| The Integrations button is still there | The Code Interpreter is still on: "Once: turn off the Code Interpreter" above |
| A reply ignores "draw…" or "search…" | The classifier failed or took over 4 s, so the turn was plain chat (the log says `classifier failed`). Ask again, or say "generate an image of…" |
| "Running the code…" and then an answer without results | The browser couldn't run the Python (tab closed, or Pyodide still loading on a slow phone): ask again with the chat open |
| `[load_functions] … FAILED` in the log | Usually the admin password differs between the app and the file (see above) |
