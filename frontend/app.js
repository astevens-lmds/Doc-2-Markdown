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

    // Backend status
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

    // Drop zone
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(ev => {
        dropZone.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); }, false);
    });
    ['dragenter', 'dragover'].forEach(ev => {
        dropZone.addEventListener(ev, () => dropZone.classList.add('dragover'), false);
    });
    ['dragleave', 'drop'].forEach(ev => {
        dropZone.addEventListener(ev, () => dropZone.classList.remove('dragover'), false);
    });

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', function() {
        if (this.files.length > 0) startBatch([...this.files]);
        this.value = '';   // allow re-selecting the same file later
    });
    dropZone.addEventListener('drop', (e) => {
        const files = [...(e.dataTransfer.files || [])];
        if (files.length > 0) startBatch(files);
    }, false);

    // Convert View elements
    const useAiToggle = document.getElementById('use-ai-toggle');
    const useOcrToggle = document.getElementById('use-ocr-toggle');
    const uploadModel = document.getElementById('upload-model');
    const uploadModelCost = document.getElementById('upload-model-cost');
    const visionHint = document.getElementById('vision-hint');
    const jobsContainer = document.getElementById('jobs-container');

    // ---- Model picker on the upload screen ----
    // Cache structure: { "<provider>:<vision_only?>": {models, default} }
    const modelCache = {};
    let activeProvider = null;
    let estimateMap = {};
    let lastEstimateProvider = null;

    function fmtRate(rate) {
        if (rate == null || rate === 0) return '–';
        if (rate < 0.01) return '$' + rate.toFixed(4);
        return '$' + rate.toFixed(2);
    }
    function fmtEst(cost) {
        if (cost == null) return '';
        if (cost < 0.005) return '<$0.01';
        if (cost < 1) return '$' + cost.toFixed(3);
        return '$' + cost.toFixed(2);
    }
    function modelLabel(id, meta) {
        const display = meta.name || id;
        const rate = `${fmtRate(meta.input)} in / ${fmtRate(meta.output)} out per 1M`;
        const est = estimateMap[id];
        const estPart = est ? `  •  est ${fmtEst(est.total_cost)}` : '';
        const visionBadge = meta.vision ? '  👁' : '';
        return `${display} — ${rate}${estPart}${visionBadge}`;
    }
    function cacheKey() {
        return `${activeProvider}:${useOcrToggle.checked ? 'v' : 'a'}`;
    }
    function refreshModelOptions() {
        const cached = modelCache[cacheKey()];
        if (!cached) return;
        const models = cached.models || {};
        const ids = Object.keys(models);
        const prev = uploadModel.value;
        uploadModel.innerHTML = '';
        if (ids.length === 0) {
            const opt = document.createElement('option');
            opt.textContent = useOcrToggle.checked
                ? '(no vision-capable models — add a provider key in Settings)'
                : '(no models — add a provider key in Settings)';
            opt.disabled = true;
            uploadModel.appendChild(opt);
            uploadModelCost.textContent = '';
            return;
        }
        ids.forEach(id => {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = modelLabel(id, models[id]);
            uploadModel.appendChild(opt);
        });
        const target = (prev && ids.includes(prev))
            ? prev
            : (cached.default && ids.includes(cached.default) ? cached.default : ids[0]);
        uploadModel.value = target;
        updateCostBadge();
    }
    function updateCostBadge() {
        const id = uploadModel.value;
        const cached = modelCache[cacheKey()];
        if (!cached || !cached.models[id]) {
            uploadModelCost.textContent = '';
            return;
        }
        const meta = cached.models[id];
        const rates = `${fmtRate(meta.input)} / ${fmtRate(meta.output)} per 1M`;
        const est = estimateMap[id];
        uploadModelCost.innerHTML = est
            ? `${rates}<span class="estimate">~${fmtEst(est.total_cost)}</span>`
            : rates;
    }
    uploadModel.addEventListener('change', updateCostBadge);

    function loadUploadModels(forceRefresh) {
        return fetch('/api/config').then(r => r.json()).then(cfg => {
            activeProvider = cfg.active_provider || 'openrouter';
            const visionOnly = useOcrToggle.checked;
            const key = `${activeProvider}:${visionOnly ? 'v' : 'a'}`;
            if (!forceRefresh && modelCache[key]) {
                refreshModelOptions();
                return;
            }
            const url = `/api/models?provider=${encodeURIComponent(activeProvider)}`
                      + (visionOnly ? '&vision_only=true' : '');
            return fetch(url).then(r => r.json()).then(data => {
                modelCache[key] = {
                    models: data.models || {},
                    default: cfg.default_model || data.default,
                };
                refreshModelOptions();
            });
        }).catch(err => console.error('loadUploadModels failed', err));
    }

    useOcrToggle.addEventListener('change', () => {
        if (useOcrToggle.checked) visionHint.classList.remove('hidden');
        else visionHint.classList.add('hidden');
        loadUploadModels();
    });

    loadUploadModels();

    // ---- Cost estimation ----
    function estimateForFile(file) {
        // Skip if we already have an estimate for this provider — single
        // file estimates apply roughly to the rest of a batch too.
        if (lastEstimateProvider === activeProvider) return Promise.resolve();
        lastEstimateProvider = activeProvider;
        const fd = new FormData();
        fd.append('file', file);
        if (activeProvider) fd.append('provider', activeProvider);
        return fetch('/api/estimate', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data && data.estimates) {
                    estimateMap = data.estimates;
                    refreshModelOptions();
                }
            })
            .catch(err => console.warn('estimate failed', err));
    }

    // ---- Job card UI ----
    // Each upload creates a card with progress bar, status, and (on done)
    // a download button + collapsed preview.
    function createJobCard(file) {
        const card = document.createElement('div');
        card.className = 'job-card';
        card.innerHTML = `
            <div class="job-card-header">
                <div class="job-card-name" title="${escapeAttr(file.name)}">${escapeHtml(file.name)}</div>
                <div class="job-card-status">Queued…</div>
                <div class="job-card-actions"></div>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width:0%"></div>
            </div>
            <div class="job-card-message"></div>
        `;
        jobsContainer.prepend(card);
        return card;
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[c]));
    }
    function escapeAttr(s) {
        return escapeHtml(s);
    }

    // ---- Batch upload ----
    function startBatch(files) {
        // Fire estimate once per provider per session (covers the whole batch)
        if (files[0]) estimateForFile(files[0]);
        files.forEach(file => startOne(file));
    }

    function startOne(file) {
        const card = createJobCard(file);
        const fill = card.querySelector('.progress-bar-fill');
        const statusEl = card.querySelector('.job-card-status');
        const messageEl = card.querySelector('.job-card-message');
        const actionsEl = card.querySelector('.job-card-actions');

        const formData = new FormData();
        formData.append('file', file);
        formData.append('use_ai', useAiToggle.checked);
        formData.append('use_ocr', useOcrToggle.checked);
        if (uploadModel.value) formData.append('model', uploadModel.value);
        // When Force OCR is on, the picked model also acts as the vision
        // model. Backend will use this for the per-page image pass.
        if (useOcrToggle.checked && uploadModel.value) {
            formData.append('vision_model', uploadModel.value);
        }

        fetch('/api/convert', { method: 'POST', body: formData })
            .then(r => r.json())
            .then(data => {
                if (data.error || !data.job_id) {
                    renderError(card, data.error || 'Failed to start conversion', data.log_file);
                    return;
                }
                pollJob(data.job_id, card, fill, statusEl, messageEl, actionsEl, file.name);
            })
            .catch(err => renderError(card, 'Connection error: ' + err.message, null));
    }

    function pollJob(jobId, card, fill, statusEl, messageEl, actionsEl, originalName) {
        let lastMessage = '';
        const iv = setInterval(() => {
            fetch(`/api/convert/status/${encodeURIComponent(jobId)}`)
                .then(r => r.json())
                .then(state => {
                    if (state.status === 'done') {
                        clearInterval(iv);
                        card.classList.add('done');
                        fill.style.width = '100%';
                        fill.style.animation = 'none';
                        statusEl.textContent = 'Done';
                        messageEl.textContent = state.cost_info
                            ? `Tokens: ${state.cost_info.input_tokens.toLocaleString()} in / ${state.cost_info.output_tokens.toLocaleString()} out  •  $${(state.cost_info.cost || 0).toFixed(4)}`
                            : 'Complete.';
                        renderDownload(actionsEl, card, state);
                        return;
                    }
                    if (state.status === 'error') {
                        clearInterval(iv);
                        renderError(card, state.error || 'Unknown error', state.log_file);
                        return;
                    }
                    // running / queued
                    const pct = typeof state.percent === 'number' ? state.percent : 0;
                    fill.style.width = pct + '%';
                    statusEl.textContent = (state.current != null && state.total)
                        ? `${pct}%  (${state.current}/${state.total})`
                        : pct + '%';
                    if (state.message && state.message !== lastMessage) {
                        messageEl.textContent = state.message;
                        lastMessage = state.message;
                    }
                })
                .catch(err => {
                    // Don't kill the poller on transient network errors
                    console.warn('poll error', err);
                });
        }, 700);
    }

    function renderDownload(actionsEl, card, state) {
        actionsEl.innerHTML = '';
        const btn = document.createElement('button');
        btn.className = 'btn btn-primary';
        btn.textContent = 'Download';
        btn.addEventListener('click', () => {
            if (state.path) {
                window.location.href = `/api/download?path=${encodeURIComponent(state.path)}`;
            }
        });
        actionsEl.appendChild(btn);

        const toggle = document.createElement('button');
        toggle.className = 'btn';
        toggle.style.background = 'rgba(255,255,255,0.08)';
        toggle.style.color = 'var(--text-main)';
        toggle.textContent = 'Preview';
        toggle.addEventListener('click', () => {
            let pre = card.querySelector('.job-card-preview');
            if (pre) {
                pre.remove();
                toggle.textContent = 'Preview';
            } else {
                pre = document.createElement('div');
                pre.className = 'job-card-preview';
                pre.textContent = state.markdown || '(no content)';
                card.appendChild(pre);
                toggle.textContent = 'Hide preview';
            }
        });
        actionsEl.appendChild(toggle);
    }

    function renderError(card, msg, logFile) {
        card.classList.add('error');
        const statusEl = card.querySelector('.job-card-status');
        const fill = card.querySelector('.progress-bar-fill');
        statusEl.textContent = 'Error';
        fill.style.animation = 'none';
        fill.style.background = '#ff453a';
        const err = document.createElement('div');
        err.className = 'job-card-error';
        err.textContent = msg + (logFile ? `\nFull traceback in: ${logFile}` : '');
        card.appendChild(err);
    }

    // ---- Settings View ----
    const providerSelect = document.getElementById('setting-provider');
    const modelSelect = document.getElementById('setting-model');
    const providers = ['datalab', 'openrouter', 'openai', 'anthropic', 'google'];

    function updateKeyVisibility(activeProviderId) {
        providers.forEach(p => {
            const group = document.getElementById(`group-${p}`);
            if (group) group.style.display = (p === activeProviderId) ? 'block' : 'none';
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
                const provider = data.active_provider || 'datalab';
                providerSelect.value = provider;
                updateKeyVisibility(provider);
                if (data.api_keys) {
                    providers.forEach(p => {
                        const input = document.getElementById(`setting-key-${p}`);
                        if (input && data.api_keys[p]) input.value = data.api_keys[p];
                    });
                }
                populateModels(provider, data.default_model);
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
                // Invalidate upload-screen model cache so provider switch is reflected
                Object.keys(modelCache).forEach(k => delete modelCache[k]);
                loadUploadModels(true);
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
