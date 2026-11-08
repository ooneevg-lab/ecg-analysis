/**
 * Analysis page JavaScript for ECG Analysis.
 */

let analysisResult = null;

// API base path (use v1 for new features, fallback to non-versioned for compatibility)
const API_BASE = '/api/v1';

function apiUrl(path) {
    return `${API_BASE}${path}`;
}

function modelClassLabel(className) {
    const labels = {
        NORM: 'Норма',
        PATHOLOG: 'Патология',
        SR: 'Синусовый ритм'
    };
    return labels[className] || className || '-';
}

function resultClassProbability(result) {
    return Number(result.class_probability ?? result.confidence ?? result.probability ?? 0) || 0;
}

function resultPathologyProbability(result) {
    return Number(result.pathology_probability ?? result.probability ?? 0) || 0;
}

function probabilityPercent(value) {
    return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function verdictTone(predClass, rhythmClass) {
    if (predClass === 'PATHOLOG') return 'pathology';
    if (predClass === 'SR' || (rhythmClass && rhythmClass !== 'normal' && rhythmClass !== 'unknown')) return 'rhythm-issue';
    return 'normal';
}

function renderVerdictIcon(predClass, rhythmClass) {
    const tone = verdictTone(predClass, rhythmClass);
    if (tone === 'pathology') return Icons.alertCircle(28);
    if (tone === 'rhythm-issue') return Icons.heartPulse(28);
    return Icons.checkCircle(28);
}

async function hydrateResultSignal(result) {
    if (!result || result.ecg_data) return result;
    try {
        const response = await fetch(apiUrl(`/analysis/signal/${result.file_id}`));
        const data = await response.json();
        if (response.ok && data.success && data.signal) {
            return Object.assign({}, result, data.signal);
        }
    } catch (e) {
        console.warn('Failed to load cached result signal:', e);
    }
    return result;
}

async function runAnalysis(fileId, threshold = 0.35, forceReanalyze = false) {
    const progressSection = document.getElementById('analysisProgress');
    const resultsSection = document.getElementById('analysisResults');
    const errorSection = document.getElementById('analysisError');

    progressSection.style.display = 'block';
    resultsSection.style.display = 'none';
    errorSection.style.display = 'none';
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active', 'completed'));

    // Activate model-loading step first
    updateStep('model-loading', 'active');

    const steps = ['validation', 'preprocessing', 'classification'];
    let currentStep = 0;
    const stepInterval = setInterval(() => {
        if (currentStep < steps.length) {
            updateStep(steps[currentStep], 'active');
            if (currentStep > 0) updateStep(steps[currentStep - 1], 'completed');
            currentStep++;
        }
    }, 400);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);

    try {
        let response = await fetch(apiUrl(`/analysis/result/${fileId}`), {
            method: 'GET',
            signal: controller.signal
        });

        let data = await response.json();
        if (response.ok && data.success) {
            // Check if threshold differs from stored result
            const storedThreshold = data.result.threshold;
            if (storedThreshold !== null && !forceReanalyze && Math.abs(storedThreshold - threshold) > 0.001) {
                clearInterval(stepInterval);
                clearTimeout(timeoutId);
                updateStep('model-loading', 'completed');
                steps.forEach(step => updateStep(step, 'completed'));
                analysisResult = await hydrateResultSignal(data.result);
                displayResults(analysisResult);
                showThresholdMismatch(storedThreshold, threshold);
                progressSection.style.display = 'none';
                resultsSection.style.display = 'block';
                return;
            }

            clearInterval(stepInterval);
            clearTimeout(timeoutId);
            updateStep('model-loading', 'completed');
            steps.forEach(step => updateStep(step, 'completed'));
            analysisResult = await hydrateResultSignal(data.result);
            displayResults(analysisResult);
            progressSection.style.display = 'none';
            resultsSection.style.display = 'block';
            return;
        }

        response = await fetch(apiUrl(`/analysis/run/${fileId}`), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ threshold, include_xai: true, force_reanalyze: forceReanalyze }),
            signal: controller.signal
        });

        clearInterval(stepInterval);
        clearTimeout(timeoutId);
        updateStep('model-loading', 'completed');
        steps.forEach(step => updateStep(step, 'completed'));

        data = await response.json();
        if (response.ok && data.success) {
            analysisResult = data.result;
            displayResults(data.result);
            progressSection.style.display = 'none';
            resultsSection.style.display = 'block';
        } else {
            showAnalysisError(data.error || 'Неизвестная ошибка анализа');
        }
    } catch (error) {
        clearInterval(stepInterval);
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            showAnalysisError('Анализ занимает слишком много времени. Возможно, модель загружается. Попробуйте ещё раз.');
        } else {
            showAnalysisError('Ошибка соединения с сервером: ' + error.message);
        }
    }
}

