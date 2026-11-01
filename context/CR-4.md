# CR-4: Formats chosen automatically, no integrations button

The chat input loses its integrations button. The Router works out from each message whether the reply should be
an image, an answer from the web, a voice note or code that runs, and returns that format. A cheap classifier
decides before each turn, and models that can call tools may also call them while answering. Misses are recorded,
and the strong ones become examples that improve the classifier over time for the person who wrote them. This replaces CR-1's toggles (§3.1 web
search, §3.2 Create image, §3.4 Voice reply, D7). **State: done; live on the server since 2026-11-01, and every check
passed on the owner's phone.**

## 1. Requirements

| # | Requirement | Comes from |
|---|---|---|
| R1 | No integrations button in the chat input: no Web search, Create image, Voice reply or Code Interpreter toggles | Owner |
| R2 | A request for a picture ("create an image of…", "draw me…", "make it bluer" after an image) is answered with a generated image | Owner |
| R3 | A request to research something, or a question that needs current information, is answered with OpenRouter's web search and sources, as the Web search toggle does today | Owner |
| R4 | A request for an audio reply is answered with a voice note. By default only that reply; "from now on reply in voice notes" keeps voice notes on for the rest of that chat, until the user asks to stop | Owner |
| R5 | A request that needs code run (calculations, data, charts) gets the code run and its results in the reply | Owner |
| R6 | Detection: a cheap classifier on every turn, **and** models that support tool calls get tools, so they can choose what the request needs while answering | Owner |
| R7 | Detections that went wrong are recorded, and strong misses become classifier examples **for the user who wrote them, automatically**. Nobody's words go into another person's calls | Owner (per user rather than shared, D5) |
| R8 | CR-1's guardrails stay: privacy order, the $10/month budget, the four options in the picker, no refusal detector | CLAUDE.md |

Not in scope: picking the chain automatically (the picker stays: Auto, Creative, Smart, Free), editing an uploaded
photo (image generation stays text-to-image), and Open WebUI's own web search and image engines (stay off).

## 2. What Open WebUI already does (checked in v0.12.5, 2026-11-01)

| Fact | Consequence |
|---|---|
| The **Integrations** button in the chat input shows when the user can use web search, image generation or the code interpreter, or when the chat's models have **toggle** filters (`toggle = True`) | Our three toggles and `ENABLE_CODE_INTERPRETER=true` are what show it today. Confirmed (O1): it goes only when both are gone; the Code Interpreter alone still shows it |
| Filters without `toggle` run on every message and show no button | `voice_note.py` can stay a filter that runs on every reply and decides for itself (§3.5) |
| Inlet filters run before Open WebUI reads the turn's `features` | Not needed by this design (the Router decides inside the pipe), but it's a way to set features automatically if the spike needs one |
| `function_calling` defaults to **native**. With native calling, the Code Interpreter toggle adds an `execute_code` tool to the request, and our pipe never passes tools to OpenRouter | **Today's Code Interpreter toggle very likely does nothing with the Router.** The `<code_interpreter>` tag path only runs with `function_calling: legacy` |
| The pyodide engine runs code in the user's browser: the server sends `execute:python` through the event caller and waits for the result | The Router already holds the event caller (`__event_call__`, used by the "Still thinking" dialog), so it can run Python in the browser itself (O2) |
| `load_functions.py` only creates and updates functions | Deleting `web_search.py` and `image_mode.py` from the repo would leave them installed. The loader has to remove them (§3.6) |

## 3. Design

```
 user message ─▶ Router
                  │ 1. classifier (CLASSIFIER_MODEL, JSON schema) ──▶ {image, web, code, voice, image_prompt, corrects_previous}
                  │      + current voice mode of the chat, + the user's learned examples (store: router.sqlite)
                  │ 2. image?  ─▶ image models (as today) ─▶ image in the reply
                  │    else    ─▶ chain, with the web plugin if web, tool-capable models only if code
                  │               └─ tools offered when every model in the batch supports them:
                  │                  generate_image · web_search · run_python · voice_reply
                  │ 3. records the turn: what the classifier said, what tools the model called, corrections
                  ▼
           voice_note outlet: reads the Router's decision for this message ─▶ Kokoro ─▶ voice-note player
```

