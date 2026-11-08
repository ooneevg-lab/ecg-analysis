/**
 * Modal confirm dialog (replaces window.confirm for destructive actions).
 *
 * Usage:
 *   const ok = await Modal.confirm({
 *       title: 'Удалить файл?',
 *       description: 'Действие нельзя отменить.',
 *       confirmLabel: 'Удалить',
 *       confirmTone: 'danger',
 *   });
 *   if (ok) { ... }
 */
(function (global) {
    'use strict';

    let activeOverlay = null;

    function createOverlay() {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');

        const card = document.createElement('div');
        card.className = 'modal-card';
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        return { overlay, card };
    }

    function close(resolve) {
        if (!activeOverlay) {
            resolve(false);
            return;
        }
        const { overlay } = activeOverlay;
        overlay.classList.remove('modal-overlay--visible');
        activeOverlay = null;
        setTimeout(() => {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            document.removeEventListener('keydown', onKey);
        }, 180);
        resolve(false);
    }

    function onKey(e) {
        if (e.key === 'Escape') {
            if (activeOverlay) {
                const { resolve } = activeOverlay;
                close(resolve);
            }
        } else if (e.key === 'Enter') {
            if (activeOverlay) {
                const { resolve } = activeOverlay;
                const { overlay } = activeOverlay;
                overlay.classList.remove('modal-overlay--visible');
                activeOverlay = null;
                setTimeout(() => {
                    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                    document.removeEventListener('keydown', onKey);
                }, 180);
                resolve(true);
            }
        }
    }

    function confirm(options = {}) {
        const {
            title = 'Подтверждение',
            description = '',
            confirmLabel = 'Подтвердить',
            cancelLabel = 'Отмена',
            confirmTone = 'primary',
        } = options;

        return new Promise(resolve => {
            if (activeOverlay) {
                resolve(false);
                return;
            }

            const { overlay, card } = createOverlay();

            const iconWrap = document.createElement('div');
            iconWrap.className = `modal-icon modal-icon--${confirmTone}`;
            const iconName = confirmTone === 'danger' ? 'alert' : 'info';
            if (global.Icons && global.Icons[iconName]) {
                iconWrap.innerHTML = global.Icons[iconName]();
            }
            card.appendChild(iconWrap);

            const titleEl = document.createElement('h3');
            titleEl.className = 'modal-title';
            titleEl.textContent = title;
            card.appendChild(titleEl);

            if (description) {
                const descEl = document.createElement('p');
                descEl.className = 'modal-description';
                descEl.textContent = description;
                card.appendChild(descEl);
            }

            const actions = document.createElement('div');
            actions.className = 'modal-actions';

            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'btn btn-secondary';
            cancelBtn.textContent = cancelLabel;
            cancelBtn.onclick = () => close(resolve);

            const confirmBtn = document.createElement('button');
            confirmBtn.className = `btn btn-${confirmTone === 'danger' ? 'danger' : 'primary'}`;
            confirmBtn.textContent = confirmLabel;
            confirmBtn.onclick = () => {
                overlay.classList.remove('modal-overlay--visible');
                activeOverlay = null;
                setTimeout(() => {
                    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                    document.removeEventListener('keydown', onKey);
                }, 180);
                resolve(true);
            };

            actions.appendChild(cancelBtn);
            actions.appendChild(confirmBtn);
            card.appendChild(actions);

            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) close(resolve);
            });

            activeOverlay = { overlay, resolve };
            document.addEventListener('keydown', onKey);
            requestAnimationFrame(() => overlay.classList.add('modal-overlay--visible'));
            setTimeout(() => confirmBtn.focus(), 50);
        });
    }

    global.Modal = { confirm };
})(window);
