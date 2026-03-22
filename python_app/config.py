import os
from dotenv import load_dotenv

# Load environment variables from parent .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Supabase configuration
SUPABASE_URL = os.getenv('VITE_SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.getenv('VITE_SUPABASE_PUBLISHABLE_KEY', '')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')

# Anthropic API (used when LOCAL_LLM_URL is not set)
ANTHROPIC_API_KEY = os.getenv('VITE_ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_API_KEY', '')

# ── Local LLM (Ollama / Assessment Service) ──────────────────────────
# LOCAL_LLM_URL  → assess service POST /assess endpoint
# OLLAMA_HOST    → direct Ollama REST API (used for commodity classifier)
# OLLAMA_MODEL   → vision model for label classification (default llava:7b)
LOCAL_LLM_URL = os.getenv('LOCAL_LLM_URL', '').rstrip('/')
OLLAMA_HOST   = os.getenv('OLLAMA_HOST', '').rstrip('/')
OLLAMA_MODEL  = os.getenv('OLLAMA_MODEL', 'llava:7b')

# Malware scanning
MALWARE_SCAN_API = 'https://api.malwaredetection.me/v1/scan'

# Rate limiting
RATE_LIMIT_WINDOW_MS = 60 * 1000  # 1 minute
RATE_LIMIT_MAX_REQUESTS = 10  # max 10 requests per minute

# File upload constraints
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_PRODUCT_DETAILS_LENGTH = 5000

# Valid image types
VALID_IMAGE_TYPES = [
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
]