### 3.1 The classifier
- One call per user turn to a new valve, `CLASSIFIER_MODEL` (`deepseek/deepseek-v4.1-flash`, D15), before the
  answer. It asks for a strict JSON schema reply (`response_format: json_schema`), reasoning off, temperature 0, and
  `provider.require_parameters` so only providers that honour the schema answer. Titles and tags stay on
  `TASK_MODEL`. Its input: the last user message (and whether it has images),
  the previous user message and what format the last reply had, whether voice mode is on in this chat, the seed
  examples in `router.py` (one line each, `message → labels`, §3.8), and this user's learned examples (§3.7).
- Its output:

| Field | Values | Effect |
|---|---|---|
| `image` | bool | Image turn (§3.2). Takes priority over everything except voice, which an image reply has nothing to say for |
| `image_prompt` | text | The prompt for the image model, written with the chat's context ("make it bluer" becomes a full description) |
| `web` | bool | The web plugin goes on this turn, as the toggle did (R3) |
| `code` | bool | Code turn (§3.4) |
| `voice` | `none`, `once`, `on`, `off` | `once`: this reply. `on`/`off`: voice mode for the chat (§3.5) |
| `corrects_previous` | null or the labels the previous turn should have had | "No, I wanted a picture" records the last turn as a miss (§3.7) |

- **Time-boxed** (`CLASSIFIER_TIMEOUT_SECONDS`, 4 s) and never fatal: if it fails or times out, the turn is plain
  chat, and the tools (§3.3) are the safety net. Privacy order as every call: non-logging providers first.
- **Read with care (D16):** asking for the voice mode the chat is already in counts as `none` (the spike saw `off`
  when voice was off). `corrects_previous` is ignored when there is no previous turn, and a miss is recorded only
  when the corrected formats differ from what the previous turn actually used.
- The footer names what was chosen: `… · auto: web search, voice`. Its cost is added to the reply's.
- Background tasks (titles, tags, follow-ups) skip it, as they skip the chain today.

### 3.2 Images (R2)
As the toggle did today: `IMAGE_MODELS`, cheapest first, the image stored as an Open WebUI file. The prompt is
the classifier's `image_prompt` rather than the raw message, so follow-ups like "same, but at night" work.

### 3.3 Tools for models that support them (R6)
- OpenRouter's model list (already fetched hourly for vision) also says which models take `tools`
  (`supported_parameters`). A batch gets the tools **only when every model in it supports them**: a mixed batch
  with tools quietly goes to its tool-capable model only (O4), which for Creative would replace the roleplay models
  with DeepSeek. Auto, Smart, Free and the vision models all take tools; Creative's first batch doesn't, so Creative
  relies on the classifier.
- The tools:

| Tool | Who runs it | Limit per turn |
|---|---|---|
| `generate_image(prompt)` | The Router: §3.2's image path; the image goes into the reply, and the model is told it's shown | 1 |
| `web_search` | **OpenRouter**, as its server tool `openrouter:web_search` (`parallel` engine, `fast` mode, 3 results). The model searches and cites without a round trip through the Router (D10) | `max_uses` 2 (OpenRouter ran one more than asked, O6) |
| `run_python(code)` | The Router, §3.4 | 3 |
| `voice_reply(mode)` | The Router: same as the classifier's `voice` | 1 |

- **The loop:** the reply streams as now. Tool calls arrive in the stream in OpenAI's format (an index, an id, then
  the name and the arguments in pieces) and the reply ends with `finish_reason: tool_calls` (O4). The Router runs
  them, shows a status ("Making the image…"), and asks **the model that answered** (not the whole batch) again with
  the results, up to `MAX_TOOL_ROUNDS` (3). Thinking and the thinking time limit work as today. Web search sources
  arrive as `url_citation` annotations; the footer counts searches from `usage.server_tool_use_details`.
- Tools the classifier already chose aren't offered again. For example, a web turn (the plugin, as today) gets no
  `web_search` tool, so one turn can't search twice.

### 3.4 Running code (R5)
- A code turn uses the chain's tool-capable models, or `TOOL_MODELS` when none are (as `VISION_MODELS` does for
  images), and offers `run_python`.
