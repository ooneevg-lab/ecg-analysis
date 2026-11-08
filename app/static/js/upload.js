/**
 * File upload functionality for ECG Analysis.
 */

let uploadZone, fileInput, uploadProgress, progressFill, progressText;
let globalThreshold = 0.35;
let currentSortMode = localStorage.getItem('sortMode') || 'group';
let currentViewFilter = 'all';
let currentClassFilter = null;
let _cachedFiles = [];
let _uploadAbort = null;
let _watcherStatus = null;
let _watcherPollTimer = null;
let _lastImportedCount = 0;
let _thresholdSaveTimer = null;
let _currentPage = 1;
let _totalPages = 1;

// API base path (use v1 for new features, fallback to non-versioned for compatibility)
const API_BASE = '/api/v1';

function apiUrl(path) {
    return `${API_BASE}${path}`;
}

const STAT_FILTERS = {
    pathology: { label: 'Патология',       match: f => f.analyzed && f.pred_class === 'PATHOLOG' },
    sr:        { label: 'Синусовый ритм',  match: f => f.analyzed && f.pred_class === 'SR' },
    normal:    { label: 'Норма',           match: f => f.analyzed && f.pred_class === 'NORM' },
    queue:     { label: 'В очереди',       match: f => !f.analyzed }
};

function formatRecordDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function recordDateKey(iso) {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return 'unknown';
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function recordDateLabel(iso) {
    if (!iso) return 'Без даты';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return 'Без даты';
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
}

function modelClassLabel(className) {
    const labels = {
        NORM: 'Норма',
        PATHOLOG: 'Патология',
        SR: 'Синусовый ритм'
    };
    return labels[className] || className || '';
}

function isDisplayedRhythmClass(rhythmClass) {
    return rhythmClass === 'bradycardia' || rhythmClass === 'tachycardia';
}

function isPathologyFile(file) {
    return file.pred_class === 'PATHOLOG' || file.prediction === 1;
}

function pathologyProbability(file) {
    return Number(file.pathology_probability ?? file.probability ?? 0) || 0;
}

function classProbability(file) {
    return Number(file.class_probability ?? file.confidence ?? file.probability ?? 0) || 0;
}

function percentText(value) {
    return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function applyThresholdValue(value, persistLocal = true) {
    const threshold = Number(value);
    if (!Number.isFinite(threshold)) return;
    globalThreshold = threshold;
    const slider = document.getElementById('globalThreshold');
    const valueEl = document.getElementById('globalThresholdValue');
    if (slider) slider.value = globalThreshold;
    if (valueEl) valueEl.textContent = globalThreshold.toFixed(2);
    if (persistLocal) localStorage.setItem('ecgThreshold', globalThreshold);
}

async function loadThresholdSetting() {
    try {
        const resp = await fetch(apiUrl('/session/threshold'));
        const data = await resp.json();
        if (data.success && data.threshold != null) {
            applyThresholdValue(data.threshold);
        }
    } catch (e) {
        const savedThreshold = localStorage.getItem('ecgThreshold');
        if (savedThreshold) applyThresholdValue(parseFloat(savedThreshold), false);
    }
}

function saveThresholdSetting(value) {
    clearTimeout(_thresholdSaveTimer);
    _thresholdSaveTimer = setTimeout(async () => {
        try {
            const resp = await fetch(apiUrl('/session/threshold'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ threshold: value }),
            });
            const data = await resp.json();
            if (data.success && data.threshold != null) {
                applyThresholdValue(data.threshold);
            }
        } catch (e) {
            Toast.warning('Не удалось сохранить порог на сервере');
        }
    }, 250);
}

function badgeIcon(badgeClass) {
    if (badgeClass === 'pathology') return Icons.alertCircle(18);
    if (badgeClass === 'rhythm-issue') return Icons.heartPulse(18);
    if (badgeClass === 'normal') return Icons.checkCircle(18);
    if (badgeClass === 'parse-error') return Icons.alert(18);
    if (badgeClass === 'skipped') return Icons.alert(18);
    return Icons.file(18);
}

function initUpload() {
    uploadZone = document.getElementById('uploadZone');
    fileInput = document.getElementById('fileInput');
    uploadProgress = document.getElementById('uploadProgress');
    progressFill = document.getElementById('progressBar') ? document.getElementById('progressBar').querySelector('.progress__fill') : null;
    progressText = document.getElementById('progressText');

    if (uploadZone) {
        uploadZone.addEventListener('click', () => fileInput && fileInput.click());
        uploadZone.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                fileInput && fileInput.click();
            }
        });
    }
    if (fileInput) fileInput.addEventListener('change', handleFileSelect);
    if (uploadZone) {
        uploadZone.addEventListener('dragover', handleDragOver);
        uploadZone.addEventListener('dragleave', handleDragLeave);
        uploadZone.addEventListener('drop', handleDrop);
    }

    const uploadIcon = document.getElementById('uploadIcon');
    if (uploadIcon) uploadIcon.innerHTML = Icons.filePlus(40);

    const emptyIcon = document.getElementById('emptyIcon');
    if (emptyIcon) emptyIcon.innerHTML = Icons.upload(56);

    const emptyBtn = document.getElementById('emptyStateBtn');
    if (emptyBtn) {
        emptyBtn.addEventListener('click', () => fileInput && fileInput.click());
        const lbl = emptyBtn.querySelector('.btn__label') || emptyBtn;
        if (emptyBtn.children.length === 0) emptyBtn.innerHTML = `${Icons.upload(16)} <span>Загрузить первый файл</span>`;
    }

    const statIcons = {
        statIconPathology: Icons.alertCircle,
        statIconSr: Icons.heartPulse,
        statIconNormal: Icons.checkCircle,
        statIconQueue: Icons.layers
    };
    Object.entries(statIcons).forEach(([id, fn]) => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = fn(22);
    });

    const bannerIcon = document.getElementById('classFilterBannerIcon');
    if (bannerIcon) bannerIcon.innerHTML = Icons.filter(16);

    const resetIcon = document.getElementById('classFilterResetBtnIcon');
    if (resetIcon) resetIcon.innerHTML = Icons.x(12);

    const filteredEmptyIcon = document.getElementById('filteredEmptyIcon');
    if (filteredEmptyIcon) filteredEmptyIcon.innerHTML = Icons.info(28);

    const filteredResetIcon = document.getElementById('filteredEmptyResetIcon');
    if (filteredResetIcon) filteredResetIcon.innerHTML = Icons.arrowLeft(14);

    const dashboard = document.getElementById('dashboard');
    if (dashboard) {
        dashboard.addEventListener('click', (e) => {
            const card = e.target.closest('.stat-card[data-filter]');
            if (card) toggleClassFilter(card.dataset.filter);
        });
        dashboard.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const card = e.target.closest('.stat-card[data-filter]');
            if (!card) return;
            e.preventDefault();
            toggleClassFilter(card.dataset.filter);
        });
    }

    const resetBtn = document.getElementById('classFilterResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', () => {
        currentClassFilter = null;
        updateClassFilterUI();
        applySorting();
    });

    const filteredEmptyReset = document.getElementById('filteredEmptyResetBtn');
    if (filteredEmptyReset) filteredEmptyReset.addEventListener('click', () => {
        currentClassFilter = null;
        updateClassFilterUI();
        applySorting();
    });

    const thresholdSlider = document.getElementById('globalThreshold');
    const thresholdValue = document.getElementById('globalThresholdValue');

    if (thresholdSlider && thresholdValue) {
        const savedThreshold = localStorage.getItem('ecgThreshold');
        if (savedThreshold) {
            applyThresholdValue(parseFloat(savedThreshold), false);
        }
        loadThresholdSetting();

        thresholdSlider.addEventListener('input', (e) => {
            applyThresholdValue(parseFloat(e.target.value));
            saveThresholdSetting(globalThreshold);
        });
    }

    initFolderCard();
    setupBulkActions();
    initWatcher();
    startWatcherPolling();
}

window.addEventListener('pageshow', () => {
    if (document.getElementById('fileList')) {
        loadFileList();
    }
});

function markViewedBeforeOpen(fileId) {
    const file = _cachedFiles.find(f => f.file_id === fileId);
    const prev = file ? file.viewed : false;
    const href = `/analysis/${fileId}?threshold=${globalThreshold}`;
    let canOpen = true;

    if (file) {
        file.viewed = true;
        applySorting();
    }

    fetch(apiUrl(`/session/viewed/${fileId}`), { method: 'POST' })
        .then(resp => {
            if (resp.status === 404) {
                canOpen = false;
                throw new Error('not found');
            }
            if (!resp.ok) throw new Error('viewed update failed');
        })
        .catch(() => {
            if (file) {
                file.viewed = prev;
                applySorting();
            }
            if (!canOpen) {
                Toast.error('Запись уже удалена или ещё не готова');
                loadFileList();
            }
        })
        .finally(() => {
            if (canOpen) window.location.href = href;
        });

    return false;
}