function showThresholdMismatch(storedThreshold, currentThreshold) {
    const notice = document.getElementById('thresholdMismatchNotice');
    const reanalyzeBtn = document.getElementById('reanalyzeBtn');
    const reanalyzeBtnText = document.getElementById('reanalyzeBtnText');
    
    if (notice) {
        notice.innerHTML = `
            <div class="alert alert--warning">
                <strong>Внимание:</strong> Этот файл был проанализирован с порогом <strong>${storedThreshold.toFixed(2)}</strong>, 
                а текущий порог — <strong>${currentThreshold.toFixed(2)}</strong>. 
                Результаты могут отличаться.
            </div>
        `;
        notice.style.display = 'block';
    }
    if (reanalyzeBtn) {
        reanalyzeBtn.style.display = 'inline-flex';
        reanalyzeBtn.onclick = () => runAnalysis(
            document.querySelector('.analysis-page').dataset.fileId,
            currentThreshold,
            true
        );
    }
}

function hideThresholdMismatch() {
    const notice = document.getElementById('thresholdMismatchNotice');
    const reanalyzeBtn = document.getElementById('reanalyzeBtn');
    if (notice) notice.style.display = 'none';
    if (reanalyzeBtn) reanalyzeBtn.style.display = 'none';
}

function updateStep(stepName, state) {
    const step = document.querySelector(`.step[data-step="${stepName}"]`);
    if (!step) return;
    step.classList.remove('active', 'completed');
    if (state) step.classList.add(state);
}

function showAnalysisError(message) {
    document.getElementById('analysisProgress').style.display = 'none';
    document.getElementById('analysisResults').style.display = 'none';
    document.getElementById('analysisError').style.display = 'block';
    document.getElementById('analysisErrorText').textContent = message;
    const errorIcon = document.getElementById('analysisErrorIcon');
    if (errorIcon) errorIcon.innerHTML = Icons.alert(48);
}

function renderClassProbabilities(result) {
    const container = document.getElementById('classProbabilities');
    if (!container || !result.probabilities) return;

    const order = ['NORM', 'PATHOLOG', 'SR'];
    const max = order.reduce((m, name) => {
        const v = Number(result.probabilities[name] || 0);
        return v > m ? v : m;
    }, 0.0001);

    container.innerHTML = order
        .filter(name => Object.prototype.hasOwnProperty.call(result.probabilities, name))
        .map(name => {
            const prob = Number(result.probabilities[name] || 0);
            const isActive = result.pred_class === name;
            const active = isActive ? ' prob-bar--active' : '';
            const tone = ` prob-bar--${name}`;
            const widthPct = Math.max(2, (prob / max) * 100);
            return `
                <div class="prob-bar${active}${tone}">
                    <div class="prob-bar__label">${modelClassLabel(name)}</div>
                    <div class="prob-bar__track">
                        <div class="prob-bar__fill" style="width: ${widthPct.toFixed(1)}%"></div>
                    </div>
                    <div class="prob-bar__value">${probabilityPercent(prob)}</div>
                </div>
            `;
        })
        .join('');

    const note = document.getElementById('thresholdRuleNote');
    if (!note) return;
    const rule = result.threshold_rule;
    if (result.threshold_applied && rule) {
        note.textContent = `Класс изменён правилом постобработки: ${modelClassLabel(rule.source_class)} → ${modelClassLabel(rule.target_class)} при ${modelClassLabel(rule.target_class)} ≥ ${(rule.threshold * 100).toFixed(1)}%.`;
        note.style.display = 'block';
    } else {
        note.textContent = '';
        note.style.display = 'none';
    }
}

