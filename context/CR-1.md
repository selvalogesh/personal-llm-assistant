# CR-1: Family chat service on the home server

A private chat service at openai.selvalogesh.com for the owner, family and friends. It runs Open WebUI on the OMV
home server, sends model traffic to OpenRouter through a routing pipe function, and handles voice locally. This
file records the requirements, the proposed design and the software it uses. **State: requirements agreed; design
proposed and waiting for the owner's review. Nothing has been built yet.**

## 1. Requirements

| # | Requirement | Comes from |
|---|---|---|
| R1 | Chat service at **openai.selvalogesh.com**, running Open WebUI on the OMV home server | Owner |
| R2 | Users: the owner, family and friends. All adults, all with the same access to every option | Owner |
| R3 | Accounts: anyone with the link can sign up. New accounts stay pending until the owner approves them | Owner |
| R4 | Uses: general Q&A, coding, creative writing and roleplay, documents, web search, images | Owner |
| R5 | **Top priority: accept every request; don't refuse** | Owner |
| R6 | Privacy: providers that don't log or train on prompts come first. Logging providers are allowed only as a last resort, when R5 needs them | Owner (R5 wins a conflict) |
| R7 | Budget: **$10/month for everyone combined**, the existing OpenRouter workspace budget | Owner |
| R8 | Web search through OpenRouter's web plugin | Owner |
| R9 | Images: understand uploaded images (vision), generate images with image models, and draw images as SVG or Python code | Owner |
| R10 | Documents: embeddings for document search go through OpenRouter, not the server | Owner |
| R11 | Routing: a new Open WebUI pipe function. `openrouter-routing-stack` is not reused | Owner |
| R12 | Deployment like finance-analysis CR-5 §7: private repo; GitHub Actions builds a private GHCR image (linux/amd64); OMV Compose stack; updates are a manual Pull, then Up | Owner |
| R13 | Secrets (the OpenRouter key and others) come from read-only files in `/StorageDrive/Chat/secrets`, never from the compose environment, the same way as finance. Ports are configurable. The owner handles the reverse proxy, TLS and DNS | Owner (first the compose env; changed to files, D5) |
| R14 | Only Open WebUI's port is published; every other service stays internal to the stack | Owner |
| R15 | Voice, English only: speech-to-text with **local Whisper**, text-to-speech with **local Kokoro**, both on the server | Owner |
| R16 | Voice modes: dictate a message; hands-free call mode; **voice-note replies**, where the reply arrives as an audio clip to tap and play, like WhatsApp | Owner |

**Hardware** (from finance CR-5): OMV, Intel N100 (4 cores), 16 GB RAM (about 11 GB free), Docker. ownCloud and
finance already run there.

## 2. Tensions the design has to handle

| Between | Problem | How the design handles it |
|---|---|---|
| R5 and R7 | Images, paid searches and long roleplay will likely use up the $10 before the month ends | When OpenRouter answers 402 (out of credits), the pipe switches to free models. Per-user usage is visible on OpenRouter (§3.1) |
| R5 and R6 | Few permissive models are served by providers that don't log | Each turn first asks only for non-logging providers. If none can serve the chain, it asks again with logging allowed (§3.1) |
| R6 and R10 | Documents leave the server in chunks to be embedded | Accepted by the owner. Embedding requests ask for non-logging providers where Open WebUI allows it (open item O3) |
| R1 | A hostname containing "openai" can be flagged by browser phishing filters | Accepted by the owner |
| R5 | No setup can guarantee zero refusals | Low-refusal models are first in every default chain. There's no refusal detector (decision D3) |
| R15 and the N100 | Whisper and Kokoro share 4 cores with ownCloud and finance | Small English models. Expect a few seconds per voice turn, and longer when several people use voice at once |

## 3. Proposed design

```
 openai.selvalogesh.com ─▶ (owner's proxy / tunnel) ─▶ host :CHAT_UI_PORT
                                                              │
 OMV Compose stack "Chat"                                     ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ webui: ghcr.io/selvalogesh/personal-llm-assistant (Open WebUI, pinned)     │
 │  ├─ accounts (pending approval), chats, files      → ${CHAT}/data          │
 │  ├─ pipe "Router": chains → OpenRouter chat API    ─────────────┐          │
 │  ├─ tool "Generate image" → OpenRouter image models ────────────┤          │
 │  ├─ filter "Voice note": reply → Kokoro → audio clip            │          │
 │  ├─ Whisper (built in, local) for dictation and call mode       │          │
 │  ├─ document search: embeddings via OpenRouter ─────────────────┤          │
 │  └─ code runner (in the browser) and SVG rendering for drawn images        │
 │                                                                 │          │
 │ kokoro: Kokoro-FastAPI (CPU), internal only ◀── speech          │          │
 └─────────────────────────────────────────────────────────────────┼──────────┘
                                                                   ▼
                                     OpenRouter: chat, web plugin, images, embeddings
                                     ($10/month workspace budget)
```

