/* auth.js – auth helpers with demo-mode support */

/* ── Storage helpers (sessionStorage may be blocked in some browsers) ── */
const _storage = (() => {
    try { sessionStorage.setItem('_sb_test', '1'); sessionStorage.removeItem('_sb_test'); return sessionStorage; }
    catch { return { getItem: () => null, setItem: () => {}, removeItem: () => {} }; }
})();

/* ── Demo session helpers ────────────────────────────────────────────── */
function getDemoSession() {
    try {
        const raw = _storage.getItem('demo_session');
        return raw ? JSON.parse(raw) : null;
    } catch { return null; }
}

/** Read the demo_role cookie that demoLogin() sets for server-side auth */
function getDemoCookieRole() {
    try {
        const m = document.cookie.match(/(?:^|;\s*)demo_role=([^;]+)/);
        const role = m ? m[1] : null;
        const valid = ['industry', 'staff', 'admin'];
        return valid.includes(role) ? role : null;
    } catch { return null; }
}

function clearDemoSession() {
    _storage.removeItem('demo_session');
    // Also expire the server-side cookie
    document.cookie = 'demo_role=; path=/; max-age=0; SameSite=Lax';
}

/* ── Core auth functions (used by every page) ───────────────────────── */
async function getCurrentUser() {
    // 1. Check sessionStorage demo session
    const demo = getDemoSession();
    if (demo?.user) return demo.user;

    // 2. Cookie fallback (when sessionStorage is unavailable)
    const cookieRole = getDemoCookieRole();
    if (cookieRole) {
        const emails = { industry: 'user45@gmail.com', staff: 'john@ttb.gov', admin: 'admin47@treasury.gov' };
        return { id: 'demo-' + cookieRole + '-001', email: emails[cookieRole] || cookieRole + '@demo', role: cookieRole };
    }

    // 3. Fall back to Supabase
    try {
        const { data } = await window.supabaseClient.auth.getSession();
        return data.session?.user ?? null;
    } catch { return null; }
}

async function getAuthToken() {
    // 1. Check sessionStorage demo session
    const demo = getDemoSession();
    if (demo?.token) return demo.token;

    // 2. Cookie fallback (when sessionStorage is unavailable)
    const cookieRole = getDemoCookieRole();
    if (cookieRole) return 'demo-' + cookieRole;

    // 3. Fall back to Supabase
    try {
        const { data } = await window.supabaseClient.auth.getSession();
        return data.session?.access_token ?? null;
    } catch { return null; }
}

async function getUserRole() {
    // 1. Check sessionStorage demo session
    const demo = getDemoSession();
    if (demo?.user?.role) return demo.user.role;

    // 2. Cookie fallback
    const cookieRole = getDemoCookieRole();
    if (cookieRole) return cookieRole;

    // 3. Look up role from Supabase
    const user = await getCurrentUser();
    if (!user) return null;
    try {
        const { data } = await window.supabaseClient
            .from('user_roles').select('role').eq('user_id', user.id).maybeSingle();
        return data?.role ?? 'industry';
    } catch { return 'industry'; }
}

async function checkAuth() {
    return (await getAuthToken()) !== null;
}

async function handleSignOut() {
    clearDemoSession();
    try { await window.supabaseClient.auth.signOut(); } catch { /* ignore */ }
    window.location.href = '/auth';
}

/* ── Authenticated fetch ──────────────────────────────────────────────── */
async function apiCall(endpoint, method = 'GET', data = null) {
    const token = await getAuthToken();
    if (!token) { window.location.href = '/auth'; return null; }
    const opts = {
        method,
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    };
    if (data) opts.body = JSON.stringify(data);
    try {
        const res = await fetch(endpoint, opts);
        if (res.status === 401) { window.location.href = '/auth'; return null; }
        return await res.json();
    } catch (err) {
        if (typeof showToast === 'function') showToast('Request failed', err.message, 'error');
        return null;
    }
}

/* ── Route guard (protected pages) ────────────────────────────────────── */
const _PROTECTED = [
    '/verify', '/history', '/settings',
    '/industry/', '/staff/', '/admin/',
];

// Loop-breaker: track redirect attempts per page load (resets on navigation)
let _guardFired = false;

