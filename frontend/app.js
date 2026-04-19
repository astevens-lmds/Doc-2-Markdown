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
                alert("Error: " + data.error);
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
    function loadSettings() {
        fetch('/api/config')
            .then(res => res.json())
            .then(data => {
                const provider = document.getElementById('setting-provider');
                const key = document.getElementById('setting-key-openrouter');
                if (data.active_provider) provider.value = data.active_provider;
                if (data.api_keys && data.api_keys.openrouter) {
                    key.value = data.api_keys.openrouter;
                }
            });
    }

    document.getElementById('btn-save-settings').addEventListener('click', () => {
        const provider = document.getElementById('setting-provider').value;
        const key = document.getElementById('setting-key-openrouter').value;

        fetch('/api/config')
            .then(res => res.json())
            .then(data => {
                data.active_provider = provider;
                if (!data.api_keys) data.api_keys = {};
                data.api_keys.openrouter = key;

                return fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
            })
            .then(() => showToast('Settings saved successfully'))
            .catch(() => showToast('Error saving settings'));
    });

    function showToast(msg) {
        const toast = document.getElementById('toast');
        toast.textContent = msg;
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 3000);
    }
});