### 3.1 Routing pipe ("Router")
- A manifold pipe. It shows a few options in the model picker. Each is a **chain** of OpenRouter models defined in
  the pipe's Valves (JSON), editable in Admin → Functions with no rebuild:

| Option | Chain (example; slugs are checked against OpenRouter's model list before release) | For |
|---|---|---|
| **Auto** (default) | DeepSeek V4.1 Flash → GLM-5.3 → a third lab → a `:free` model | Everyday chat, coding, writing |
| **Creative** | Permissive open models suited to fiction and roleplay | Long fiction and roleplay |
| **Smart** | Stronger frontier models (stricter, cost more) | When quality matters more than cost; picked on purpose |
| **Free** | `:free` models only | Also the automatic fallback when credits run out |

- **One request per turn:** `models: [chain]` with `provider.allow_fallbacks`. OpenRouter fails over on outages,
  rate limits, context-length errors and moderation flags. Replies stream token by token.
- **Privacy order (R6):** the first request sets `provider.data_collection: "deny"`. If no provider can serve the
  chain under that rule, the pipe repeats the request with `"allow"`, and the reply's footer says so.
- **Vision (R9):** if a message carries images, the pipe uses the chain's vision-capable models.
- **Web search (R8):** when the user turns on web search, the pipe adds OpenRouter's web plugin with a cheap engine
  (`parallel`, $0.001–0.005 per search, against $0.007 for the default Exa).
- **Out of credits:** a 402 switches the turn to the Free chain.
- **Never a raw error:** if everything fails, the chat shows a short message with the error type and what to do.
  Details go to the container log.
- **Footer per reply:** the model that answered, plus tokens and cost.
- **Per-user usage:** each request carries the Open WebUI user id in OpenRouter's `user` field, so usage can be
  split by person on OpenRouter.
- **`max_tokens` cap** in the Valves, which avoids "can only afford N tokens" failures.
- Open WebUI's direct OpenRouter model list (300+ models) is hidden from users, so only the pipe's options appear
  and the budget stays predictable.

### 3.2 Images (R9)
- **Understanding:** vision chains in the pipe (§3.1).
- **Generating:** a Tool, "Generate image", that calls OpenRouter's image models, cheapest first, and attaches the
  result to the chat. Images are billed per image.
- **Drawn as code:** built into Open WebUI. SVG and HTML in a reply render in the chat, and Python (for example,
  matplotlib) runs in the browser's code runner. No extra cost beyond the chat tokens.

### 3.3 Documents (R10)
- Open WebUI's built-in document search, with embeddings from OpenRouter (an OpenAI-compatible embeddings
  endpoint). Vectors are stored in the bundled ChromaDB on the server.

### 3.4 Voice (R15, R16)
- **Dictation and call mode:** Open WebUI's built-in Whisper (`base.en`, upgradeable to `small.en`), running in the
  webui container. The model is cached in the data folder.
- **Speech output:** Kokoro-FastAPI (CPU image) in the stack, internal only. Open WebUI's text-to-speech uses it
  as an OpenAI-compatible endpoint (`http://kokoro:8880/v1`).
- **Voice-note replies:** a Filter function, "Voice note", that each user can switch on. After a reply finishes,
  it sends the text to Kokoro and attaches the audio file to the message as a playable clip.

### 3.5 Image and startup
- `deploy/Dockerfile`: `FROM` Open WebUI at a pinned version, plus the functions (pipe, tool, filter) and an
  entrypoint script.
- **Entrypoint:**
  - reads the secrets (OpenRouter key, Open WebUI secret key, admin API key) from the files in the read-only
    `secrets/` mount, via `*_FILE` paths set in `compose.yaml`. It exports them only to the Open WebUI process,
    and stops with a clear error if a file is missing or still a placeholder;
  - starts Open WebUI;
  - after `/health` passes, **upserts** the functions through Open WebUI's API. Only the code is updated, so
    Valves changed in the UI are kept.
