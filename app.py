from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
import shutil
import hashlib
import logging
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from werkzeug.utils import secure_filename
from pdf_to_markdown import DocumentConverter, load_config, save_config, check_dependencies, load_usage, PROVIDER_MODELS

app = Flask(__name__, static_folder='frontend', static_url_path='')
CORS(app)

# Ensure temp directory exists
TEMP_DIR = Path(tempfile.gettempdir()) / "doc2md_uploads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Persistent log file. Lives next to the rest of the user data so it
# survives across app launches and is visible whether you ran from
# /Applications, a DMG mount, or a source checkout.
LOG_DIR = Path(os.environ.get("DOC2MD_DATA_DIR") or Path(__file__).parent) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"


def _configure_logging():
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.INFO)

    for h in list(app.logger.handlers):
        app.logger.removeHandler(h)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(stream_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False

    # Werkzeug's request log too, so HTTP 5xx requests are easy to spot
    werk = logging.getLogger("werkzeug")
    werk.addHandler(file_handler)
    werk.setLevel(logging.INFO)


_configure_logging()
app.logger.info("Doc-2-Markdown starting (log file: %s)", LOG_FILE)

# Initialize document converter
converter = DocumentConverter()

# A safe default model per provider — used when the saved config has a
# model that doesn't belong to the currently selected provider (e.g. user
# switched from Datalab to OpenRouter but config.default_model is still
# "datalab/marker-ocr", which OpenRouter rejects with HTTP 400).
# Defaults verified against the live OpenRouter catalog. OpenRouter uses
# dots in version suffixes (claude-sonnet-4.6), the static PROVIDER_MODELS
# table in pdf_to_markdown.py used dashes (claude-sonnet-4-6) which is why
# the saved config could end up pointing at a model that doesn't exist.
PROVIDER_DEFAULT_MODELS = {
    "openrouter": "anthropic/claude-sonnet-4.6",
    "openai": "gpt-5-mini",
    "anthropic": "claude-sonnet-4-5",
    "google": "gemini-2.5-flash",
    "datalab": "datalab/marker-ocr",
}


def _resolve_model(provider, configured_model):
    """Return a model id valid for the given provider, falling back to a
    sensible default when the configured one doesn't belong here.

    For OpenRouter we check the live catalog (cached) first, because the
    static PROVIDER_MODELS table has gone stale and contains model ids
    that OpenRouter actually rejects (e.g. openai/gpt-5.5-mini)."""
    if not configured_model:
        return PROVIDER_DEFAULT_MODELS.get(provider)

    if provider == "openrouter":
        live = _fetch_openrouter_models()
        if live and configured_model in live:
            return configured_model
        if live:
            # Live catalog reachable but the saved model isn't real — fall
            # back to a default that IS real on OpenRouter, in priority
            # order. These are checked at request time against the live
            # list, so the first one that's actually offered wins.
            for candidate in (PROVIDER_DEFAULT_MODELS.get("openrouter"),
                              "anthropic/claude-sonnet-4.5",
                              "anthropic/claude-3.5-sonnet",
                              "openai/gpt-5-mini",
                              "openai/gpt-4o-mini"):
                if candidate and candidate in live:
                    return candidate
            return next(iter(live))

    if configured_model in PROVIDER_MODELS.get(provider, {}):
        return configured_model
    return PROVIDER_DEFAULT_MODELS.get(provider, configured_model)

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    missing_deps = check_dependencies()
    return jsonify({
        "status": "online",
        "missing_dependencies": missing_deps,
        "log_file": str(LOG_FILE),
    })

@app.route('/api/config', methods=['GET', 'POST'])
def config_api():
    if request.method == 'GET':
        return jsonify(load_config())
    else:
        # Merge incoming changes onto the existing config so partial saves
        # don't wipe unrelated fields.
        existing = load_config()
        incoming = request.json or {}
        merged = {**existing, **incoming}
        if "api_keys" in incoming:
            merged["api_keys"] = {**existing.get("api_keys", {}), **incoming["api_keys"]}
        # Keep default_model consistent with the active provider.
        merged["default_model"] = _resolve_model(
            merged.get("active_provider"), merged.get("default_model"))
        save_config(merged)
        return jsonify({"status": "success", "config": merged})
        
@app.route('/api/usage', methods=['GET'])
def usage_api():
    return jsonify(load_usage())

def _hash_stream(stream, chunk_size=1 << 20):
    """Stream-hash an uploaded file and rewind it for later use."""
    h = hashlib.sha256()
    stream.seek(0)
    while True:
        buf = stream.read(chunk_size)
        if not buf:
            break
        h.update(buf)
    stream.seek(0)
    return h.hexdigest()[:16]


# Async job state. /api/convert kicks off a background thread and returns
# immediately so the browser can poll /api/convert/status/<job_id> for live
# progress updates. The conversion's progress_callback writes (current,
# total, message) into _JOBS[job_id] under _JOBS_LOCK.
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _set_job(job_id, **patch):
    with _JOBS_LOCK:
        state = _JOBS.setdefault(job_id, {})
        state.update(patch)
        state['updated_at'] = time.time()


def _get_job(job_id):
    with _JOBS_LOCK:
        state = _JOBS.get(job_id)
        return dict(state) if state else None


def _run_conversion(job_id, input_path, output_path, output_filename,
                    job_dir, config, use_ai_flag, use_ocr_flag,
                    provider, api_key, vision_model, override_model):
    """Run conversion in a background thread, streaming progress to _JOBS."""
    try:
        from pdf_to_markdown import ClientFactory

        is_high_end = provider == "datalab"
        use_vision = is_high_end or (
            use_ocr_flag and bool(vision_model) and bool(api_key))

        def progress_callback(current, total, message):
            pct = int((current / total) * 100) if total else 0
            _set_job(job_id,
                     status='running',
                     current=current,
                     total=total,
                     percent=max(0, min(100, pct)),
                     message=message or '')

        kwargs = {
            'use_ai': use_ai_flag,
            'use_ocr': use_ocr_flag,
            'use_vision': use_vision,
            'checkpoint_dir': str(job_dir),
            'progress_callback': progress_callback,
        }

        ai_model = None
        if (use_ai_flag or use_vision) and api_key:
            kwargs['ai_client'] = ClientFactory.get_client(provider, api_key)
            ai_model = _resolve_model(
                provider, override_model or config.get("default_model"))
            kwargs['ai_model'] = ai_model
            kwargs['vision_model'] = vision_model

        _set_job(job_id, status='running', percent=0, total=1, current=0,
                 message='Starting conversion...', model=ai_model)

        markdown_content, result_path, cost_info = converter.convert_file(
            input_path=str(input_path),
            output_path=str(output_path),
            **kwargs
        )

        # Persist output outside the job_dir so we can tear it down
        persisted_output = TEMP_DIR / f"{job_id}_{output_filename}"
        shutil.copy2(result_path, persisted_output)
        shutil.rmtree(job_dir, ignore_errors=True)

        _set_job(job_id,
                 status='done',
                 percent=100,
                 message='Complete.',
                 filename=output_filename,
                 markdown=markdown_content,
                 path=str(persisted_output),
                 cost_info=cost_info)

    except Exception as e:
        app.logger.exception(
            "Conversion failed (job_id=%s, provider=%s)", job_id, provider)
        _set_job(job_id,
                 status='error',
                 error=str(e),
                 resumable=(job_dir / 'checkpoint.json').exists(),
                 log_file=str(LOG_FILE))


@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    config = load_config()

    filename = secure_filename(file.filename)
    job_id = _hash_stream(file.stream)
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / filename
    if not input_path.exists():
        file.save(input_path)

    output_filename = f"{Path(filename).stem}.md"
    output_path = job_dir / output_filename

    # Provider can be overridden per-request from the upload screen
    # (cross-provider model picker). Falls back to the saved active provider.
    provider = (request.form.get('provider')
                or config.get("active_provider", "openrouter"))
    api_key = config.get("api_keys", {}).get(provider, "")
    use_ai_flag = request.form.get('use_ai', 'true').lower() == 'true'
    use_ocr_flag = request.form.get('use_ocr', 'false').lower() == 'true'
    override_model = request.form.get('model') or None
    # When Force OCR is on, the upload-screen model picker is what reads
    # each page as an image. Fall back to the saved vision_model config
    # only if the caller didn't pick one for this run.
    vision_model = (request.form.get('vision_model')
                    or (override_model if use_ocr_flag else None)
                    or config.get("vision_model"))

    _set_job(job_id,
             status='queued',
             filename=output_filename,
             input_name=filename,
             percent=0,
             current=0,
             total=1,
             message='Queued...',
             provider=provider,
             resumed=(job_dir / 'checkpoint.json').exists())

    t = threading.Thread(
        target=_run_conversion,
        args=(job_id, input_path, output_path, output_filename, job_dir,
              config, use_ai_flag, use_ocr_flag, provider, api_key,
              vision_model, override_model),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "queued"})


