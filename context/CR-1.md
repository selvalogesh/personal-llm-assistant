# CR-1: Family chat service on the home server

A private chat service at openai.selvalogesh.com for the owner, family and friends. It runs Open WebUI on the OMV
home server, sends model traffic to OpenRouter through a routing pipe function, and handles voice locally. This
file records the requirements, the design and the software it uses. **State: built and verified on the laptop;
server setup (§6, phase 7) is the owner's next step.**

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
| R10 | Documents: embeddings for document search go through OpenRouter, not the server. **Superseded by [CR-3](CR-3.md):** embeddings on the server with bge-small-en-v1.5 | Owner |
| R11 | Routing: a new Open WebUI pipe function. `openrouter-routing-stack` is not reused | Owner |
| R12 | Deployment like finance-analysis CR-5 §7: public repo; GitHub Actions builds a public GHCR image (linux/amd64); OMV Compose stack; updates are a manual Pull, then Up. **Superseded by [CR-2](CR-2.md):** the official Open WebUI image, our files fetched from git | Owner |
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
| R6 and R10 | Documents leave the server in chunks to be embedded | Accepted by the owner. Embedding requests ask for non-logging providers where Open WebUI allows it (open item O3). **Resolved by [CR-3](CR-3.md):** documents no longer leave the server |
| R1 | A hostname containing "openai" can be flagged by browser phishing filters | Accepted by the owner |
| R5 | No setup can guarantee zero refusals | Low-refusal models are first in every default chain. There's no refusal detector (decision D3) |
| R15 and the N100 | Whisper and Kokoro share 4 cores with ownCloud and finance | Small English models. Expect a few seconds per voice turn, and longer when several people use voice at once |

## 3. Design

```
 openai.selvalogesh.com ─▶ (owner's proxy / tunnel) ─▶ host :CHAT_UI_PORT
                                                              │
 OMV Compose stack "Chat"                                     ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ webui: ghcr.io/selvalogesh/personal-llm-assistant (Open WebUI, pinned)     │
 │  ├─ accounts (pending approval), chats, files      → ${CHAT}/data          │
 │  ├─ pipe "Router": chains → OpenRouter chat API    ─────────────┐          │
 │  ├─ toggles: Web search, Create image (→ Router), Voice reply   │          │
 │  ├─ Voice reply: reply → Kokoro → voice-note player             │          │
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
- A manifold pipe, [functions/router.py](../functions/router.py). It shows four options in the model picker. Each
  is a **chain** of OpenRouter models in the pipe's Valves (comma-separated), editable in Admin → Functions with no
  rebuild. Defaults, checked against OpenRouter's live list on 2026-10-31:

| Option | Chain | For |
|---|---|---|
| **Auto** (default) | `deepseek/deepseek-v4.1-flash`, `z-ai/glm-5.3`, `qwen/qwen3.7-flash`, `mistralai/mistral-small-2603` | Everyday chat, coding, writing |
| **Creative** | `thedrummer/cydonia-24b-v4.1`, `sao10k/l3.1-euryale-70b`, `deepseek/deepseek-v4.1-flash` | Long fiction and roleplay |
| **Smart** | `anthropic/claude-sonnet-5.5`, `openai/gpt-5.6-terra`, `google/gemini-3.5-flash` | When quality matters more than cost; picked on purpose |
| **Free** | `google/gemma-4-31b-it:free`, `nvidia/nemotron-3.5-super-vl:free`, `nvidia/nemotron-3-ultra-550b-a55b:free` | Also the last resort for every other option |

- **Requests in groups of three:** OpenRouter's `models` takes at most 3 entries (found in the spike), so a chain
  goes in groups of up to 3, each with `provider.allow_fallbacks`. Within a group OpenRouter fails over on
  outages, rate limits, context-length errors and moderation flags. Replies stream token by token, and reasoning
  shows as Open WebUI's collapsible "thinking" block.
- **Privacy order (R6):** each group is asked first with `provider.data_collection: "deny"`. If that fails for any
  reason except the key, credits or budget, the same group is asked again with `"allow"` (R5 wins), then the next
  group. After the chain, the Free chain gets the same treatment. The footer says when a logging provider or the
  free fallback answered.
- **Vision (R9):** if a message carries images, the pipe uses the chain's vision-capable models.
- **Web search (R8):** a toggle in the chat input, the filter [web_search.py](../functions/web_search.py), marks
  the turn, and the pipe adds OpenRouter's web plugin with a cheap engine (`parallel`, $0.001–0.005 per search,
  against $0.007 for the default Exa). Open WebUI's own web search stays off: it would need a search engine of
  its own.
- **Titles, tags and follow-ups:** Open WebUI's background tasks go to one cheap model (`TASK_MODEL`,
  `qwen/qwen3.7-flash`), never to the chosen chain.
- **Out of credits or budget:** the rest of the paid chain is skipped and the Free chain answers.
- **Never a raw error:** if everything fails, the chat shows a short message with the error type and what to do.
  Details go to the container log.
- **Footer per reply:** the model that answered, plus tokens and cost.
- **Per-user usage:** each request carries the Open WebUI user id in OpenRouter's `user` field, so usage can be
  split by person on OpenRouter.
- **A thinking time limit instead of a token cap (D13).** No `max_tokens` is sent (unless a chat sets its own), so
  thinking can't use up the reply. When a model has thought for `THINKING_LIMIT_SECONDS` (2 minutes) without
  starting the answer, an Open WebUI dialog asks whether to keep waiting (asked again every 2 minutes) or stop.
  The thinking keeps streaming meanwhile, and the dialog closes by itself when the answer starts. A reply cut at a
  length limit gets a note under it. When low credits can't cover a model's full output ("can only afford N
  tokens"), the same models are asked again with that many tokens before the Free chain.
- Open WebUI's direct OpenRouter model list (300+ models) is hidden from users, so only the pipe's options appear
  and the budget stays predictable.

### 3.2 Images (R9)
- **Understanding:** vision chains in the pipe (§3.1).
- **Generating:** a "Create image" toggle in the chat input, the filter [image_mode.py](../functions/image_mode.py).
  With it on, the pipe sends the message to OpenRouter's image API (`/images`) with `IMAGE_MODELS`, cheapest
  first: `recraft/recraft-v4.1-flash` ($0.007), `bytedance-seed/seedream-5-0-flash` ($0.018),
  `qwen/qwen-image-2.1-turbo` ($0.018). The image is stored as an Open WebUI file and shown in the reply. This
  replaced the planned Tool: a toggle is deterministic, while a Tool depends on a model deciding to call it.
- **Drawn as code:** built into Open WebUI. SVG and HTML in a reply render in the chat, and Python (for example,
  matplotlib) runs in the browser's code runner. No extra cost beyond the chat tokens.

### 3.3 Documents (R10)

**Superseded by [CR-3](CR-3.md):** embeddings run on the server with `BAAI/bge-small-en-v1.5`.
- Open WebUI's built-in document search, with embeddings from OpenRouter's OpenAI-compatible `/embeddings`
  (`voyageai/voyage-4-lite`, $0.02 per 1M tokens, 1024 dimensions; tested). Vectors are stored in the bundled
  ChromaDB on the server. Open WebUI sends only `model` and `input`, so embeddings use OpenRouter's default
  provider routing (O3).

### 3.4 Voice (R15, R16)
- **Dictation and call mode:** Open WebUI's built-in Whisper (`base.en`, upgradeable to `small.en`), running in the
  webui container. The model is cached in the data folder.
- **Speech output:** Kokoro-FastAPI (CPU image) in the stack, internal only. Open WebUI's text-to-speech uses it
  as an OpenAI-compatible endpoint (`http://kokoro:8880/v1`).