- `run_python` sends `execute:python` through the event caller, so **Pyodide runs it in the user's browser**, as
  Open WebUI's interpreter does: free, nothing runs on the N100, and no new container. It works with the Code
  Interpreter setting off (O2). The result is `{stdout, stderr, result}`; a matplotlib chart arrives inside
  `stdout` as a `data:image/png;base64,…` string, which the Router stores as an Open WebUI file (as §3.2 does) and
  shows as an image, so neither the chat nor the model gets the base64. The code and its text output appear in a
  collapsed block.
- The call waits `timeout_ms` (Open WebUI clamps it to 60–240 s); the first run took 6 s to load Pyodide. If the
  browser can't run it (tab closed, timed out), the model is told so and answers without it.
- `ENABLE_CODE_INTERPRETER` goes off, so the toggle (and with it the integrations button) goes away. The "Run"
  button on code blocks (`ENABLE_CODE_EXECUTION`) stays.

### 3.5 Voice notes (R4)
- **Voice mode per chat** lives in the Router's store (§3.7), keyed by chat id. The classifier sees it, so "stop the
  voice notes" turns it off, and `once` doesn't change it.
- For each reply that should be voiced (`once`, or voice mode on), the Router records the assistant message id
  (`__metadata__["message_id"]`). `voice_note.py` stops being a toggle: it runs after every reply, checks the
  store for the outlet body's `id`, which is the same id (O3), and only then makes the voice note exactly as today
  (Kokoro, 1,200 characters, inline mp3). Done in the outlet, as today, so the text is complete and marked done
  before the recording starts.
- The two functions share only the store's file and its table layout, not code, so each stays self-contained.

### 3.6 Removing the toggles (R1)
- `functions/web_search.py` and `functions/image_mode.py` are deleted. The Router no longer reads their flags.
- `load_functions.py` gets a short list of retired function ids and deletes those if they're installed. It
  deletes only the ids on that list, so functions the owner adds in the UI are never touched.
- `settings.env`: `ENABLE_CODE_INTERPRETER=false`. The live server keeps its saved value, so switching it off there
  is a one-time step for the owner in Admin → Settings → Code Execution, as in CR-3 D2.

### 3.7 The store, the miss log and learning (R7)
- **Store:** one SQLite file, `/app/backend/data/router/router.sqlite` (in `${CHAT}/data`, so it survives Up and
  upgrades; the function runs as root and creates it, O8). Valve `STORE_PATH`. Tables: `turns` (each chat's last
  turn: its text, message id and the formats it used, kept a day, so a correction or a thumbs-down can be checked
  against what actually happened), `voice_chats` (chat id, on since), `voice_messages` (message ids to voice,
  dropped after a day), `detections`. Old rows go when the Router writes to their table (§3.8).
- **What counts as a miss:**

| Signal | Strength | Recorded as |
|---|---|---|
| The model called a tool for a format the classifier didn't pick | Strong | The turn's text with the corrected labels |
| The next message corrects it (`corrects_previous`) | Strong | The previous turn's text with the labels the correction gives |
| A thumbs-down on the reply, read on the user's next turn from Open WebUI's `feedback` table (`Feedbacks.get_feedbacks_by_user_id`: rating -1 with the message id, O7) | Weak: it may be about the content | Logged for the owner, never an example |

- **Learning, per user:** each user's latest `LEARNED_EXAMPLES` (20) strong misses, without duplicates, go into
  **that user's** classifier calls as examples, after the seed examples in `router.py`. A newer miss on the same
  text replaces the older one. A new user starts with the seed examples only.
- **Privacy (D5):** a user's message text only goes into their own classifier calls, so nobody's words reach
  OpenRouter inside someone else's turn. The text is also cut to 300 characters and misses are deleted after 90
  days. Classifier calls keep the non-logging-first order.
- **Improving it for everyone** goes through the owner: when the owner reviews the misses with Claude, good ones,
  rewritten without personal details, go into the seed examples in `router.py`. There they're tested, reach every
  user, and outlive the 20-example window.