function setupBulkActions() {
    const selectAll = document.getElementById('selectAll');
    const deleteSelectedBtn = document.getElementById('deleteSelectedBtn');
    const deleteAllBtn = document.getElementById('deleteAllBtn');
    const analyzeAllBtn = document.getElementById('analyzeAllBtn');
    const exportStatsBtn = document.getElementById('exportStatsBtn');
    const sortByProbBtn = document.getElementById('sortByProbBtn');
    const viewFilter = document.getElementById('viewFilter');

    if (selectAll) {
        selectAll.addEventListener('change', (e) => {
            document.querySelectorAll('.file-checkbox').forEach(cb => cb.checked = e.target.checked);
            updateBulkButtons();
        });
    }

    if (deleteSelectedBtn) deleteSelectedBtn.addEventListener('click', deleteSelected);
    if (deleteAllBtn) deleteAllBtn.addEventListener('click', deleteAll);
    if (analyzeAllBtn) analyzeAllBtn.addEventListener('click', analyzeAll);
    if (exportStatsBtn) exportStatsBtn.addEventListener('click', exportAllStats);
    if (sortByProbBtn) sortByProbBtn.addEventListener('click', toggleSortByProbability);

    if (viewFilter) {
        viewFilter.value = currentViewFilter;
        viewFilter.addEventListener('change', (e) => {
            currentViewFilter = e.target.value;
            applySorting();
        });
    }

    const archiveBtn = document.getElementById('archiveBtn');
    if (archiveBtn) archiveBtn.addEventListener('click', openArchiveModal);

    const archiveCloseBtn = document.getElementById('archiveModalClose');
    if (archiveCloseBtn) archiveCloseBtn.addEventListener('click', closeArchiveModal);
    const archiveBackdrop = document.getElementById('archiveModalBackdrop');
    if (archiveBackdrop) archiveBackdrop.addEventListener('click', closeArchiveModal);

    const archiveSelectAllBtn = document.getElementById('archiveSelectAllBtn');
    if (archiveSelectAllBtn) archiveSelectAllBtn.addEventListener('click', toggleArchiveSelectAll);

    const archiveDeleteSelectedBtn = document.getElementById('archiveDeleteSelectedBtn');
    if (archiveDeleteSelectedBtn) archiveDeleteSelectedBtn.addEventListener('click', deleteArchiveSelected);

    if (selectAll) {
        selectAll.parentElement.querySelector('span').innerHTML = `${Icons.check(16)} <span>Выбрать все</span>`;
    }
    if (deleteSelectedBtn) deleteSelectedBtn.innerHTML = `${Icons.trash(16)} <span>Удалить выбранные</span>`;
    if (deleteAllBtn) deleteAllBtn.innerHTML = `${Icons.trash(16)} <span>Удалить все</span>`;
    if (analyzeAllBtn) analyzeAllBtn.innerHTML = `${Icons.play(16)} <span>Анализировать все</span>`;
    if (exportStatsBtn) exportStatsBtn.innerHTML = `${Icons.chart(16)} <span>Экспорт статистики</span>`;
    if (sortByProbBtn) {
        const sortStates = [
            { mode: 'pathology-desc', label: 'По вероятности ↓' },
            { mode: 'pathology-asc', label: 'По вероятности ↑' },
            { mode: 'class-desc', label: 'По уверенности ↓' },
            { mode: 'date-desc', label: 'По дате ↓' },
            { mode: 'date-asc', label: 'По дате ↑' },
            { mode: 'name', label: 'По имени А-Я' },
            { mode: 'group', label: 'По группам' }
        ];
        const cur = sortStates.find(s => s.mode === currentSortMode) || sortStates[3];
        sortByProbBtn.innerHTML = `${Icons.sort(16)} <span id="sortLabel">${cur.label}</span>`;
    }
}

function updateBulkButtons() {
    const selected = document.querySelectorAll('.file-checkbox:checked');
    const wrap = document.getElementById('deleteSelectedWrap');
    if (wrap) {
        wrap.classList.toggle('file-list-toolbar__btn-wrap--hidden', selected.length === 0);
    }
}

function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    uploadZone.classList.add('upload-zone--dragover');
}

function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    uploadZone.classList.remove('upload-zone--dragover');
}

function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    uploadZone.classList.remove('upload-zone--dragover');

    const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.xml'));
    if (files.length > 0) uploadFiles(files);
    else Toast.warning('Пожалуйста, выберите XML-файлы');
}

function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    if (files.length > 0) uploadFiles(files);
    e.target.value = '';
}

async function uploadFiles(files) {
    uploadProgress.style.display = 'block';
    if (progressFill) progressFill.style.width = '0%';
    const progressBar = document.getElementById('progressBar');
    if (progressBar) progressBar.classList.remove('progress--indeterminate');

    const cancelBtn = document.getElementById('cancelUploadBtn');
    if (cancelBtn) cancelBtn.style.display = 'inline-flex';

    _uploadAbort = new AbortController();
    let aborted = false;
    let uploaded = 0;
    let skipped = [];
    let parseErrors = [];

    for (const file of files) {
        if (aborted) break;
        if (_uploadAbort.signal.aborted) {
            aborted = true;
            break;
        }

        if (!file.name.toLowerCase().endsWith('.xml')) {
            skipped.push(file.name);
            continue;
        }

        progressText.textContent = `Загрузка ${file.name}…`;

        const formData = new FormData();
        formData.append('file', file);
        if (file.lastModified) {
            formData.append('last_modified_ms', String(file.lastModified));
        }

        try {
            const response = await fetch(apiUrl('/upload/ecg'), {
                method: 'POST',
                body: formData,
                signal: _uploadAbort.signal
            });
            const data = await response.json();

            if (data.success) {
                uploaded++;
                if (data.parse_error) parseErrors.push(file.name);
            } else {
                Toast.error(`Ошибка загрузки ${file.name}: ${data.error || 'неизвестная ошибка'}`);
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                aborted = true;
                break;
            }
            Toast.error(`Ошибка соединения: ${file.name}`);
        }

        if (progressFill) progressFill.style.width = ((uploaded + skipped.length) / files.length * 100) + '%';
    }

    if (cancelBtn) cancelBtn.style.display = 'none';

    if (aborted) {
        if (progressFill) progressFill.style.width = '100%';
        progressText.textContent = `Загрузка отменена. Загружено: ${uploaded}`;
        Toast.warning(`Загрузка отменена. Загружено файлов: ${uploaded}`);
    } else {
        if (progressFill) progressFill.style.width = '100%';
        let summaryText = `Загружено файлов: ${uploaded}`;
        if (parseErrors.length > 0) summaryText += ` (ошибка парсинга: ${parseErrors.length})`;
        if (skipped.length > 0) summaryText += `. Пропущено (не XML): ${skipped.length}`;
        progressText.textContent = summaryText;
        if (uploaded > 0) {
            Toast.success(`Загружено файлов: ${uploaded}`);
        } else if (parseErrors.length === 0 && skipped.length === 0) {
            Toast.info('Файлы не загружены');
        }
    }

    setTimeout(() => {
        uploadProgress.style.display = 'none';
        loadFileList();
        refreshFolderStats();
    }, 1000);
}

function cancelUpload() {
    if (_uploadAbort && !_uploadAbort.signal.aborted) {
        _uploadAbort.abort();
        progressText.textContent = 'Отмена загрузки…';
        const cancelBtn = document.getElementById('cancelUploadBtn');
        if (cancelBtn) cancelBtn.style.display = 'none';
    }
}

async function loadFileList(page = 1) {
    const fileListSection = document.getElementById('fileListSection');
    const emptyState = document.getElementById('emptyState');

    try {
        const response = await fetch(apiUrl(`/session/files?page=${page}&per_page=50`));
        const data = await response.json();

        const files = (data.success && data.files) ? data.files : [];
        _cachedFiles = files; // Keep only current page in cache for display
        _currentPage = data.page || 1;
        _totalPages = data.pages || 1;

        if (data.success && data.threshold != null) applyThresholdValue(data.threshold);
        updateDashboard(files);
        updateArchiveBadge(data.archive_count || 0);

        if (files.length > 0 || (data.archive_count || 0) > 0) {
            fileListSection.style.display = 'block';
            if (emptyState) emptyState.style.display = 'none';
            updateClassFilterUI();
            applySorting();
            renderPagination();
        } else {
            fileListSection.style.display = 'none';
            if (emptyState) emptyState.style.display = 'flex';
            hidePagination();
        }
    } catch (error) {
        console.error('Error loading file list:', error);
        Toast.error('Не удалось загрузить список файлов');
    }
}

