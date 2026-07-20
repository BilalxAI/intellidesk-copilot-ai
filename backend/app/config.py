import os
import uuid
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

try:
    from dotenv import load_dotenv

    # Load env from repo root first, then db/.env (your current layout).
    # Override existing process vars so stale shell values do not break bot auth.
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    load_dotenv(dotenv_path=ROOT_DIR / "db" / ".env", override=True)
except Exception:
    pass


def _normalize_root_path(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "/":
        return ""
    return f"/{value.strip('/')}"


def _get_env(*keys: str, default: str = "") -> str:
    """Return the first non-empty environment variable value."""
    for key in keys:
        value = os.getenv(key, "")
        if value and value.strip():
            return value.strip()
    return default


def _canonical_microsoft_app_id(value: str) -> str:
    """Normalize GUIDs from .env/JWT so Bot Framework compares match reliably."""
    if not value:
        return ""
    s = value.strip().lstrip("\ufeff")
    if len(s) >= 2 and s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    try:
        return str(uuid.UUID(s))
    except ValueError:
        return s.casefold()

# ==================== OLLAMA CONFIG ====================
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# ==================== API CONFIG ====================
API_TITLE = "IT Support Assistant API"
API_VERSION = "1.0.0"
API_HOST = "0.0.0.0"
API_PORT = int(os.getenv("API_PORT", "8000"))
API_ROOT_PATH = _normalize_root_path(os.getenv("API_ROOT_PATH", ""))
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]

# ==================== BOT FRAMEWORK CONFIG ====================
BOT_APP_ID = _get_env(
    "MICROSOFT_APP_ID",
    "MicrosoftAppId",
    "BOT_APP_ID",
    "APP_ID",
    "app_id",
)
BOT_APP_PASSWORD = _get_env(
    "MICROSOFT_APP_PASSWORD",
    "MicrosoftAppPassword",
    "BOT_APP_PASSWORD",
    "CLIENT_SECRET",
    "client_secret",
)
BOT_ID_RAW = _get_env("BOT_ID", "bot_id")
BOT_APP_ID = _canonical_microsoft_app_id(BOT_APP_ID)
BOT_ID = _canonical_microsoft_app_id(BOT_ID_RAW) if BOT_ID_RAW else ""
# Single-tenant bots (common in Teams org apps): tenant where the app registration lives.
BOT_CHANNEL_AUTH_TENANT = _canonical_microsoft_app_id(
    _get_env(
        "MICROSOFT_APP_TENANT_ID",
        "MicrosoftAppTenantId",
        "BOT_CHANNEL_AUTH_TENANT",
        "BOT_AUTH_TENANT",
        "TENANT_ID",
        "tenant_id",
    )
)

# ==================== ISSUE CATEGORIES ====================
ISSUE_CATEGORIES: List[str] = [
    "HEADSET_ISSUE",
    "DISPLAY_ISSUE",
    "KEYBOARD_MOUSE_ISSUE",
    "NETWORK_ISSUE",
    "TEAMS_ISSUE",
    "OUTLOOK_ISSUE",
    "SOFTWARE_INSTALLATION",
    "NEEDS_LLM_CLASSIFICATION",  # Used when rule-based classifier is uncertain
    "UNKNOWN",
]

# Threshold for re-classifying a NEW issue (vs staying on prior conversation)
# If confidence < this and user appears to have new issue → re-classify with LLM
NEW_ISSUE_CLASSIFICATION_THRESHOLD = float(os.getenv("NEW_ISSUE_CLASSIFICATION_THRESHOLD", "0.65"))

# ==================== KB CONFIG ====================
KB_PATH = os.getenv("KB_PATH", str(ROOT_DIR / "kb.json"))
DATASET_PATH = os.getenv("DATASET_PATH", str(ROOT_DIR / "dataset.jsonl"))
CLASSIFICATION_CONFIDENCE_THRESHOLD = float(os.getenv("CLASSIFICATION_CONFIDENCE_THRESHOLD", "0.45"))

# ==================== DATABASE CONFIG ====================
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'it_support.db'}")
MAX_MESSAGES_PER_CONVERSATION = int(os.getenv("MAX_MESSAGES_PER_CONVERSATION", "10"))
# Session expiry for Teams/web: end a session after N minutes of inactivity
SESSION_EXPIRY_MINUTES = int(os.getenv("SESSION_EXPIRY_MINUTES", "30"))

# ==================== LOGGING ====================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/it_support.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))

# Expose logs via API (guarded). Recommended: keep disabled in prod and use DevOps log streaming instead.
ENABLE_LOGS_API = os.getenv("ENABLE_LOGS_API", "False").lower() == "true"
# Optional shared secret for logs endpoint. If set, clients must send header `x-logs-token`.
LOGS_API_TOKEN = os.getenv("LOGS_API_TOKEN", "").strip()
# Default number of log lines to return for tail endpoint.
LOGS_API_DEFAULT_LINES = int(os.getenv("LOGS_API_DEFAULT_LINES", "200"))
# Absolute max lines per request to prevent large reads.
LOGS_API_MAX_LINES = int(os.getenv("LOGS_API_MAX_LINES", "2000"))

# ==================== LLM PARAMETERS ====================
LLM_TEMPERATURE = 0.3  # Low temp for consistent, factual responses
LLM_TOP_P = 0.9
LLM_MAX_TOKENS = 500