function displayResults(result) {
    hideThresholdMismatch();
    document.getElementById('fileInfo').textContent = result.filename || '';

    const verdictCard = document.getElementById('verdictCard');
    verdictCard.classList.remove('normal', 'rhythm-issue', 'pathology');

    let verdictClass;
    let verdictText;
    const isPathology = result.pred_class === 'PATHOLOG' || result.prediction === 1;

    if (isPathology) {
        verdictClass = 'pathology';
        verdictText = modelClassLabel('PATHOLOG');
    } else if (result.pred_class === 'SR') {
        verdictClass = 'rhythm-issue';
        verdictText = 'Синусовый ритм';
    } else if (result.rhythm_class && result.rhythm_class !== 'normal') {
        verdictClass = 'rhythm-issue';
        verdictText = result.rhythm_class_ru || 'Нарушение ритма';
    } else {
        verdictClass = 'normal';
        verdictText = modelClassLabel('NORM');
    }

    verdictCard.classList.add(verdictClass);

    const verdictIcon = document.getElementById('verdictIcon');
    if (verdictIcon) verdictIcon.innerHTML = renderVerdictIcon(result.pred_class, result.rhythm_class);

    document.getElementById('verdictText').textContent = verdictText;
    document.getElementById('verdictProbability').innerHTML = `
        <div class="metric">
            <div class="metric__label">Вероятность патологии</div>
            <div class="metric__value">${probabilityPercent(resultPathologyProbability(result))}</div>
        </div>
        <div class="metric">
            <div class="metric__label">Уверенность класса</div>
            <div class="metric__value">${probabilityPercent(resultClassProbability(result))}</div>
        </div>
    `;

    renderClassProbabilities(result);

    let rhythmDisplay;
    if (result.rhythm_class && result.rhythm_class !== 'unknown') {
        rhythmDisplay = result.rhythm_class_ru || 'Норма';
    } else {
        rhythmDisplay = result.rhythm_class_ru || 'Не удалось определить';
    }
    document.getElementById('rhythmValue').textContent = rhythmDisplay;
    document.getElementById('hrValue').textContent = result.hr_bpm ? `${Math.round(result.hr_bpm)} уд/мин` : '—';

    // XAI: only top_leads (saliency_map loaded on-demand)
    displayTopLeads(result.xai?.top_leads || []);
    setupToggles();
    initializeECGViewer(result);
    setupExportButton(result.file_id);
}

function displayTopLeads(leads) {
    const container = document.getElementById('topLeads');
    if (!container) return;

    if (!leads || leads.length === 0) {
        container.innerHTML = '<p class="muted">Данные недоступны</p>';
        return;
    }

    container.innerHTML = leads.slice(0, 3).map((lead, i) => {
        const explanation = LEAD_EXPLANATIONS[lead.lead_name] ||
            'Это отведение показало заметный вклад в результат классификации.';
        const contribution = Number(lead.contribution || 0).toFixed(1);
        return `
            <div class="lead-card">
                <div class="lead-rank">#${i + 1}</div>
                <div class="lead-info">
                    <div class="lead-name">${lead.lead_name} — вклад ${contribution}%</div>
                    <div class="lead-explanation">${explanation}</div>
                </div>
            </div>
        `;
    }).join('');
}

