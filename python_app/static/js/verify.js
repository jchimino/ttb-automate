/* ─── verify.js – single + batch label verification ─────────────── */

/* Fix subtitle: remove any "Claude" / "Anthropic" references in the page header */
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('p').forEach(p => {
        if (p.textContent.includes('using Claude')) {
            p.textContent = p.textContent.replace(/\s*using Claude\s*/i, '');
        }
    });
});

let currentMode = 'single';

/* Single */
let singleFile   = null;
let singleB64    = null;

/* Batch */
let batchFiles   = [];   // { file, b64, name, status, result }

/* ─── Mode toggle ───────────────────────────────────────────────── */
function setMode(mode) {
    currentMode = mode;
    ['single','batch'].forEach(m => {
        document.getElementById('panel-' + m).classList.toggle('hidden', m !== mode);
        const btn = document.getElementById('mode-' + m);
        if (m === mode) {
            btn.classList.replace('border-transparent', 'border-secondary');
            btn.classList.replace('text-gray-500',      'text-secondary');
        } else {
            btn.classList.replace('border-secondary',   'border-transparent');
            btn.classList.replace('text-secondary',     'text-gray-500');
        }
    });
}

/* ─── Drag & drop dispatcher ────────────────────────────────────── */
function handleDrop(e, target) {
    e.preventDefault();
    const zone = document.getElementById('drop-' + target);
    zone.classList.remove('border-secondary','bg-blue-50');
    const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/'));
    if (!files.length) { showToast('No images', 'Please drop image files (JPG, PNG, WEBP).', 'warning'); return; }
    if (target === 'single') { loadSingleFile(files[0]); }
    else                     { loadBatchFiles(files);    }
}

/* ─── Single label ──────────────────────────────────────────────── */
function loadSingleFile(file) {
    if (!file) return;
    singleFile = file;
    const reader = new FileReader();
    reader.onload = e => {
        singleB64 = e.target.result;
        document.getElementById('img-single').src    = singleB64;
        document.getElementById('fname-single').textContent = file.name + ' (' + fmtSize(file.size) + ')';
        document.getElementById('preview-single').classList.remove('hidden');
        document.getElementById('drop-single').classList.add('hidden');
        document.getElementById('btn-single').disabled = false;
    };
    reader.readAsDataURL(file);
}

function clearSingle() {
    singleFile = singleB64 = null;
    document.getElementById('img-single').src = '';
    document.getElementById('preview-single').classList.add('hidden');
    document.getElementById('drop-single').classList.remove('hidden');
    document.getElementById('btn-single').disabled = true;
    document.getElementById('input-single').value = '';
}

