/**
 * Toast notification system.
 * Replaces alert(), showError() and showInfo() with a unified, stacked, dismissible UI.
 *
 * Usage:
 *   Toast.success('Файл загружен');
 *   Toast.error('Ошибка соединения');
 *   Toast.info('Анализ запущен', { duration: 2000 });
 *   Toast.show({ type: 'warning', message: '...', action: { label: 'Отменить', onClick: fn } });
 */
(function (global) {
    'use strict';

    const MAX_TOASTS = 4;
    const DEFAULT_DURATION = 4000;
    const ERROR_DURATION = 6000;

    const ICON_MAP = {
        success: 'checkCircle',
        error: 'alertCircle',
        warning: 'alert',
        info: 'info',
    };

    let container = null;
    let counter = 0;
    const registry = new Map();

    function ensureContainer() {
        if (container) return container;
        container = document.createElement('div');
        container.className = 'toast-container';
        container.setAttribute('role', 'region');
        container.setAttribute('aria-label', 'Уведомления');
        document.body.appendChild(container);
        return container;
    }

    function dismiss(toast) {
        toast.classList.remove('toast--visible');
        toast.classList.add('toast--leaving');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 220);
    }

    function createToast(options) {
        const {
            type = 'info',
            message = '',
            description = null,
            duration,
            action = null,
        } = options;

        const root = ensureContainer();

        if (root.children.length >= MAX_TOASTS) {
            root.removeChild(root.firstElementChild);
        }

        const el = document.createElement('div');
        el.className = `toast toast--${type}`;
        el.setAttribute('role', type === 'error' ? 'alert' : 'status');
        const id = ++counter;
        el.dataset.toastId = id;

        const iconName = ICON_MAP[type] || 'info';
        const iconHtml = global.Icons && global.Icons[iconName]
            ? global.Icons[iconName]()
            : '';

        const messageEl = document.createElement('div');
        messageEl.className = 'toast__message';
        messageEl.textContent = message;
        if (description) {
            const descEl = document.createElement('div');
            descEl.className = 'toast__description';
            descEl.textContent = description;
            messageEl.appendChild(descEl);
        }

        const closeBtn = document.createElement('button');
        closeBtn.className = 'toast__close';
        closeBtn.setAttribute('aria-label', 'Закрыть');
        closeBtn.innerHTML = global.Icons ? global.Icons.x() : '×';
        closeBtn.onclick = () => dismiss(el);

        const content = document.createElement('div');
        content.className = 'toast__content';
        if (iconHtml) {
            const iconWrap = document.createElement('div');
            iconWrap.className = 'toast__icon';
            iconWrap.innerHTML = iconHtml;
            content.appendChild(iconWrap);
        }
        content.appendChild(messageEl);

        if (action && action.label) {
            const actionBtn = document.createElement('button');
            actionBtn.className = 'toast__action';
            actionBtn.textContent = action.label;
            actionBtn.onclick = () => {
                if (typeof action.onClick === 'function') {
                    try { action.onClick(); } catch (e) { /* noop */ }
                }
                dismiss(el);
            };
            content.appendChild(actionBtn);
        }

        el.appendChild(content);
        el.appendChild(closeBtn);

        const finalDuration = duration !== undefined
            ? duration
            : (type === 'error' || type === 'warning' ? ERROR_DURATION : DEFAULT_DURATION);

        if (finalDuration > 0) {
            const progress = document.createElement('div');
            progress.className = 'toast__progress';
            el.appendChild(progress);
            requestAnimationFrame(() => {
                progress.style.transitionDuration = `${finalDuration}ms`;
                progress.style.transform = 'scaleX(0)';
            });
            el.dataset.timeout = setTimeout(() => dismiss(el), finalDuration);
        }

        root.appendChild(el);
        requestAnimationFrame(() => el.classList.add('toast--visible'));

        const handle = {
            id,
            element: el,
            messageEl,
            dismiss: () => {
                if (el.dataset.timeout) clearTimeout(parseInt(el.dataset.timeout, 10));
                dismiss(el);
                registry.delete(id);
            },
        };
        registry.set(id, handle);
        return id;
    }

    const Toast = {
        show: createToast,
        success: (message, options = {}) => createToast({ ...options, type: 'success', message }),
        error: (message, options = {}) => createToast({ ...options, type: 'error', message }),
        warning: (message, options = {}) => createToast({ ...options, type: 'warning', message }),
        info: (message, options = {}) => createToast({ ...options, type: 'info', message }),
        update(id, patch = {}) {
            const entry = registry.get(id);
            if (!entry) return;
            if (patch.message !== undefined) {
                const msgNode = entry.messageEl.firstChild;
                if (msgNode) msgNode.textContent = patch.message;
                else entry.messageEl.textContent = patch.message;
            }
            if (patch.description !== undefined) {
                let descEl = entry.messageEl.querySelector('.toast__description');
                if (patch.description === null || patch.description === '') {
                    if (descEl) descEl.remove();
                } else if (!descEl) {
                    descEl = document.createElement('div');
                    descEl.className = 'toast__description';
                    descEl.textContent = patch.description;
                    entry.messageEl.appendChild(descEl);
                } else {
                    descEl.textContent = patch.description;
                }
            }
            if (patch.type) {
                entry.element.classList.remove('toast--success', 'toast--error', 'toast--warning', 'toast--info');
                entry.element.classList.add(`toast--${patch.type}`);
            }
        },
        dismiss(id) {
            const entry = registry.get(id);
            if (!entry) return;
            if (entry.element.dataset.timeout) {
                clearTimeout(parseInt(entry.element.dataset.timeout, 10));
            }
            dismiss(entry.element);
            registry.delete(id);
        },
        dismissAll() {
            if (!container) return;
            Array.from(container.children).forEach(el => {
                const id = parseInt(el.dataset.toastId, 10);
                if (el.dataset.timeout) clearTimeout(parseInt(el.dataset.timeout, 10));
                dismiss(el);
                if (!Number.isNaN(id)) registry.delete(id);
            });
        },
    };

    global.Toast = Toast;
})(window);