function setupToggles() {
    const leadsToggle = document.getElementById('leadsToggle');
    const leadsContent = document.getElementById('leadsContent');

    if (leadsToggle && leadsContent) {
        leadsToggle.onchange = (e) => {
            leadsContent.classList.toggle('hidden', !e.target.checked);
        };
    }
}

function initializeECGViewer(result) {
    const viewer = initECGViewer('ecgViewer');

    if (result.ecg_data) {
        viewer.setData(
            result.ecg_data_raw || result.ecg_data,
            result.ecg_data,
            result.sampling_rate || 500,
            null,  // saliency_map - loaded on-demand
            result.r_peaks || null,
            result.ecg_data_full || null
        );
        viewer.render();
    }

    const viewModeSelect = document.getElementById('viewModeSelect');
    const dataModeSelect = document.getElementById('dataModeSelect');
    const leadSelectorGroup = document.getElementById('leadSelectorGroup');
    const leadSelect = document.getElementById('leadSelect');

    if (viewModeSelect) {
        viewModeSelect.addEventListener('change', (e) => {
            const mode = e.target.value;
            viewer.setViewMode(mode);
            leadSelectorGroup.style.display = mode === 'single' ? 'flex' : 'none';
        });
    }

    if (leadSelect) {
        leadSelect.addEventListener('change', (e) => {
            viewer.setSelectedLead(parseInt(e.target.value, 10));
        });
    }

    // Fullscreen modal
    const fullscreenBtn = document.getElementById('fullscreenBtn');
    const fullscreenModal = document.getElementById('ecgFullscreenModal');
    const fullscreenClose = document.getElementById('ecgFullscreenClose');
    const fullscreenBackdrop = document.getElementById('ecgFullscreenBackdrop');
    let fullscreenViewer = null;
    let fullSignalLoaded = Boolean(result.ecg_data_full);
    let fullSignalLoading = null;

    async function ensureFullSignalLoaded(fileId) {
        if (fullSignalLoaded) return true;
        if (!fullSignalLoading) {
            fullSignalLoading = (async () => {
                const response = await fetch(apiUrl(`/analysis/signal/${fileId}`));
                const data = await response.json();
                if (!response.ok || !data.success || !data.signal) {
                    throw new Error(data.error || 'Полный сигнал недоступен');
                }
                const signal = data.signal;
                viewer.setData(
                    signal.ecg_data_raw || signal.ecg_data,
                    signal.ecg_data,
                    signal.sampling_rate || 500,
                    null,
                    signal.r_peaks || null,
                    signal.ecg_data_full || null
                );
                fullSignalLoaded = Boolean(signal.ecg_data_full);
                return fullSignalLoaded;
            })().finally(() => {
                fullSignalLoading = null;
            });
        }
        return fullSignalLoading;
    }

    if (dataModeSelect) {
        dataModeSelect.addEventListener('change', async (e) => {
            const mode = e.target.value;
            const fileId = document.querySelector('.analysis-page').dataset.fileId;
            if (mode === 'full') {
                try {
                    const loaded = await ensureFullSignalLoaded(fileId);
                    if (!loaded) {
                        dataModeSelect.value = 'fragment';
                        Toast.warning('Полный сигнал недоступен');
                        return;
                    }
                } catch (err) {
                    dataModeSelect.value = 'fragment';
                    Toast.error(err.message || 'Не удалось загрузить весь файл');
                    return;
                }
            }
            viewer.setDataMode(mode);
        });
    }

    async function openFullscreen() {
        if (!fullscreenModal || !viewer) return;
        fullscreenModal.hidden = false;
        // Initialize fullscreen viewer if not exists
        if (!fullscreenViewer) {
            fullscreenViewer = initECGViewer('ecgFullscreenViewer');
        }
        // Load full signal on-demand if not already loaded
        const fileId = document.querySelector('.analysis-page').dataset.fileId;
        try {
            await ensureFullSignalLoaded(fileId);
        } catch (e) {
            console.warn('Failed to prepare fullscreen signal:', e);
        }
        // Clone state from main viewer
        fullscreenViewer.applyState(viewer.getState());
        fullscreenViewer.render();
    }

    function closeFullscreen() {
        if (fullscreenModal) fullscreenModal.hidden = true;
    }

    if (fullscreenBtn) fullscreenBtn.addEventListener('click', openFullscreen);
    if (fullscreenClose) fullscreenClose.addEventListener('click', closeFullscreen);
    if (fullscreenBackdrop) fullscreenBackdrop.addEventListener('click', closeFullscreen);

    // Sync controls between main and fullscreen viewers
    const syncControls = () => {
        if (!fullscreenViewer) return;
        const state = viewer.getState();
        fullscreenViewer.applyState(state);
        fullscreenViewer.render();
    };

    // Override viewer methods to sync
    const origSetViewMode = viewer.setViewMode.bind(viewer);
    viewer.setViewMode = (mode) => {
        origSetViewMode(mode);
        syncControls();
    };

    const origSetSelectedLead = viewer.setSelectedLead.bind(viewer);
    viewer.setSelectedLead = (leadIdx) => {
        origSetSelectedLead(leadIdx);
        syncControls();
    };

    const origSetDataMode = viewer.setDataMode.bind(viewer);
    viewer.setDataMode = (mode) => {
        origSetDataMode(mode);
        syncControls();
    };
}

