# CR-4: Formats chosen automatically, no integrations button

The chat input loses its integrations button. The Router works out from each message whether the reply should be
an image, an answer from the web, a voice note or code that runs, and returns that format. A cheap classifier
decides before each turn, and models that can call tools may also call them while answering. Misses are recorded,
and the strong ones become examples that improve the classifier over time for the person who wrote them. This replaces CR-1's toggles (§3.1 web
search, §3.2 Create image, §3.4 Voice reply, D7). **State: plan; nothing built yet.**

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
| The integrations menu shows when the user can use web search, image generation or the code interpreter, or when the chat's models have **toggle** filters (`toggle = True`). Two more conditions in the minified frontend weren't identified | Our three toggles and `ENABLE_CODE_INTERPRETER=true` are what shows it today. Without toggle filters and with the code interpreter off, it should go (O1) |
| Filters without `toggle` run on every message and show no button | `voice_note.py` can stay a filter that runs on every reply and decides for itself (§3.5) |
| Inlet filters run before Open WebUI reads the turn's `features` | Not needed by this design (the Router decides inside the pipe), but it's a way to set features automatically if the spike needs one |
| `function_calling` defaults to **native**. With native calling, the Code Interpreter toggle adds an `execute_code` tool to the request, and our pipe never passes tools to OpenRouter | **Today's Code Interpreter toggle very likely does nothing with the Router.** The `<code_interpreter>` tag path only runs with `function_calling: legacy` |
| The pyodide engine runs code in the user's browser: the server sends `execute:python` through the event caller and waits for the result | The Router already holds the event caller (`__event_call__`, used by the "Still thinking" dialog), so it can run Python in the browser itself (O2) |
| `load_functions.py` only creates and updates functions | Deleting `web_search.py` and `image_mode.py` from the repo would leave them installed. The loader has to remove them (§3.6) |

## 3. Design

