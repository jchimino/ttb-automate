/* ─── Toast notifications ─────────────────────────────────────────── */
function showToast(title, description = '', type = 'info', duration = 4500) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `
        <div class="toast-body">
            ${title ? `<div class="toast-title">${title}</div>` : ''}
            ${description ? `<div class="toast-desc">${description}</div>` : ''}
        </div>
        <button class="toast-x" aria-label="Dismiss">&times;</button>`;
    t.querySelector('.toast-x').addEventListener('click', () => dismissToast(t));
    container.appendChild(t);
    if (duration > 0) setTimeout(() => dismissToast(t), duration);
}
function dismissToast(el) {
    if (!el || el.classList.contains('toast-out')) return;
    el.classList.add('toast-out');
    setTimeout(() => el.remove(), 260);
}

/* ─── Auth helpers & route guard are provided by auth.js (loaded after this file) ─── */
/* config.js intentionally does NOT define auth functions or route guards to avoid    */
/* conflicts with auth.js, which has full demo-mode support.                          */