- Settings like the OpenRouter base URL, the Kokoro URL, the Whisper model and pending sign-ups are default
  environment values in the image or the stack.

### 3.6 Deployment (finance CR-5 §7 pattern)

| Item | Detail |
|---|---|
| `.github/workflows/image.yml` | On push to `main` and on manual dispatch, one run at a time: pytest, then build `deploy/Dockerfile` for `linux/amd64`, then push `:latest` and `:sha-<short>` to GHCR (private). No GHA cache (finance lesson) |
| `deploy/compose.yaml` | OMV stack **"Chat"**: `webui` (published on `${CHAT_UI_PORT}`) and `kokoro` (internal only). `${CHAT}` = `/StorageDrive/Chat` in OMV's global env holds `data/` and `secrets/` (read-only). No defaults: every variable is required |
| `deploy/.env.example` | Settings only (port, `WEBUI_URL`, model sizes). Never passwords |
| `deploy/README.md` | Runbook: GHCR login check (already set up for finance); folders and secrets; first Up; create the admin account, then the admin API key; settings; owner's proxy step; "Done when"; updating |
| Secrets (`/StorageDrive/Chat/secrets`, folder 700, files 600, mounted read-only) | `openrouter-api-key` (the `chat-server` key), `webui-secret-key`, `webui-admin-api-key`. The stack environment carries settings only |
| Tests | Offline pytest of the pipe, tool and filter with a fake HTTP layer |

## 4. Software

