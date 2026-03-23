"""HTML page routes"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
import os

from config import SUPABASE_URL, SUPABASE_ANON_KEY

# Resolve the templates directory — try multiple candidate paths so the app
# works whether templates are copied into the image or bind-mounted at runtime.
def _find_templates_dir() -> str:
    candidates = [
        # Relative to this file (routers/ → ../templates)
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates')),
        # Absolute container path used by docker-compose bind-mount
        '/app/templates',
        # CWD-relative fallback
        os.path.join(os.getcwd(), 'templates'),
    ]
    for path in candidates:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, 'landing.html')):
            return path
    # Last resort: return the first candidate and let Jinja2 report a clear error
    return candidates[0]

_templates_dir = _find_templates_dir()
templates = Jinja2Templates(directory=_templates_dir)

router = APIRouter()

# Valid demo roles
_DEMO_ROLES = {'industry', 'staff', 'admin'}

# Context variables passed to all templates
def get_context(request: Request):
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
    }


def get_demo_role(request: Request) -> str | None:
    """Read the demo_role cookie set by demoLogin() in the browser."""
    role = request.cookies.get('demo_role', '').strip()
    return role if role in _DEMO_ROLES else None


def require_auth(request: Request, allowed_roles: set | None = None) -> str | None:
    """
    Return the demo role if the request is authenticated, else None.
    For page routes we only check the demo cookie (real Supabase JWT is validated
    client-side; the browser can't send Bearer tokens on plain GET requests).
    """
    role = get_demo_role(request)
    if role and (allowed_roles is None or role in allowed_roles):
        return role
    return None


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Landing page – redirect to dashboard if already logged in."""
    role = get_demo_role(request)
    if role:
        dest = '/industry/dashboard' if role == 'industry' else '/staff/dashboard'
        return RedirectResponse(url=dest, status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "landing.html", context)


@router.get("/auth", response_class=HTMLResponse)
async def auth(request: Request):
    """Auth page – redirect to dashboard if already logged in (demo cookie)."""
    role = get_demo_role(request)
    if role:
        next_url = request.query_params.get('next', '')
        if next_url.startswith('/') and not next_url.startswith('//'):
            return RedirectResponse(url=next_url, status_code=302)
        dest = '/industry/dashboard' if role == 'industry' else '/staff/dashboard'
        return RedirectResponse(url=dest, status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "auth.html", context)


@router.get("/verify", response_class=HTMLResponse)
async def verify(request: Request):
    """Label verification page"""
    if not require_auth(request):
        return RedirectResponse(url='/auth?next=/verify', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "verify.html", context)


@router.get("/regulations", response_class=HTMLResponse)
async def regulations(request: Request):
    """Regulatory reference page"""
    context = get_context(request)
    return templates.TemplateResponse(request, "regulations.html", context)


@router.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    """Verification history page"""
    if not require_auth(request):
        return RedirectResponse(url='/auth?next=/history', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "history.html", context)


@router.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    """User settings page"""
    if not require_auth(request):
        return RedirectResponse(url='/auth?next=/settings', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "settings.html", context)


@router.get("/industry/dashboard", response_class=HTMLResponse)
async def industry_dashboard(request: Request):
    """Industry member dashboard"""
    if not require_auth(request, {'industry', 'admin', 'staff'}):
        return RedirectResponse(url='/auth?next=/industry/dashboard', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "industry/dashboard.html", context)


@router.get("/industry/applications/{app_id}", response_class=HTMLResponse)
async def application_detail(request: Request, app_id: str):
    """Application detail page"""
    if not require_auth(request, {'industry', 'admin', 'staff'}):
        return RedirectResponse(url=f'/auth?next=/industry/applications/{app_id}', status_code=302)
    context = get_context(request)
    context["app_id"] = app_id
    return templates.TemplateResponse(request, "industry/application_detail.html", context)


@router.get("/staff/dashboard", response_class=HTMLResponse)
async def staff_dashboard(request: Request):
    """Staff review dashboard"""
    if not require_auth(request, {'staff', 'admin'}):
        return RedirectResponse(url='/auth?next=/staff/dashboard', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "staff/dashboard.html", context)


@router.get("/staff/audit-log", response_class=HTMLResponse)
async def audit_log(request: Request):
    """Audit log page"""
    if not require_auth(request, {'staff', 'admin'}):
        return RedirectResponse(url='/auth?next=/staff/audit-log', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "staff/audit_log.html", context)


@router.get("/admin/quarantine", response_class=HTMLResponse)
async def quarantine(request: Request):
    """Quarantine review page"""
    if not require_auth(request, {'admin'}):
        return RedirectResponse(url='/auth?next=/admin/quarantine', status_code=302)
    context = get_context(request)
    return templates.TemplateResponse(request, "staff/quarantine.html", context)


# ── Documentation pages ───────────────────────────────────────────────────────

_DOCS_ROOT = os.path.join(os.path.dirname(__file__), '..')

_DOC_FILES = {
    "readme":   ("README.md",   "README"),
    "security": ("SECURITY.md", "Security Architecture"),
}


@router.get("/docs/{doc_name}", response_class=HTMLResponse)
async def docs_page(request: Request, doc_name: str):
    """Render a documentation page."""
    if doc_name not in _DOC_FILES:
        return RedirectResponse(url='/docs/readme', status_code=302)
    _, title = _DOC_FILES[doc_name]
    context = get_context(request)
    context["doc_name"]  = doc_name
    context["doc_title"] = title
    return templates.TemplateResponse(request, "docs.html", context)


@router.get("/api/docs/{doc_name}", response_class=PlainTextResponse)
async def docs_raw(doc_name: str):
    """Serve raw markdown content for the docs viewer."""
    if doc_name not in _DOC_FILES:
        return PlainTextResponse("Not found", status_code=404)
    filename, _ = _DOC_FILES[doc_name]
    path = os.path.normpath(os.path.join(_DOCS_ROOT, filename))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return PlainTextResponse(f.read())
    except FileNotFoundError:
        return PlainTextResponse(f"# {filename} not found", status_code=404)