function setupExportButton(fileId) {
    document.getElementById('exportPdfBtn').onclick = () => exportPDF(fileId);
}

async function pickSavePath(suggestedFilename) {
    try {
        const resp = await fetch(apiUrl('/export/pick-save-path'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: suggestedFilename }),
        });
        if (resp.status === 501) {
            const data = await resp.json().catch(() => ({}));
            Toast.warning(data.error || 'Выбор пути сохранения недоступен.');
            return null;
        }
        const data = await resp.json();
        if (!data.success || data.cancelled) {
            if (data.cancelled) Toast.info('Сохранение отменено');
            return null;
        }
        return data.path;
    } catch (e) {
        Toast.error('Ошибка выбора пути сохранения');
        return null;
    }
}

async function exportPDF(fileId) {
    const btn = document.getElementById('exportPdfBtn');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner spinner--sm"></span> Выбор пути…`;

    // Step 1: open save dialog
    const fileInfoEl = document.getElementById('fileInfo');
    const baseName = fileInfoEl ? fileInfoEl.textContent.trim() || `ecg_report_${fileId}` : `ecg_report_${fileId}`;
    const suggestedName = baseName.replace(/\.[^.]+$/, '') + '_report.pdf';
    const savePath = await pickSavePath(suggestedName);
    if (!savePath) {
        btn.innerHTML = original;
        btn.disabled = false;
        return;
    }

    // Step 2: generate, save to disk, and download
    btn.innerHTML = `<span class="spinner spinner--sm"></span> Генерация…`;

    try {
        const response = await fetch(apiUrl(`/export/pdf/${fileId}`), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ save_path: savePath }),
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ecg_report_${fileId}.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            Toast.success(`PDF-отчёт сохранён:\n${savePath}`);
        } else if (response.status === 404 || response.status === 400) {
            const data = await response.json();
            Toast.error(data.error || 'Не удалось создать PDF');
        } else {
            const data = await response.json();
            Toast.error(data.error || 'Не удалось создать PDF');
        }
    } catch (error) {
        Toast.error('Ошибка экспорта: ' + error.message);
    } finally {
        btn.innerHTML = original;
        btn.disabled = false;
    }
}

// Auto-initialize on analysis page
document.addEventListener('DOMContentLoaded', function() {
    const page = document.querySelector('.analysis-page');
    if (page) {
        const fileId = page.dataset.fileId;
        const urlParams = new URLSearchParams(window.location.search);
        const threshold = urlParams.get('threshold') || localStorage.getItem('ecgThreshold') || 0.35;
        runAnalysis(fileId, parseFloat(threshold));
    }
});
