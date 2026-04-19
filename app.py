from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS
import os
import sys
import json
import tempfile
from pathlib import Path
from werkzeug.utils import secure_filename
from pdf_to_markdown import DocumentConverter, load_config, save_config, check_dependencies, load_usage

app = Flask(__name__, static_folder='frontend', static_url_path='')
CORS(app)

# Ensure temp directory exists
TEMP_DIR = Path(tempfile.gettempdir()) / "doc2md_uploads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Initialize document converter
converter = DocumentConverter()

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
        new_config = request.json
        save_config(new_config)
        return jsonify({"status": "success", "config": new_config})
        
@app.route('/api/usage', methods=['GET'])
def usage_api():
    return jsonify(load_usage())

@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400
        
    # Get config settings
    config = load_config()
    
    # Save uploaded file
    filename = secure_filename(file.filename)
    input_path = TEMP_DIR / filename
    file.save(input_path)
    
    # Determine output path
    output_filename = f"{Path(filename).stem}.md"
    output_path = TEMP_DIR / output_filename
    
    try:
        # Check high-end and standard settings
        is_high_end = config.get("active_provider") == "datalab"
        
        kwargs = {
            'use_ai': request.form.get('use_ai', 'true').lower() == 'true',
            'use_ocr': request.form.get('use_ocr', 'false').lower() == 'true',
            'use_vision': is_high_end, # If high-end datalab, vision logic kicks in inside convert_pdf_to_markdown
        }
        
        # Instantiate AI client explicitly from config
        from pdf_to_markdown import ClientFactory
        
        provider = config.get("active_provider", "openrouter")
        api_key = config.get("api_keys", {}).get(provider, "")
        
        if kwargs['use_ai'] and api_key:
            kwargs['ai_client'] = ClientFactory.get_client(provider, api_key)
            kwargs['ai_model'] = config.get("default_model")
            kwargs['vision_model'] = config.get("vision_model")

        markdown_content, result_path, _cost_info = converter.convert_file(
            input_path=str(input_path),
            output_path=str(output_path),
            **kwargs
        )

        return jsonify({
            "status": "success",
            "filename": output_filename,
            "markdown": markdown_content,
            "path": str(result_path)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Cleanup input file
        if input_path.exists():
            input_path.unlink()

@app.route('/api/models', methods=['GET'])
def get_models():
    sys.path.append(str(Path(__file__).parent))
    try:
        from pdf_to_markdown import PROVIDER_MODELS
        models = PROVIDER_MODELS.get("openrouter", {})
        # Filter for vision enabled models
        vision_models = {k: v for k, v in models.items() if v.get('vision')}
        return jsonify(vision_models)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    print("Starting Doc-2-Markdown Web Server on http://127.0.0.1:5000")
    app.run(debug=False, use_reloader=False, port=5000, host="127.0.0.1")