- **Voice-note replies:** a "Voice reply" toggle, the filter [voice_note.py](../functions/voice_note.py). After a
  reply finishes, it strips reasoning, code and markdown, sends up to 1,200 characters (about 75 s) to Kokoro
  (`af_heart`, mp3), and attaches a WhatsApp-style player to the message as an Open WebUI embed. The audio is
  inline (a data URL, about 1 MB a minute), so it plays without another request.

### 3.5 Image and startup

**Superseded by [CR-2](CR-2.md) §3.2–3.3:** no image of our own; the settings moved to `deploy/settings.env`. The
entrypoint below still applies, run from the fetched copy.
- `deploy/Dockerfile`: `FROM` Open WebUI at a pinned version, plus the functions (pipe, tool, filter) and an
  entrypoint script.
- **Entrypoint:**
  - reads the secrets (OpenRouter key, Open WebUI secret key, admin password) from the files in the read-only
    `secrets/` mount. It exports them only to the Open WebUI process, and stops with a clear error if a file is
    missing or still a placeholder. The Router reads the key file itself on every request;
  - starts Open WebUI. On the very first start Open WebUI creates the admin account from `WEBUI_ADMIN_EMAIL` and
    the password file, so nobody can claim admin by signing up first;
  - runs [deploy/load_functions.py](../deploy/load_functions.py) in the background: after `/health` passes it
    signs in as that admin and **upserts** the functions through Open WebUI's API, turns them on and makes the
    filters global. Only the code is updated, so Valves changed in the UI are kept. On the first start only, it
    turns sign-ups back on as pending (Open WebUI turns them off when it creates the admin).
- Settings like the OpenRouter base URL, the Kokoro URL, the Whisper model and pending sign-ups are default
  environment values in the image or the stack.

