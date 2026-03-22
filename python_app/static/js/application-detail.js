/**
 * Application detail page – industry view with submit support
 * Label preview + verify link are injected by JS (no template changes needed)
 */

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    if (!user) { window.location.href = '/auth?portal=industry'; return; }
    await loadApplication();
});

async function loadApplication() {
    const appId   = window.APP_ID;
    const loading = document.getElementById('loading');
    const content = document.getElementById('content');

    const result = await apiCall(`/api/applications/${appId}`);
    if (!result) {
        loading.innerHTML = '<p class="text-red-600 text-sm">Application not found or you do not have access.</p>';
        return;
    }

    // Title / breadcrumb
    document.getElementById('app-title').textContent  = result.product_name || result.brand_name || 'Application';
    document.getElementById('app-name').textContent   = result.product_name || result.brand_name || '—';
    document.getElementById('app-id-display').textContent = `Application ID: ${appId.slice(0, 8).toUpperCase()}`;

    // Fields
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || '—'; };
    set('product-name',    result.product_name);
    set('brand-name',      result.brand_name);
    set('product-type',    result.product_type);
    set('alcohol-content', result.alcohol_content);
    set('net-contents',    result.net_contents);
    set('created-at',      result.created_at ? new Date(result.created_at).toLocaleDateString() : '—');

    // Status badge
    const statusEl = document.getElementById('app-status');
    const statusCfg = {
        approved:       { text: '✓ Approved',       cls: 'bg-green-100 text-green-900' },
        rejected:       { text: '✕ Rejected',       cls: 'bg-red-100 text-red-900' },
        pending_review: { text: '⏳ Pending Review', cls: 'bg-yellow-100 text-yellow-900' },
        returned:       { text: '↩ Returned',       cls: 'bg-orange-100 text-orange-900' },
        draft:          { text: '✏ Draft',          cls: 'bg-gray-100 text-gray-700' },
    };
    const cfg = statusCfg[result.status] || { text: result.status, cls: 'bg-gray-100 text-gray-700' };
    statusEl.textContent = cfg.text;
    statusEl.className   = `px-4 py-1.5 rounded font-bold text-sm ${cfg.cls}`;

    // Submit button only for drafts
    if (result.status === 'draft') {
        document.getElementById('submit-btn').classList.remove('hidden');
    }

    // Rejection reason
    if (result.rejection_reason) {
        document.getElementById('rejection-banner').classList.remove('hidden');
        document.getElementById('rejection-text').textContent = result.rejection_reason;
    }

    // AI verification results
    if (result.ai_verification_result) {
        document.getElementById('verification-section').classList.remove('hidden');
        displayVerificationResults(result.ai_verification_result);
    }

    // ── Label preview: inject dynamically into the sidebar ──
    if (result.label_url) {
        _injectLabelPreview(result);
    }

    // ── Update verify link with label params ──
    _updateVerifyLink(result);

    // Status timeline
    buildTimeline(result);

    loading.classList.add('hidden');
    content.classList.remove('hidden');
}

/* Show label image in the pre-built sidebar card */
function _injectLabelPreview(result) {
    var card = document.getElementById('label-preview-card');
    var img  = document.getElementById('label-img');
    var link = document.getElementById('label-img-link');
    if (!card || !img) return;

    img.src = result.label_url;
    img.alt = (result.product_name || 'Label') + ' preview';
    if (link) link.href = result.label_url;
    card.classList.remove('hidden');
}

/* Update the "Run Label Verification" link to carry label_url + details */
function _updateVerifyLink(result) {
    // Find the link by its text content
    var links = document.querySelectorAll('a[href="/verify"]');
    links.forEach(function(link) {
        if (!link.textContent.includes('Label Verification') && !link.textContent.includes('Verify')) return;
        if (result.label_url) {
            var params = new URLSearchParams({
                label_url: result.label_url,
                app_name:  result.product_name || result.brand_name || '',
                details:   [result.brand_name, result.product_type, result.alcohol_content, result.net_contents].filter(Boolean).join(', '),
            });
            link.href = '/verify?' + params.toString();
        }
    });
}

function buildTimeline(app) {
    const el   = document.getElementById('status-timeline');
    const events = [];
    if (app.created_at)   events.push({ label: 'Application Created',   date: app.created_at,   icon: '✏',  color: '#6b7280' });
    if (app.submitted_at) events.push({ label: 'Submitted for Review',  date: app.submitted_at, icon: '📤', color: '#1d4ed8' });
    if (app.reviewed_at)  events.push({
        label: app.status === 'approved' ? 'Approved by TTB' : app.status === 'rejected' ? 'Rejected by TTB' : 'Returned for Revision',
        date: app.reviewed_at,
        icon: app.status === 'approved' ? '✓' : app.status === 'rejected' ? '✕' : '↩',
        color: app.status === 'approved' ? '#15803d' : app.status === 'rejected' ? '#b91c1c' : '#b45309',
    });

    if (!events.length) {
        el.innerHTML = '<p class="text-gray-400 text-xs">No activity yet.</p>';
        return;
    }
    el.innerHTML = events.map(ev => `
        <div style="display:flex;gap:0.75rem;align-items:flex-start;">
            <div style="width:1.5rem;height:1.5rem;border-radius:50%;background:${ev.color};display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:0.65rem;color:#fff;margin-top:0.1rem;">${ev.icon}</div>
            <div>
                <p style="font-weight:600;font-size:0.8rem;color:#111827;margin:0;">${ev.label}</p>
                <p style="font-size:0.7rem;color:#9ca3af;margin:0;">${new Date(ev.date).toLocaleString()}</p>
            </div>
        </div>
    `).join('');
}

function displayVerificationResults(data) {
    const el     = document.getElementById('results-content');
    const checks = data.checks || [];
    const overall = (data.overallStatus || data.overall_status || '').toUpperCase();

    el.innerHTML = `
        <div class="p-3 rounded mb-3 ${overall === 'PASS' ? 'bg-green-50 border-l-4 border-green-500' : 'bg-red-50 border-l-4 border-red-500'}">
            <p class="font-bold text-sm ${overall === 'PASS' ? 'text-green-900' : 'text-red-900'}">
                ${overall === 'PASS' ? '✓ Compliant' : '✕ Non-Compliant'}
            </p>
        </div>
        <div class="space-y-2">
            ${checks.map(c => `
                <div class="p-2.5 border rounded text-sm ${c.status === 'PASS' || c.status === 'pass' ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'}">
                    <p class="font-semibold">${c.field || c.name || '—'}</p>
                    ${c.reason || c.details ? `<p class="text-xs text-gray-600 mt-0.5">${c.reason || c.details}</p>` : ''}
                </div>
            `).join('')}
        </div>`;
}

async function submitApplication() {
    const appId = window.APP_ID;
    const btn   = document.getElementById('submit-btn');
    btn.disabled    = true;
    btn.textContent = 'Submitting…';

    const result = await apiCall(`/api/applications/${appId}/submit`, 'PUT', {});
    if (!result) {
        btn.disabled    = false;
        btn.textContent = 'Submit for Review';
        showToast('Error', 'Failed to submit application.', 'error');
        return;
    }

    showToast('Submitted!', 'Your application is now pending TTB review.', 'success');
    const statusEl = document.getElementById('app-status');
    statusEl.textContent = '⏳ Pending Review';
    statusEl.className   = 'px-4 py-1.5 rounded font-bold text-sm bg-yellow-100 text-yellow-900';
    btn.classList.add('hidden');
}