- **Looking and deleting:** `deploy/detections.py`, run with `docker exec` in `webui`, lists misses (all, or one
  user's), deletes one, or clears a user's or all. The runbook has the commands.

### 3.8 Keeping `router.py` small
The file was estimated to grow from about 520 lines to 830 (950 before these rules, D14). **After phase 2 it is
888**, mostly the classifier's prompt and schema (about 75 lines) and the store (about 100); the tools (phase 3)
will take it past 1,000. The rules still apply:
- **One `_complete()`** for every short call that returns text rather than a streamed reply: titles and tags, and
  the classifier. The privacy order lives there once; titles keep today's single call that allows logging
  providers. (The `web_search` tool needs no call of its own: OpenRouter runs it, D10.)
- **One `_make_image()`** (models cheapest first, storing the file, the markdown) for both image turns (§3.2) and
  the `generate_image` tool.
- **The store clears old rows when it writes:** a voice mark older than a day goes when a new mark is written, and
  a miss older than 90 days when a new miss is. No timer and no clean-up on start.
- **Seed examples one per line** (`"draw a conclusion from this" → chat`), not a JSON object each.

### 3.9 What gets better, what gets worse
- **Better:** no buttons to find. Images, search, voice and code just happen, including in call mode. Image
  follow-ups keep their context. Code really runs, which the Code Interpreter toggle very likely never did
  with the Router (§2).
- **Worse:** the classifier adds about 0.6 s before the first word (1.2 s in slow cases; time-boxed at 4 s) and
  about $0.00005 a turn (O5). A wrong guess costs a wasted image ($0.007–0.018) or search ($0.001–0.005), which the user
  can only fix by rephrasing. Tool definitions add tokens to every tool-capable turn. Running code downloads
  Pyodide into the browser on first use (heavy on a phone). Learning is slower than shared examples would be: a
  miss only helps the person who made it, until the owner promotes it to a seed example (D5).

## 4. Spike findings (2026-11-01, laptop, Open WebUI v0.12.5, laptop key)

The live API questions (O4–O6) ran against OpenRouter directly, for about $0.10 in all. The rest ran on a throwaway
stack from the working tree, driven through Edge with Playwright, with a throwaway pipe and filter installed through
Open WebUI's API. Nothing from the spike is committed except these findings and the labelled set (phase 2 moves it
into `tests/`).

