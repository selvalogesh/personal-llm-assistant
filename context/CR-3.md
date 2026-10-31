# CR-3: Document embeddings on the server

Document search embeds with Open WebUI's own embedding model, running in the `webui` container, instead of
sending document chunks to OpenRouter's `/embeddings`. This replaces CR-1's R10 and §3.3. **State: done; the server
embeds documents with bge-small-en-v1.5 since 2026-10-31, and the OpenRouter key exists only in its secrets file.**

## 1. Requirements

| # | Requirement | Comes from |
|---|---|---|
| R1 | Document embeddings run on the server, in Open WebUI's own embedding engine, with `BAAI/bge-small-en-v1.5`. No document text goes to OpenRouter for embedding. Documents are mainly English | Owner (model chosen over MiniLM and bge-m3, D1) |
| R2 | The live server switches too, not only fresh installs, and documents uploaded before the switch still work or are clearly re-done | Follows from R1: Open WebUI keeps these settings in its database (§2) |
| R3 | CR-1's guardrails stay: the OpenRouter key lives only in its secrets file | CLAUDE.md |

## 2. What the image and Open WebUI already do (checked in v0.12.5, 2026-10-31)

| Fact | Consequence |
|---|---|
| The image bakes its default embedding model, `all-MiniLM-L6-v2`, and Whisper `base` under `/app/backend/data/cache/`; our mount of `${CHAT}/data` over `/app/backend/data` hides them (this is also why the first dictation downloads Whisper's model) | bge-small isn't baked in anyway. Open WebUI downloads it from Hugging Face on the first start into `data/cache/embedding/models`, where it stays: about 380 MB, because it fetches the model's whole repository (the weights in three formats), not only the 130 MB it loads |
| `sentence-transformers` and PyTorch are already in the image | Nothing to add: this is part of what makes the image 6.4 GB |
| The embedding settings (`rag.embedding_engine`, `rag.embedding_model`, `rag.openai.api_key`, …) are saved in the database on the first start; the environment is ignored after that | `settings.env` only sets them for a fresh install. The live server switches once in Admin → Settings → Documents (§3.3) |
| Switching the engine (`POST /api/v1/retrieval/embedding/update`) doesn't clear `rag.openai.api_key` | The copy of the OpenRouter key that CR-1 put in the database would stay there unused. The switch blanks it first (§3.3) |
| Vectors from different models don't mix: voyage-4-lite gives 1024 dimensions, bge-small 384 | Documents embedded before the switch must be embedded again, and until then they **fail silently**: searches return nothing, with no error (O2). Admin → Settings → Documents has "Reindex knowledge base vectors" (`POST /api/v1/knowledge/reindex`) for knowledge bases. Files attached straight to a chat stay empty even after re-processing, and must be uploaded again |

## 3. Design

### 3.1 Settings (`deploy/settings.env`)

| Setting | Before (CR-1) | After |
|---|---|---|
| `RAG_EMBEDDING_ENGINE` | `openai` | empty: Open WebUI's local SentenceTransformers |
| `RAG_EMBEDDING_MODEL` | `voyageai/voyage-4-lite` | `BAAI/bge-small-en-v1.5` |
| `RAG_OPENAI_API_BASE_URL` | OpenRouter | removed |

`RAG_EMBEDDING_BATCH_SIZE` stays unset: 1 and 32 took the same time (O3, D6).

### 3.2 Startup (`deploy/entrypoint.sh`)

- It no longer reads `openrouter-api-key` into `RAG_OPENAI_API_KEY`. After CR-3, the key is read only by the Router,
  from its file, on each message. Open WebUI's process no longer holds it.
- It still checks the file at start, without keeping the value, so a placeholder still stops the start with a
  clear message instead of failing at the first chat.
- The secrets files don't change: `openrouter-api-key` stays, for the Router.

### 3.3 The live server: a one-time switch (owner)

In Admin → Settings → Documents, in this order:
1. With the engine still on OpenAI, **blank the API key** field and save, so the database drops CR-1's copy of the
   key.
2. Set the engine to **Default (SentenceTransformers)** and the model to `BAAI/bge-small-en-v1.5`, then save.
   Open WebUI downloads the model (about 380 MB; the save took 73 s on the laptop).
3. Click **Reindex knowledge base vectors**. Until then, old knowledge bases silently find nothing. Files attached
   straight to a chat before the switch also find nothing, and re-processing doesn't fix them: tell people to
   upload them again.

A fresh install needs none of this: `settings.env` applies on its first start.

### 3.4 What gets better, what gets worse

- **Better:** documents never leave the server (closes CR-1's R6/R10 tension). One fewer copy of the key. No
  embedding cost (it was $0.02 per 1M tokens, so tiny). Document search keeps working when OpenRouter or the
  credits are down.
- **Worse:** bge-small is a small English model (33M parameters, 384 dimensions, 512-token input). On this repo's
  docs it found the right passage in the top 3 as often as voyage-4-lite (12/12), but it's poor for other
  languages. Embedding uses the CPU: about 30 s per 280 kB of text (about 90 pages) on 4 laptop cores, so longer on
  the N100. `webui` holds about 120 MB more at rest and peaked about 400 MB higher while embedding (O3).

## 4. Spike findings (2026-10-31, laptop, Open WebUI v0.12.5, `webui` limited to 4 cores)

The spike started a stack the way the server ran (OpenRouter embeddings, laptop key), indexed a knowledge base of
this repo's docs (68 kB) and a file attached to a chat, then switched it through the API exactly as in §3.3. Search
quality was scored with 12 questions whose answers are in known passages.

| # | Question | Finding |
|---|---|---|
| O1 | First-start download, and a later start without Hugging Face | The switch downloaded about 380 MB (§2) in 73 s. **Restarted with no network**, `webui` was healthy in about 15 s, loaded the model from `data/cache`, and answered a search correctly |
| O2 | Switching an install with OpenAI-engine documents | Blanking the key first leaves `rag.openai.api_key` empty (D3). Before the reindex, the old knowledge base found **nothing (0/12), silently**; the reindex took 15 s, then 12/12. The old chat file stayed empty even after `POST /retrieval/process/file`; uploaded again, it embedded in 1.5 s and was found |
| O3 | Memory and embedding time | `webui`: 741 MiB at start, 861 MiB with the model loaded, 1.12 GiB after embedding. A 280 kB file took 30–33 s at batch size 1 and 31 s at 32: no difference (D6) |
| O4 | BAAI's query instruction | **No gain:** 9/12 first and 12/12 in the top 3, with and without it (D5). voyage-4-lite also scored 12/12 in the top 3 |

## 5. Build order

Each phase ends in a commit (`CR-3: …`) on branch `story/main/CR-3-local-embeddings`, which is fast-forward merged
into `main` once phase 4 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O4** on the laptop | Findings in §4; the design adjusted where needed | Done (§4; D6 added, sizes and chat files corrected) |
| 2 | **Settings and startup** | `settings.env` (§3.1), `entrypoint.sh` (§3.2); tests: the engine is local and no `RAG_OPENAI_*` setting remains | Done |
| 3 | **Docs** | `deploy/README.md` (the one-time switch as a (you) step, key rotation without the Documents copy, a troubleshooting row), `ENVIRONMENT.md` (settings table, secrets table: the key is read by the Router only), and "superseded by CR-3" notes on CR-1's R10, §2 (R6 and R10 row) and §3.3 | Done |
| 4 | **Local stack check** | A fresh stack embeds and answers from a knowledge base with the local engine; a stack switched from the OpenAI engine, as in §3.3 (the spike) | Done (§6) |
| 5 | **Server (owner)** | Down, Up, then the runbook's "Once: switch the running server to on-server embeddings", and a question about a document | Done (2026-10-31, below) |

**On the server (2026-10-31):** the engine was switched to Default before the key was cleared, so Open WebUI kept
its saved copy of the OpenRouter key: it saves the key field only while the engine is OpenAI, and hides it on
Default. Fixed with the same two saves the page makes (OpenAI with an empty key, then Default with bge-small); the
model loaded from disk again, and the runbook now says how to recover from this. Checked afterwards: engine Default,
model bge-small, no key saved, and no table in Open WebUI's database holds the key; the model is 382 MB in
`data/`; the reindex found no knowledge bases (the server had no documents before, only generated images, so
nobody had files to upload again); a test file was processed in 2 s, Auto answered a question about it with a
citation, and the file was deleted; `webui` uses about 840 MB.

## 6. Verification

**Results on the laptop (2026-10-31):** 53 offline tests pass. The switched install is the spike (§4). A **fresh
install** from the working tree downloaded the model during its first start (`/health` answered after 109 s; Docker
showed the container unhealthy until its next check), loaded it, and saved the local engine with bge-small and no
OpenRouter key. A knowledge base of this repo's docs embedded in 6 s and scored 12/12 in the top 3. A chat on Auto
with the knowledge base answered from it, with a citation ("`af_heart` … [1]"). No process in `webui` had the
OpenRouter key in its environment.

| Check | How |
|---|---|
| Offline tests | `pytest`: the embedding engine is local, the model is bge-small-en-v1.5, no `RAG_OPENAI_*` setting, and the entrypoint doesn't export `RAG_OPENAI_API_KEY` |
| Nothing goes to OpenRouter | OpenRouter's activity shows no embedding request after uploading and asking about a PDF |
| Fresh install | An uploaded PDF is answered from on the first start (after the model download) |
| Switched install | After §3.3, a reindexed knowledge base answers, and `rag.openai.api_key` is empty |
| Key out of Open WebUI | `docker exec` into `webui`: no process environment holds the OpenRouter key |
| Server (owner) | The §3.3 switch, then a question about an uploaded PDF |

## 7. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | `BAAI/bge-small-en-v1.5` | Owner's choice; documents are mainly English. On English it comes within a few points of bge-m3 at about 1/17 of the compute, and Open WebUI's ~1,000-character chunks (~250 tokens) fit its 512-token input, so bge-m3's long input would go unused. Uploads take seconds on the N100, not minutes. Rejected: `all-MiniLM-L6-v2`, Open WebUI's default (weakest retrieval, and its 256-token input sits right at the chunk size); `BAAI/bge-m3` (best and multilingual, but a 2.2 GB download, about 2.5 GB of memory, and minutes of all four cores per large PDF); `intfloat/multilingual-e5-small` (only worth it for non-English documents). If those appear, bge-m3 is an Admin → Settings change plus a reindex |
| D2 | The live server switches by hand in Admin → Settings, once | It's three clicks, once. A loader step that rewrites saved settings would also override the admin's later choices, and the retrieval API isn't a stable interface |
| D3 | Blank the OpenRouter key in Documents before switching the engine | The switch leaves the key in the database otherwise, a copy the guardrail says shouldn't exist |
| D4 | Keep mounting all of `${CHAT}/data`, so the model is downloaded once into `data/cache` | bge-small isn't among the baked models anyway, and the download (about 380 MB) happens once |
| D5 | No query prefix | O4: it made no difference on the spike's questions. BAAI says v1.5 loses only slightly without it, and a prefix is another saved setting the live server would need to match by hand |
| D6 | No `RAG_EMBEDDING_BATCH_SIZE` setting | O3: batch sizes 1 and 32 took the same time |
