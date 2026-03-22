/**
 * Staff dashboard — review queue with approve/reject/return modal
 */

let _allApps = [];
let _currentAppId = null;
let currentDecision = null;

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    const role = await getUserRole();
    if (!user || (role !== 'staff' && role !== 'admin')) {
        window.location.href = '/auth?portal=staff';
        return;
    }
    await loadApps();
});

async function loadApps() {
    const body = document.getElementById('app-table-body');
    body.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-gray-400">Loading…</td></tr>';

    const result = await apiCall('/api/applications');
    _allApps = result?.applications || [];

    updateStats();
    filterApps();
}

function updateStats() {
    const el = id => document.getElementById(id);
    el('pending-count').textContent  = _allApps.filter(a => a.status === 'pending_review').length;
    el('approved-count').textContent = _allApps.filter(a => a.status === 'approved').length;
    el('rejected-count').textContent = _allApps.filter(a => a.status === 'rejected').length;
}

function filterApps() {
    const status = document.getElementById('status-filter').value;
    const q      = (document.getElementById('search-input').value || '').toLowerCase();

    const filtered = _allApps.filter(a => {
        const matchStatus = !status || a.status === status;
        const name = ((a.product_name || '') + ' ' + (a.brand_name || '')).toLowerCase();
        const matchSearch = !q || name.includes(q);
        return matchStatus && matchSearch;
    });

    renderTable(filtered);
}

function renderTable(apps) {
    const body    = document.getElementById('app-table-body');
    const empty   = document.getElementById('empty-state');

    if (!apps.length) {
        body.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const statusBadge = s => {
        const cfg = {
            approved:       'bg-green-50 text-green-800',
            rejected:       'bg-red-50 text-red-800',
            pending_review: 'bg-yellow-50 text-yellow-800',
            returned:       'bg-orange-50 text-orange-800',
            draft:          'bg-gray-100 text-gray-600',
        };
        return `<span class="px-2 py-0.5 rounded text-xs font-bold ${cfg[s]||'bg-gray-100 text-gray-600'}">${s.replace(/_/g,' ').toUpperCase()}</span>`;
    };

    body.innerHTML = apps.map(app => `
        <tr class="border-b border-gray-100 hover:bg-gray-50">
            <td class="px-4 py-3">
                <div style="display:flex;align-items:center;gap:0.75rem;">
                    ${app.label_url
                        ? '<img src="' + app.label_url + '" alt="Label" style="width:40px;height:50px;object-fit:cover;border:1px solid #e5e7eb;border-radius:2px;flex-shrink:0;">'
                        : '<div style="width:40px;height:50px;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:2px;flex-shrink:0;"></div>'}
                    <div>
                        <p class="font-semibold text-gray-900 text-sm">${app.product_name || app.brand_name || '—'}</p>
                        <p class="text-xs text-gray-400">${app.brand_name || ''}</p>
                    </div>
                </div>
            </td>
            <td class="px-4 py-3 text-sm text-gray-600">
                <p>${app.product_type || '—'}</p>
                <p class="text-xs text-gray-400">${app.alcohol_content || ''}</p>
            </td>
            <td class="px-4 py-3 text-sm text-gray-500">
                ${app.submitted_at ? new Date(app.submitted_at).toLocaleDateString() : '—'}
            </td>
            <td class="px-4 py-3">${statusBadge(app.status)}</td>
            <td class="px-4 py-3">
                <div class="flex gap-1.5">
                    <a href="/industry/applications/${app.id}" class="text-xs text-secondary hover:underline px-2 py-1 border border-secondary">View</a>
                    ${app.status === 'pending_review' ? `
                    <button onclick="openReviewModal('${app.id}')"
                        class="text-xs text-white bg-primary hover:bg-blue-900 px-2 py-1 border border-primary font-semibold">
                        Review
                    </button>` : ''}
                </div>
            </td>
        </tr>
    `).join('');
}

/* ── Review Modal ───────────────────────────────────────────────── */
function openReviewModal(appId) {
    _currentAppId = appId;
    currentDecision = null;

    const app = _allApps.find(a => a.id === appId);
    document.getElementById('modal-title').textContent = `Review: ${app?.product_name || app?.brand_name || appId}`;

    const labelThumb = app?.label_url
        ? `<a href="${app.label_url}" target="_blank" title="Open full size" style="display:block;margin-top:0.75rem;">
               <img src="${app.label_url}" alt="Label" style="max-height:180px;max-width:100%;object-fit:contain;border:1px solid #e5e7eb;cursor:zoom-in;">
           </a>`
        : '';

    document.getElementById('modal-app-info').innerHTML = `
        <strong>${app?.product_name || '—'}</strong> &bull; ${app?.brand_name || ''}<br>
        <span style="color:#6b7280">${app?.product_type || ''} &bull; ${app?.alcohol_content || ''}</span>
        ${labelThumb}
    `;
    document.getElementById('review-notes').value    = '';
    document.getElementById('reviewer-notes').value  = '';
    document.getElementById('reason-block').style.display = 'none';
    document.getElementById('modal-error').style.display  = 'none';
    ['approve','return','reject'].forEach(d => resetDecisionBtn(d));

    const modal = document.getElementById('review-modal');
    modal.style.display = 'flex';
}

function closeReviewModal() {
    document.getElementById('review-modal').style.display = 'none';
    _currentAppId = null;
    currentDecision = null;
}

function setDecision(decision) {
    currentDecision = decision;
    ['approve','return','reject'].forEach(d => resetDecisionBtn(d));

    const btn = document.getElementById('btn-' + decision);
    const colors = { approve: '#16a34a', return: '#d97706', reject: '#dc2626' };
    btn.style.borderColor = colors[decision];
    btn.style.background  = decision === 'approve' ? '#f0fdf4' : decision === 'return' ? '#fffbeb' : '#fef2f2';
    btn.style.color       = colors[decision];

    // Show reason box for reject/return
    document.getElementById('reason-block').style.display =
        (decision === 'reject' || decision === 'return') ? 'block' : 'none';
}

function resetDecisionBtn(d) {
    const btn = document.getElementById('btn-' + d);
    btn.style.borderColor = '#d1d5db';
    btn.style.background  = '#fff';
    btn.style.color       = '#374151';
}

async function submitReview() {
    if (!currentDecision) {
        showModalError('Please select a decision (Approve, Return, or Reject).');
        return;
    }
    const notes       = document.getElementById('reviewer-notes').value.trim();
    const reason      = document.getElementById('review-notes').value.trim();
    if ((currentDecision === 'reject' || currentDecision === 'return') && !reason) {
        showModalError('Please provide a reason for rejection or return.');
        return;
    }

    const submitBtn = document.getElementById('submit-review-btn');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting…';
    document.getElementById('modal-error').style.display = 'none';

    const result = await apiCall(`/api/applications/${_currentAppId}/review`, 'PUT', {
        action: currentDecision,
        notes: notes || null,
        rejection_reason: reason || null,
    });

    submitBtn.disabled = false;
    submitBtn.textContent = 'Submit Decision';

    if (!result) { showModalError('Failed to submit decision. Please try again.'); return; }

    // Update in-memory list
    const idx = _allApps.findIndex(a => a.id === _currentAppId);
    if (idx !== -1) _allApps[idx] = { ..._allApps[idx], ...result };

    closeReviewModal();
    updateStats();
    filterApps();
    showToast('Decision recorded', `Application ${currentDecision}d successfully.`, 'success');
}

function showModalError(msg) {
    const el = document.getElementById('modal-error');
    el.textContent = msg;
    el.style.display = 'block';
}