@app.route('/api/convert/status/<job_id>', methods=['GET'])
def convert_status(job_id):
    """Poll endpoint for live job progress. Returns the current job state."""
    state = _get_job(job_id)
    if state is None:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify(state)


@app.route('/api/estimate', methods=['POST'])
def estimate():
    """Estimate per-model conversion cost for an uploaded file.

    Reads the file's text once via PyMuPDF (or pdftotext fallback), counts
    tokens, then multiplies by each model's input/output rate. Output is
    estimated at 1.2x input (AI enhancement typically expands slightly).
    Returns {token_count, page_count, estimates: {model_id: {input_cost,
    output_cost, total_cost}}}.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    suffix = Path(filename).suffix.lower()

    # Save to a temp file for extraction
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        text = ""
        page_count = 0
        if suffix == '.pdf':
            try:
                import fitz
                doc = fitz.open(tmp_path)
                page_count = len(doc)
                # Cap at first 50 pages for estimation; large books would
                # otherwise burn seconds extracting text we'll just count.
                for i, page in enumerate(doc):
                    if i >= 50:
                        # Extrapolate from sampled pages
                        avg = len(text) / max(1, i)
                        text += " " * int(avg * (page_count - i))
                        break
                    text += page.get_text()
                doc.close()
            except Exception as e:
                app.logger.warning("estimate: PDF text extraction failed: %s", e)
        else:
            try:
                with open(tmp_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            except Exception:
                text = ""

        # Rough token count: chars / 4 (avoids loading tiktoken just for est)
        token_count = max(1, len(text) // 4)
        # Output usually a bit longer than input after AI enhancement
        output_tokens = int(token_count * 1.2)

        # Build estimates. If all_providers=true is set OR no specific
        # provider was requested, sum costs across every provider the user
        # has a key for, keyed by "<provider>::<model_id>" so the frontend
        # can map directly to its merged dropdown.
        config = load_config()
        all_providers = request.form.get('all_providers', '').lower() == 'true'
        provider_req = request.form.get('provider')

        def cost_for(meta):
            in_rate = float(meta.get('input', 0) or 0)
            out_rate = float(meta.get('output', 0) or 0)
            input_cost = (token_count / 1_000_000) * in_rate
            output_cost = (output_tokens / 1_000_000) * out_rate
            return {
                'input_cost': round(input_cost, 4),
                'output_cost': round(output_cost, 4),
                'total_cost': round(input_cost + output_cost, 4),
            }

        estimates = {}
        if all_providers:
            keys = config.get("api_keys", {}) or {}
            for prov in ("datalab", "anthropic", "openai", "google", "openrouter"):
                if not keys.get(prov):
                    continue
                for mid, meta in _models_for_provider(prov).items():
                    estimates[f"{prov}::{mid}"] = cost_for(meta)
            response_provider = 'merged'
        else:
            provider = provider_req or config.get('active_provider') or 'openrouter'
            for mid, meta in _models_for_provider(provider).items():
                estimates[mid] = cost_for(meta)
            response_provider = provider

        return jsonify({
            'token_count': token_count,
            'output_tokens_est': output_tokens,
            'page_count': page_count,
            'provider': response_provider,
            'estimates': estimates,
        })
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


_CHECKPOINT_ARTIFACTS = {'checkpoint.json', 'checkpoint.json.tmp'}


@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    """List incomplete conversion jobs that can be resumed."""
    jobs = []
    for d in TEMP_DIR.iterdir():
        if not d.is_dir():
            continue
        checkpoint_file = d / 'checkpoint.json'
        if not checkpoint_file.exists():
            continue
        try:
            state = json.loads(checkpoint_file.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        inputs = [p.name for p in d.iterdir()
                  if p.is_file() and p.name not in _CHECKPOINT_ARTIFACTS]
        jobs.append({
            "job_id": d.name,
            "input_files": inputs,
            "chunk_index": state.get('chunk_index', 0),
            "total_chunks": state.get('total_chunks', 0),
            "input_tokens": state.get('input_tokens', 0),
            "output_tokens": state.get('output_tokens', 0),
        })
    return jsonify(jobs)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def delete_job(job_id):
    """Discard a partial job and its checkpoint."""
    # Prevent path traversal
    safe_id = Path(job_id).name
    job_dir = TEMP_DIR / safe_id
    if job_dir.is_dir() and job_dir.parent == TEMP_DIR:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"status": "deleted", "job_id": safe_id})
    return jsonify({"error": "job not found"}), 404

_OPENROUTER_CACHE = {"models": None, "fetched_at": 0}


def _fetch_openrouter_models():
    """Pull the live OpenRouter catalog so users only ever see real model
    IDs. Cached for the lifetime of the process plus 1 hour to avoid
    hammering the endpoint. Returns {} on any failure so the static
    fallback is used."""
    import time
    import urllib.request
    if (_OPENROUTER_CACHE["models"] is not None
            and time.time() - _OPENROUTER_CACHE["fetched_at"] < 3600):
        return _OPENROUTER_CACHE["models"]
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "Doc-2-Markdown/2.3"})
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read().decode("utf-8"))
        out = {}
        for entry in payload.get("data", []):
            mid = entry.get("id")
            if not mid:
                continue
            pricing = entry.get("pricing", {}) or {}
            arch = entry.get("architecture", {}) or {}
            modalities = set(arch.get("input_modalities") or [])
            try:
                input_cost = float(pricing.get("prompt", 0)) * 1_000_000
                output_cost = float(pricing.get("completion", 0)) * 1_000_000
            except (TypeError, ValueError):
                input_cost = output_cost = 0.0
            out[mid] = {
                "name": entry.get("name") or mid,
                "input": round(input_cost, 4),
                "output": round(output_cost, 4),
                "vision": "image" in modalities,
            }
        _OPENROUTER_CACHE["models"] = out
        _OPENROUTER_CACHE["fetched_at"] = time.time()
        app.logger.info("Fetched %d OpenRouter models", len(out))
        return out
    except Exception as e:
        app.logger.warning("OpenRouter model fetch failed: %s — using static fallback", e)
        return {}


def _models_for_provider(provider):
    """Return {model_id: meta} for the given provider. Uses live OpenRouter
    catalog when available, falls back to the static PROVIDER_MODELS table."""
    if provider == "openrouter":
        return _fetch_openrouter_models() or PROVIDER_MODELS.get("openrouter", {})
    return PROVIDER_MODELS.get(provider, {})


@app.route('/api/models', methods=['GET'])
def get_models():
    """Return the model catalog.

    Query params:
      provider: provider id. Defaults to active provider in config.
      all_providers: 'true' returns the union of every provider where the
                     user has an API key configured. Each model entry gains
                     a 'provider' field so the caller knows which client
                     to instantiate for that model.
      vision_only: 'true' filters to vision-capable models only.
    """
    config = load_config()
    vision_only = request.args.get('vision_only', '').lower() == 'true'

    if request.args.get('all_providers', '').lower() == 'true':
        keys = config.get("api_keys", {}) or {}
        merged = {}
        providers_seen = []
        for prov in ("datalab", "anthropic", "openai", "google", "openrouter"):
            if not keys.get(prov):
                continue
            for mid, meta in _models_for_provider(prov).items():
                if vision_only and not meta.get('vision'):
                    continue
                entry = dict(meta)
                entry['provider'] = prov
                # Namespace the key to avoid collisions across providers
                # (e.g. "claude-sonnet-4-6" exists on both Anthropic-direct
                # and as "anthropic/claude-sonnet-4-6" on OpenRouter).
                merged[f"{prov}::{mid}"] = entry
            providers_seen.append(prov)
        return jsonify({
            "all_providers": True,
            "providers": providers_seen,
            "active_provider": config.get("active_provider"),
            "default": PROVIDER_DEFAULT_MODELS.get(config.get("active_provider")),
            "models": merged,
            "model_count": len(merged),
        })

    provider = request.args.get('provider') or config.get("active_provider", "openrouter")
    models = _models_for_provider(provider)
    if vision_only:
        models = {k: v for k, v in models.items() if v.get('vision')}

    return jsonify({
        "provider": provider,
        "default": PROVIDER_DEFAULT_MODELS.get(provider),
        "models": models,
        "model_count": len(models),
    })

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Return the tail of the server log file as plain text.

    Query params:
      lines: number of trailing lines to return (default 200, max 5000)
    """
    try:
        n = int(request.args.get('lines', '200'))
    except ValueError:
        n = 200
    n = max(1, min(n, 5000))
    if not LOG_FILE.exists():
        return ("(log file does not exist yet — no errors logged)\n",
                200, {"Content-Type": "text/plain; charset=utf-8"})
    with open(LOG_FILE, encoding='utf-8', errors='replace') as f:
        tail = f.readlines()[-n:]
    return ("".join(tail), 200, {"Content-Type": "text/plain; charset=utf-8"})


@app.route('/api/download', methods=['GET'])
def download():
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
        
    return send_from_directory(
        os.path.dirname(path), 
        os.path.basename(path),
        as_attachment=True
    )

if __name__ == '__main__':
    port = int(os.environ.get("DOC2MD_PORT", "5005"))
    print(f"Starting Doc-2-Markdown Web Server on http://127.0.0.1:{port}")
    app.run(debug=False, use_reloader=False, port=port, host="127.0.0.1")