### 3.6 Deployment (finance CR-5 §7 pattern)

**Superseded by [CR-2](CR-2.md) §3.4–3.5:** CI tests and moves the branch `stable` instead of pushing an image.

| Item | Detail |
|---|---|
| `.github/workflows/image.yml` | On push to `main` and on manual dispatch, one run at a time: pytest, then build `deploy/Dockerfile` for `linux/amd64`, then push `:latest` and `:sha-<short>` to GHCR (public). No GHA cache (finance lesson) |
| `deploy/compose.yaml` | OMV stack **"Chat"**: `webui` (published on `${CHAT_UI_PORT}`) and `kokoro` (internal only). `${CHAT}` = `/StorageDrive/Chat` in OMV's global env holds `data/` and `secrets/` (read-only). No defaults: every variable is required |
| `deploy/.env.example` | Settings only (port, `WEBUI_URL`, model sizes). Never passwords |
| `deploy/README.md` | Runbook: GHCR login check (already set up for finance); folders and secrets; first Up; sign in as the admin; owner's proxy step; "Done when"; updating |
| Secrets (`/StorageDrive/Chat/secrets`, folder 700, files 600, mounted read-only) | `openrouter-api-key` (the `chat-server` key), `webui-secret-key`, `webui-admin-password`. The stack environment carries settings only |
| Tests | Offline pytest of the pipe and filters with a fake HTTP layer (`httpx.MockTransport`) |

## 4. Software

