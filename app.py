from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
import shutil
import hashlib
import tempfile
from pathlib import Path
from werkzeug.utils import secure_filename
from pdf_to_markdown import DocumentConverter, load_config, save_config, check_dependencies, load_usage, PROVIDER_MODELS

app = Flask(__name__, static_folder='frontend', static_url_path='')
CORS(app)

# Ensure temp directory exists
TEMP_DIR = Path(tempfile.gettempdir()) / "doc2md_uploads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Initialize document converter
converter = DocumentConverter()

# A safe default model per provider — used when the saved config has a
# model that doesn't belong to the currently selected provider (e.g. user
# switched from Datalab to OpenRouter but config.default_model is still
# "datalab/marker-ocr", which OpenRouter rejects with HTTP 400).
PROVIDER_DEFAULT_MODELS = {
    "openrouter": "anthropic/claude-sonnet-4-6",
    "openai": "gpt-6-omni-mini",
    "anthropic": "claude-sonnet-4-6",
    "google": "gemini-3.0-flash",
    "datalab": "datalab/marker-ocr",
}


def _resolve_model(provider, configured_model):
    """Return a model id valid for the given provider, falling back to a
    sensible default when the configured one doesn't belong here."""
    if configured_model and configured_model in PROVIDER_MODELS.get(provider, {}):
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
        "missing_dependencies": missing_deps
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

    resumed = (job_dir / 'checkpoint.json').exists()

    try:
        is_high_end = config.get("active_provider") == "datalab"

        kwargs = {
            'use_ai': request.form.get('use_ai', 'true').lower() == 'true',
            'use_ocr': request.form.get('use_ocr', 'false').lower() == 'true',
            'use_vision': is_high_end,
            'checkpoint_dir': str(job_dir),
        }

        from pdf_to_markdown import ClientFactory

        provider = config.get("active_provider", "openrouter")
        api_key = config.get("api_keys", {}).get(provider, "")

        if kwargs['use_ai'] and api_key:
            kwargs['ai_client'] = ClientFactory.get_client(provider, api_key)
            kwargs['ai_model'] = _resolve_model(provider, config.get("default_model"))
            kwargs['vision_model'] = config.get("vision_model")

        markdown_content, result_path, _cost_info = converter.convert_file(
            input_path=str(input_path),
            output_path=str(output_path),
            **kwargs
        )

        response = {
            "status": "success",
            "filename": output_filename,
            "markdown": markdown_content,
            "path": str(result_path),
            "job_id": job_id,
            "resumed": resumed,
        }

        # Success: tear down job dir but preserve the final output elsewhere
        persisted_output = TEMP_DIR / f"{job_id}_{output_filename}"
        shutil.copy2(result_path, persisted_output)
        response["path"] = str(persisted_output)
        shutil.rmtree(job_dir, ignore_errors=True)

        return jsonify(response)

    except Exception as e:
        # Preserve job_dir so the next upload of this file can resume
        return jsonify({
            "error": str(e),
            "job_id": job_id,
            "resumable": (job_dir / 'checkpoint.json').exists(),
        }), 500


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

@app.route('/api/models', methods=['GET'])
def get_models():
    """Return the model catalog for a provider.

    Query params:
      provider: provider id (openrouter, openai, anthropic, google, datalab).
                Defaults to the active provider in config.
      vision_only: 'true' to filter to vision-capable models only.
    """
    provider = request.args.get('provider') or load_config().get("active_provider", "openrouter")
    models = PROVIDER_MODELS.get(provider, {})
    if request.args.get('vision_only', '').lower() == 'true':
        models = {k: v for k, v in models.items() if v.get('vision')}
    return jsonify({
        "provider": provider,
        "default": PROVIDER_DEFAULT_MODELS.get(provider),
        "models": models,
    })

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