document.addEventListener('DOMContentLoaded', async () => {
    const path = window.location.pathname;

    if (_PROTECTED.some(p => path.startsWith(p))) {
        if (_guardFired) return;   // never redirect twice per page load
        _guardFired = true;

        const ok = await checkAuth();
        if (!ok) {
            // Extra safety: if we're already headed to /auth, don't pile on
            const dest = '/auth?next=' + encodeURIComponent(path);
            if (window.location.href !== dest) {
                window.location.href = dest;
            }
        }
    }

    /* Inject demo banner if in demo mode */
    const _DEMO_EMAILS = { industry: 'user45@gmail.com', staff: 'john@ttb.gov', admin: 'admin47@treasury.gov' };
    const demoUser = (getDemoSession()?.user) || (() => {
        const r = getDemoCookieRole();
        return r ? { email: _DEMO_EMAILS[r] || r + '@demo', role: r } : null;
    })();
    if (demoUser) {
        const banner = document.createElement('div');
        banner.id = 'demo-banner';
        banner.style.cssText = 'background:#1a3d6b;color:#fff;text-align:center;font-size:0.8rem;padding:0.35rem 1rem;letter-spacing:0.03em;position:sticky;top:0;z-index:999;';
        banner.innerHTML = `🔖 <strong>DEMO MODE</strong> — signed in as <strong>${demoUser.email}</strong> (${demoUser.role}) &nbsp;·&nbsp; <a href="/auth" onclick="clearDemoSession()" style="color:#93c5fd;text-decoration:underline;">Switch account</a>`;
        document.body.insertBefore(banner, document.body.firstChild);
    }
});

/* ── Auth message helper ─────────────────────────────────────────────── */
function showAuthMsg(msg, isError = false) {
    const el = document.getElementById('auth-message');
    if (!el) return;
    el.textContent = msg;
    el.className = 'mt-4 p-3 rounded text-sm font-medium ' +
        (isError ? 'bg-red-50 text-red-800 border border-red-200'
                 : 'bg-green-50 text-green-800 border border-green-200');
    el.classList.remove('hidden');
}

/* ── Demo login (set cookie + sessionStorage + redirect) ─────────────── */
/* Defined here so it's always available regardless of template caching.  */
function demoLogin(email, role) {
    document.cookie = 'demo_role=' + role + '; path=/; max-age=86400; SameSite=Lax';
    var session = {
        demo: true,
        user: { id: 'demo-' + role + '-001', email: email, role: role },
        token: 'demo-' + role,
    };
    try { sessionStorage.setItem('demo_session', JSON.stringify(session)); } catch(e) {}

    var next = new URLSearchParams(window.location.search).get('next');
    if (next && next.startsWith('/') && !next.startsWith('//')) {
        window.location.href = next; return;
    }
    window.location.href = (role === 'industry') ? '/industry/dashboard' : '/staff/dashboard';
}

/* ── Sign-in with demo email interception ─────────────────────────────── */
/* Overrides any inline handleSignIn from auth.html to ensure demo emails  */
/* always bypass Supabase. This file is served as a static asset from the  */
/* volume mount, so it's always up-to-date.                                */
const _DEMO_ACCOUNTS_JS = {
    'admin47@treasury.gov': 'admin',
    'john@ttb.gov':         'staff',
    'user45@gmail.com':     'industry',
};

async function handleSignIn() {
    var email = (document.getElementById('si-email')?.value || '').trim().toLowerCase();
    var pass  = (document.getElementById('si-pass')?.value || '');
    if (!email || !pass) { showAuthMsg('Please enter your email and password.', true); return; }

    // Intercept known demo accounts — bypass Supabase entirely
    if (_DEMO_ACCOUNTS_JS[email]) {
        demoLogin(email, _DEMO_ACCOUNTS_JS[email]);
        return;
    }

    // Real Supabase sign-in
    if (typeof setLoading === 'function') setLoading('si-btn', true, 'Sign In');
    try {
        var _sb = window.supabaseClient;
        var result = await _sb.auth.signInWithPassword({ email: email, password: pass });
        if (result.error) throw result.error;

        // Role-based redirect
        var rd = await _sb.from('user_roles').select('role').eq('user_id', result.data.user.id).maybeSingle();
        var role = rd?.data?.role || 'industry';

        var next = new URLSearchParams(window.location.search).get('next');
        if (next) { window.location.href = next; return; }
        window.location.href = (role === 'industry') ? '/industry/dashboard' : '/staff/dashboard';
    } catch (err) {
        showAuthMsg(err.message || 'Sign-in failed. Please check your credentials.', true);
        if (typeof setLoading === 'function') setLoading('si-btn', false, 'Sign In');
    }
}