| # | Question | Finding |
|---|---|---|
| O1 | Does the Integrations button go with the toggles removed and the Code Interpreter off? | **Yes**, for an admin, a user and at phone width; the "+" menu then holds only attachments. **The Code Interpreter alone still shows it**, so the server's one-time switch (§3.6) is required, not optional |
| O2 | Can a pipe run Python in the browser through `execute:python`? | **Yes, with the Code Interpreter setting off.** Result `{stdout, stderr, result}` after 6 s on the first run (Pyodide loading). A matplotlib chart came back inside `stdout` as a 36 kB `data:image/png;base64` string (§3.4). The wait is `timeout_ms`, clamped to 60–240 s |
| O3 | Does a non-toggle global filter's outlet get the ids it needs? | **Yes.** The outlet body has `chat_id`, `session_id` and `id`, the assistant message id, which equals the pipe's `__metadata__["message_id"]`. The messages the pipe gets carry no ids. Open WebUI's title task also calls the pipe, with no message id |
| O4 | Tools across a `models` batch | Streamed `tool_calls` arrive in OpenAI's format, ending with `finish_reason: tool_calls`. Auto picked the right tool on its own (`generate_image` for a lighthouse, `run_python` for an exact Fibonacci product, none for a poem). **A mixed batch with tools quietly goes to its tool-capable model**: Creative answered from DeepSeek. Without tools, Euryale replied "I'm not capable of drawing", so Creative needs the classifier. `supported_parameters`: every chain model takes tools except Cydonia and Euryale; `qwen3.7-flash` has `response_format` but not `structured_outputs` |
| O5 | The classifier | Labelled set of 43 messages, with traps like "draw a conclusion" and "write a Python function"; prompt about 670 tokens with 22 one-line seed examples. Three runs each: **deepseek-v4.1-flash** 42–43/43, 0.6 s median, 1.15 s p90, $0.00005 a call, strict JSON schema; qwen3.7-flash 42/43, 0.95 s / 1.6 s, $0.000016, plain JSON only, and it answered voice `off` 13 times when voice was already off; glm-5.3-flash 43/43, 1.25 s / 2–2.8 s, $0.00008; gpt-oss-120b 39/43 (reasoning can't be turned off); mistral-nemo has no non-logging provider for the schema. DeepSeek's miss was a false `corrects_previous` on a first message (D15, D16) |
| O6 | Web search as a tool | **OpenRouter runs it as a server tool**, `openrouter:web_search`: sources come back as `url_citation` annotations, the count in `usage.server_tool_use_details`. One search (`max_uses` 1) gave a stale answer; `parallel` in `fast` mode with `max_uses` 2 ran 3 searches for **$0.0024** and answered correctly, against $0.005 for today's plugin and $0.02 with no limit. `max_uses` isn't strict: it ran one more than asked each time |
| O7 | Can the Router read a thumbs-down? | **Yes**, on the user's next turn: `Feedbacks.get_feedbacks_by_user_id` returned rating -1 with the chat and message ids |
| O8 | Can the function write the store? | **Yes**: it runs as root and created `data/router/` and an SQLite file (mode 644) |

## 5. Build order

Each phase ends in a commit (`CR-4: …`) on branch `story/main/CR-4-auto-formats`, which is fast-forward merged
into `main` once phase 7 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O8** on the laptop | Findings in §4; the design adjusted where needed | Done (§4; D10 changed, D15 and D16 added) |
| 2 | **Classifier and store** | `router.py`: `CLASSIFIER_MODEL`, the classifier with seed examples, the store, the miss log (corrections, thumbs-down) and learned examples, voice mode on and off, the footer label, one shared connection (D17); the spike's labelled set in `tests/` and `tools/eval_classifier.py`; tests | Done: 74 offline tests; the eval scored 43/43 twice, 0.7 s median |
| 3 | **Tools** | `router.py`: tool-capable batches, `TOOL_MODELS`, the tool loop, `run_python` through the browser, caps, misses from tool calls; tests with a fake HTTP layer | Done: 86 offline tests. Live, the Router ran a code turn through a stand-in browser and answered correctly. A tool round sends back only the calls and their results: DeepSeek, GLM, Qwen, Gemini (streamed) and Sonnet all answered without their reasoning passed back |
| 4 | **Voice** | `voice_note.py` without the toggle, reading the store; the Router's per-reply marks (`once`, or voice mode on); tests | Done: 91 offline tests, including one where the filter reads the mark the Router wrote |
| 5 | **Toggles out** | Delete `web_search.py` and `image_mode.py`; retired ids in `load_functions.py`; `ENABLE_CODE_INTERPRETER=false`; `deploy/detections.py`; tests | Done: 99 offline tests; a test checks no function is a toggle |
| 6 | **Docs** | `deploy/README.md` (the one-time Code Interpreter switch, checks, the `detections.py` commands, a troubleshooting row), `ENVIRONMENT.md` (new valves, the store), "superseded by CR-4" notes on CR-1 §3.1 (web toggle), §3.2, §3.4 and D7 | Done, with the root README |
| 7 | **Local stack check** | §6 against a stack from the working tree, and a stack upgraded from the current `stable` | Done (below); it found the voice-note wording (D18) |
| 8 | **Server (owner)** | Down, Up after CI moves `stable`, then the one-time switch and the runbook's checks | Done (2026-11-01, below) |

**On the laptop (2026-11-01, phase 7):** a stack started from `stable` (commit "CR-1: Generated image shows when the
prompt has new lines or brackets", four functions), then Down, Up on the working tree, in Edge through Playwright,
with the laptop key (about $0.15 in all):
- **Upgrade:** the loader updated the Router and the voice filter and logged `web_search: removed (retired)` and
  `image_mode: removed (retired)`. The Code Interpreter was still saved as on, so the Integrations button stayed;
  after the one-time switch it was gone for the admin and for a user at phone width.
- **Formats:** "Draw a lighthouse at dusk" gave an image (`auto: image`, $0.0071) and "same, but in winter" a
  matching one from a rewritten prompt; today's technology news came with sources (`auto: web search`); "plot sin(x)
  from 0 to 10" ran in Pyodide in the browser and showed a collapsed Python block, the chart and the explanation
  (`auto: code`, $0.0007); "Reply with a voice note" voiced that reply only; "From now on, voice notes" voiced the
  next three replies and "stop the voice notes" ended it.
- **The voice-note wording (D18):** the first run's voiced replies said "I can't actually record audio, but here's
  the script" above the real voice note. After the fix, they just talk.
- **Learning:** "Describe a sunset over Marina beach in a few words", then "No, I meant an actual picture" gave an
  image and a learned miss in `detections.py list`.
- **Tools as backup:** with `CLASSIFIER_MODEL` set to a model that doesn't exist (the log: `classifier failed,
  answering as chat`), a picture request was answered by the `generate_image` tool and a search request by
  OpenRouter's search tool, with sources; both became learned misses.
- **Fresh install** (an empty folder, the working tree): the loader installed only `router` and `voice_note`, the
  Code Interpreter was off from `settings.env`, and the chat input had no Integrations button, with no manual step.

**On the server (2026-11-01):** `main` was fast-forwarded to the branch; CI passed and moved `stable`. A session on
the server ran Down, Up from the shell (the chat was down about a minute; `compose.yaml` hadn't changed, so nothing
was pasted into OMV). `app/DEPLOYED` showed the commit "CR-4: Local stack check passed, upgraded from stable and
fresh; …" and the log showed `web_search: removed (retired)`, `image_mode: removed (retired)` and `[load_functions]
done` with no errors; Admin → Functions listed only Router and Voice reply. The one-time switch was done through the
admin settings API (signed in with the secrets file's password): Code Interpreter off and saved, Code Execution
still on. On the owner's phone, every check passed: no Integrations button, the lighthouse and its winter version,
today's news with sources, the sin(x) plot, a voice note, and voice notes on and then off.

## 6. Verification

| Check | How |
|---|---|
| Offline tests | `pytest`: classifier parsing, timeout and failure fall back to chat; image, web, code and voice routing; tools only on all-capable batches; tool loop and caps; voice once/on/off; misses from tools and corrections; learned examples per user, capped and deduplicated, and never in another user's call; the loader removes retired ids only; `ENABLE_CODE_INTERPRETER=false` |
| Classifier accuracy | `tools/eval_classifier.py` on the labelled set scores at least 42/43, the spike's result (O5), and lists the misses. It calls OpenRouter, so it runs on purpose before a release, never in `pytest` (D12) |
| No button | The chat input has no integrations button, for an admin and for a user (O1) |
| Each format | "Draw a lighthouse at dusk" gives an image; "same, but in winter" gives a matching one; "what's in today's news" answers with sources; "plot sin(x) from 0 to 10" runs and shows the chart; "reply with a voice note" voices one reply; "from now on, voice notes" voices the next three until "stop the voice notes" |
| Tools as backup | With the classifier's model set to something broken, a tool-capable chain still makes an image and searches the web when asked |
| Learning | A corrected turn appears in `detections.py list` and in that user's next classifier call, and not in another user's |
| Upgrade | On a stack upgraded from `stable`: the old toggle functions are gone and the voice note still works |
| Server (owner) | The one-time switch, then the "Each format" checks on a phone |

## 7. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | A classifier on every turn **and** tools for models that support them | Owner's choice. The classifier works for every chain (Creative's roleplay models have no tools) and decides before anything is spent; tools catch what it misses and need no extra call. Supersedes CR-1 D7, which rejected tools as the only trigger |
| D2 | Images take the whole turn; web, code and voice combine | An image model can't search or run code, and a voice note of "here's your image" is noise |
| D3 | Voice mode lives in the Router's store, with a per-message mark the voice filter reads | "From now on" has to outlive the chat window the classifier sees. The filter keeps making the note after the reply is done, as today |
| D4 | Code runs in the browser through Pyodide, started by the Router | No server load on the N100 and no new container. Open WebUI's own interpreter path needs legacy function calling and never reaches our pipe (§2) |
| D5 | Strong misses become examples automatically, **per user** (each user's latest 20, cut to 300 characters, 90 days); the owner promotes good ones to seed examples for everyone | Owner's choice. It first chose shared examples, then changed to per user: each person's classifier still improves with no work from the owner, and nobody's words go to OpenRouter inside someone else's turn. Rejected: shared automatic examples (cross-user privacy cost) and owner review only (no improvement between reviews) |
| D6 | Tools only for batches where every model supports them | A mixed batch with tools quietly goes to its tool-capable model (O4): Creative would lose its roleplay models on every turn |
| D7 | Retired functions are removed by an explicit id list in the loader | Removing whatever isn't in the repo would also delete functions the owner added in the UI |
| D8 | The live server turns the Code Interpreter off by hand, once | As CR-3 D2: a saved setting, changed once, and the loader shouldn't override the admin's choices |
| D9 | The classifier lives inside the Router, not in a filter that runs before it | Owner's choice. One place talks to OpenRouter: a filter would need its own copy of the key reading, HTTP calls, privacy order and error handling, and the Router needs the store for misses anyway |
| D10 | The `web_search` tool is OpenRouter's server tool, `openrouter:web_search` (`parallel`, `fast`, 3 results, `max_uses` 2). **Changed by the spike**: the plan was a separate cheap call with the web plugin, whose findings went back to the answering model | O6: OpenRouter runs the search inside the model's own request, so there's no round trip through the Router and no extra call, and it cost $0.0024 against $0.005 for the plugin. Classifier web turns keep the plugin, as today (R3). Rejected before the spike: throwing the turn away and asking again with the plugin on (the thinking already shown would repeat), and the plugin on every turn (a search costs $0.001–0.005, often more than the reply itself; results add tokens and delay to every turn; every message would go to the search engine, outside the providers' privacy order; and search results distract creative and roleplay replies) |
| D11 | Code runs through the Router's own tool loop (`execute:python`), not Open WebUI's interpreter | Owner's choice. Open WebUI's interpreter needs legacy function calling, set per chat or per model before any filter runs, and our options have no model records. Fallback if O2 fails: model records for the four options with legacy calling |
| D12 | Classifier accuracy is measured by `tools/eval_classifier.py`, run on purpose | Owner's choice. `pytest` never calls OpenRouter (CLAUDE.md); offline tests check only that the classifier's answers are read correctly. A run costs a fraction of a cent |
| D13 | Every reply waits for the classifier before the main call | Owner's choice. Starting the answer at the same time would be faster but pays for a wasted start whenever the format changes. The real delay is measured in phase 7 before anything is optimised |
| D14 | `router.py` stays small through shared helpers, clean-up on write and one-line seed examples (§3.8) | Owner's choice, to keep the single-file function readable (it must be one file, CLAUDE.md). Behaviour doesn't change. Considered and kept: the thumbs-down signal (O7) and the `voice_reply` tool |
| D15 | The classifier runs on `deepseek/deepseek-v4.1-flash`, in its own valve `CLASSIFIER_MODEL`; titles and tags stay on `TASK_MODEL` (qwen3.7-flash) | Owner's choice (O5). Every reply waits for it, and it was the fastest (0.6 s median against Qwen's 0.95 s), with a strict JSON schema, so every field is always there. Accuracy was within one message of Qwen and GLM. It costs about 3× Qwen, about $0.15 per 3,000 turns. Rejected: qwen3.7-flash (no new valve and cheapest, but slower, plain JSON only and loose with the voice field) and glm-5.3-flash (43/43, but 2–2.8 s in slow cases) |
| D16 | The Router reads the classifier with care: the voice mode the chat is already in counts as `none`; `corrects_previous` is ignored without a previous turn; a correction is recorded only when it differs from what the previous turn used | O5: Qwen answered `off` when voice was already off, and DeepSeek once marked a first message as a correction. Without these, a false miss would become a learned example |
| D17 | The Router keeps one HTTP client open and reuses it, instead of a new one per call | Found in phase 2: the first eval took 1.6 s median against the spike's 0.6 s, and a new connection per call was the whole difference (about 0.7 s each). The classifier would have paid it before every answer, and every chat reply was already paying it before CR-4. The client is made again when the event loop, the transport (tests) or `TIMEOUT_SECONDS` changes |
| D18 | A reply the Router marks for a voice note gets a system note: it will be read aloud, so write it as spoken and don't say audio can't be sent | Found in phase 7: without it, DeepSeek wrote "I can't actually record audio, but here's the script" above the voice note it was sending. Code turns already get a note the same way (`with_note`) |
