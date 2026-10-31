# CR-3: Document embeddings on the server

Document search embeds with Open WebUI's own embedding model, running in the `webui` container, instead of
sending document chunks to OpenRouter's `/embeddings`. This replaces CR-1's R10 and §3.3. **State: planned, not
started.**

## 1. Requirements

| # | Requirement | Comes from |
|---|---|---|
| R1 | Document embeddings run on the server, in Open WebUI's own embedding engine, with `BAAI/bge-small-en-v1.5`. No document text goes to OpenRouter for embedding. Documents are mainly English | Owner (model chosen over MiniLM and bge-m3, D1) |
| R2 | The live server switches too, not only fresh installs, and documents uploaded before the switch still work or are clearly re-done | Follows from R1: Open WebUI keeps these settings in its database (§2) |
| R3 | CR-1's guardrails stay: the OpenRouter key lives only in its secrets file | CLAUDE.md |

## 2. What the image and Open WebUI already do (checked in v0.12.5, 2026-10-31)

| Fact | Consequence |
|---|---|
| The image bakes its default embedding model, `all-MiniLM-L6-v2`, and Whisper `base` under `/app/backend/data/cache/`; our mount of `${CHAT}/data` over `/app/backend/data` hides them (this is also why the first dictation downloads Whisper's model) | bge-small isn't baked in anyway. Open WebUI downloads it (about 130 MB) from Hugging Face on the first start into `data/cache/embedding/models`, where it stays |
| `sentence-transformers` and PyTorch are already in the image | Nothing to add: this is part of what makes the image 6.4 GB |
| The embedding settings (`rag.embedding_engine`, `rag.embedding_model`, `rag.openai.api_key`, …) are saved in the database on the first start; the environment is ignored after that | `settings.env` only sets them for a fresh install. The live server switches once in Admin → Settings → Documents (§3.3) |
| Switching the engine (`POST /api/v1/retrieval/embedding/update`) doesn't clear `rag.openai.api_key` | The copy of the OpenRouter key that CR-1 put in the database would stay there unused. The switch blanks it first (§3.3) |
| Vectors from different models don't mix: voyage-4-lite gives 1024 dimensions, bge-small 384 | Documents embedded before the switch must be embedded again. Admin → Settings → Documents has "Reindex knowledge base vectors" (`POST /api/v1/knowledge/reindex`); files attached straight to a chat are re-uploaded (O2) |

## 3. Design

### 3.1 Settings (`deploy/settings.env`)

| Setting | Before (CR-1) | After |
|---|---|---|
| `RAG_EMBEDDING_ENGINE` | `openai` | empty: Open WebUI's local SentenceTransformers |
| `RAG_EMBEDDING_MODEL` | `voyageai/voyage-4-lite` | `BAAI/bge-small-en-v1.5` |
| `RAG_OPENAI_API_BASE_URL` | OpenRouter | removed |
| `RAG_EMBEDDING_BATCH_SIZE` | (1) | set from the spike's timing on the laptop (O3), if a larger batch is clearly faster |

### 3.2 Startup (`deploy/entrypoint.sh`)

- It no longer reads `openrouter-api-key` into `RAG_OPENAI_API_KEY`. After CR-3, the key is read only by the Router,
  from its file, on each message. Open WebUI's process no longer holds it.
- The secrets files don't change: `openrouter-api-key` stays, for the Router.

### 3.3 The live server: a one-time switch (owner)

In Admin → Settings → Documents, in this order:
1. With the engine still on OpenAI, **blank the API key** field and save, so the database drops CR-1's copy of the
   key.
2. Set the engine to **Default (SentenceTransformers)** and the model to `BAAI/bge-small-en-v1.5`, then save.
   Open WebUI downloads the model (about 130 MB).
3. Click **Reindex knowledge base vectors**. Files attached straight to a chat before the switch are uploaded again
   when someone needs them (O2).

A fresh install needs none of this: `settings.env` applies on its first start.

### 3.4 What gets better, what gets worse

- **Better:** documents never leave the server (closes CR-1's R6/R10 tension). One fewer copy of the key. No
  embedding cost (it was $0.02 per 1M tokens, so tiny). Document search keeps working when OpenRouter or the
  credits are down.
- **Worse:** bge-small is a small English model (33M parameters, 384 dimensions, 512-token input), so retrieval
  is somewhat weaker than voyage-4-lite, and poor for documents in other languages. Embedding uses the N100's CPU
  (seconds for a typical PDF) and adds about 200 MB to `webui`'s memory (O3).

## 4. Open items (verified in phase 1)

| # | Question |
|---|---|
| O1 | A fresh install with `settings.env`'s new values downloads bge-small into `data/cache` on the first start, and a later start works with Hugging Face unreachable (the model comes from the cache) |
| O2 | Switching an install that has OpenAI-engine documents: blanking the key clears `rag.openai.api_key`; after the switch, old knowledge bases fail until reindexed and work after; what happens to a file attached to an old chat (an error, or nothing found) |
| O3 | Cost on the laptop, limited to 4 cores (`--cpus 4`): `webui`'s memory before and after the model loads (`docker stats`), and the time to embed a 50-page PDF with batch sizes 1 and 32. The N100's cores are slower; the runbook gives the laptop numbers as a best case |
| O4 | BAAI's optional query instruction (`RAG_EMBEDDING_QUERY_PREFIX` = "Represent this sentence for searching relevant passages: "): on a test PDF with about ten questions, does it find the right passage more often than no prefix? |

## 5. Build order

Each phase ends in a commit (`CR-3: …`) on branch `story/main/CR-3-local-embeddings`, which is fast-forward merged
into `main` once phase 4 passes.

| # | Phase | Output | State |
|---|---|---|---|
| 0 | **Plan** | This file and the [index](README.md) entry | Done |
| 1 | **Spike: O1–O4** on the laptop | Findings in §4; the design adjusted where needed | Not started |
| 2 | **Settings and startup** | `settings.env` (§3.1), `entrypoint.sh` (§3.2); tests: the engine is local and no `RAG_OPENAI_*` setting remains | Not started |
| 3 | **Docs** | `deploy/README.md` (the one-time switch as a (you) step, key rotation without the Documents copy, a troubleshooting row), `ENVIRONMENT.md` (settings table, secrets table: the key is read by the Router only), and "superseded by CR-3" notes on CR-1's R10, §2 (R6 and R10 row) and §3.3 | Not started |
| 4 | **Local stack check** | A fresh stack embeds and answers from an uploaded PDF with no `/embeddings` call on OpenRouter's activity; a stack switched from the OpenAI engine, as in §3.3 | Not started |
| 5 | **Server (owner)** | After CR-2's cutover: Down, Up, then the §3.3 switch, and a question about an uploaded PDF | Not started |

## 6. Verification

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
| D4 | Keep mounting all of `${CHAT}/data`, so the model is downloaded once into `data/cache` | bge-small isn't among the baked models anyway, and the download is about 130 MB, once |
| D5 | No query prefix, unless O4 shows a clear gain | BAAI says v1.5 loses only slightly without its instruction. A prefix is another saved setting the live server would need to match by hand |
