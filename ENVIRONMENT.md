# Settings, secrets and local development

On the server, the OMV Compose stack "Chat" runs [deploy/compose.yaml](deploy/compose.yaml) with the stack
environment from [deploy/.env.example](deploy/.env.example). OMV commits that environment to git, so it carries
settings only; secrets are files. The runbook is [deploy/README.md](deploy/README.md).

## Stack environment (settings only)

| Variable | Example | Purpose |
|---|---|---|
| `CHAT` | `/StorageDrive/Chat` (OMV's global env) | Host folder holding `data/` and `secrets/` |
| `CHAT_UI_PORT` | `127.0.0.1:8020` or `8020` | Host port the proxy or tunnel points at; with an address, it binds only there |
| `CHAT_WEBUI_URL` | `https://openai.selvalogesh.com` | The public address |
| `CHAT_ADMIN_EMAIL` | your email | The admin account created on the first start |
| `CHAT_WHISPER_MODEL` | `base.en` | Speech-to-text model: `base.en` (fast) or `small.en` (more accurate) |

## Secrets files (`/StorageDrive/Chat/secrets/`, mounted read-only at `/run/chat-secrets`)

Each file is mode 600 and holds only its value. `init` creates the folder and placeholders and never overwrites.

| File | What | Read by |
|---|---|---|
| `openrouter-api-key` | The **chat-server** OpenRouter key | The Router on every message; document search (copied into Open WebUI's settings on the first start) |
| `webui-admin-password` | The admin account's password | Open WebUI (creates the admin on the first start); the function loader (signs in on every start) |
| `webui-secret-key` | Random session key (`init` generates it) | Open WebUI; changing it signs everyone out |

[deploy/entrypoint.sh](deploy/entrypoint.sh) reads them into the Open WebUI process only, so they never appear in
the image, the stack environment or `docker inspect`.

## Image defaults ([deploy/Dockerfile](deploy/Dockerfile))

Open WebUI saves most of these to its database on the first start; after that, change them in Admin → Settings.

| Setting | Value | Why |
|---|---|---|
| `ENABLE_OPENAI_API`, `ENABLE_OLLAMA_API` | `false` | Only the Router's options appear (CR-1 D6) |
| `BYPASS_MODEL_ACCESS_CONTROL` | `true` | Pipe models are otherwise admin-only; everyone gets the same access (R2) |
| `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE` | `true`, `pending` | New accounts wait for approval (R3) |
| `ENABLE_WEB_SEARCH`, `ENABLE_IMAGE_GENERATION` | `false` | Both go through the Router's toggles instead |
| `RAG_EMBEDDING_ENGINE`, `RAG_OPENAI_API_BASE_URL`, `RAG_EMBEDDING_MODEL` | `openai`, OpenRouter, `voyageai/voyage-4-lite` | Document search via OpenRouter (R10) |
| `AUDIO_STT_ENGINE`, `WHISPER_MODEL` | local, `base.en` | Dictation and call mode (R15) |
| `AUDIO_TTS_ENGINE`, `AUDIO_TTS_OPENAI_API_BASE_URL`, `AUDIO_TTS_VOICE` | `openai`, `http://kokoro:8880/v1`, `af_heart` | Read aloud and call mode through Kokoro (R15) |
| `ENABLE_CODE_INTERPRETER`, `CODE_INTERPRETER_ENGINE` | `true`, `pyodide` | Python charts and drawings run in the browser (R9) |

## Router Valves (Admin → Functions → Router)

| Valve | Default | Purpose |
|---|---|---|
| `AUTO_MODELS`, `CREATIVE_MODELS`, `SMART_MODELS`, `FREE_MODELS` | see [CR-1 §3.1](context/CR-1.md) | Each option's chain, comma-separated, asked in groups of three |
| `VISION_MODELS` | DeepSeek V4.1 Flash, Qwen 3.7 Flash, Gemma 4 free | For images when the chosen chain has no vision model |
| `IMAGE_MODELS` | Recraft 4.1 Flash, Seedream 5 Flash, Qwen Image 2.1 Turbo | "Create image", cheapest first |
| `TASK_MODEL` | `qwen/qwen3.7-flash` | Titles, tags and follow-up suggestions |
| `MAX_TOKENS` | `4096` | Reply cap; avoids "can only afford N tokens" when credits are low |
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

**Local stack check** before pushing a change to the image or the functions (needs Docker Desktop):

1. Build: `docker build -f deploy/Dockerfile -t ghcr.io/selvalogesh/personal-llm-assistant:latest .`
2. Make an env file outside the repo with `CHAT=<a throwaway folder>`, `CHAT_UI_PORT=127.0.0.1:3001`,
   `CHAT_WEBUI_URL=http://localhost:3001`, `CHAT_ADMIN_EMAIL=admin@test.local`, `CHAT_WHISPER_MODEL=base.en`.
3. `docker compose -p chattest --env-file <that file> -f deploy/compose.yaml up -d`, fill the two placeholder
   secrets in the throwaway folder (use the laptop key for testing), and Up again.
4. Open http://localhost:3001, then `docker compose -p chattest ... down` and delete the throwaway folder, which
   holds the key.