```
 user message ─▶ Router
                  │ 1. classifier (TASK_MODEL, JSON) ──▶ {image, web, code, voice, image_prompt, corrects_previous}
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
- One call per user turn to `TASK_MODEL` (`qwen/qwen3.7-flash`) before the answer, with a JSON schema reply
  (`response_format`, if the model supports it, O5). Its input: the last user message (and whether it has images),
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
- The footer names what was chosen: `… · auto: web search, voice`. Its cost is added to the reply's.
- Background tasks (titles, tags, follow-ups) skip it, as they skip the chain today.

### 3.2 Images (R2)
As the toggle did today: `IMAGE_MODELS`, cheapest first, the image stored as an Open WebUI file. The prompt is
the classifier's `image_prompt` rather than the raw message, so follow-ups like "same, but at night" work.

### 3.3 Tools for models that support them (R6)
- OpenRouter's model list (already fetched hourly for vision) also says which models take `tools`
  (`supported_parameters`). A batch gets the tools **only when every model in it supports them**, because a mixed
  batch's behaviour is unknown (O4). Creative's roleplay models likely get none; the classifier covers them.
- The tools, all run by the Router:

| Tool | What the Router does | Limit per turn |
|---|---|---|
| `generate_image(prompt)` | §3.2's image path; the image goes into the reply, and the model is told it's shown | 1 |
| `web_search(query)` | A separate call to `TASK_MODEL` with the web plugin on. Its findings and links go back to the answering model as the tool's result, and it carries on (D10; O6 may find a better way) | 1 |
| `run_python(code)` | §3.4 | 3 |
| `voice_reply(mode)` | Same as the classifier's `voice` | 1 |

- **The loop:** the reply streams as now. When the model stops with `tool_calls`, the Router runs them, shows a
  status ("Searching the web…"), and asks **the model that answered** (not the whole batch) again with the
  results, up to `MAX_TOOL_ROUNDS` (3). Thinking and the thinking time limit work as today.
- Tools the classifier already chose aren't offered again. For example, a web turn gets no `web_search` tool, so
  one turn can't search twice.

### 3.4 Running code (R5)
- A code turn uses the chain's tool-capable models, or `TOOL_MODELS` when none are (as `VISION_MODELS` does for
  images), and offers `run_python`.
- `run_python` sends `execute:python` through the event caller, so **Pyodide runs it in the user's browser**, as
  Open WebUI's interpreter does: free, nothing runs on the N100, and no new container. The code and its output
  appear in the reply in a collapsed block; charts come back as images (how, O2).
- If the browser can't run it (tab closed, the call times out), the model is told so and answers without it.
- `ENABLE_CODE_INTERPRETER` goes off, so the toggle (and with it the integrations button) goes away. The "Run"
  button on code blocks (`ENABLE_CODE_EXECUTION`) stays.

### 3.5 Voice notes (R4)
- **Voice mode per chat** lives in the Router's store (§3.7), keyed by chat id. The classifier sees it, so "stop the
  voice notes" turns it off, and `once` doesn't change it.
- For each reply that should be voiced (`once`, or voice mode on), the Router records the assistant message id.
  `voice_note.py` stops being a toggle: it runs after every reply, checks the store for that message id, and only
  then makes the voice note exactly as today (Kokoro, 1,200 characters, inline mp3). Done in the outlet, as today,
  so the text is complete and marked done before the recording starts (O3).
- The two functions share only the store's file and its table layout, not code, so each stays self-contained.

### 3.6 Removing the toggles (R1)
- `functions/web_search.py` and `functions/image_mode.py` are deleted. The Router no longer reads their flags.
- `load_functions.py` gets a short list of retired function ids and deletes those if they're installed. It
  deletes only the ids on that list, so functions the owner adds in the UI are never touched.
- `settings.env`: `ENABLE_CODE_INTERPRETER=false`. The live server keeps its saved value, so switching it off there
  is a one-time step for the owner in Admin → Settings → Code Execution, as in CR-3 D2.

### 3.7 The store, the miss log and learning (R7)
- **Store:** one SQLite file, `/app/backend/data/router/router.sqlite` (in `${CHAT}/data`, so it survives Up and
  upgrades). Valve `STORE_PATH`. Tables: `voice_chats` (chat id, on since), `voice_messages` (message ids to voice,
  dropped after a day), `detections`. Old rows go when the Router writes to their table (§3.8).
- **What counts as a miss:**

| Signal | Strength | Recorded as |
|---|---|---|
| The model called a tool for a format the classifier didn't pick | Strong | The turn's text with the corrected labels |
| The next message corrects it (`corrects_previous`) | Strong | The previous turn's text with the labels the correction gives |
| A thumbs-down on the reply, if the Router can read Open WebUI's rating (O7) | Weak: it may be about the content | Logged for the owner, never an example |

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
The file grows from about 520 lines to an estimated 830 (950 before these rules, D14):
- **One `_complete()`** for every short call that returns text rather than a streamed reply: titles and tags, the
  classifier and the `web_search` tool's call. The privacy order lives there once; titles keep today's single
  call that allows logging providers.
- **One `_make_image()`** (models cheapest first, storing the file, the markdown) for both image turns (§3.2) and
  the `generate_image` tool.
- **The store clears old rows when it writes:** a voice mark older than a day goes when a new mark is written, and
  a miss older than 90 days when a new miss is. No timer and no clean-up on start.
- **Seed examples one per line** (`"draw a conclusion from this" → chat`), not a JSON object each.

### 3.9 What gets better, what gets worse
- **Better:** no buttons to find. Images, search, voice and code just happen, including in call mode. Image
  follow-ups keep their context. Code really runs, which the Code Interpreter toggle very likely never did
  with the Router (§2).
- **Worse:** the classifier adds up to a second before the first word (time-boxed at 4 s) and a small cost per
  turn (measured in O5). A wrong guess costs a wasted image ($0.007–0.018) or search ($0.001–0.005), which the user
  can only fix by rephrasing. Tool definitions add tokens to every tool-capable turn. Running code downloads
  Pyodide into the browser on first use (heavy on a phone). Learning is slower than shared examples would be: a
  miss only helps the person who made it, until the owner promotes it to a seed example (D5).

## 4. Spike questions (to answer on the laptop before building)

| # | Question |
|---|---|
| O1 | With no toggle filters and the code interpreter off (and web search and image generation off, as now), does the integrations button disappear, for an admin and for a user? If not, what else shows it? |
| O2 | Does a pipe's `__event_call__` with `execute:python` run code in the browser and return its output, with the Code Interpreter setting off? How do matplotlib charts come back, and how long can the call wait? |
| O3 | Does a non-toggle global filter's outlet get the chat id and the assistant message id it needs to find the Router's decision? |
| O4 | OpenRouter with `tools` and a `models` batch: is `supported_parameters` reliable for the chain models, which current chain models take tools, what happens when one model in the batch doesn't, and how do streamed `tool_calls` arrive? |
| O5 | The classifier: does `TASK_MODEL` follow a JSON schema, and what are its latency, cost and accuracy on a labelled set of about 40 messages (kept in `tests/`, including traps like "draw a conclusion")? Is a different cheap model better? |
| O6 | The `web_search` tool: is asking again with the web plugin the best way, or does OpenRouter offer web search as a tool the model calls itself? |
| O7 | Can the Router read Open WebUI's thumbs-down for a message? If not cleanly, the weak signal is dropped |
| O8 | Can the function create and write `data/router/router.sqlite` (owner, mode) on the server's layout? |

## 5. Build order

Each phase ends in a commit (`CR-4: …`) on branch `story/main/CR-4-auto-formats`, which is fast-forward merged
into `main` once phase 7 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O8** on the laptop | Findings in a new §4 table; the design adjusted where needed | Not started |
| 2 | **Classifier and store** | `router.py`: the classifier with seed examples, the store, the miss log and learned examples, the footer label; the labelled set and `tools/eval_classifier.py`; tests | Not started |
| 3 | **Tools** | `router.py`: tool-capable batches, `TOOL_MODELS`, the tool loop, `run_python` through the browser, caps; tests with a fake HTTP layer | Not started |
| 4 | **Voice** | `voice_note.py` without the toggle, reading the store; voice mode on/off/once; tests | Not started |
| 5 | **Toggles out** | Delete `web_search.py` and `image_mode.py`; retired ids in `load_functions.py`; `ENABLE_CODE_INTERPRETER=false`; `deploy/detections.py`; tests | Not started |
| 6 | **Docs** | `deploy/README.md` (the one-time Code Interpreter switch, checks, the `detections.py` commands, a troubleshooting row), `ENVIRONMENT.md` (new valves, the store), "superseded by CR-4" notes on CR-1 §3.1 (web toggle), §3.2, §3.4 and D7 | Not started |
| 7 | **Local stack check** | §6 against a stack from the working tree, and a stack upgraded from the current `stable` | Not started |
| 8 | **Server (owner)** | Down, Up after CI moves `stable`, then the one-time switch and the runbook's checks | Not started |

## 6. Verification

| Check | How |
|---|---|
| Offline tests | `pytest`: classifier parsing, timeout and failure fall back to chat; image, web, code and voice routing; tools only on all-capable batches; tool loop and caps; voice once/on/off; misses from tools and corrections; learned examples per user, capped and deduplicated, and never in another user's call; the loader removes retired ids only; `ENABLE_CODE_INTERPRETER=false` |
| Classifier accuracy | `tools/eval_classifier.py` on the labelled set (O5) scores at or above the spike's result and lists the misses. It calls OpenRouter, so it runs on purpose before a release, never in `pytest` (D12) |
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
| D6 | Tools only for batches where every model supports them | What OpenRouter does with a mixed batch is unknown (O4), and an error there would be read as a privacy failure and retried |
| D7 | Retired functions are removed by an explicit id list in the loader | Removing whatever isn't in the repo would also delete functions the owner added in the UI |
| D8 | The live server turns the Code Interpreter off by hand, once | As CR-3 D2: a saved setting, changed once, and the loader shouldn't override the admin's choices |
| D9 | The classifier lives inside the Router, not in a filter that runs before it | Owner's choice. One place talks to OpenRouter: a filter would need its own copy of the key reading, HTTP calls, privacy order and error handling, and the Router needs the store for misses anyway |
| D10 | The `web_search` tool is a separate cheap call with the web plugin, whose findings go back to the answering model | Owner's choice. It fits the tool loop and costs about the same. Rejected: throwing the turn away and asking again with the plugin on (the thinking already shown would repeat), and the plugin on every turn (a search costs $0.001–0.005, often more than the reply itself; results add tokens and delay to every turn; every message would go to the search engine, outside the providers' privacy order; and search results distract creative and roleplay replies) |
| D11 | Code runs through the Router's own tool loop (`execute:python`), not Open WebUI's interpreter | Owner's choice. Open WebUI's interpreter needs legacy function calling, set per chat or per model before any filter runs, and our options have no model records. Fallback if O2 fails: model records for the four options with legacy calling |
| D12 | Classifier accuracy is measured by `tools/eval_classifier.py`, run on purpose | Owner's choice. `pytest` never calls OpenRouter (CLAUDE.md); offline tests check only that the classifier's answers are read correctly. A run costs a fraction of a cent |
| D13 | Every reply waits for the classifier before the main call | Owner's choice. Starting the answer at the same time would be faster but pays for a wasted start whenever the format changes. The real delay is measured in phase 7 before anything is optimised |
| D14 | `router.py` stays small through shared helpers, clean-up on write and one-line seed examples (§3.8) | Owner's choice, to keep the single-file function readable (it must be one file, CLAUDE.md). Behaviour doesn't change. Considered and kept: the thumbs-down signal (O7) and the `voice_reply` tool |
