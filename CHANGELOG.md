# Changelog

All notable changes to PDF-MD Converter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.3] - 2026-04-26 — Provider/Model Mismatch Fix & Model Picker

### Fixed

- **OpenRouter (and other non-Datalab providers) returned HTTP 400 `"datalab/marker-ocr is not a valid model ID"`** for every conversion. The default config ships with `default_model: "datalab/marker-ocr"`, and the convert handler passed it straight through to whatever provider was active. When the user switched the active provider in Settings, the model field was never updated (the UI didn't expose it), so requests to OpenRouter / OpenAI / Anthropic / Google all carried a Datalab-only model id.

### Added

- **`PROVIDER_DEFAULT_MODELS` table + `_resolve_model()` guard** in `app.py`. Every place the model is read for a request, it is validated against `PROVIDER_MODELS[active_provider]`; if it doesn't belong, a sensible per-provider default is substituted (OpenRouter → `anthropic/claude-sonnet-4-6`, OpenAI → `gpt-6-omni-mini`, Anthropic → `claude-sonnet-4-6`, Google → `gemini-3.0-flash`, Datalab → `datalab/marker-ocr`).
- **Same sanitization on save** — `POST /api/config` now merges incoming changes onto the existing config and resolves `default_model` against `active_provider` before writing, so the saved file can never hold an invalid combo even if edited externally.
- **Model picker in Settings UI.** A `Default Model` `<select>` is populated from a new `GET /api/models?provider=<id>` endpoint and re-populates when the provider dropdown changes. The user's choice is sent back in the save payload and the dropdown reselects whatever the server resolved to.
- **`GET /api/models`** generalized — accepts `?provider=<id>` (defaults to active provider) and `?vision_only=true`; returns `{provider, default, models}`.

### Changed

- `POST /api/config` now performs a deep merge with the existing config (top-level + `api_keys` map) instead of overwriting the entire file. Partial saves no longer wipe unrelated fields.
- Removed the stale `sys.path.append` workaround in `/api/models` (the import is at module scope now) and the unused `sys` / `make_response` imports.

---

## [2.3.2] - 2026-04-25 — Settings Panel Visibility Fix

### Fixed

- **Settings tab rendered blank.** The `<section id="view-settings">` element shipped with both the `view-section` and `hidden` classes. `.hidden { display: none !important; }` overrode the `.view-section.active { display: block; }` toggle written by the navigation handler, so clicking Settings produced an empty pane. Removed the redundant `hidden` class from the markup; `view-section` already defaults to `display: none` and is shown via the `active` class.

---

## [2.3.1] - 2026-04-23 — macOS DMG Launcher Fixes

### Fixed

- **Launcher crashed when the app was run directly from a mounted DMG** (`[Errno 30] Read-only file system` on `.venv` creation → cascading `pip: command not found` → `ModuleNotFoundError: No module named 'flask'`). The launcher now puts its venv and runtime data in `~/Library/Application Support/Doc-2-Markdown/` instead of trying to write inside the (potentially read-only) app bundle.
- **Port 5000 collision with macOS AirPlay Receiver** (produced an opaque HTTP 403 in Chrome when AirPlay Receiver was enabled in Control Center). Default port moved to 5005; overridable via the `DOC2MD_PORT` environment variable.

### Changed

- `pdf_to_markdown.py` resolves `config.json`, `usage.json`, and `custom_prompts.json` against `$DOC2MD_DATA_DIR` when set, falling back to the module directory for dev-mode runs from a source checkout.
- `app.py` reads `$DOC2MD_PORT` on startup.
- `start_mac.sh` rewritten to: resolve its own bundle dir, create the venv and data dir under Application Support, install requirements against the bundled `requirements.txt`, clear any stale listener on the chosen port, and export both `DOC2MD_DATA_DIR` and `DOC2MD_PORT` before launching `app.py`.

---

## [2.3.0] - 2026-04-19 — Resumable Conversions & Build Fixes

### Added

#### Resume-after-interrupt for AI enhancement
- **Per-job checkpoint directory** under the OS temp dir, keyed by a sha256 hash of the uploaded file. The input, a single atomic `checkpoint.json`, and the output share this directory for the life of the job.
- **`DocumentConverter._run_ai_chunks()`** — new helper that drives the AI enhancement loop with optional file-based checkpointing. After every completed chunk it persists the full running state (index, token totals, joined partial markdown, input hash) in a single JSON file written via `os.replace`, so the on-disk state is always consistent with a chunk boundary even across crashes mid-write.
- **Content-hash invalidation** — the checkpoint stores a sha256 of the concatenated input chunks. A re-upload with the same file but different settings (e.g., OCR toggled) produces different chunks and the stale checkpoint is discarded rather than silently resumed.
- **Auto-resume on re-upload** — re-POSTing the same file to `/api/convert` picks up at the first incomplete chunk instead of restarting from scratch.
- **`GET /api/jobs`** — lists incomplete jobs with chunk progress and accumulated token totals.
- **`DELETE /api/jobs/<job_id>`** — discards a partial job (path-traversal guarded).
- Success response from `/api/convert` now includes `job_id` and `resumed`; error response includes `job_id` and `resumable`.

### Fixed

- **`/api/models` NameError** — `app.py` referenced `sys` without importing it; any request to `/api/models` raised `NameError`.
- **`/api/convert` never worked** — the handler bound `converter.convert_file(...)` to a single name but the method returns a 3-tuple `(content, path, cost_info)`, and then called `open()` on that tuple. Also passed an invalid `config=` kwarg that propagated into `convert_pdf_to_markdown` as an unexpected keyword argument. Both paths crashed every conversion.
- **Missing runtime dependencies** — `pymupdf4llm` (used by default in the PDF path) and `requests` (used by `DatalabClient`) were imported at module scope but absent from `requirements.txt`, so fresh installs failed on first PDF or first Datalab request.
- **Input `accept` mismatch** — the upload control advertised `.doc` (unsupported server-side) and omitted `.eml` and `.mobi` (both supported). Corrected to match the backend's actual dispatch table.
- **Installer shipped `.DS_Store`** — `build_mac_installer.sh` did not exclude macOS metadata files from the DMG payload. Added to the rsync exclude list.
- **O(N²) newline collapse** — the post-extraction cleanup used `while "\n\n\n" in s: s = s.replace("\n\n\n", "\n\n")`, which rebuilds the full string each iteration. Replaced with a single `re.sub(r'\n{3,}', '\n\n', ...)` pass.

### Notes

- OpenRouter is a stateless request/response API — it has no server-side notion of "resume a prior generation." Recovery is implemented entirely on the app side, on top of the existing per-request retry loop (3 attempts with exponential backoff on `IncompleteRead` / 5xx / `URLError` / `TimeoutError`). The retry now sits *inside* the checkpointed loop, so a chunk that survives the retries is persisted before work moves on to the next chunk.
- Checkpointing is wired through the PDF path (both the PyMuPDF4LLM and fallback AI loops). The EPUB AI loop is unchanged.

---

## [2.2.1] - 2026-02-18 — Wave 22: Documentation Polish

### Added
- Expanded README with additional conversion examples (DOCX, email, EPUB, batch CLI)
- Comprehensive input/output samples for each supported format
- Updated CHANGELOG with improvement wave history

---

## [2.2.0] - 2026-01-04

### Added

#### PyMuPDF4LLM Integration (2025 Best Practice for LLM/RAG)
- **New `extract_with_pymupdf4llm()` method** - Superior markdown extraction:
  - Optimized for LLM/RAG applications with proper reading order
  - Automatic table detection and GitHub-compatible markdown formatting
  - Header detection based on font size analysis
  - Page chunking with metadata for citation support
- **Image handling options**:
  - `extract_images`: Save images to disk with configurable DPI/format
  - `embed_images`: Base64-encode images directly in markdown
- **Automatic fallback**: Uses PyMuPDF4LLM when available, falls back to traditional extraction

#### Enhanced Table Extraction (96% Accuracy)
- **Optimized pdfplumber parameters** based on 2025 research:
  - `snap_x_tolerance`: Improved column boundary detection
  - `snap_y_tolerance`: Better row alignment
  - Dual-strategy approach: strict line-based first, text-based fallback
- **Table filtering**: Removes false positives (single-cell "tables")
- **Lattice + Stream modes**: Handles both bordered and borderless tables

#### Marker-style LLM Enhancement
- **`use_pymupdf4llm` parameter** (default: True) for best-quality extraction
- **Extraction method tracking**: Output includes `extraction_method` field
- **Combined workflow**: PyMuPDF4LLM extraction + AI enhancement option

### Changed

- **Default extraction method**: Now uses PyMuPDF4LLM when available (4x faster, more accurate)
- **Table detection**: Enhanced with optimized parameters for complex layouts

### Dependencies

New optional dependency:
```
pymupdf4llm>=0.2.0  # LLM-optimized PDF extraction
```

---

## [2.1.0] - 2026-01-04

### Added

#### Advanced RAG Chunking Strategies
- **Semantic Chunking** (`chunk_semantic()`) - Embedding similarity-based chunking:
  - Splits text at natural semantic boundaries using cosine similarity
  - Configurable similarity threshold (default: 0.75)
  - Up to 70% improvement in retrieval accuracy over fixed-size chunking
- **Contextual Chunking** (`chunk_contextual()`) - Document context preservation:
  - Adds document context prefix to each chunk
  - Auto-generates context summary if not provided
  - 2-18% improvement in retrieval performance
- **Hybrid/Advanced Chunking** (`chunk_advanced()`) - Master strategy selector:
  - Supports strategies: `structure`, `semantic`, `contextual`, `hybrid`
  - Content retention verification (95%+ threshold)
  - Returns detailed statistics (chunks, coverage, retention percentage)
- **Content Retention Verification** (`_verify_content_retention()`):
  - Character-level and word-level retention tracking
  - Ensures no content loss during chunking process
  - Warning logs if retention falls below 95%

#### SimpleVectorStore (ChromaDB Alternative)
- **New `SimpleVectorStore` class** - Numpy-based vector storage:
  - Zero external dependencies (only numpy required)
  - Fallback when ChromaDB has compatibility issues
  - Persistent JSON + numpy storage format
  - Full CRUD operations (add, delete, search)
  - Cosine similarity search with metadata filtering
- **`get_vector_store()` factory function** - Automatic backend selection:
  - Tries ChromaDB first, falls back to SimpleVectorStore
  - Transparent API compatibility

#### Enhanced EPUB Processing (2025 Best Practices)
- **BeautifulSoup-based HTML parsing** with regex fallback:
  - Robust handling of malformed EPUB HTML
  - Proper tag nesting and attribute extraction
- **Complete Metadata Extraction**:
  - Title, author, publisher, publication date
  - ISBN, language, subjects/categories
  - All Dublin Core metadata fields
- **Table of Contents Preservation**:
  - Extracts NCX navigation structure
  - Generates markdown TOC with proper hierarchy
- **Table Detection and Conversion**:
  - HTML table to Markdown table conversion
  - Proper column alignment and header detection
- **Footnote/Endnote Processing**:
  - Detects and extracts footnotes from EPUB content
  - Appends footnotes section to markdown output
- **Image Reference Handling**:
  - Extracts image references with alt text
  - Preserves image paths for potential extraction

#### Network Resilience
- **API Retry Logic** in `_make_request()`:
  - 3 retry attempts with exponential backoff
  - Handles `IncompleteRead`, `HTTPError`, `URLError`, `TimeoutError`
  - 300-second timeout for large documents

### Fixed

- **ChromaDB/Pydantic 2.x Compatibility** - SimpleVectorStore fallback eliminates dependency conflicts
- **"log_message" AttributeError** - Fixed 10 occurrences to use correct `log()` method
- **IncompleteRead Network Errors** - Retry logic prevents failed conversions on unstable connections

### Dependencies

New requirements added to `requirements.txt`:
```
# Enhanced EPUB Processing
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

---

## [2.0.0] - 2025-12-29

### Added

#### RAG (Retrieval-Augmented Generation) System
- **New `rag_module.py`** (~900 lines) - Complete RAG implementation for legal documents
- **RAGChunker class** - Legal document-aware hierarchical chunking with:
  - Document type configurations (statute, case_law, treatise, rule, contract, pleading)
  - Citation preservation to prevent splitting legal citations
  - Configurable overlap (10-15%) between chunks
  - Header-based structure detection
- **EmbeddingClient class** - OpenAI embeddings integration:
  - Support for `text-embedding-3-large` (3072 dimensions)
  - Batch processing with rate limiting
  - Cost estimation for budget tracking
- **LocalEmbeddingClient class** - Free offline embeddings:
  - `all-MiniLM-L6-v2` (384 dimensions, fast)
  - `all-mpnet-base-v2` (768 dimensions, better quality)
  - `legal-bert-base-uncased` (768 dimensions, legal domain optimized)
  - Automatic fallback when OpenAI API unavailable
- **VectorStore class** - ChromaDB integration:
  - Dual API support for ChromaDB 0.3.x and 0.4+
  - Persistent storage with metadata filtering
  - Document management (add, delete, search)
  - Statistics tracking (document count, chunk count, storage size)
- **HybridRetriever class** - Advanced search:
  - 70% dense (vector) / 30% sparse (BM25) hybrid search
  - Reciprocal Rank Fusion for result merging
  - Metadata filtering by jurisdiction, document type, date range
  - Configurable top-k retrieval
- **get_embedding_client()** factory function - Automatic provider selection

#### GUI Enhancements
- **New "RAG/Vector" tab** with full search interface:
  - Vector database status panel (documents, chunks, storage size)
  - Document management treeview with vectorization status
  - Retrieval search interface with query input
  - Filter dropdowns for document type and jurisdiction
  - Results display with similarity scores and source preview
  - Full chunk preview panel
  - Settings panel for auto-vectorize and hybrid search toggles
- **Auto-vectorize after conversion** - One-click conversion and indexing workflow
- **Vectorize folder** - Batch vectorization of existing markdown files
- **Delete from vector DB** - Remove documents from the index

#### LLM Model Updates
- **Gemini 2.x models** (11 new models):
  - `google/gemini-2.5-pro` - Best quality
  - `google/gemini-2.5-pro-preview` - Preview version
  - `google/gemini-2.5-pro-exp-03-25` - Free experimental
  - `google/gemini-2.5-flash` - Fast, cost-effective
  - `google/gemini-2.5-flash-preview-09-2025` - Preview version
  - `google/gemini-2.5-flash-lite` - Ultra-fast, cheapest
  - `google/gemini-2.5-flash-lite-preview-09-2025` - Preview version
  - `google/gemini-2.0-flash-001` - Stable 2.0
  - `google/gemini-2.0-flash-lite-001` - Cheapest Gemini
  - `google/gemini-2.0-flash-exp:free` - Free experimental
  - `google/gemini-2.0-flash-thinking-exp:free` - Free thinking model
- **Updated Claude models**:
  - `anthropic/claude-opus-4.5` - Best quality
  - `anthropic/claude-sonnet-4.5` - Balanced (default)
- **Updated GPT models**:
  - `openai/gpt-4o` - Latest GPT-4 Omni
  - `openai/gpt-4o-mini` - Cost-effective
  - `openai/gpt-4.1` - Latest GPT-4.1
  - `openai/gpt-4.1-mini` - Mini version
  - `openai/gpt-4.1-nano` - Smallest/fastest
- **DeepSeek models**:
  - `deepseek/deepseek-chat-v3-0324` - Latest DeepSeek
  - `deepseek/deepseek-r1` - Reasoning model
  - `deepseek/deepseek-r1:free` - Free tier

### Changed

- **Default model** changed from `anthropic/claude-3.5-sonnet` to `anthropic/claude-sonnet-4.5`
- **Config schema** extended with `rag_settings` section:
  ```json
  {
    "rag_settings": {
      "enabled": true,
      "auto_vectorize": true,
      "embedding_provider": "openai",
      "embedding_model": "text-embedding-3-large",
      "vector_db_path": "./vector_db",
      "default_doc_type": "default",
      "chunk_overlap_pct": 0.12,
      "retrieval_k": 10,
      "hybrid_search": true
    }
  }
  ```

### Fixed

- **OpenRouter API 404 errors** - Updated all model IDs to current 2025 naming conventions
- **ChromaDB compatibility** - Added support for both old (0.3.x) and new (0.4+) APIs:
  - Old API: `chromadb.Client(Settings(chroma_db_impl="duckdb+parquet", persist_directory=...))`
  - New API: `chromadb.PersistentClient(path=...)`
  - Automatic detection and fallback

### Dependencies

New requirements added to `requirements.txt`:
```
# RAG and Vector Database
chromadb>=0.4.0
rank-bm25>=0.2.2
openai>=1.0.0
PyYAML>=6.0
```

Optional for local embeddings:
```
sentence-transformers>=2.2.0
```

---

## [1.0.0] - 2025-12-XX

### Added
- Initial release
- PDF to Markdown conversion with AI enhancement
- Multi-provider LLM support (OpenRouter, OpenAI, Anthropic, Google)
- OCR support via Tesseract
- Table detection with pdfplumber and tabula-py
- Multiple input formats: PDF, DOCX, TXT, MSG, EML, EPUB, MOBI
- Multiple output formats: Markdown, HTML, DOCX
- Drag & drop interface
- Batch processing with parallel workers
- Cost tracking and budget management
- Custom prompt templates
- Preview tab with live markdown rendering
