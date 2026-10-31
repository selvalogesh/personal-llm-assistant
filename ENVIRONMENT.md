# Settings, secrets and local development

On the server, the OMV Compose stack "Chat" runs [deploy/compose.yaml](deploy/compose.yaml) with the stack
environment from [deploy/.env.example](deploy/.env.example). OMV commits that environment to git, so it carries
settings only; secrets are files. The runbook is [deploy/README.md](deploy/README.md).

## Stack environment (settings only)

| Variable | Example | Purpose |
|---|---|---|
| `CHAT` | `/StorageDrive/Chat` (OMV's global env) | Host folder holding `app/`, `data/` and `secrets/` |
| `CHAT_REF` | `stable` | What `init` fetches on every Up: `stable` (the newest commit whose tests passed), or a tag or full commit hash to roll back |
| `CHAT_UI_PORT` | `127.0.0.1:8020` or `8020` | Host port the proxy or tunnel points at; with an address, it binds only there |
| `CHAT_WEBUI_URL` | `https://openai.selvalogesh.com` | The public address |
| `CHAT_ADMIN_EMAIL` | your email | The admin account created on the first start |
| `CHAT_WHISPER_MODEL` | `base.en` | Speech-to-text model: `base.en` (fast) or `small.en` (more accurate) |

## Secrets files (`/StorageDrive/Chat/secrets/`, mounted read-only at `/run/chat-secrets`)

Each file is mode 600 and holds only its value. `init` creates the folder and placeholders and never overwrites.

| File | What | Read by |
|---|---|---|
| `openrouter-api-key` | The **chat-server** OpenRouter key | The Router only, on every message (since CR-3; the entrypoint checks it isn't a placeholder) |
| `webui-admin-password` | The admin account's password | Open WebUI (creates the admin on the first start); the function loader (signs in on every start) |
| `webui-secret-key` | Random session key (`init` generates it) | Open WebUI; changing it signs everyone out |

[deploy/entrypoint.sh](deploy/entrypoint.sh) reads them into the Open WebUI process only, so they never appear in
the stack environment, the repo or `docker inspect`.

## Open WebUI settings ([deploy/settings.env](deploy/settings.env))

The entrypoint loads this file over the official image's defaults (CR-2). It never sets a variable the stack
environment sets, and a test checks that. Open WebUI saves most of these to its database on the first start; after
that, change them in Admin → Settings.

| Setting | Value | Why |
|---|---|---|
| `ENABLE_OPENAI_API`, `ENABLE_OLLAMA_API` | `false` | Only the Router's options appear (CR-1 D6) |
| `BYPASS_MODEL_ACCESS_CONTROL` | `true` | Pipe models are otherwise admin-only; everyone gets the same access (R2) |
| `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE` | `true`, `pending` | New accounts wait for approval (R3) |
| `ENABLE_WEB_SEARCH`, `ENABLE_IMAGE_GENERATION` | `false` | Both go through the Router's toggles instead |
| `RAG_EMBEDDING_ENGINE`, `RAG_EMBEDDING_MODEL` | local (empty), `BAAI/bge-small-en-v1.5` | Document search on the server; nothing goes to OpenRouter (CR-3) |
| `AUDIO_STT_ENGINE` | local (empty); the model is `CHAT_WHISPER_MODEL` | Dictation and call mode (R15) |
| `AUDIO_TTS_ENGINE`, `AUDIO_TTS_OPENAI_API_BASE_URL`, `AUDIO_TTS_VOICE` | `openai`, `http://kokoro:8880/v1`, `af_heart` | Read aloud and call mode through Kokoro (R15) |
| `ENABLE_CODE_INTERPRETER`, `CODE_INTERPRETER_ENGINE` | `true`, `pyodide` | Python charts and drawings run in the browser (R9) |

## Router Valves (Admin → Functions → Router)

| Valve | Default | Purpose |
|---|---|---|
| `AUTO_MODELS`, `CREATIVE_MODELS`, `SMART_MODELS`, `FREE_MODELS` | see [CR-1 §3.1](context/CR-1.md) | Each option's chain, comma-separated, asked in groups of three |
| `VISION_MODELS` | DeepSeek V4.1 Flash, Qwen 3.7 Flash, Gemma 4 free | For images when the chosen chain has no vision model |
| `IMAGE_MODELS` | Recraft 4.1 Flash, Seedream 5 Flash, Qwen Image 2.1 Turbo | "Create image", cheapest first |
| `TASK_MODEL` | `qwen/qwen3.7-flash` | Titles, tags and follow-up suggestions |
| `THINKING_LIMIT_SECONDS` | `120` | After this long thinking without an answer, a dialog asks to keep waiting or stop (D13); `0` never asks. There is no token cap |
| `WEB_ENGINE`, `WEB_MAX_RESULTS` | `parallel`, `3` | OpenRouter web plugin settings |
| `SHOW_FOOTER` | `true` | The status line with model, tokens and cost |
| `API_KEY_FILE` | `/run/chat-secrets/openrouter-api-key` | Where the key is read from |

The Voice reply filter has its own Valves: Kokoro's URL, the voice, the speed and `MAX_CHARS` (1,200, about 75 s).

## Local development (laptop)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q        # offline: OpenRouter and Kokoro are faked
```

**Local stack check** before pushing a change to the functions, the startup files or the Open WebUI version (needs
Docker Desktop):

1. Make an env file outside the repo with `CHAT=<a throwaway folder>`, `CHAT_UI_PORT=127.0.0.1:3001`,
   `CHAT_WEBUI_URL=http://localhost:3001`, `CHAT_ADMIN_EMAIL=admin@test.local`, `CHAT_WHISPER_MODEL=base.en`,
   `CHAT_REF=stable`.
2. `docker compose -p chattest --env-file <that file> -f deploy/compose.yaml -f deploy/compose.local.yaml up -d`.
   With [compose.local.yaml](deploy/compose.local.yaml), `init` copies your working tree instead of fetching, so
   uncommitted changes are tested.
3. Fill the two placeholder secrets in the throwaway folder (use the laptop key for testing), then `down` and `up`
   again with the same files.
4. Open http://localhost:3001, then `docker compose -p chattest ... down` and delete the throwaway folder, which
   holds the key.

To test the real fetch, push the branch, leave out `compose.local.yaml`, and set `CHAT_REF` to the branch name.