function renderPagination() {
    const container = document.getElementById('pagination');
    if (!container) return;

    if (_totalPages <= 1) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }

    container.style.display = 'flex';
    let html = '';

    // Previous button
    html += `<button class="btn btn--ghost btn--sm" ${_currentPage === 1 ? 'disabled' : ''} onclick="loadFileList(${_currentPage - 1})">
        ${Icons.chevronLeft(16)} Назад
    </button>`;

    // Page numbers
    const maxPages = 5;
    let start = Math.max(1, _currentPage - Math.floor(maxPages / 2));
    let end = Math.min(_totalPages, start + maxPages - 1);
    if (end - start + 1 < maxPages) {
        start = Math.max(1, end - maxPages + 1);
    }

    for (let i = start; i <= end; i++) {
        html += `<button class="btn btn--${i === _currentPage ? 'primary' : 'ghost'} btn--sm" onclick="loadFileList(${i})">${i}</button>`;
    }

    // Next button
    html += `<button class="btn btn--ghost btn--sm" ${_currentPage === _totalPages ? 'disabled' : ''} onclick="loadFileList(${_currentPage + 1})">
        Вперёд ${Icons.chevronRight(16)}
    </button>`;

    container.innerHTML = html;
}

function hidePagination() {
    const container = document.getElementById('pagination');
    if (container) container.style.display = 'none';
}

function updateArchiveBadge(count) {
    const btn = document.getElementById('archiveBtn');
    if (!btn) return;
    const label = btn.querySelector('.archive-btn__count');
    if (label) label.textContent = String(count);
    btn.disabled = false;
}

function initWatcher() {
    const card = document.getElementById('watcherCard');
    if (!card) return;

    document.getElementById('watcherCardIcon').innerHTML = Icons.eye(20);
    document.getElementById('watcherPauseBtnIcon').innerHTML = '';
    document.getElementById('watcherRescanBtnIcon').innerHTML = '';
    document.getElementById('watcherConfigBtnIcon').innerHTML = Icons.settings(14);
    document.getElementById('watcherAuditBtnIcon').innerHTML = Icons.fileText(16);

    const pauseBtn = document.getElementById('watcherPauseBtn');
    if (pauseBtn) pauseBtn.addEventListener('click', toggleWatcherPause);

    const rescanBtn = document.getElementById('watcherRescanBtn');
    if (rescanBtn) rescanBtn.addEventListener('click', async () => {
        try {
            const resp = await fetch(apiUrl('/watcher/rescan'), { method: 'POST' });
            const data = await resp.json();
            if (!data.success) {
                Toast.error(data.error || 'Не удалось запустить ресскан');
            } else {
                Toast.info('Ресскан запущен');
                refreshWatcherStatus();
            }
        } catch (e) {
            Toast.error('Ошибка ресскана');
        }
    });

    const auditBtn = document.getElementById('watcherAuditBtn');
    if (auditBtn) auditBtn.addEventListener('click', openWatcherAuditModal);

    const configBtn = document.getElementById('watcherConfigBtn');
    if (configBtn) configBtn.addEventListener('click', openWatcherConfigModal);

    const cancelBtn = document.getElementById('watcherConfigCancel');
    if (cancelBtn) cancelBtn.addEventListener('click', closeWatcherConfigModal);
    const backdrop = document.getElementById('watcherConfigBackdrop');
    if (backdrop) backdrop.addEventListener('click', closeWatcherConfigModal);
    const saveBtn = document.getElementById('watcherConfigSave');
    if (saveBtn) saveBtn.addEventListener('click', saveWatcherConfig);

    const auditCloseBtn = document.getElementById('watcherAuditModalClose');
    if (auditCloseBtn) auditCloseBtn.addEventListener('click', closeWatcherAuditModal);
    const auditBackdrop = document.getElementById('watcherAuditBackdrop');
    if (auditBackdrop) auditBackdrop.addEventListener('click', closeWatcherAuditModal);

    const browseBtn = document.getElementById('watcherFolderBrowseBtn');
    if (browseBtn) browseBtn.addEventListener('click', pickWatcherFolder);

    refreshWatcherStatus();
}

function openWatcherAuditModal() {
    const modal = document.getElementById('watcherAuditModal');
    if (!modal) return;
    modal.hidden = false;
    loadWatcherAudit();
}

function closeWatcherAuditModal() {
    const modal = document.getElementById('watcherAuditModal');
    if (modal) modal.hidden = true;
}

async function loadWatcherAudit() {
    const list = document.getElementById('watcherAuditList');
    const empty = document.getElementById('watcherAuditEmpty');
    if (!list) return;
    list.innerHTML = '<div class="watcher-audit-list__loading">Загрузка…</div>';
    if (empty) empty.style.display = 'none';
    try {
        const resp = await fetch(apiUrl('/watcher/audit?limit=200'));
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Ошибка загрузки');
        const entries = data.entries || [];
        if (entries.length === 0) {
            list.innerHTML = '';
            if (empty) empty.style.display = 'block';
            return;
        }
        list.innerHTML = entries.map(createWatcherAuditEntry).join('');
        list.querySelectorAll('[data-audit-open-id]').forEach(btn => {
            btn.addEventListener('click', () => openWatcherAuditEntry(btn.dataset.auditOpenId));
        });
    } catch (e) {
        list.innerHTML = '<div class="watcher-audit-list__error">Не удалось загрузить журнал</div>';
    }
}

function createWatcherAuditEntry(entry) {
    const date = formatRecordDate(entry.timestamp) || '—';
    const eventLabels = {
        'import_started': 'Начало импорта',
        'import_completed': 'Импорт завершён',
        'import_failed': 'Ошибка импорта',
        'skipped_unchanged': 'Пропущен (без изменений)',
        'original_deleted': 'Оригинал удалён',
    };
    const eventLabel = eventLabels[entry.event] || entry.event;
    const eventClass = entry.event === 'import_failed' ? 'watcher-audit-entry--error'
        : entry.event === 'import_completed' ? 'watcher-audit-entry--success'
        : entry.event === 'skipped_unchanged' ? 'watcher-audit-entry--skipped'
        : '';
    const verdict = entry.verdict ? ` · ${entry.verdict}` : '';
    const prob = entry.probability != null ? ` · ${(entry.probability * 100).toFixed(1)}%` : '';
    const rhythm = entry.rhythm_class ? ` · ${entry.rhythm_class}` : '';
    const hr = entry.hr_bpm ? ` · ${entry.hr_bpm} уд/мин` : '';
    const error = entry.error ? `<div class="watcher-audit-entry__error">${escapeHtml(entry.error)}</div>` : '';
    const canOpen = entry.file_id && (entry.event === 'import_completed' || entry.event === 'skipped_unchanged');
    return `
        <div class="watcher-audit-entry ${eventClass}" data-file-id="${entry.file_id || ''}">
            <div class="watcher-audit-entry__main">
                <div class="watcher-audit-entry__header">
                    <span class="watcher-audit-entry__event">${escapeHtml(eventLabel)}</span>
                    <span class="watcher-audit-entry__time">${escapeHtml(date)}</span>
                </div>
                <div class="watcher-audit-entry__file">${escapeHtml(entry.filename || '—')}</div>
                <div class="watcher-audit-entry__details">
                    ${entry.source_path ? `<span class="watcher-audit-entry__path" title="${escapeHtml(entry.source_path)}">${escapeHtml(entry.source_path)}</span>` : ''}
                    ${verdict}${prob}${rhythm}${hr}
                </div>
                ${error}
            </div>
            ${canOpen ? `
            <div class="watcher-audit-entry__actions">
                <button class="btn btn--secondary btn--sm" type="button" data-audit-open-id="${escapeHtml(entry.file_id)}">
                    ${Icons.eye ? Icons.eye(14) : ''}
                    <span>Просмотр</span>
                </button>
            </div>
            ` : ''}
        </div>
    `;
}