# ==================== RESPONSE FORMATTING ====================
# Caps on how many troubleshooting steps the assistant returns.
# Increase these if you want the bot to output "all steps" from `kb.json`.
MAX_STEPS_FIRST_RESPONSE = int(os.getenv("MAX_STEPS_FIRST_RESPONSE", "6"))
MAX_STEPS_FOLLOW_UP = int(os.getenv("MAX_STEPS_FOLLOW_UP", "3"))

# If True, bypass LLM formatting and return ALL approved KB steps for the matched issue/category.
# This is useful for debugging and for deterministic output.
RETURN_ALL_KB_STEPS = os.getenv("RETURN_ALL_KB_STEPS", "False").lower() == "true"

# When user asks "what next" / "next step", return the next chunk of KB steps.
NEXT_STEPS_CHUNK_SIZE = int(os.getenv("NEXT_STEPS_CHUNK_SIZE", "3"))

# If True, always construct responses from KB steps (deterministic) and use the LLM only for
# step explanations / classification gates. Prevents the model from inventing or swapping steps.
KB_STEPS_RESPONSE_MODE = os.getenv("KB_STEPS_RESPONSE_MODE", "True").lower() == "true"

# ==================== REQUEST QUEUE CONFIG ====================
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "1000"))
QUEUE_TIMEOUT_SECONDS = int(os.getenv("QUEUE_TIMEOUT_SECONDS", "300"))
ENABLE_CACHING = os.getenv("ENABLE_CACHING", "True").lower() == "true"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))  # 1 hour
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
REQUEST_WORKERS = int(os.getenv("REQUEST_WORKERS", "1"))
LLM_TOP_K = 40
MAX_TOKENS = 500

# ==================== OPTIONAL INTEGRATIONS ====================
# Webhook escalation (unused unless wired to Power Automate / Logic App later).
ESCALATION_WEBHOOK_URL = os.getenv("ESCALATION_WEBHOOK_URL", "").strip()
ESCALATION_WEBHOOK_API_KEY = os.getenv("ESCALATION_WEBHOOK_API_KEY", "").strip()
ESCALATION_WEBHOOK_TIMEOUT_SECONDS = int(os.getenv("ESCALATION_WEBHOOK_TIMEOUT_SECONDS", "10"))

# Microsoft Graph escalation (delegated user).
# This lets a "delegate" user account post into an IT Support group chat.
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "").strip()
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "").strip()
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "").strip()
GRAPH_DELEGATE_USERNAME = os.getenv("GRAPH_DELEGATE_USERNAME", "").strip()
GRAPH_DELEGATE_PASSWORD = os.getenv("GRAPH_DELEGATE_PASSWORD", "").strip()
# Optional override for OAuth scopes (space-separated). For delegated chat posting,
# common scopes are: https://graph.microsoft.com/Chat.ReadWrite and/or
# https://graph.microsoft.com/ChatMessage.Send
GRAPH_SCOPES = os.getenv("GRAPH_SCOPES", "").strip()
# Target can be either:
# - a Teams group chat id (preferred): /chats/{id}/messages
# - or a channel (team_id + channel_id): /teams/{teamId}/channels/{channelId}/messages
IT_SUPPORT_CHAT_ID = os.getenv("IT_SUPPORT_CHAT_ID", "").strip()
IT_SUPPORT_TEAM_ID = os.getenv("IT_SUPPORT_TEAM_ID", "").strip()
IT_SUPPORT_CHANNEL_ID = os.getenv("IT_SUPPORT_CHANNEL_ID", "").strip()
GRAPH_TIMEOUT_SECONDS = int(os.getenv("GRAPH_TIMEOUT_SECONDS", "10"))

# ==================== TICKETING SYSTEM ====================
# MVP ticket store. SQLite by default (fine for a single-instance pilot).
# Move to Postgres before running multiple technicians/dashboards concurrently
# against this (see PROJECT discussion: SQLite serializes writes and can race
# two simultaneous ticket assignments).
TICKETING_ENABLED = os.getenv("TICKETING_ENABLED", "True").lower() == "true"
TICKETING_DATABASE_URL = os.getenv(
    "TICKETING_DATABASE_URL", f"sqlite:///{DATA_DIR / 'tickets.db'}"
)
TECHNICIANS_PATH = os.getenv("TECHNICIANS_PATH", str(ROOT_DIR / "technicians.json"))

# Placeholder priority-by-category matrix. Replace with the real SME-approved
# impact/urgency matrix once defined; until then this keeps priority assignment
# deterministic and visible instead of guessed by the LLM.
TICKET_PRIORITY_BY_CATEGORY = {
    "NETWORK_ISSUE": "P2",
    "OUTLOOK_ISSUE": "P2",
    "TEAMS_ISSUE": "P3",
    "HEADSET_ISSUE": "P3",
    "DISPLAY_ISSUE": "P3",
    "HARDWARE_ISSUE": "P3",
    "KEYBOARD_MOUSE_ISSUE": "P4",
    "SOFTWARE_INSTALLATION": "P4",
    "UNKNOWN": "P3",
}
TICKET_DEFAULT_PRIORITY = "P3"

# Placeholder average handling time (minutes) per priority, used only to compute
# an estimated response time from queue position. Replace with real historical
# averages once the ticketing system has data; do not present this as a hard SLA.
TICKET_AVG_HANDLE_MINUTES = {"P1": 15, "P2": 20, "P3": 30, "P4": 45}
