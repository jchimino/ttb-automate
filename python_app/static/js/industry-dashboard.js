/**
 * Industry dashboard page logic
 */

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    if (!user) {
        window.location.href = '/auth?portal=industry';
        return;
    }

    await loadApplications();

    document.getElementById('new-app-btn').addEventListener('click', openNewAppModal);
    document.getElementById('create-first-btn')?.addEventListener('click', openNewAppModal);
});

/* ── Application list ──────────────────────────────────────────────── */
async function loadApplications() {
    const listEl  = document.getElementById('applications-list');
    const emptyEl = document.getElementById('empty-state');

    try {
        const result = await apiCall('/api/applications');
        const apps   = result?.applications || [];

        if (apps.length === 0) {
            listEl.classList.add('hidden');
            emptyEl.classList.remove('hidden');
            return;
        }

        listEl.innerHTML = apps.map(app => `
            <div class="border border-gray-200 rounded-lg p-4 hover:bg-gray-50 transition-colors cursor-pointer"
                 onclick="viewApplication('${app.id}')">
                <div class="flex items-start gap-4">
                    ${app.label_url ? `
                    <img src="${app.label_url}" alt="Label"
                         style="width:64px;height:80px;object-fit:cover;border:1px solid #e5e7eb;flex-shrink:0;border-radius:2px;">
                    ` : ''}
                    <div class="flex-1 min-w-0">
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <p class="font-bold text-gray-900">${app.product_name || app.brand_name}</p>
                                <p class="text-sm text-gray-500">
                                    ${app.product_type || ''} &bull; ${app.alcohol_content || ''}
                                </p>
                                <p class="text-xs text-gray-400 mt-1">
                                    Created: ${new Date(app.created_at).toLocaleDateString()}
                                </p>
                            </div>
                            <span class="px-3 py-1 rounded-lg font-bold text-sm flex-shrink-0 ${
                                app.status === 'approved'       ? 'bg-green-50 text-green-900' :
                                app.status === 'rejected'       ? 'bg-red-50 text-red-900'     :
                                app.status === 'pending_review' ? 'bg-yellow-50 text-yellow-900' :
                                'bg-gray-100 text-gray-700'
                            }">
                                ${app.status.replace(/_/g, ' ').toUpperCase()}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        `).join('');

        emptyEl.classList.add('hidden');
        listEl.classList.remove('hidden');
    } catch (err) {
        console.error('Error loading applications:', err);
        emptyEl.classList.remove('hidden');
        listEl.classList.add('hidden');
    }
}

function viewApplication(appId) {
    window.location.href = `/industry/applications/${appId}`;
}

/* ── New Application modal ─────────────────────────────────────────── */
function openNewAppModal() {
    // Remove any existing modal
    document.getElementById('new-app-modal')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'new-app-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center;padding:1rem;';
    overlay.innerHTML = `
        <div style="background:#fff;width:100%;max-width:500px;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <div style="background:#112e51;color:#fff;padding:1rem 1.25rem;display:flex;justify-content:space-between;align-items:center;">
                <h2 style="font-size:1rem;font-weight:700;">New COLA Application</h2>
                <button onclick="closeNewAppModal()" style="background:none;border:none;color:#fff;font-size:1.4rem;cursor:pointer;line-height:1;">&times;</button>
            </div>
            <form id="new-app-form" onsubmit="submitNewApp(event)" style="padding:1.25rem;display:flex;flex-direction:column;gap:1rem;">
                <div>
                    <label style="display:block;font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">Product Name *</label>
                    <input name="product_name" required placeholder="e.g. Blue Ridge Bourbon"
                        style="width:100%;padding:0.5rem 0.75rem;border:1px solid #d1d5db;font-size:0.875rem;font-family:inherit;box-sizing:border-box;">
                </div>
                <div>
                    <label style="display:block;font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">Brand Name *</label>
                    <input name="brand_name" required placeholder="e.g. Blue Ridge Distillery"
                        style="width:100%;padding:0.5rem 0.75rem;border:1px solid #d1d5db;font-size:0.875rem;font-family:inherit;box-sizing:border-box;">
                </div>
                <div>
                    <label style="display:block;font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">Product Type *</label>
                    <select name="product_type" required
                        style="width:100%;padding:0.5rem 0.75rem;border:1px solid #d1d5db;font-size:0.875rem;font-family:inherit;box-sizing:border-box;background:#fff;">
                        <option value="">Select type…</option>
                        <option>Straight Bourbon Whiskey</option>
                        <option>Blended Scotch Whisky</option>
                        <option>Vodka</option>
                        <option>Gin</option>
                        <option>Rum</option>
                        <option>Brandy</option>
                        <option>Table Wine</option>
                        <option>Sparkling Wine</option>
                        <option>Malt Beverage</option>
                        <option>Other Distilled Spirits</option>
                    </select>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;">
                    <div>
                        <label style="display:block;font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">Alcohol Content *</label>
                        <input name="alcohol_content" required placeholder="e.g. 40% Alc./Vol."
                            style="width:100%;padding:0.5rem 0.75rem;border:1px solid #d1d5db;font-size:0.875rem;font-family:inherit;box-sizing:border-box;">
                    </div>
                    <div>
                        <label style="display:block;font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">Net Contents</label>
                        <input name="net_contents" placeholder="e.g. 750 mL"
                            style="width:100%;padding:0.5rem 0.75rem;border:1px solid #d1d5db;font-size:0.875rem;font-family:inherit;box-sizing:border-box;">
                    </div>
                </div>
                <div id="new-app-error" style="display:none;padding:0.5rem 0.75rem;background:#fef2f2;border:1px solid #fca5a5;color:#7f1d1d;font-size:0.8rem;"></div>
                <div style="display:flex;gap:0.75rem;justify-content:flex-end;padding-top:0.25rem;">
                    <button type="button" onclick="closeNewAppModal()"
                        style="padding:0.5rem 1.25rem;border:1px solid #d1d5db;background:#fff;font-weight:600;font-size:0.875rem;cursor:pointer;font-family:inherit;">
                        Cancel
                    </button>
                    <button type="submit" id="new-app-submit"
                        style="padding:0.5rem 1.5rem;background:#005ea2;color:#fff;font-weight:700;font-size:0.875rem;border:none;cursor:pointer;font-family:inherit;">
                        Create Draft
                    </button>
                </div>
            </form>
        </div>`;

    document.body.appendChild(overlay);
    // Close on backdrop click
    overlay.addEventListener('click', e => { if (e.target === overlay) closeNewAppModal(); });
    overlay.querySelector('input[name="product_name"]').focus();
}

function closeNewAppModal() {
    document.getElementById('new-app-modal')?.remove();
}

async function submitNewApp(e) {
    e.preventDefault();
    const form   = e.target;
    const btn    = document.getElementById('new-app-submit');
    const errEl  = document.getElementById('new-app-error');
    const data   = Object.fromEntries(new FormData(form).entries());

    btn.disabled    = true;
    btn.textContent = 'Saving…';
    errEl.style.display = 'none';

    try {
        const result = await apiCall('/api/applications', 'POST', data);
        if (!result) throw new Error('No response from server');
        closeNewAppModal();
        showToast('Application created', `"${data.product_name}" saved as draft.`, 'success');
        await loadApplications();
    } catch (err) {
        errEl.textContent    = err.message || 'Failed to create application.';
        errEl.style.display  = 'block';
        btn.disabled         = false;
        btn.textContent      = 'Create Draft';
    }
}
