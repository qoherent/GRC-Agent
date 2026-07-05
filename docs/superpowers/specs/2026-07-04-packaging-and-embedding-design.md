# Packaging & Embedding Provider Design

- **Date:** 2026-07-04
- **Status:** Approved
- **Scope:** Single combined spec — (A) packaging/README/deps cleanup + (B) embedding-provider abstraction, per-backend vector stores, GUI embedding controls, and `.env`-as-truth persistence refactor.

## Context & verified facts

- Packaging baseline is already production-grade: `uv` + `hatchling` + `src/` layout + `pyproject.toml` + `uv.lock` + MIT. Output artifacts (`dist/`, `.grc_agent/`, `vectors/`, `tests/output/`) are already gitignored.
- **Embeddings are Ollama-only by construction.** `runtime/doc_answer.py:get_embedding` POSTs to Ollama's **deprecated** `/api/embeddings` native endpoint via `httpx`. On the OpenRouter backend the chat URL is reused, so the embed call would hit `openrouter.ai/api/embeddings` and fail — OpenRouter mode silently has no working RAG today.
- **The "configurable embedding model" is dead.** `LlamaConfig.embedding_model`, `grc_agent.toml [llama].embedding_model`, and `agent._embedding_model` are set and never read (1 occurrence — the assignment). The live value is the literal `_EMBED_MODEL = "embeddinggemma:latest"` in `runtime/_embedding_config.py:14`. The CHANGELOG claim is stale.
- **Dimension is hardcoded.** `_EMBED_DIM = 768` is baked into both `vec0` schemas (`catalog_vector.py`, `doc_answer.py:120`) and re-shadowed in `doc_answer.py:120`. Any embedding-model swap to a different dimension corrupts the index.
- **Inconsistent persistence.** Ollama chat model → `grc_agent.toml` + `preferences.json` (no env var). OpenRouter chat model → `.env` (`OPENROUTER_MODEL`). OpenRouter GUI swap ignores the typed name and re-reads env (fragile coupling via write-then-read).
- **Latent packaging bug.** `python-dotenv` and `openai` are used directly by app code but only transitively declared (via ToolAgents). If ToolAgents drops either, the app breaks.
- **External (grounded):** `perplexity/pplx-embed-v1-0.6b` exists on OpenRouter (released 2026-03-16, $0.004/M tokens, 32K context, single provider). OpenRouter embeddings are OpenAI-compatible at `/api/v1/embeddings` (supports `dimensions`, `encoding_format`, string-or-array input). Ollama exposes `/v1/embeddings` usable via the `openai` Python SDK with `base_url=http://localhost:11434/v1/` — **verified by smoke test on Ollama 0.30.11** (returns 768-dim vector for `embeddinggemma:latest`).

## Decisions (locked)

1. **Persistence:** `.env` is the single source of truth for all model names AND keys. GUI edits write `.env` bidirectionally. No live file-watch (restart-to-apply for manual edits).
2. **Embedding backend coupling:** embedding follows the chat backend. OpenRouter chat → OpenRouter embeddings (no local Ollama required). Ollama chat → Ollama embeddings.
3. **Two per-backend vector stores:** Ollama pair and OpenRouter pair coexist; switching backend swaps which pair is active — no rebuild on switch. Each pair rebuilds only if its embedding-model name changes.
4. **Embedding code path (Approach A):** the `openai` SDK `/v1/embeddings` endpoint for both backends, mirroring how chat already uses the `openai` SDK for both. Deletes the bespoke `httpx`/`/api/embeddings` code and the deprecated-endpoint knowledge.
5. **Distribution:** source-only via `uv` (git clone + `uv sync`, plus `uv tool install git+…`). No PyPI pipeline (GNU Radio is a hard system dep; a PyPI install would silently lack it).
6. **Repo pruning:** delete unreferenced `docs/adhoc_cleaner_agent_prompt.md` and `docs/superpowers/plans/`. Keep `docs/superpowers/specs/`.

## Design

### §1 Config & persistence — `.env` as single source of truth

Six variables in `.env`:

| Var | Scope | Default | Status |
|---|---|---|---|
| `OPENROUTER_API_KEY` | chat+embed auth (OpenRouter) | — | exists |
| `OLLAMA_API_KEY` | web-search tool auth (Ollama) | — | exists |
| `OLLAMA_MODEL` | chat model (Ollama) | `gemma4:e4b-it-qat-120k` | NEW |
| `OPENROUTER_MODEL` | chat model (OpenRouter) | `deepseek/deepseek-v4-flash` | exists |
| `OLLAMA_EMBEDDING_MODEL` | embedding model (Ollama) | `embeddinggemma:latest` | NEW |
| `OPENROUTER_EMBEDDING_MODEL` | embedding model (OpenRouter) | `perplexity/pplx-embed-v1-0.6b` | NEW |

