/**
 * Audit log — shows application review decisions made by staff/admin
 */

let _allEntries = [];

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    const role = await getUserRole();
    if (!user || (role !== 'staff' && role !== 'admin')) {
        window.location.href = '/auth?portal=staff';
        return;
    }

    // Default date range: last 30 days
    const today        = new Date();
    const thirtyAgo    = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);
    document.getElementById('start-date').valueAsDate = thirtyAgo;
    document.getElementById('end-date').valueAsDate   = today;

    await loadAuditLog();

    document.getElementById('start-date').addEventListener('change', renderFiltered);
    document.getElementById('end-date').addEventListener('change', renderFiltered);
    document.getElementById('staff-filter').addEventListener('change', renderFiltered);
    document.getElementById('search-query').addEventListener('input', renderFiltered);
});

async function loadAuditLog() {
    const table      = document.getElementById('audit-table');
    const emptyState = document.getElementById('empty-state');
    table.innerHTML  = '<tr><td colspan="6" class="px-4 py-6 text-center text-gray-400 text-sm">Loading…</td></tr>';
    emptyState.classList.add('hidden');

    // Derive audit entries from reviewed applications
    const result = await apiCall('/api/applications');
    const apps   = result?.applications || [];

    // Build audit entries: one per reviewed application
    _allEntries = apps
        .filter(a => a.reviewed_at && (a.status === 'approved' || a.status === 'rejected' || a.status === 'returned'))
        .map(a => ({
            date:      a.reviewed_at,
            staff:     a.reviewer_id || 'TTB Staff',
            action:    a.status,           // approved / rejected / returned
            product:   a.product_name || a.brand_name || '—',
            app_id:    a.id,
            reason:    a.rejection_reason || a.reviewer_notes || '—',
        }))
        .sort((a, b) => new Date(b.date) - new Date(a.date));

    // Populate staff filter dropdown
    const staffIds   = [...new Set(_allEntries.map(e => e.staff))];
    const staffSel   = document.getElementById('staff-filter');
    staffSel.innerHTML = '<option value="">All Staff</option>' +
        staffIds.map(s => `<option value="${s}">${s}</option>`).join('');

    renderFiltered();
}

function renderFiltered() {
    const table      = document.getElementById('audit-table');
    const emptyState = document.getElementById('empty-state');

    const startDate  = document.getElementById('start-date').value;
    const endDate    = document.getElementById('end-date').value;
    const staffVal   = document.getElementById('staff-filter').value;
    const query      = (document.getElementById('search-query').value || '').toLowerCase();

    const filtered = _allEntries.filter(e => {
        const d = new Date(e.date);
        if (startDate && d < new Date(startDate)) return false;
        if (endDate   && d > new Date(endDate + 'T23:59:59')) return false;
        if (staffVal  && e.staff !== staffVal) return false;
        if (query && !e.product.toLowerCase().includes(query) && !(e.reason || '').toLowerCase().includes(query)) return false;
        return true;
    });

    if (!filtered.length) {
        table.innerHTML = '';
        emptyState.classList.remove('hidden');
        return;
    }
    emptyState.classList.add('hidden');

    const actionBadge = action => {
        const cfg = {
            approved: 'bg-green-50 text-green-800',
            rejected: 'bg-red-50 text-red-800',
            returned: 'bg-orange-50 text-orange-800',
        };
        return `<span class="px-2 py-0.5 rounded text-xs font-bold ${cfg[action]||'bg-gray-100 text-gray-700'}">${action.toUpperCase()}</span>`;
    };

    table.innerHTML = filtered.map(e => `
        <tr class="hover:bg-gray-50">
            <td class="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">
                ${new Date(e.date).toLocaleDateString()}<br>
                <span class="text-xs text-gray-400">${new Date(e.date).toLocaleTimeString()}</span>
            </td>
            <td class="px-4 py-3 text-sm text-gray-700">${escHtml(e.staff)}</td>
            <td class="px-4 py-3 text-sm">
                <p class="font-semibold text-gray-900">${escHtml(e.product)}</p>
                <a href="/industry/applications/${e.app_id}" class="text-xs text-secondary hover:underline">View application →</a>
            </td>
            <td class="px-4 py-3">Application Review</td>
            <td class="px-4 py-3">${actionBadge(e.action)}</td>
            <td class="px-4 py-3 text-sm text-gray-500 max-w-xs">
                <span title="${escHtml(e.reason)}">${truncate(e.reason, 60)}</span>
            </td>
        </tr>
    `).join('');
}

function truncate(str, n) {
    if (!str || str === '—') return '—';
    return str.length > n ? str.slice(0, n) + '…' : str;
}

function escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