async function openWatcherAuditEntry(fileId) {
    try {
        const resp = await fetch(apiUrl('/watcher/audit/open'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            Toast.error(data.error || 'Не удалось открыть запись журнала');
            return;
        }
        window.location.href = `/analysis/${data.file_id}?threshold=${globalThreshold}`;
    } catch (e) {
        Toast.error('Ошибка открытия записи журнала');
    }
}

function startWatcherPolling() {
    if (_watcherPollTimer) return;
    _watcherPollTimer = setInterval(refreshWatcherStatus, 3000);
}

async function refreshWatcherStatus() {
    const card = document.getElementById('watcherCard');
    if (!card) return;
    try {
        const resp = await fetch(apiUrl('/watcher/status'));
        if (resp.status === 503) {
            card.hidden = true;
            return;
        }
        const data = await resp.json();
        if (!data.success) {
            card.hidden = true;
            return;
        }
        _watcherStatus = data.status;
        card.hidden = false;
        renderWatcherCard(data.status);

        // Real-time dashboard: if the watcher imported new files since the
        // last poll, refresh the file list so the stat cards and list update
        // without a manual page reload.
        const newCount = Number(data.status.imported_count || 0);
        if (newCount > _lastImportedCount) {
            _lastImportedCount = newCount;
            loadFileList();
            refreshFolderStats();
        } else if (newCount < _lastImportedCount) {
            // The user cleared the state, or the server restarted — sync.
            _lastImportedCount = newCount;
        }
    } catch (e) {
        // network error — keep last known state
    }
}

function renderWatcherCard(status) {
    const title = document.getElementById('watcherCardTitle');
    const sub = document.getElementById('watcherCardSub');
    const pauseBtn = document.getElementById('watcherPauseBtn');
    const pauseLabel = document.getElementById('watcherPauseBtnLabel');
    const pauseIcon = document.getElementById('watcherPauseBtnIcon');

    const folder = status.folder || '(папка не задана)';
    const imported = status.imported_count || 0;
    const queue = status.queue_size || 0;
    const thresholdText = status.threshold != null ? ` · порог: ${Number(status.threshold).toFixed(2)}` : '';
    const escapedFolder = escapeHtml(folder);

    if (status.running) {
        title.innerHTML = `<strong>${escapedFolder}</strong> · <span class="watcher-card__pill watcher-card__pill--ok">работает</span>`;
        sub.textContent = `импортировано: ${imported} · в очереди: ${queue}${thresholdText}`;
        if (pauseLabel) pauseLabel.textContent = 'Пауза';
        if (pauseIcon) pauseIcon.innerHTML = '';
        if (pauseBtn) {
            pauseBtn.hidden = false;
            pauseBtn.disabled = false;
        }
    } else {
        title.innerHTML = `<strong>${escapedFolder}</strong> · <span class="watcher-card__pill watcher-card__pill--off">пауза</span>`;
        if (status.folder) {
            sub.textContent = status.last_error
                ? `ошибка: ${status.last_error}`
                : `импортировано: ${imported}${thresholdText}`;
        } else {
            sub.textContent = 'Нажмите «Настроить», чтобы выбрать папку';
        }
        if (pauseLabel) pauseLabel.textContent = 'Запустить';
        if (pauseIcon) pauseIcon.innerHTML = '';
        if (pauseBtn) {
            pauseBtn.hidden = false;
            pauseBtn.disabled = !status.folder;
        }
    }
}


async function toggleWatcherPause() {
    const running = _watcherStatus && _watcherStatus.running;
    const endpoint = running ? apiUrl('/watcher/pause') : apiUrl('/watcher/resume');
    try {
        const resp = await fetch(endpoint, { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            Toast.error(data.error || 'Ошибка');
        } else {
            refreshWatcherStatus();
        }
    } catch (e) {
        Toast.error('Сетевая ошибка');
    }
}

function openWatcherConfigModal() {
    const modal = document.getElementById('watcherConfigModal');
    if (!modal) return;
    const folder = (_watcherStatus && _watcherStatus.folder) || '';
    const auto = _watcherStatus && _watcherStatus.auto_analyze;
    const del = _watcherStatus && _watcherStatus.delete_after_import;
    document.getElementById('watcherFolderInput').value = folder || '';
    document.getElementById('watcherAutoAnalyze').checked = !!auto;
    document.getElementById('watcherDeleteAfter').checked = !!del;
    document.getElementById('watcherFolderHint').textContent = '';
    modal.hidden = false;
}

function closeWatcherConfigModal() {
    const modal = document.getElementById('watcherConfigModal');
    if (modal) modal.hidden = true;
}

async function saveWatcherConfig() {
    const folder = document.getElementById('watcherFolderInput').value.trim();
    const autoAnalyze = document.getElementById('watcherAutoAnalyze').checked;
    const deleteAfter = document.getElementById('watcherDeleteAfter').checked;
    const hint = document.getElementById('watcherFolderHint');
    hint.textContent = 'Сохранение…';
    try {
        const resp = await fetch(apiUrl('/watcher/config'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder: folder || null,
                auto_analyze: autoAnalyze,
                delete_after_import: deleteAfter,
            }),
        });
        const data = await resp.json();
        if (!data.success) {
            hint.textContent = data.error || 'Не удалось сохранить';
            return;
        }
        closeWatcherConfigModal();
        Toast.success('Настройки слежения сохранены');
        refreshWatcherStatus();
        loadFileList();
    } catch (e) {
        hint.textContent = 'Сетевая ошибка';
    }
}

function openArchiveModal() {
    const modal = document.getElementById('archiveModal');
    if (!modal) return;
    modal.hidden = false;
    loadArchiveList();
}

function closeArchiveModal() {
    const modal = document.getElementById('archiveModal');
    if (modal) modal.hidden = true;
    // Reset selection state
    const selectAllBtn = document.getElementById('archiveSelectAllBtn');
    if (selectAllBtn) selectAllBtn.dataset.selected = 'false';
    const deleteSelectedBtn = document.getElementById('archiveDeleteSelectedBtn');
    if (deleteSelectedBtn) deleteSelectedBtn.style.display = 'none';
}

function toggleArchiveSelectAll() {
    const list = document.getElementById('archiveList');
    const selectAllBtn = document.getElementById('archiveSelectAllBtn');
    const deleteSelectedBtn = document.getElementById('archiveDeleteSelectedBtn');
    if (!list || !selectAllBtn) return;

    const checkboxes = list.querySelectorAll('.archive-checkbox');
    const currentlySelected = selectAllBtn.dataset.selected === 'true';
    const newSelected = !currentlySelected;

    checkboxes.forEach(cb => cb.checked = newSelected);
    selectAllBtn.dataset.selected = newSelected ? 'true' : 'false';

    const icon = document.getElementById('archiveSelectAllBtnIcon');
    if (icon) icon.innerHTML = newSelected ? Icons.check(14) : Icons.checkSquare(14);
    const label = selectAllBtn.querySelector('span:last-child');
    if (label) label.textContent = newSelected ? 'Снять выделение' : 'Выбрать все';

    if (deleteSelectedBtn) deleteSelectedBtn.style.display = newSelected ? 'inline-flex' : 'none';
}

async function deleteArchiveSelected() {
    const list = document.getElementById('archiveList');
    if (!list) return;

    const checkboxes = list.querySelectorAll('.archive-checkbox:checked');
    if (checkboxes.length === 0) {
        Toast.info('Ничего не выбрано');
        return;
    }

    const ok = await Modal.confirm({
        title: `Удалить ${checkboxes.length} файл(ов) из архива?`,
        description: 'Выбранные файлы будут удалены навсегда. Это действие нельзя отменить.',
        confirmText: 'Удалить',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;

    const fileIds = Array.from(checkboxes).map(cb => cb.value);
    try {
        const resp = await fetch(apiUrl('/session/archive/bulk'), {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds })
        });
        const data = await resp.json();
        if (!data.success) {
            Toast.error(data.error || 'Не удалось удалить');
            return;
        }
        Toast.success(`Удалено файлов: ${data.deleted}`);
        loadArchiveList();
        loadFileList();
    } catch (e) {
        Toast.error('Сетевая ошибка');
    }
}

async function loadArchiveList() {
    const list = document.getElementById('archiveList');
    const empty = document.getElementById('archiveEmpty');
    if (!list) return;
    list.innerHTML = '<div class="archive-list__loading">Загрузка…</div>';
    if (empty) empty.style.display = 'none';
    try {
        const resp = await fetch(apiUrl('/session/archive'));
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Ошибка загрузки');
        const files = data.files || [];
        if (files.length === 0) {
            list.innerHTML = '';
            if (empty) empty.style.display = 'block';
            return;
        }
        list.innerHTML = files.map(createArchiveItem).join('');
        list.querySelectorAll('[data-open-id]').forEach(btn => {
            btn.addEventListener('click', () => markViewedBeforeOpen(btn.dataset.openId));
        });
        list.querySelectorAll('[data-restore-id]').forEach(btn => {
            btn.addEventListener('click', () => restoreFromArchive(btn.dataset.restoreId));
        });
        list.querySelectorAll('[data-delete-id]').forEach(btn => {
            btn.addEventListener('click', () => deleteFromArchive(btn.dataset.deleteId));
        });
    } catch (e) {
        list.innerHTML = '<div class="archive-list__error">Не удалось загрузить архив</div>';
    }
}

