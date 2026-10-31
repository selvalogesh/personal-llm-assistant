# Discovery: initial ideation (before CR-1)

What was explored and decided before the first change request. It summarises two source documents:
- the **architecture guide** (`ai_assistant_architecture_setup_guide.md`): an earlier research chat about hosting
  options, providers and a first deployment sketch;
- the **setup session** (2026-10-31): signing up to OpenRouter, keys, budget and connecting VS Code.

Where a later decision replaced something here, the row points to [CR-1.md](CR-1.md).

## 1. Hosting: run models yourself, or call an API?

The guide's question: what's the cheapest way to run a personal LLM assistant?

| Option | Monthly cost | Verdict |
|---|---|---|
| AWS GPU (`g4dn.xlarge`, T4) | ~$380 on demand, ~$100–150 spot (can be interrupted) | Too expensive for one person |
| AWS CPU (`t4g.xlarge` / Lightsail 4 vCPU 16 GB) for Ollama or llama.cpp | ~$84–127 | Slow: virtualised memory bandwidth gives ~4–8 tokens/s on an 8B model; burstable CPU credits run out |
| Google Colab / Colab Pro | $0–10 | Built for notebooks, not always-on services: sessions disconnect and compute units run out |
| GPU cloud (RunPod, Vast.ai, Lambda) | ~$30–70 | Cheaper than AWS, still a lot for occasional use |
| Home mini PC with Ollama | One-off ~$350–450, then ~$1–3 power | Private and 2–3× faster than cloud CPU, but needs hardware |
| **Pay-per-use API for open models** | **Usage based** | **Chosen:** no idle cost, no server to maintain |

**Providers compared:** OpenRouter (one key for many providers, routing), Groq (very fast), DeepInfra
(cheapest) and Together AI (fine-tuning). **OpenRouter was chosen.** Every option uses the OpenAI API format, so
switching later is cheap.

## 2. First architecture sketch (the guide)

- **Web chat:** Open WebUI in Docker behind Nginx (TLS via certbot, with SSE and WebSocket settings) at
  chat.selvalogesh.com, pointing at OpenRouter.
- **VS Code:** Continue (chat and autocomplete) or Cline (an agent), both talking to OpenRouter directly.

**Review findings** in the setup session; the guide itself was not updated:

| Finding | Fix |
|---|---|
| `-p 8080:8080` publishes Open WebUI on every interface and bypasses Nginx and TLS; Docker's firewall rules also get past ufw | Bind to `127.0.0.1:8080` |
| The first account to sign up becomes admin | Create the admin at once, then turn off or control sign-ups. CR-1: sign-ups wait for approval (R3) |
| Nginx limits uploads to 1 MB, so PDF uploads fail with 413 | `client_max_body_size 50M;` |
| The key in the `docker run` command ends up in shell history | Keep it out of commands. CR-1: secrets files (D5) |
| Model names are a generation old (Llama 3.3, Qwen 2.5 Coder, Claude 3.5 Sonnet) | Pick current models from OpenRouter's list |
| Continue's `tabAutocompleteModel` is the old config format | In `config.yaml`, a model with `roles: [autocomplete]`, ideally a FIM model |
| The "<$1–3/month" estimate only holds for light chat | Agents like Cline cost far more (see §5) |

**Superseded by CR-1:**
- The service runs on the **OMV home server**, not a separate web server.
- The owner handles the proxy, TLS and DNS, so Nginx is not part of the project.
- The hostname is openai.selvalogesh.com.

## 3. OpenRouter account and keys

- **Keys, one per place,** so a leak can be revoked without affecting the other:
  - `vscode-laptop` lives in the Windows user env var `OPENROUTER_API_KEY` (the standard name; the variable was
    renamed from `OPEN_ROUTER_KEY`). It was tested and works.
  - `chat-server` lives in `OPEN_ROUTER_SERVER` for now. It moves to the server (CR-1: secrets file
    `openrouter-api-key`) and is then removed from the laptop.
- **Master copies** live in a password manager. Keys are never pasted into chats or committed.
- **Workload Identity Federation (short-lived credentials): rejected.**
  - It's for Business/Enterprise plans only.
  - The server would need an identity provider to issue tokens.
  - The tokens last 15 minutes, which Open WebUI and Cline can't refresh.
  - Instead, limit what a leaked key can spend.
- **Management key:** used once to check the setup, then deleted on OpenRouter and removed from the environment.
  It can create keys with no limit, so it is never kept around.
- **Budget:** the "Default Workspace" has a **$10 monthly budget**, and both keys draw from it. When it runs out,
  requests fail (403) until it resets.
  - The pool is shared: a heavy Cline month can stop the chat service too.
  - Optional per-key limits within it (e.g. laptop $7, server $3) were offered but not set.
- **Credits:** OpenRouter is prepaid. The budget caps spending but doesn't fund it.
  - The first Cline request failed with "can only afford 4000 tokens" because the balance was $0.
  - The fix is buying $5–10 of credits with auto top-up **off**, so the balance is a second hard limit.
- **Free models** (`:free`) have tight daily limits, and some of their providers log prompts. They're useful for
  testing, and in CR-1 they're the fallback when credits run out.

## 4. VS Code: Cline

- **Continue vs Cline:** Continue (chat, inline edits, autocomplete) overlaps little with Claude Code. The owner
  wanted Claude Code-like abilities, so **Cline** was installed (`saoudrizwan.claude-dev` 4.1.26).
- **How Cline maps to Claude Code:** Plan/Act mode is plan mode; auto-approve settings are permission modes;
  `.clinerules` is CLAUDE.md; checkpoints are rewind; it also supports MCP servers.
- **Connection:** "Use your own API key" → OpenRouter → the `vscode-laptop` key, which Cline keeps in VS Code's
  secret storage.
- **Models** (OpenRouter prices per 1M tokens, input / output, as of 2026-10-31):

| Use | Model | Price |
|---|---|---|
| Default (cheap) | `deepseek/deepseek-v4.1-flash` | $0.04 / $0.50 |
| Cheap alternative | `z-ai/glm-5.3` | $0.07 / $1.40 |
| Worth trying (check price) | `anthropic/claude-haiku-5.5` | $0.10 / $0.50 |
| Strongest | `anthropic/claude-sonnet-5.5` | $2 / $10 |
| Skip | `moonshotai/kimi-k3.1-pro` | Sonnet's price, lower score |

- **Cost:** Cline resends ~20–30k tokens of context every step. A 100-step session costs about $0.15 on DeepSeek
  V4.1 Flash and about $5 on Sonnet 5.5, so use the cheap model by default.
- **Settings:** different Plan and Act models; auto-approve starts with "read files" only; a new task per topic.
- **State at the end of discovery:** connected, waiting for credits to be bought.

## 5. Lessons carried into CR-1

| Lesson | Where it went |
|---|---|
| A pay-per-use API beats self-hosting for one person; OpenRouter for one key and routing | CR-1 R11, §3.1 |
| Spending, not leaks, is the real risk: cap it with the workspace budget and prepaid credits | CR-1 R7; the Free chain on 402 |
| Keys stay out of commands, chats, git and committed environments | CR-1 R13, D5 (secrets files) |
| Sign-ups must be controlled from the first minute | CR-1 R3 |
| Only Open WebUI's port should be reachable | CR-1 R14 |
| Model names go stale fast; check OpenRouter's live list | CR-1 open item O6 |
