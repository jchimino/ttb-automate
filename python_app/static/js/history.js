/**
 * Verification history page
 */

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    if (!user) { window.location.href = '/auth'; return; }
    await loadHistory();
});

async function loadHistory() {
    const loading   = document.getElementById('loading');
    const listEl    = document.getElementById('history-list');
    const emptyEl   = document.getElementById('empty-state');
    const clearBtn  = document.getElementById('clear-btn');

    const result = await apiCall('/api/verification-history');
    const records = result?.history || [];

    loading.classList.add('hidden');

    if (!records.length) {
        emptyEl.classList.remove('hidden');
        return;
    }

    clearBtn.classList.remove('hidden');
    listEl.classList.remove('hidden');

    listEl.innerHTML = records.map((r, i) => {
        const status = (r.overall_status || r.overallStatus || '').toUpperCase();
        const checks = r.checks || [];
        const passed = checks.filter(c => (c.status || '').toUpperCase() === 'PASS').length;
        const date   = r.created_at ? new Date(r.created_at).toLocaleString() : 'Unknown date';

        return `
        <div class="bg-white border border-gray-200 p-4">
            <div class="flex items-start justify-between gap-4">
                ${r.image_thumbnail ? `<img src="${r.image_thumbnail}" alt="Label" class="w-16 h-16 object-cover border border-gray-200 flex-shrink-0 rounded">` : '<div class="w-16 h-16 bg-gray-100 flex-shrink-0 rounded flex items-center justify-center text-gray-400 text-xs">No img</div>'}
                <div class="flex-1">
                    <div class="flex items-center justify-between gap-2 mb-1">
                        <span class="font-bold text-sm ${status === 'PASS' ? 'text-green-800' : 'text-red-800'}">
                            ${status === 'PASS' ? '✓ Compliant' : '✕ Non-Compliant'}
                        </span>
                        <span class="text-xs text-gray-400">${date}</span>
                    </div>
                    ${r.commodity_type ? `<p class="text-xs text-gray-500">${r.commodity_type}</p>` : ''}
                    <p class="text-xs text-gray-500">${passed}/${checks.length} checks passed</p>
                    ${r.product_details ? `<p class="text-xs text-gray-400 mt-1 truncate">${r.product_details}</p>` : ''}
                </div>
                <button onclick="deleteRecord('${r.id}')" title="Delete" class="text-gray-300 hover:text-red-500 text-lg flex-shrink-0">&times;</button>
            </div>
        </div>`;
    }).join('');
}

async function deleteRecord(id) {
    await apiCall(`/api/verification-history/${id}`, 'DELETE');
    await loadHistory();
}

async function clearAllHistory() {
    if (!confirm('Delete all verification history? This cannot be undone.')) return;
    await apiCall('/api/verification-history', 'DELETE');
    await loadHistory();
}