function createArchiveItem(file) {
    const date = formatRecordDate(file.created_at) || '—';
    const verdict = file.verdict || (file.pred_class ? modelClassLabel(file.pred_class) : '—');
    const rhythm = file.rhythm_class_ru || '';
    const hr = file.hr_bpm ? `${Math.round(file.hr_bpm)} уд/мин` : '';
    const prob = file.pathology_probability != null
        ? `${(file.pathology_probability * 100).toFixed(0)}%`
        : '';
    const sourceMark = file.auto_imported
        ? '<span class="file-item__source-mark" title="Импортировано из папки">📥</span>'
        : '';
    return `
        <div class="archive-item" data-file-id="${file.file_id}">
            <label class="archive-item__checkbox-wrap">
                <input type="checkbox" class="archive-checkbox" value="${file.file_id}" aria-label="Выбрать для удаления">
                <span class="archive-checkbox__box"></span>
            </label>
            <div class="archive-item__main">
                <div class="archive-item__title">
                    ${sourceMark}
                    <span class="archive-item__filename">${escapeHtml(file.filename)}</span>
                </div>
                <div class="archive-item__meta">
                    <span>${date}</span>
                    <span class="archive-item__sep">·</span>
                    <span>${escapeHtml(verdict)}</span>
                    ${rhythm ? `<span class="archive-item__sep">·</span><span>${escapeHtml(rhythm)}</span>` : ''}
                    ${hr ? `<span class="archive-item__sep">·</span><span>${hr}</span>` : ''}
                    ${prob ? `<span class="archive-item__sep">·</span><span>${prob}</span>` : ''}
                </div>
            </div>
            <div class="archive-item__actions">
                ${file.can_open_analysis ? `
                <button class="btn btn--secondary btn--sm" data-open-id="${file.file_id}">
                    Просмотр
                </button>
                ` : ''}
                <button class="btn btn--ghost btn--sm" data-restore-id="${file.file_id}">
                    Восстановить
                </button>
                <button class="btn btn--ghost btn--sm btn--danger" data-delete-id="${file.file_id}" title="Удалить навсегда">
                    ${Icons.trash(14)}
                </button>
            </div>
        </div>
    `;
}

async function restoreFromArchive(fileId) {
    try {
        const resp = await fetch(apiUrl(`/session/restore/${encodeURIComponent(fileId)}`), {
            method: 'POST',
        });
        const data = await resp.json();
        if (!data.success) {
            Toast.error(data.error || 'Не удалось восстановить');
            return;
        }
        Toast.success('Файл возвращён в активный список');
        loadArchiveList();
        loadFileList();
    } catch (e) {
        Toast.error('Сетевая ошибка');
    }
}