async function verifySingle() {
    if (!singleB64) { showToast('No image', 'Please select a label image first.', 'warning'); return; }
    const btn = document.getElementById('btn-single');
    btn.disabled = true;
    btn.textContent = 'Verifying with AI…';
    showToast('Running compliance check', 'Executing compliance analysis…', 'info', 0);

    try {
        const token = await getAuthToken();
        if (!token) { window.location.href = '/auth?next=/verify'; return; }

        const res = await fetch('/api/verify-label', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image_base64:    singleB64,
                product_details: document.getElementById('details-single').value || null,
            }),
        });

        // Clear the "sending" toast
        document.querySelectorAll('.toast-info').forEach(t => dismissToast(t));

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Server error' }));
            throw new Error(err.detail || 'Verification failed (' + res.status + ')');
        }
        const data = await res.json();
        renderSingleResult(data);
        showToast('Done', 'Compliance check complete.', data.overall_status === 'PASS' ? 'success' : 'warning');

        // Save to history (fire-and-forget — don't block UI on failure)
        saveVerificationToHistory(data, singleB64, document.getElementById('details-single').value);
    } catch (err) {
        document.querySelectorAll('.toast-info').forEach(t => dismissToast(t));
        showToast('Verification error', err.message, 'error');
        document.getElementById('results-single').innerHTML = `
            <div class="border border-red-200 rounded p-4 bg-red-50 text-sm text-red-800">
                <strong>Error:</strong> ${escHtml(err.message)}
            </div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Verify Compliance';
    }
}

function renderSingleResult(r) {
    const pass = r.overall_status === 'PASS';
    const color = pass ? { bg:'bg-green-50', border:'border-green-400', text:'text-green-900', badge:'bg-green-100 text-green-800' }
                       : { bg:'bg-red-50',   border:'border-red-400',   text:'text-red-900',   badge:'bg-red-100 text-red-800'   };
    let html = `
    <div class="space-y-4">
        <div class="rounded border-l-4 ${color.border} ${color.bg} p-4">
            <div class="flex items-center justify-between">
                <div class="font-bold text-lg ${color.text}">
                    ${pass ? '✓ Compliant' : '✗ Issues Found'}
                </div>
                <div class="text-2xl font-bold ${color.text}">${r.compliance_score}<span class="text-sm font-normal">/100</span></div>
            </div>
            <div class="text-sm ${color.text} mt-1">
                Commodity: <strong>${escHtml(r.commodity_type)}</strong>
            </div>
        </div>`;

    if (r.critical_failures?.length) {
        html += `<div class="rounded border-l-4 border-red-500 bg-red-50 p-3 text-sm">
            <p class="font-bold text-red-900 mb-1">Critical Failures</p>
            <ul class="list-disc list-inside text-red-800 space-y-0.5">
                ${r.critical_failures.map(f => `<li>${escHtml(f)}</li>`).join('')}
            </ul></div>`;
    }
    if (r.warnings?.length) {
        html += `<div class="rounded border-l-4 border-yellow-400 bg-yellow-50 p-3 text-sm">
            <p class="font-bold text-yellow-900 mb-1">Warnings</p>
            <ul class="list-disc list-inside text-yellow-800 space-y-0.5">
                ${r.warnings.map(w => `<li>${escHtml(w)}</li>`).join('')}
            </ul></div>`;
    }

    html += `<div class="space-y-2">
        <p class="text-sm font-semibold text-gray-700">Compliance Checks (${r.checks.length})</p>`;
    r.checks.forEach(c => {
        const ok = c.status === 'PASS';
        html += `<div class="rounded border ${ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'} p-3 text-sm">
            <div class="flex gap-2">
                <span class="font-bold text-base ${ok ? 'text-green-700' : 'text-red-700'}">${ok ? '✓' : '✗'}</span>
                <div class="flex-1">
                    <p class="font-semibold ${ok ? 'text-green-900' : 'text-red-900'}">${escHtml(c.field)}</p>
                    ${c.reason     ? `<p class="text-gray-600 mt-0.5">${escHtml(c.reason)}</p>` : ''}
                    ${c.label_value ? `<p class="mt-0.5"><span class="font-medium">Found:</span> ${escHtml(c.label_value)}</p>` : ''}
                    ${c.text_value  ? `<p class="mt-0.5"><span class="font-medium">Expected:</span> ${escHtml(c.text_value)}</p>` : ''}
                </div>
            </div></div>`;
    });
    html += '</div></div>';
    document.getElementById('results-single').innerHTML = html;
}

/* ─── Batch labels ──────────────────────────────────────────────── */
function loadBatchFiles(fileList) {
    const newFiles = [...fileList].filter(f => f.type.startsWith('image/'));
    if (!newFiles.length) { showToast('No images', 'Please select image files.', 'warning'); return; }

    const remaining = 20 - batchFiles.length;
    if (remaining <= 0) { showToast('Limit reached', 'Maximum 20 images per batch.', 'warning'); return; }
    const toAdd = newFiles.slice(0, remaining);
    if (newFiles.length > remaining) showToast('Truncated', `Only the first ${remaining} images were added (20 max).`, 'warning');

    toAdd.forEach(file => {
        const reader = new FileReader();
        reader.onload = e => {
            batchFiles.push({ file, b64: e.target.result, name: file.name, status: 'pending', result: null });
            renderBatchQueue();
        };
        reader.readAsDataURL(file);
    });
}

function renderBatchQueue() {
    if (!batchFiles.length) {
        document.getElementById('batch-queue').classList.add('hidden');
        return;
    }
    document.getElementById('batch-queue').classList.remove('hidden');
    document.getElementById('batch-count-label').textContent = batchFiles.length + ' image' + (batchFiles.length !== 1 ? 's' : '') + ' queued';

    const grid = document.getElementById('batch-grid');
    grid.innerHTML = '';
    batchFiles.forEach((item, i) => {
        const statusColor = { pending:'bg-gray-100', running:'bg-blue-100', done_pass:'bg-green-100', done_fail:'bg-red-100', error:'bg-yellow-100' }[item.status] || 'bg-gray-100';
        const statusIcon  = { pending:'•', running:'⟳', done_pass:'✓', done_fail:'✗', error:'!' }[item.status] || '•';
        const d = document.createElement('div');
        d.className = 'relative border border-gray-200 rounded overflow-hidden ' + statusColor;
        d.innerHTML = `
            <img src="${item.b64}" alt="${escHtml(item.name)}" class="w-full h-28 object-contain bg-gray-50">
            <button onclick="removeBatchItem(${i})" class="absolute top-1 right-1 bg-white rounded-full w-5 h-5 text-xs text-gray-500 hover:text-red-600 flex items-center justify-center border border-gray-200" title="Remove">✕</button>
            <div class="p-1.5">
                <p class="text-xs text-gray-600 truncate" title="${escHtml(item.name)}">${escHtml(item.name)}</p>
                <p class="text-xs font-bold mt-0.5">${statusIcon} ${item.status === 'running' ? 'Checking…' : item.status === 'done_pass' ? 'PASS' : item.status === 'done_fail' ? 'FAIL' : item.status === 'error' ? 'Error' : 'Pending'}</p>
            </div>`;
        grid.appendChild(d);
    });
}

function removeBatchItem(i) {
    batchFiles.splice(i, 1);
    renderBatchQueue();
    if (!batchFiles.length) document.getElementById('batch-results').classList.add('hidden');
}

function clearBatch() {
    batchFiles = [];
    document.getElementById('input-batch').value = '';
    renderBatchQueue();
    document.getElementById('batch-results').classList.add('hidden');
}

async function verifyBatch() {
    if (!batchFiles.length) return;
    const token = await getAuthToken();
    if (!token) { window.location.href = '/auth?next=/verify'; return; }

    const btn = document.getElementById('btn-batch');
    btn.disabled = true;
    btn.textContent = 'Running…';

    const resultsDiv = document.getElementById('batch-results');
    resultsDiv.classList.remove('hidden');
    resultsDiv.innerHTML = '';

    // Reset statuses
    batchFiles.forEach(item => item.status = 'pending');
    renderBatchQueue();

    let passed = 0, failed = 0, errors = 0;

    for (let i = 0; i < batchFiles.length; i++) {
        const item = batchFiles[i];
        item.status = 'running';
        renderBatchQueue();

        try {
            const res = await fetch('/api/verify-label', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_base64: item.b64 }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: 'Error' }));
                throw new Error(err.detail || 'HTTP ' + res.status);
            }
            const data = await res.json();
            item.result = data;
            item.status = data.overall_status === 'PASS' ? 'done_pass' : 'done_fail';
            if (item.status === 'done_pass') passed++; else failed++;
            // Save to history (fire-and-forget)
            saveVerificationToHistory(data, item.b64, null);
        } catch (err) {
            item.status = 'error';
            item.result = { error: err.message };
            errors++;
        }

        renderBatchQueue();
        renderBatchResults();
    }

    btn.disabled = false;
    btn.textContent = 'Verify All';
    showToast(
        'Batch complete',
        `${batchFiles.length} labels checked — ${passed} pass, ${failed} fail${errors ? ', ' + errors + ' errors' : ''}.`,
        failed || errors ? 'warning' : 'success'
    );
}

function renderBatchResults() {
    const div = document.getElementById('batch-results');
    div.innerHTML = '<p class="text-sm font-semibold text-gray-700 mb-2">Results</p>';

    batchFiles.forEach((item, i) => {
        if (!item.result) return;
        if (item.result.error) {
            div.innerHTML += `<div class="border border-yellow-200 rounded p-3 bg-yellow-50 text-sm flex gap-3">
                <span class="text-yellow-600 font-bold">!</span>
                <div><p class="font-semibold text-yellow-900">${escHtml(item.name)}</p>
                <p class="text-yellow-700">${escHtml(item.result.error)}</p></div></div>`;
            return;
        }
        const r = item.result;
        const pass = r.overall_status === 'PASS';
        div.innerHTML += `
        <details class="border ${pass ? 'border-green-200' : 'border-red-200'} rounded overflow-hidden">
            <summary class="${pass ? 'bg-green-50' : 'bg-red-50'} px-4 py-3 cursor-pointer flex items-center justify-between text-sm">
                <span class="flex items-center gap-2">
                    <span class="font-bold ${pass ? 'text-green-700' : 'text-red-700'}">${pass ? '✓' : '✗'}</span>
                    <span class="font-medium text-gray-800">${escHtml(item.name)}</span>
                    <span class="text-xs text-gray-500">${escHtml(r.commodity_type)}</span>
                </span>
                <span class="font-bold ${pass ? 'text-green-700' : 'text-red-700'}">${r.compliance_score}/100</span>
            </summary>
            <div class="p-4 bg-white space-y-2 text-sm">
                ${r.critical_failures?.length ? `<div class="text-red-800 bg-red-50 border border-red-200 p-2 rounded"><strong>Critical:</strong> ${r.critical_failures.map(escHtml).join('; ')}</div>` : ''}
                ${r.warnings?.length          ? `<div class="text-yellow-800 bg-yellow-50 border border-yellow-200 p-2 rounded"><strong>Warnings:</strong> ${r.warnings.map(escHtml).join('; ')}</div>` : ''}
                ${r.checks.map(c => `
                    <div class="flex gap-2 ${c.status==='PASS' ? 'text-green-800' : 'text-red-800'}">
                        <span class="font-bold">${c.status==='PASS' ? '✓' : '✗'}</span>
                        <span><strong>${escHtml(c.field)}</strong>${c.reason ? ' — ' + escHtml(c.reason) : ''}</span>
                    </div>`).join('')}
            </div>
        </details>`;
    });
}

/* ─── History saving ────────────────────────────────────────────── */
async function saveVerificationToHistory(data, imageThumbnail, productDetails) {
    try {
        const token = await getAuthToken();
        if (!token) return;

        // Thumbnail: use a compact version of the image (max ~50KB)
        // Just pass the raw base64 — backend will store it
        const thumbnail = imageThumbnail || null;

        await fetch('/api/verification-history', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({
                overall_status:   data.overall_status,
                commodity_type:   data.commodity_type,
                compliance_score: data.compliance_score,
                checks:           data.checks,
                product_details:  productDetails || null,
                image_thumbnail:  thumbnail,
            }),
        });
        // No error handling needed — this is fire-and-forget
    } catch { /* silent */ }
}

/* ─── Auto-preload from URL params ─────────────────────────────── */
/* If the page is opened with ?label_url=... (e.g. from application detail),
   automatically fetch that image and preload it into the single-label panel. */
document.addEventListener('DOMContentLoaded', async () => {
    const params   = new URLSearchParams(window.location.search);
    const labelUrl = params.get('label_url');
    const appName  = params.get('app_name') || '';

    if (!labelUrl) return;

    try {
        const res  = await fetch(labelUrl);
        if (!res.ok) throw new Error('Image fetch failed');
        const blob = await res.blob();
        const reader = new FileReader();
        reader.onload = e => {
            singleB64 = e.target.result;
            singleFile = new File([blob], labelUrl.split('/').pop() || 'label.jpg', { type: blob.type });
            document.getElementById('img-single').src = singleB64;
            document.getElementById('fname-single').textContent =
                (appName ? appName + ' — ' : '') + singleFile.name + ' (' + fmtSize(blob.size) + ')';
            document.getElementById('preview-single').classList.remove('hidden');
            document.getElementById('drop-single').classList.add('hidden');
            document.getElementById('btn-single').disabled = false;
        };
        reader.readAsDataURL(blob);
    } catch (err) {
        console.warn('Failed to preload label from URL:', err);
    }

    // Pre-fill product details if provided
    const details = params.get('details');
    if (details) {
        const el = document.getElementById('details-single');
        if (el) el.value = details;
    }
});

/* ─── Helpers ───────────────────────────────────────────────────── */
function fmtSize(b) {
    if (b < 1024)       return b + ' B';
    if (b < 1024*1024)  return (b/1024).toFixed(1) + ' KB';
    return (b/(1024*1024)).toFixed(1) + ' MB';
}

function escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
