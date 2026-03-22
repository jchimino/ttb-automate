/**
 * Quarantine review page logic
 */

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    const role = await getUserRole();
    if (!user || role !== 'admin') {
        window.location.href = '/auth?portal=staff';
        return;
    }

    await loadQuarantine();
});

async function loadQuarantine() {
    // Placeholder: In a real implementation, this would fetch from API
    const filesTable = document.getElementById('files-table');
    const emptyState = document.getElementById('empty-state');

    filesTable.innerHTML = '';
    emptyState.classList.remove('hidden');
}

function refreshQuarantine() {
    loadQuarantine();
}