async function deleteFromArchive(fileId) {
    const ok = await Modal.confirm({
        title: 'Удалить из архива?',
        description: 'Файл будет удалён навсегда. Это действие нельзя отменить.',
        confirmText: 'Удалить',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;

    try {
        const resp = await fetch(apiUrl(`/session/archive/${encodeURIComponent(fileId)}`), {
            method: 'DELETE',
        });
        const data = await resp.json();
        if (!data.success) {
            Toast.error(data.error || 'Не удалось удалить');
            return;
        }
        Toast.success('Файл удалён из архива');
        loadArchiveList();
        loadFileList();
    } catch (e) {
        Toast.error('Сетевая ошибка');
    }
}

async function pickWatcherFolder() {
    const input = document.getElementById('watcherFolderInput');
    const hint = document.getElementById('watcherFolderHint');
    hint.textContent = 'Открываем диалог выбора папки…';

    // Delegate to the server: the browser-side showDirectoryPicker hides the
    // absolute path, so we ask the local Flask process to run a Tk dialog.
    try {
        const resp = await fetch(apiUrl('/watcher/pick-folder'), { method: 'POST' });
        if (resp.status === 501) {
            // No Tk available on the server — fall back to a text prompt.
            const data = await resp.json().catch(() => ({}));
            hint.textContent = data.error || 'Введите путь вручную.';
            const value = prompt('Введите полный путь к папке (например, C:\\cardiograph\\export):', input.value);
            if (value !== null) input.value = value;
            return;
        }
        const data = await resp.json();
        if (data.success) {
            if (data.folder) {
                input.value = data.folder;
                hint.textContent = `Выбрано: ${data.folder}. Нажмите «Сохранить».`;
            } else {
                hint.textContent = 'Диалог закрыт без выбора';
            }
        } else {
            hint.textContent = data.error || 'Не удалось открыть диалог';
        }
    } catch (e) {
        hint.textContent = 'Сетевая ошибка';
    }
}

function updateDashboard(files) {
    const pathology = files.filter(f => f.analyzed && f.pred_class === 'PATHOLOG').length;
    const sr = files.filter(f => f.analyzed && f.pred_class === 'SR').length;
    const normal = files.filter(f => f.analyzed && f.pred_class === 'NORM').length;
    const queue = files.filter(f => !f.analyzed).length;

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    setText('statPathology', pathology);
    setText('statSr', sr);
    setText('statNormal', normal);
    setText('statQueue', queue);
}

function toggleClassFilter(key) {
    if (!STAT_FILTERS[key]) return;
    currentClassFilter = (currentClassFilter === key) ? null : key;
    updateClassFilterUI();
    applySorting();
}

function updateClassFilterUI() {
    const cards = document.querySelectorAll('#dashboard .stat-card[data-filter]');
    cards.forEach(card => {
        const isActive = card.dataset.filter === currentClassFilter;
        card.classList.toggle('stat-card--active', isActive);
        card.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        const baseKey = card.dataset.filter;
        card.classList.toggle(`stat-card--${baseKey}`, isActive);
    });

    const banner = document.getElementById('classFilterBanner');
    const bannerLabel = document.getElementById('classFilterBannerLabel');
    if (banner && bannerLabel) {
        if (currentClassFilter && STAT_FILTERS[currentClassFilter]) {
            banner.style.display = 'flex';
            bannerLabel.textContent = STAT_FILTERS[currentClassFilter].label;
        } else {
            banner.style.display = 'none';
            bannerLabel.textContent = '';
        }
    }
}

function renderFileList(files) {
    const fileList = document.getElementById('fileList');
    const filteredEmpty = document.getElementById('filteredEmpty');
    const filteredDesc = document.getElementById('filteredEmptyDescription');
    if (!fileList) return;

    let lastDateKey = null;
    const rows = [];
    files.forEach(file => {
        const key = recordDateKey(file.created_at);
        if (key !== lastDateKey) {
            rows.push(`<div class="file-date-separator">${escapeHtml(recordDateLabel(file.created_at))}</div>`);
            lastDateKey = key;
        }
        rows.push(createFileItem(file));
    });
    fileList.innerHTML = rows.join('');
    document.querySelectorAll('.file-checkbox').forEach(cb => cb.addEventListener('change', updateBulkButtons));
    updateBulkButtons();

    if (files.length === 0 && currentClassFilter && STAT_FILTERS[currentClassFilter] && filteredEmpty) {
        fileList.style.display = 'none';
        filteredEmpty.style.display = 'flex';
        if (filteredDesc) {
            filteredDesc.textContent = `В категории «${STAT_FILTERS[currentClassFilter].label}» пока нет записей. Загрузите новые файлы или измените порог, либо сбросьте фильтр.`;
        }
    } else if (files.length === 0 && _cachedFiles.length === 0) {
        fileList.style.display = '';
        fileList.innerHTML = `
            <div class="filtered-empty filtered-empty--inline">
                <div class="filtered-empty__title">Активных записей нет</div>
                <div class="filtered-empty__description">Откройте архив, чтобы посмотреть предыдущие результаты.</div>
                <button class="btn btn--secondary btn--sm" type="button" onclick="openArchiveModal()">Открыть архив</button>
            </div>
        `;
        if (filteredEmpty) filteredEmpty.style.display = 'none';
    } else {
        fileList.style.display = '';
        if (filteredEmpty) filteredEmpty.style.display = 'none';
    }
}

function toggleSortByProbability() {
    const btn = document.getElementById('sortByProbBtn');
    const label = document.getElementById('sortLabel');

    const states = [
        { mode: 'pathology-desc', label: 'По вероятности ↓' },
        { mode: 'pathology-asc', label: 'По вероятности ↑' },
        { mode: 'class-desc', label: 'По уверенности ↓' },
        { mode: 'date-desc', label: 'По дате ↓' },
        { mode: 'date-asc', label: 'По дате ↑' },
        { mode: 'name', label: 'По имени А-Я' },
        { mode: 'group', label: 'По группам' }
    ];

    const idx = states.findIndex(s => s.mode === currentSortMode);
    const next = states[(idx + 1) % states.length];
    currentSortMode = next.mode;
    localStorage.setItem('sortMode', currentSortMode);
    if (label) label.textContent = next.label;
    if (btn && !label) btn.querySelector('span').textContent = next.label;

    applySorting();
}

function applySorting() {
    let sorted = [..._cachedFiles];

    if (currentClassFilter && STAT_FILTERS[currentClassFilter]) {
        sorted = sorted.filter(STAT_FILTERS[currentClassFilter].match);
    }

    if (currentViewFilter === 'viewed') {
        sorted = sorted.filter(f => f.viewed);
    } else if (currentViewFilter === 'unviewed') {
        sorted = sorted.filter(f => !f.viewed);
    }

    if (currentSortMode === 'pathology-desc') {
        sorted.sort((a, b) => sortAnalyzedFirst(a, b) || pathologyProbability(b) - pathologyProbability(a));
    } else if (currentSortMode === 'pathology-asc') {
        sorted.sort((a, b) => sortAnalyzedFirst(a, b) || pathologyProbability(a) - pathologyProbability(b));
    } else if (currentSortMode === 'class-desc') {
        sorted.sort((a, b) => sortAnalyzedFirst(a, b) || classProbability(b) - classProbability(a));
    } else if (currentSortMode === 'date-desc') {
        sorted.sort((a, b) => sortAnalyzedFirst(a, b) || (Date.parse(b.created_at || 0) - Date.parse(a.created_at || 0)));
    } else if (currentSortMode === 'date-asc') {
        sorted.sort((a, b) => sortAnalyzedFirst(a, b) || (Date.parse(a.created_at || 0) - Date.parse(b.created_at || 0)));
    } else if (currentSortMode === 'name') {
        sorted.sort((a, b) => a.filename.localeCompare(b.filename, 'ru'));
    } else if (currentSortMode === 'group') {
        const groupOrder = { pathology: 0, sr: 1, normal: 2, none: 3 };
        sorted.sort((a, b) => {
            const ga = groupOrder[getFileGroup(a)];
            const gb = groupOrder[getFileGroup(b)];
            if (ga !== gb) return ga - gb;
            return pathologyProbability(b) - pathologyProbability(a);
        });
    }

    renderFileList(sorted);
}

function sortAnalyzedFirst(a, b) {
    if (!a.analyzed && !b.analyzed) return 0;
    if (!a.analyzed) return 1;
    if (!b.analyzed) return -1;
    return 0;
}

function getFileGroup(file) {
    if (!file.analyzed) return 'none';
    if (isPathologyFile(file) || file.verdict === 'Патология') return 'pathology';
    if (file.pred_class === 'SR' || isDisplayedRhythmClass(file.rhythm_class)) return 'sr';
    return 'normal';
}

function getFileThreshold(file) {
    return file.threshold ?? file.analysis_result?.threshold ?? null;
}

function hasThresholdChanged(file) {
    const fileThreshold = getFileThreshold(file);
    if (fileThreshold === null || !file.analyzed) return false;
    return Math.abs(fileThreshold - globalThreshold) > 0.001;
}

function createFileItem(file) {
    let badgeClass = '';
    let badgeText = '';
    let classProbText = '';
    let pathologyProbText = '';

    const hasParseError = file.parse_error;
    const hasValidationIssue = !file.is_valid && !hasParseError;
    const isPathology = isPathologyFile(file);

    if (hasParseError) {
        badgeClass = 'parse-error';
        badgeText = 'Ошибка';
    } else if (hasValidationIssue) {
        badgeClass = 'skipped';
        badgeText = 'Пропущен';
    } else if (file.analyzed) {
        classProbText = percentText(classProbability(file));
        pathologyProbText = percentText(pathologyProbability(file));

        if (isPathology) {
            badgeClass = 'pathology';
            badgeText = 'Патология';
        } else if (file.pred_class === 'SR') {
            badgeClass = 'rhythm-issue';
            badgeText = modelClassLabel('SR');
        } else if (file.pred_class === 'NORM') {
            badgeClass = 'normal';
            badgeText = modelClassLabel('NORM');
        } else if (isDisplayedRhythmClass(file.rhythm_class)) {
            badgeClass = 'rhythm-issue';
            badgeText = file.rhythm_class_ru || 'Нарушение ритма';
        } else {
            badgeClass = 'normal';
            badgeText = modelClassLabel('NORM');
        }
    }

    let status = '';
    if (hasParseError) {
        status = escapeHtml(file.parse_error);
    } else if (hasValidationIssue) {
        const errors = file.validation_errors || [];
        status = errors.length > 0 ? escapeHtml(errors.join('; ')) : 'Файл не прошёл валидацию';
    } else if (file.analyzed) {
        const mainVerdict = file.pred_class ? modelClassLabel(file.pred_class) : (isPathology ? 'Патология' : 'Норма');
        const verdictClass = isPathology ? 'file-item__status-value--pathology'
                           : file.pred_class === 'SR' ? 'file-item__status-value--sr'
                           : 'file-item__status-value--normal';
        const rhythmClass = file.pred_class === 'SR' ? 'file-item__status-value--sr' : 'file-item__status-value--normal';
        status = `класс: <strong class="file-item__status-value ${verdictClass}">${escapeHtml(mainVerdict)}</strong>`
               + `, уверенность <strong class="file-item__status-value">${classProbText}</strong>`
               + ` · патология <strong class="file-item__status-value file-item__status-value--prob">${pathologyProbText}</strong>`;
        if (file.rhythm_class && file.rhythm_class !== 'unknown') {
            status += ` · ритм: <strong class="file-item__status-value ${rhythmClass}">${escapeHtml(file.rhythm_class_ru || 'Норма')}</strong>`;
        }
        if (file.threshold_applied && file.threshold_rule) {
            status += ' · класс изменён правилом постобработки';
        }
    } else {
        status = 'Ожидает анализа';
    }

    const verdictBadge = file.analyzed || !file.is_valid
        ? `<span class="file-item__verdict file-item__verdict--${badgeClass}">${badgeIcon(badgeClass)}<span>${escapeHtml(badgeText)}</span></span>`
        : '';
    const viewedBadge = file.viewed
        ? '<span class="viewed-badge viewed-badge--viewed">Прослушано</span>'
        : '<span class="viewed-badge viewed-badge--unviewed">Новое</span>';

    const thresholdChanged = hasThresholdChanged(file);
    const thresholdBadge = thresholdChanged
        ? `<span class="file-item__threshold-badge" title="Порог изменился с ${getFileThreshold(file).toFixed(2)} на ${globalThreshold.toFixed(2)}">⚠ Порог изменен</span>`
        : '';

    const dateStr = formatRecordDate(file.created_at);
    const dateHtml = dateStr ? `<span class="file-item__date" title="Дата записи">📅 ${escapeHtml(dateStr)}</span>` : '';
    const sourceMark = file.source === 'watcher'
        ? '<span class="file-item__source-mark" title="Импортировано из отслеживаемой папки">📥</span>'
        : '';

    // Button text and action for analyzed files
    let actionButton = '';
    if (file.is_valid) {
        if (file.analyzed) {
            if (thresholdChanged) {
                actionButton = `<button class="btn btn--warning btn--sm reanalyze-btn" data-file-id="${file.file_id}" data-force="true" title="Переанализировать с текущим порогом">
                    ${Icons.refresh ? Icons.refresh(14) : Icons.play(14)}
                    <span>Повторный анализ</span>
                </button>`;
            } else {
                actionButton = `<a href="/analysis/${file.file_id}?threshold=${globalThreshold}" class="btn btn--primary btn--sm" onclick="return markViewedBeforeOpen('${file.file_id}')">
                    ${Icons.eye(14)}
                    <span>Просмотр</span>
                </a>`;
            }
        } else {
            actionButton = `<button class="btn btn--primary btn--sm analyze-btn" data-file-id="${file.file_id}" data-force="false">
                ${Icons.play(14)}
                <span>Анализ</span>
            </button>`;
        }
    } else {
        actionButton = `<span class="file-item__disabled">${hasParseError ? 'Невозможно обработать' : 'Не подлежит анализу'}</span>`;
    }

    return `
        <div class="file-item ${file.viewed ? 'file-item--viewed' : ''} ${hasParseError ? 'file-item--error' : ''} ${hasValidationIssue ? 'file-item--skipped' : ''} ${thresholdChanged ? 'file-item--threshold-changed' : ''}" data-file-id="${file.file_id}" data-probability="${file.analyzed ? pathologyProbability(file) : -1}">
            <input type="checkbox" class="file-checkbox file-item__checkbox" value="${file.file_id}" aria-label="Выбрать файл">
            <div class="file-item__main">
                <div class="file-item__icon">${Icons.file(20)}</div>
                <div class="file-item__details">
                    <div class="file-item__name">${escapeHtml(file.filename)} ${sourceMark} ${thresholdBadge}</div>
                    <div class="file-item__status">${status}</div>
                </div>
            </div>
            <div class="file-item__meta">
                ${dateHtml}
                ${viewedBadge}
                ${verdictBadge}
            </div>
            <div class="file-item__actions">
                ${actionButton}
                <button class="btn btn--ghost btn--sm btn--icon" data-action="archive" data-file-id="${file.file_id}" title="В архив" aria-label="В архив">
                    ${Icons.archive ? Icons.archive(16) : Icons.file(16)}
                </button>
                <button class="btn btn--ghost btn--sm btn--icon" data-action="delete" data-file-id="${file.file_id}" title="Удалить" aria-label="Удалить файл">
                    ${Icons.trash(16)}
                </button>
            </div>
        </div>
    `;
}

document.addEventListener('click', (e) => {
    const archiveBtn = e.target.closest('[data-action="archive"]');
    if (archiveBtn) {
        e.preventDefault();
        const fileId = archiveBtn.dataset.fileId;
        if (fileId) archiveFile(fileId);
        return;
    }

    const deleteBtn = e.target.closest('[data-action="delete"]');
    if (deleteBtn) {
        e.preventDefault();
        const fileId = deleteBtn.dataset.fileId;
        if (fileId) deleteFile(fileId);
        return;
    }

    const analyzeBtn = e.target.closest('.analyze-btn, .reanalyze-btn');
    if (analyzeBtn) {
        e.preventDefault();
        const fileId = analyzeBtn.dataset.fileId;
        const force = analyzeBtn.dataset.force === 'true';
        if (fileId) runAnalysis(fileId, force);
        return;
    }
});

async function archiveFile(fileId) {
    try {
        const response = await fetch(apiUrl(`/session/archive/${fileId}`), { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            Toast.success('Файл перемещён в архив');
            loadFileList();
        } else {
            Toast.error(data.error || 'Ошибка архивации');
        }
    } catch (error) {
        Toast.error('Ошибка соединения с сервером');
    }
}

async function deleteFile(fileId) {
    const file = _cachedFiles.find(f => f.file_id === fileId);
    const name = file ? file.filename : 'файл';
    const ok = await Modal.confirm({
        title: 'Удалить файл?',
        description: `Файл «${name}» будет удалён из сессии. Это действие нельзя отменить.`,
        confirmText: 'Удалить',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;

    try {
        const response = await fetch(apiUrl(`/upload/ecg/${fileId}`), { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            Toast.success('Файл удалён');
            loadFileList();
            refreshFolderStats();
        } else {
            Toast.error(data.error || 'Ошибка удаления');
        }
    } catch (error) {
        Toast.error('Ошибка соединения с сервером');
    }
}

async function deleteSelected() {
    const selected = document.querySelectorAll('.file-checkbox:checked');
    if (selected.length === 0) return;
    const ok = await Modal.confirm({
        title: `Удалить ${selected.length} файл(ов)?`,
        description: 'Выбранные файлы будут удалены из текущей сессии.',
        confirmText: 'Удалить',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;
    for (const cb of selected) await deleteFileSilent(cb.value);
    Toast.success('Файлы удалены');
    loadFileList();
    refreshFolderStats();
}

async function deleteFileSilent(fileId) {
    try {
        await fetch(apiUrl(`/upload/ecg/${fileId}`), { method: 'DELETE' });
    } catch (e) { /* ignore */ }
}

async function deleteAll() {
    if (_cachedFiles.length === 0) {
        Toast.info('Список файлов пуст');
        return;
    }
    const ok = await Modal.confirm({
        title: 'Удалить все файлы?',
        description: `Будут удалены все ${_cachedFiles.length} файлов из текущей сессии. Это действие нельзя отменить.`,
        confirmText: 'Удалить всё',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;
    try {
        const response = await fetch(apiUrl('/session/clear'), { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            Toast.success('Все файлы удалены');
            loadFileList();
            refreshFolderStats();
        } else {
            Toast.error(data.error || 'Не удалось очистить сессию');
        }
    } catch (error) {
        Toast.error('Ошибка очистки сессии');
    }
}

async function runAnalysis(fileId, forceReanalyze = false) {
    const file = _cachedFiles.find(f => f.file_id === fileId);
    if (!file) return;

    if (!file.is_valid) {
        Toast.error('Файл не прошел валидацию');
        return;
    }

    if (file.source === 'upload' && forceReanalyze && file.analyzed) {
        // For manual uploads, original file not on server
        const ok = await Modal.confirm({
            title: 'Оригинальный файл недоступен',
            description: 'Для повторного анализа нужно загрузить файл заново. Хотите удалить эту запись и загрузить файл снова?',
            confirmText: 'Удалить и загрузить заново',
            cancelText: 'Отмена',
            tone: 'warning'
        });
        if (!ok) return;
        // Delete this file entry
        await deleteFileSilent(fileId);
        Toast.info('Загрузите файл через зону загрузки');
        loadFileList();
        return;
    }

    const btn = document.querySelector(`.analyze-btn[data-file-id="${fileId}"], .reanalyze-btn[data-file-id="${fileId}"]`);
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner spinner--sm"></span> Анализ…`;
    }

    try {
        const response = await fetch(apiUrl(`/analysis/run/${fileId}`), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ threshold: globalThreshold, include_xai: false, force_reanalyze: forceReanalyze })
        });
        const data = await response.json();
        if (response.ok && data.success) {
            Toast.success(forceReanalyze ? 'Повторный анализ завершен' : 'Анализ завершен');
            loadFileList();
        } else {
            Toast.error(data.error || 'Ошибка анализа');
        }
    } catch (error) {
        Toast.error('Ошибка соединения: ' + error.message);
    } finally {
        if (btn && originalHtml) {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    }
}

async function batchAnalyze(fileIds, forceReanalyze) {
    const toastId = Toast.show({
        type: 'info',
        title: 'Пакетный анализ',
        message: `Анализ файлов: 0/${fileIds.length}…`,
        duration: 0
    });

    let analyzed = 0;
    let errors = 0;

    for (let i = 0; i < fileIds.length; i++) {
        const fileId = fileIds[i];
        const fileData = _cachedFiles.find(f => f.file_id === fileId);
        const fname = fileData ? fileData.filename : `Файл ${i + 1}`;
        Toast.update(toastId, { message: `[${i + 1}/${fileIds.length}] ${forceReanalyze ? 'Переанализ' : 'Анализ'}: ${fname}…` });

        try {
            const response = await fetch(apiUrl(`/analysis/run/${fileId}`), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ threshold: globalThreshold, include_xai: false, force_reanalyze: forceReanalyze })
            });
            const data = await response.json();
            if (response.ok && data.success) {
                analyzed++;
            } else {
                errors++;
            }
        } catch (error) {
            errors++;
        }
    }

    Toast.dismiss(toastId);
    loadFileList();
    let message = `${forceReanalyze ? 'Переанализировано' : 'Проанализировано'} файлов: ${analyzed}`;
    if (errors > 0) message += `, ошибок: ${errors}`;
    if (errors > 0) Toast.warning(message);
    else Toast.success(message);
}

async function analyzeAll() {
    const validFiles = _cachedFiles.filter(f => f.is_valid && !f.analyzed);
    const alreadyAnalyzed = _cachedFiles.filter(f => f.is_valid && f.analyzed);
    const skippedFiles = _cachedFiles.filter(f => !f.is_valid);

    // Find already-analyzed files with different threshold
    const thresholdChangedFiles = alreadyAnalyzed.filter(f => hasThresholdChanged(f));

    // If no new files but threshold-changed files exist, ask to re-analyze
    if (validFiles.length === 0) {
        if (thresholdChangedFiles.length > 0) {
            const ok = await Modal.confirm({
                title: 'Порог изменился',
                description: `${thresholdChangedFiles.length} файлов проанализировано с другим порогом. Переанализировать их с текущим порогом (${globalThreshold.toFixed(2)})?`,
                confirmText: 'Переанализировать',
                cancelText: 'Отмена',
                tone: 'warning'
            });
            if (!ok) return;
            // Process threshold-changed files
            return batchAnalyze(thresholdChangedFiles.map(f => f.file_id), true);
        }
        if (alreadyAnalyzed.length > 0 || skippedFiles.length > 0) {
            let message = 'Нет новых файлов для анализа';
            if (alreadyAnalyzed.length > 0) message += `. Уже готово: ${alreadyAnalyzed.length}`;
            if (skippedFiles.length > 0) message += `. Пропущено невалидных: ${skippedFiles.length}`;
            Toast.info(message);
        } else {
            Toast.info('Нет файлов для анализа');
        }
        return;
    }

    // Combine new files and threshold-changed files
    let allFilesToAnalyze = [...validFiles];
    let reanalyzeCount = 0;

    if (thresholdChangedFiles.length > 0) {
        const ok = await Modal.confirm({
            title: 'Порог изменился',
            description: `${thresholdChangedFiles.length} уже проанализированных файлов имеют другой порог. Включить их в пакетный анализ?`,
            confirmText: 'Да, переанализировать',
            cancelText: 'Только новые',
            tone: 'warning'
        });
        if (ok) {
            allFilesToAnalyze = [...allFilesToAnalyze, ...thresholdChangedFiles];
            reanalyzeCount = thresholdChangedFiles.length;
        }
    }

    const toastId = Toast.show({
        type: 'info',
        title: 'Пакетный анализ',
        message: `Анализ файлов: 0/${allFilesToAnalyze.length}…`,
        duration: 0
    });

    const allFileIds = allFilesToAnalyze.map(f => f.file_id);
    let analyzed = 0;
    let errors = 0;

    for (let i = 0; i < allFileIds.length; i++) {
        const fileId = allFileIds[i];
        const fileData = _cachedFiles.find(f => f.file_id === fileId);
        const fname = fileData ? fileData.filename : `Файл ${i + 1}`;
        const isReanalyze = fileData && fileData.analyzed && hasThresholdChanged(fileData);
        Toast.update(toastId, { message: `[${i + 1}/${allFileIds.length}] ${isReanalyze ? 'Переанализ' : 'Анализ'}: ${fname}…` });

        try {
            const response = await fetch(apiUrl(`/analysis/run/${fileId}`), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ threshold: globalThreshold, include_xai: false, force_reanalyze: isReanalyze })
            });
            const data = await response.json();
            if (response.ok && data.success) {
                analyzed++;
            } else {
                errors++;
            }
        } catch (error) {
            errors++;
        }
    }

    Toast.dismiss(toastId);
    loadFileList();
    let message = `Проанализировано файлов: ${analyzed}`;
    if (alreadyAnalyzed.length > 0) message += `. Уже готово: ${alreadyAnalyzed.length}`;
    if (skippedFiles.length > 0) message += `. Пропущено невалидных: ${skippedFiles.length}`;
    if (errors > 0) message += `, ошибок: ${errors}`;
    if (errors > 0) Toast.warning(message);
    else Toast.success(message);
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

async function exportAllStats() {
    const btn = document.getElementById('exportStatsBtn');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner spinner--sm"></span> <span>Выбор пути…</span>`;

    // Step 1: open save dialog
    const savePath = await pickSavePath('ecg_stats_report.pdf');
    if (!savePath) {
        btn.innerHTML = original;
        btn.disabled = false;
        return;
    }

    // Step 2: generate, save to disk, and download
    btn.innerHTML = `<span class="spinner spinner--sm"></span> <span>Генерация…</span>`;

    try {
        const response = await fetch(apiUrl('/export/stats'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ save_path: savePath }),
        });
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'ecg_stats_report.pdf';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            Toast.success(`PDF со статистикой сохранён:\n${savePath}`);
        } else {
            const data = await response.json();
            Toast.info(data.error || 'Нет проанализированных файлов');
        }
    } catch (error) {
        Toast.error('Ошибка экспорта: ' + error.message);
    } finally {
        btn.innerHTML = original;
        btn.disabled = false;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

/* ── Folder management ─────────────────────────────────── */

function initFolderCard() {
    const card = document.getElementById('folderCard');
    if (!card) return;

    const iconEl = document.getElementById('folderCardIcon');
    if (iconEl) iconEl.innerHTML = Icons.folder(20);

    const configBtn = document.getElementById('folderConfigBtn');
    if (configBtn) {
        const icon = document.getElementById('folderConfigBtnIcon');
        if (icon) icon.innerHTML = Icons.settings(14);
        configBtn.addEventListener('click', openFolderConfigModal);
    }

    const clearBtn = document.getElementById('folderClearBtn');
    if (clearBtn) {
        const icon = document.getElementById('folderClearBtnIcon');
        if (icon) icon.innerHTML = Icons.trash(14);
        clearBtn.addEventListener('click', clearFolder);
    }

    // Modal bindings
    const cancelBtn = document.getElementById('folderConfigCancel');
    if (cancelBtn) cancelBtn.addEventListener('click', closeFolderConfigModal);
    const backdrop = document.getElementById('folderConfigBackdrop');
    if (backdrop) backdrop.addEventListener('click', closeFolderConfigModal);
    const saveBtn = document.getElementById('folderConfigSave');
    if (saveBtn) saveBtn.addEventListener('click', saveFolderConfig);
    const resetBtn = document.getElementById('folderConfigReset');
    if (resetBtn) resetBtn.addEventListener('click', resetFolderConfig);
    const browseBtn = document.getElementById('folderBrowseBtn');
    if (browseBtn) browseBtn.addEventListener('click', pickFolder);

    loadFolderConfig();
}

async function loadFolderConfig() {
    try {
        const resp = await fetch(apiUrl('/session/folder'));
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Ошибка загрузки');
        updateFolderCardUI(data);
    } catch (e) {
        const pathEl = document.getElementById('folderCardPath');
        if (pathEl) pathEl.textContent = 'Ошибка загрузки';
    }
}

function updateFolderCardUI(data) {
    const pathEl = document.getElementById('folderCardPath');
    const statsEl = document.getElementById('folderCardStats');
    if (pathEl) {
        pathEl.textContent = data.folder || '—';
        pathEl.title = data.folder || '';
    }
    if (statsEl) {
        const size = data.size_formatted || '0 B';
        const files = data.file_count || 0;
        const xmls = data.xml_count || 0;
        statsEl.textContent = `${size}, файлов: ${files} (XML: ${xmls})`;
    }
}

function openFolderConfigModal() {
    const modal = document.getElementById('folderConfigModal');
    if (!modal) return;
    const input = document.getElementById('folderInput');
    const currentPath = document.getElementById('folderCurrentPath');
    const hint = document.getElementById('folderHint');
    if (input) input.value = '';
    const pathEl = document.getElementById('folderCardPath');
    if (currentPath) currentPath.value = (pathEl && pathEl.textContent) || '—';
    if (hint) hint.textContent = '';
    modal.hidden = false;
}

function closeFolderConfigModal() {
    const modal = document.getElementById('folderConfigModal');
    if (modal) modal.hidden = true;
}

async function pickFolder() {
    const input = document.getElementById('folderInput');
    const hint = document.getElementById('folderHint');
    if (!input) return;
    hint.textContent = 'Открываем диалог выбора папки…';

    try {
        const resp = await fetch(apiUrl('/session/pick-folder'), { method: 'POST' });
        if (resp.status === 501) {
            const data = await resp.json().catch(() => ({}));
            hint.textContent = data.error || 'Введите путь вручную.';
            const value = prompt('Введите полный путь к папке:', input.value);
            if (value !== null) input.value = value;
            return;
        }
        const data = await resp.json();
        if (data.success) {
            if (data.folder) {
                input.value = data.folder;
                hint.textContent = `Выбрано: ${data.folder}. Нажмите «Сохранить».`;
            } else {
                hint.textContent = 'Диалог закрыт без выбора';
            }
        } else {
            hint.textContent = data.error || 'Не удалось открыть диалог';
        }
    } catch (e) {
        hint.textContent = 'Сетевая ошибка';
    }
}

async function saveFolderConfig() {
    const input = document.getElementById('folderInput');
    const hint = document.getElementById('folderHint');
    if (!input) return;
    const folder = input.value.trim() || null;
    hint.textContent = 'Сохранение…';

    try {
        const resp = await fetch(apiUrl('/session/folder'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder }),
        });
        const data = await resp.json();
        if (!data.success) {
            hint.textContent = data.error || 'Не удалось сохранить';
            return;
        }
        closeFolderConfigModal();
        updateFolderCardUI(data);
        Toast.success('Папка для временных файлов обновлена');
        loadFileList();
    } catch (e) {
        hint.textContent = 'Сетевая ошибка';
    }
}

