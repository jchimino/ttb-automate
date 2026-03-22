/**
 * Settings page logic
 */

document.addEventListener('DOMContentLoaded', async () => {
    const user = await getCurrentUser();
    const role = await getUserRole();

    if (user) {
        document.getElementById('user-email').textContent = user.email;
        document.getElementById('user-role').textContent = role || 'Industry';
    }

    // Load preferences from localStorage
    const emailNotifications = document.getElementById('email-notifications');
    const autoSaveHistory = document.getElementById('auto-save-history');

    emailNotifications.checked = localStorage.getItem('pref_email_notifications') !== 'false';
    autoSaveHistory.checked = localStorage.getItem('pref_auto_save_history') !== 'false';

    emailNotifications.addEventListener('change', (e) => {
        localStorage.setItem('pref_email_notifications', e.target.checked);
    });

    autoSaveHistory.addEventListener('change', (e) => {
        localStorage.setItem('pref_auto_save_history', e.target.checked);
    });
});