| Software | Version | Role | Runs | Cost |
|---|---|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui) | v0.12.5 (pinned) | Chat UI, accounts with approval, chat history, document search, voice UI, call mode, functions | `webui` container | Free |
| Router pipe; Web search, Create image and Voice reply toggles | ours | Routing, web search, image generation, voice notes | Inside Open WebUI | Free |
| faster-whisper (bundled in Open WebUI) | bundled | Speech-to-text, `base.en` | `webui` container, CPU | Free |
| [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | v0.9.2, CPU image | Text-to-speech, OpenAI-compatible | `kokoro` container, internal | Free |
| ChromaDB (bundled) | bundled | Vector store for documents | `webui` container | Free |
| SQLite (bundled) | bundled | Users, chats, settings | `${CHAT}/data` | Free |
| Pyodide code runner (bundled) | bundled | Runs Python for drawn images and charts | In the user's browser | Free |
| [OpenRouter](https://openrouter.ai) | API | Chat models, web plugin, image models, embeddings | Cloud | Paid, $10/month cap |
| Docker + OMV Compose | server's | Runs the stack | Home server | Free |
| GitHub Actions + GHCR | n/a | Tests, builds and stores the public image | GitHub | Free tier |
| pytest | latest | Offline tests | CI and laptop | Free |

## 5. Spike findings (2026-10-31, laptop, Open WebUI v0.12.5 and Kokoro v0.9.2)

| # | Question | Finding |
|---|---|---|
| O1 | Loading functions at startup | **Works.** `/api/v1/functions/create`, `/id/{id}/update`, `/toggle` and `/toggle/global` with an admin token; a second run reports every function unchanged. Better than planned: `WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_PASSWORD` make Open WebUI create the admin itself, so the loader signs in with the password and no admin API key exists. That also turns sign-ups off, so the loader turns them back on (pending) once |
| O2 | The web-search toggle reaching the pipe | **Works.** A toggle filter's inlet sets a flag on the body and the pipe receives it. Tested: a question about a release published two days earlier was answered with sources |
| O3 | Privacy setting on embeddings | **No:** Open WebUI sends only `model` and `input`. Embeddings use default routing (accepted, R10) |
| O4 | OpenRouter's image API | `POST /images` with `model` and `prompt`; the image comes back as `data[0].b64_json` plus `media_type`. **Works** end to end, stored as an Open WebUI file |
| O5 | A voice note on the reply | **Works at the data level:** the outlet's `embeds` event is saved on the message, with the "Recording…"/"ready" status. How the player looks in the browser is checked by the owner (§7) |
| O6 | Model slugs | All chain models are live; vision is detected from OpenRouter's model list at run time |
| — | **OpenRouter limit** | `models` takes at most 3 entries: chains go in groups of 3 (§3.1) |
| — | **Free models** | `:free` models are often rate-limited upstream (429), and the free pool without logging is tiny: the deny → allow retry was needed for the Free chain to answer |
| — | **Model visibility** | Pipe models without a model record are visible to admins only: `BYPASS_MODEL_ACCESS_CONTROL=true` shows them to every user (R2) |
| — | **Voice speed (laptop CPU)** | Kokoro: 6 s of speech in 3 s. Whisper `base.en`: the first call downloads the model (~50 s); it then transcribed Kokoro's audio word for word. The N100 will be slower |

## 6. Build order

Each phase ends in a commit (`CR-1: …`) on branch `story/main/CR-1-chat-service`, which is fast-forward merged into
`main` once phase 5 is green.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Repo** | `git init`; first commit on `main` holds `context/` only. Adds `CLAUDE.md` (guardrails and CR conventions, like finance), `.gitignore`, `.gitattributes` (`*.sh` LF), `.dockerignore` (allowlist). Then `gh repo create selvalogesh/personal-llm-assistant --private` | Done |
| 1 | **Spike: verify O1–O6** on the laptop | Open WebUI v0.12.5 and Kokoro run in Docker with the laptop key. The function API, the web-search flag in a pipe's body, embedding options, the image API shape, audio on a filter outlet, and current model slugs are tested. Results are recorded in §5, and the design is adjusted where needed. Throwaway: nothing from the spike is committed except the findings | Done (§5) |
| 2 | **Router pipe** | `functions/router.py` (chains in Valves, privacy order, 402 → Free, vision chain, web plugin, footer, friendly errors, `user` field) plus `tests/test_router.py` with a fake HTTP layer | Done |
| 3 | **Toggles** | `functions/web_search.py`, `functions/image_mode.py` and `functions/voice_note.py` (the image Tool became a toggle, §3.2), plus tests | Done, as toggles |
| 4 | **Image** | `deploy/Dockerfile` (`FROM` Open WebUI v0.12.5, settings as env defaults), `deploy/entrypoint.sh` (reads secrets files, starts Open WebUI, upserts functions), `deploy/load_functions.py`. Checked with a local build and run | Done |
| 5 | **CI** | `.github/workflows/image.yml` (pytest, build linux/amd64, push `:latest` and `:sha-…`). First run green; package private | Done |
| 6 | **Deploy files** | `deploy/compose.yaml` (`init` one-shot like finance's: folders, placeholder secrets, owners and modes; `webui`; `kokoro` internal), `deploy/.env.example`, `deploy/README.md` (runbook with "Done when"), `ENVIRONMENT.md` | Done |
| 7 | **Server setup (owner)** | Owner follows the runbook: global env `CHAT`, stack "Chat", secrets, first Up, proxy for openai.selvalogesh.com, sign in | Not started |

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

**Results on the laptop (2026-10-31):** 29 offline tests pass. The locally built image ran as the real compose
stack (throwaway folder, laptop key), and these passed against it: `init` creates the tree and `webui` refuses
placeholders; no secret appears in `docker inspect`; the loader installs the functions and is unchanged on restart;
a sign-up lands as pending; only the four options appear; a chat answers; an uploaded document is embedded through
OpenRouter and answered from; Kokoro speech comes back through Open WebUI and Whisper transcribes it word for word.
In the spike: web search, vision, image generation, the Creative and Free chains, and the voice note saved on a
message. **Left for the owner's browser:** how the voice-note player and the toggles look, call mode, and the
phone check in the runbook's "Done when".

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
| D7 | Web search, image generation and voice notes are **toggle filters** that the pipe or the outlet acts on | Deterministic, one click per chat; a Tool depends on a model deciding to call it (spike) |
| D8 | The admin is created by Open WebUI from `WEBUI_ADMIN_EMAIL` and the password file; the loader signs in with it; no admin API key | One secret fewer, and nobody can claim admin by signing up first (spike O1) |
| D9 | Chains go to OpenRouter in groups of three; each group tries non-logging providers, then logging ones, before the next | OpenRouter's `models` limit is 3; free models were rate-limited without the logging pool (spike) |
| D10 | `BYPASS_MODEL_ACCESS_CONTROL=true` | Pipe models are admin-only otherwise; all users have the same access (R2) |
| D11 | `init` generates `webui-secret-key` itself; the other secrets start as placeholders `webui` refuses | A session key nobody needs to know; the rest must be the owner's |
| D12 | Voice notes are inline mp3 data URLs (about 1 MB a minute), capped at 1,200 characters | Plays inside the embed without a signed-in request; the cap bounds chat size and Kokoro time on the N100 |
| D13 | No token cap: a 2-minute thinking time limit with a keep-waiting-or-stop dialog, a note under any cut reply, and a retry with what low credits can afford. `reasoning.effort` is not sent | On the server, DeepSeek V4.1 Flash spent the whole 4,096-token cap thinking about an SVG and wrote no answer, and the chat showed only the thinking. Owner's choice: limit thinking by time and let the user decide, not by tokens. `effort` would also switch thinking on for models that don't think by default |
| D14 | The repo and the GHCR package are public (they started private) | Owner's choice: no secrets in either (D5), public packages are free to store, and the server pulls without a token. A package can't be made private again |