async function resetFolderConfig() {
    const hint = document.getElementById('folderHint');
    if (hint) hint.textContent = 'Сброс…';
    try {
        const resp = await fetch(apiUrl('/session/folder'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder: null }),
        });
        const data = await resp.json();
        if (!data.success) {
            if (hint) hint.textContent = data.error || 'Не удалось сбросить';
            return;
        }
        closeFolderConfigModal();
        updateFolderCardUI(data);
        Toast.success('Папка сброшена к умолчанию');
        loadFileList();
    } catch (e) {
        if (hint) hint.textContent = 'Сетевая ошибка';
    }
}

async function clearFolder() {
    const ok = await Modal.confirm({
        title: 'Очистить папку временных файлов?',
        description: 'Все XML-файлы, кэш анализов и метаданные будут удалены. Это действие нельзя отменить.',
        confirmText: 'Очистить',
        cancelText: 'Отмена',
        tone: 'danger'
    });
    if (!ok) return;

    try {
        const resp = await fetch(apiUrl('/session/folder/clear'), { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            Toast.error(data.error || 'Не удалось очистить папку');
            return;
        }
        Toast.success(data.message || 'Папка очищена');
        loadFolderConfig();
        loadFileList();
    } catch (e) {
        Toast.error('Сетевая ошибка');
    }
}

function refreshFolderStats() {
    loadFolderConfig();
}


