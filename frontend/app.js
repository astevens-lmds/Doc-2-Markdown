document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navConvert = document.getElementById('nav-convert');
    const navSettings = document.getElementById('nav-settings');
    const viewConvert = document.getElementById('view-convert');
    const viewSettings = document.getElementById('view-settings');
    const backendStatus = document.getElementById('backend-status');
    const statusDot = document.querySelector('.dot');

    navConvert.addEventListener('click', (e) => {
        e.preventDefault();
        navConvert.classList.add('active');
        navSettings.classList.remove('active');
        viewConvert.classList.add('active');
        viewSettings.classList.remove('active');
    });

    navSettings.addEventListener('click', (e) => {
        e.preventDefault();
        navSettings.classList.add('active');
        navConvert.classList.remove('active');
        viewSettings.classList.add('active');
        viewConvert.classList.remove('active');
        loadSettings();
    });

    // Check Backend Status
    fetch('/api/status')
        .then(res => res.json())
        .then(data => {
            if (data.status === 'online') {
                backendStatus.textContent = 'Backend Connected';
                statusDot.classList.add('online');
            }
        })
        .catch(() => {
            backendStatus.textContent = 'Backend Offline';
            statusDot.classList.remove('online');
        });

    // Drag and Drop Logic
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
    });

    dropZone.addEventListener('drop', handleDrop, false);
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', function() {
        if (this.files.length > 0) {
            handleUpload(this.files[0]);
        }
    });

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleUpload(files[0]);
        }
    }

    // Convert Logic
    const progressContainer = document.getElementById('progress-container');
    const resultContainer = document.getElementById('result-container');
    const fileInfo = document.getElementById('file-info');
    const resultFilename = document.getElementById('result-filename');
    const resultPreview = document.getElementById('result-preview');
    const useAiToggle = document.getElementById('use-ai-toggle');
    const useOcrToggle = document.getElementById('use-ocr-toggle');
    let currentDownloadPath = '';

    function handleUpload(file) {
        dropZone.classList.add('hidden');
        progressContainer.classList.remove('hidden');
        resultContainer.classList.add('hidden');
        fileInfo.textContent = `Processing: ${file.name}... (This may take a minute)`;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('use_ai', useAiToggle.checked);
        formData.append('use_ocr', useOcrToggle.checked);

        fetch('/api/convert', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            progressContainer.classList.add('hidden');
            if (data.error) {
                let msg = "Error: " + data.error;
                if (data.log_file) {
                    msg += "\n\nFull traceback in: " + data.log_file
                         + "\n(or open /api/logs in this browser)";
                }
                alert(msg);
                dropZone.classList.remove('hidden');
            } else {
                resultContainer.classList.remove('hidden');
                resultFilename.textContent = data.filename;
                resultPreview.textContent = data.markdown;
                currentDownloadPath = data.path;
            }
        })
        .catch(err => {
            progressContainer.classList.add('hidden');
            dropZone.classList.remove('hidden');
            alert("Connection error occurred.");
            console.error(err);
        });
    }

    // Download Logic
    document.getElementById('btn-download').addEventListener('click', () => {
        if (currentDownloadPath) {
            window.location.href = `/api/download?path=${encodeURIComponent(currentDownloadPath)}`;
        }
    });

    // Settings Logic
    const providerSelect = document.getElementById('setting-provider');
    const modelSelect = document.getElementById('setting-model');
    const providers = ['datalab', 'openrouter', 'openai', 'anthropic', 'google'];

    function updateKeyVisibility(activeProvider) {
        providers.forEach(p => {
            const group = document.getElementById(`group-${p}`);
            if (group) group.style.display = (p === activeProvider) ? 'block' : 'none';
        });
    }

    function populateModels(provider, selectedModel) {
        return fetch(`/api/models?provider=${encodeURIComponent(provider)}`)
            .then(res => res.json())
            .then(data => {
                modelSelect.innerHTML = '';
                const models = data.models || {};
                const ids = Object.keys(models);
                if (ids.length === 0) {
                    const opt = document.createElement('option');
                    opt.textContent = '(no models for this provider)';
                    opt.disabled = true;
                    modelSelect.appendChild(opt);
                    return;
                }
                ids.forEach(id => {
                    const opt = document.createElement('option');
                    opt.value = id;
                    opt.textContent = models[id].name ? `${models[id].name} — ${id}` : id;
                    modelSelect.appendChild(opt);
                });
                const target = (selectedModel && ids.includes(selectedModel))
                    ? selectedModel
                    : (data.default && ids.includes(data.default) ? data.default : ids[0]);
                modelSelect.value = target;
            });
    }

    providerSelect.addEventListener('change', (e) => {
        updateKeyVisibility(e.target.value);
        populateModels(e.target.value);
    });

    function loadSettings() {
        fetch('/api/config')
            .then(res => res.json())
            .then(data => {
                const activeProvider = data.active_provider || 'datalab';
                providerSelect.value = activeProvider;
                updateKeyVisibility(activeProvider);

                if (data.api_keys) {
                    providers.forEach(p => {
                        const input = document.getElementById(`setting-key-${p}`);
                        if (input && data.api_keys[p]) {
                            input.value = data.api_keys[p];
                        }
                    });
                }

                populateModels(activeProvider, data.default_model);
            });
    }

    document.getElementById('btn-save-settings').addEventListener('click', () => {
        const apiKeys = {};
        providers.forEach(p => {
            const input = document.getElementById(`setting-key-${p}`);
            if (input) apiKeys[p] = input.value;
        });
        const payload = {
            active_provider: providerSelect.value,
            api_keys: apiKeys,
            default_model: modelSelect.value,
        };
        fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        })
            .then(res => res.json())
            .then(data => {
                if (data && data.config && data.config.default_model) {
                    modelSelect.value = data.config.default_model;
                }
                showToast('Settings saved.');
            })
            .catch(() => showToast('Error saving settings'));
    });

    function showToast(msg) {
        const toast = document.getElementById('toast');
        toast.textContent = msg;
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 3000);
    }
});