| Software | Version | Role | Runs | Cost |
|---|---|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui) | v0.12.5 (pinned) | Chat UI, accounts with approval, chat history, document search, voice UI, call mode, functions | `webui` container | Free |
| Router pipe, image tool, voice-note filter | ours | Routing, image generation, audio replies | Inside Open WebUI | Free |
| faster-whisper (bundled in Open WebUI) | bundled | Speech-to-text, `base.en` | `webui` container, CPU | Free |
| [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | v0.9.2, CPU image | Text-to-speech, OpenAI-compatible | `kokoro` container, internal | Free |
| ChromaDB (bundled) | bundled | Vector store for documents | `webui` container | Free |
| SQLite (bundled) | bundled | Users, chats, settings | `${CHAT}/data` | Free |
| Pyodide code runner (bundled) | bundled | Runs Python for drawn images and charts | In the user's browser | Free |
| [OpenRouter](https://openrouter.ai) | API | Chat models, web plugin, image models, embeddings | Cloud | Paid, $10/month cap |
| Docker + OMV Compose | server's | Runs the stack | Home server | Free |
| GitHub Actions + GHCR | n/a | Tests, builds and stores the private image | GitHub | Free tier |
| pytest | latest | Offline tests | CI and laptop | Free |

## 5. Open items to verify in the build

| # | Item |
|---|---|
| O1 | Open WebUI's function API (create and update by id) and admin API keys on v0.12.5, for the startup upsert |
| O2 | How a pipe sees the user's web-search toggle, so it can add the web plugin |
| O3 | Whether Open WebUI's embedding settings can pass `provider.data_collection` to OpenRouter. If not, embeddings use default routing |
| O4 | The request and response shape of OpenRouter's image API, for the tool |
| O5 | Whether a Filter outlet can attach a generated audio file to the reply as a playable clip (R16). Fallback: auto-play the reply through Kokoro |
| O6 | Current model slugs and prices for each chain, and which are vision-capable |

## 6. Build order

Each phase ends in a commit (`CR-1: …`) on branch `story/main/CR-1-chat-service`, which is fast-forward merged into
`main` once phase 5 is green.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Repo** | `git init`; first commit on `main` holds `context/` only. Adds `CLAUDE.md` (guardrails and CR conventions, like finance), `.gitignore`, `.gitattributes` (`*.sh` LF), `.dockerignore` (allowlist). Then `gh repo create selvalogesh/personal-llm-assistant --private` | Not started |
| 1 | **Spike: verify O1–O6** on the laptop | Open WebUI v0.12.5 and Kokoro run in Docker with the laptop key. The function API, the web-search flag in a pipe's body, embedding options, the image API shape, audio on a filter outlet, and current model slugs are tested. Results are recorded in §5, and the design is adjusted where needed. Throwaway: nothing from the spike is committed except the findings | Not started |
| 2 | **Router pipe** | `functions/router.py` (chains in Valves, privacy order, 402 → Free, vision chain, web plugin, footer, friendly errors, `user` field) plus `tests/test_router.py` with a fake HTTP layer | Not started |
| 3 | **Image tool, voice-note filter** | `functions/image_tool.py` and `functions/voice_note.py`, plus tests | Not started |
| 4 | **Image** | `deploy/Dockerfile` (`FROM` Open WebUI v0.12.5, settings as env defaults), `deploy/entrypoint.sh` (reads secrets files, starts Open WebUI, upserts functions), `deploy/load_functions.py`. Checked with a local build and run | Not started |
| 5 | **CI** | `.github/workflows/image.yml` (pytest, build linux/amd64, push `:latest` and `:sha-…`). First run green; package private | Not started |
| 6 | **Deploy files** | `deploy/compose.yaml` (`init` one-shot like finance's: folders, placeholder secrets, owners and modes; `webui`; `kokoro` internal), `deploy/.env.example`, `deploy/README.md` (runbook with "Done when"), `ENVIRONMENT.md` | Not started |
| 7 | **Server setup (owner)** | Owner follows the runbook: global env `CHAT`, stack "Chat", secrets, first Up, admin account and API key, proxy for openai.selvalogesh.com | Not started |

**Open WebUI settings, set as env defaults in the image** (Open WebUI saves them to its database on first start;
later changes go through Admin → Settings):

| Setting | Value | For |
|---|---|---|
| `ENABLE_SIGNUP` / `DEFAULT_USER_ROLE` | `true` / `pending` | R3 |
| `ENABLE_OPENAI_API` / `ENABLE_OLLAMA_API` | `false` / `false` | D6: only the pipe's options appear |
| `RAG_EMBEDDING_ENGINE`, `RAG_OPENAI_API_BASE_URL`, `RAG_EMBEDDING_MODEL` | `openai`, OpenRouter, a current embedding model | R10 |
| `AUDIO_STT_ENGINE`, `WHISPER_MODEL` | local (empty), `base.en` | R15 |
| `AUDIO_TTS_ENGINE`, `AUDIO_TTS_OPENAI_API_BASE_URL`, `AUDIO_TTS_VOICE` | `openai`, `http://kokoro:8880/v1`, a Kokoro English voice | R15 |
| `ENABLE_CODE_INTERPRETER` | `true` (Pyodide, in the browser) | R9 drawn images |

**Before phase 1:** the OpenRouter balance must be above $0. The spike makes a few paid calls, well under $0.10.

## 7. Verification

| Check | How |
|---|---|
| Pipe, tool and filter logic | `pytest` offline: chain selection, deny → allow retry, 402 → Free, vision switch, web plugin, all-fail message, footer |
| Secrets handling | Container stops with a clear error when a secrets file is missing or a placeholder; keys never appear in `docker inspect` env or logs |
| Image works | Local `docker run` of the built image: `/health` OK, functions installed, only Auto/Creative/Smart/Free in the picker, a streamed reply, a reply with web search |
| Voice | Dictation transcribes; call mode answers aloud; a voice-note reply plays |
| Images and documents | A photo is described; an image is generated; an SVG and a matplotlib chart render; an uploaded PDF is searchable |
| Accounts | A new sign-up waits as pending until approved |
| CI and GHCR | Workflow green; package private with `latest` and `sha-…` |
| Server (owner) | The runbook's "Done when" list |

## 8. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Routing inside Open WebUI as a pipe function, written new | Owner's choice (R11) |
| D2 | Server-side failover (`models[]`) per turn; the pipe adds only privacy order, credits fallback, vision, search and friendly errors | One request, real streaming, billed only for the model that answers |
| D3 | No refusal detector and no rerouting on refusal. Low-refusal models go first in the default chains | A detector has to buffer the reply (no streaming), every refused attempt is billed, and heuristics misfire on fiction. It is outside this project's scope |
| D4 | Speech-to-text and text-to-speech run locally (Whisper, Kokoro) | Owner's choice (R15): free and private |
| D5 | Secrets only from read-only files in `/StorageDrive/Chat/secrets` (each file mode 600, holding only its value), never from the stack environment. The same as finance | OMV commits the stack environment to git (finance decision 32). Owner's choice |
| D6 | Users see only the pipe's options | Predictable cost within $10 shared |