- Generic `set_env_model(var, value)` helper (generalizes `set_openrouter_model_env`): writes `.env` via `dotenv.set_key` + updates `os.environ`.
- `LlamaConfig.model` / `.embedding_model` read from env (not toml). `agent._embedding_model` becomes live.
- **Removed:** `grc_agent.toml [llama].model` + `[llama].embedding_model` keys; `preferences.json last_model`. `grc_agent.toml` keeps `[llama].backend`, `[llama].server_url`, `[agent].*`. `preferences.json` keeps `provider_chosen` + `schema_version`.
- Breaking change documented in README/CHANGELOG; **no shim** (per AGENTS.md "no backward compatibility").

### §2 Embedding pipeline — dimension-agnostic, per-backend, model-stamped

- `get_embedding()` rewritten to use `openai` SDK against the active backend's `/v1/embeddings`. Model + base_url + api_key from the active backend config.
- **Dimension derived, not hardcoded.** Delete both `_EMBED_DIM` literals. At index build, embed a probe → `dim = len(vec)` → use in `vec0 CREATE`.
- **Two per-backend DB pairs:** `vectors/catalog_<backend>.db` + `vectors/docs_<backend>.db` for `backend ∈ {ollama, openrouter}`. Active pair selected by `LlamaConfig.backend`.
- **Model stamp → auto-rebuild.** Each DB has a `meta` table storing `embedding_model` + `dim`. On open, if stamped model ≠ current model (or dim mismatch) → drop & rebuild. Reuses `warmup_vector_index`.
- **Task prefix stays a uniform rule.** `_DOCUMENT_PREFIX`/`_QUERY_PREFIX` applied to all embed inputs (no per-model `if "gemma"` branch — forbidden heuristic). Tradeoff: optimal for gemma, harmless-but-possibly-suboptimal for pplx-embed; flagged for future tuning.
- **Correctness fix:** `runtime/llm_client.py:call_agent_llm` currently uses hardcoded default `gemma4:e4b-it-qat-120k` and ignores GUI swaps. Repoint to the active provider model (now from `.env`).

### §3 GUI changes (`ModelToolbar`)

- Add embedding-model field mirroring chat `model_combo`:
  - Ollama selected → editable combo.
  - OpenRouter selected → pencil-edit dialog writing `OPENROUTER_EMBEDDING_MODEL`.
- Fix OpenRouter chat-model fragility: typed name writes `.env` via `set_env_model`, then read back — single code path for both backends.
- On model/embedding-model change: persist to `.env`, rebuild provider config; if embedding model changed, trigger index rebuild.

### §4 Packaging & deps

- Declare direct deps actually used: `python-dotenv>=1.0`, `openai>=1.50`.
- Drop direct `httpx` usage (becomes purely transitive after Approach A).
- Keep `ToolAgents==0.3.0`, `pydantic>=2`, `pyyaml`, `sqlite-vec`, `numpy<2` marker, `[gui]`/`[dev]` extras.
- Distribution: source-only via `uv`.

### §5 Dead code & drift cleanup

- `README.md:2` stray command.
- `_EMBED_DIM` shadow at `doc_answer.py:120`.
- `.gitignore:53` stale `scripts/regenerate_vectors.py` ref.
- `grc_agent.toml:4-6` stale `docs/AGENT_FLOW_FINDINGS.md` ref.
- `AGENTS.md` module table references absent `runtime/clarification.py` — correct the doc line.
- Delete `docs/adhoc_cleaner_agent_prompt.md` and `docs/superpowers/plans/`.

### §6 README + `.env.example`

- New tracked `.env.example` with all 6 vars + comments + defaults.
- README rewrite: prerequisites (GNU Radio → wiki link, Ollama → ollama.com link, Python 3.12 + uv), setup (`uv venv --system-site-packages`, `uv sync`), configure (`cp .env.example .env`), model-pull instructions, run (`uv run grc-agent-gui`).
- "Choosing a model" section: 3 criteria (tool-calling, served via backend, adequate context) + worked `qwen3:27b` example + honest note that 120k context lives in the Ollama Modelfile, not the request.

### §7 Migration

- One-time user action: move model names from `grc_agent.toml` → `.env`. Documented, no shim.
- Existing `*_v1.db` indexes are superseded by per-backend DBs; first run after upgrade rebuilds the active backend's pair (one-time embed cost).

## Test strategy

- Smoke (done): Ollama `/v1/embeddings` + openai SDK returns 768-dim vector.
- Unit: `set_env_model` writes `.env` + `os.environ`; config reads 6 vars with defaults; dim derived from probe; index rebuild triggers on model-stamp mismatch.
- Integration: GUI field → provider model for both backends; GUI field → embedder model for both backends.
- Regression: no `_EMBED_DIM` literal returns; `grc_agent.toml` model keys absent.
- Gate: default `pytest -m "not grc_native and not gui and not llama_eval"` stays green (341 passed baseline).
