"""
FastAPI application for IT Support Assistant.
"""

from fastapi import FastAPI, HTTPException, Request, Response, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from json import JSONDecodeError
import json
import logging
import time
import hashlib
from pathlib import Path
from collections import deque
from concurrent.futures import TimeoutError as FuturesTimeoutError
from uuid import uuid4
from collections import defaultdict
from threading import Lock
import asyncio
from threading import Thread

from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from botbuilder.schema import ConversationReference
from botframework.connector.auth import CredentialProvider, SimpleCredentialProvider

from .config import API_ROOT_PATH, API_TITLE, API_VERSION, API_HOST, API_PORT, CORS_ORIGINS, OLLAMA_MODEL
from .config import SESSION_EXPIRY_MINUTES, BOT_APP_ID, BOT_APP_PASSWORD, BOT_ID, BOT_CHANNEL_AUTH_TENANT
from .config import _canonical_microsoft_app_id
from .config import (
    ENABLE_LOGS_API,
    LOGS_API_TOKEN,
    LOGS_API_DEFAULT_LINES,
    LOGS_API_MAX_LINES,
    LOG_FILE,
)
from .schemas import ChatRequest, ChatResponse, HealthResponse, ErrorResponse
from .core.pipeline import get_pipeline
from .utils.logger import setup_logging
from .utils.text_cleaner import is_valid_input
from .services.request_queue import get_request_queue, get_rate_limiter
from .services.notifier import set_notifier
from .tickets.store import get_ticket_store
from .tickets.router import router as tickets_router
from .tickets import events as tickets_events
# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


def _canonical_aud_claim(value):
    """Normalize JWT audience (string or list) to match configured Microsoft App IDs."""
    if value is None:
        return ""
    if isinstance(value, list):
        for entry in value:
            c = _canonical_aud_claim(entry)
            if c:
                return c
        return ""
    return _canonical_microsoft_app_id(str(value))


class TeamsCredentialProvider(CredentialProvider):
    def __init__(self, app_id: str, password: str, bot_id: str):
        self.password = password
        self.trusted_app_ids = {x for x in (app_id, bot_id) if x}

    async def is_valid_appid(self, app_id: str) -> bool:
        return _canonical_aud_claim(app_id) in self.trusted_app_ids

    async def get_app_password(self, app_id: str) -> str:
        if await self.is_valid_appid(app_id):
            return self.password
        return None

    async def is_authentication_disabled(self) -> bool:
        return not self.trusted_app_ids


class ITSupportBotAdapter(BotFrameworkAdapter):
    """BotBuilder-Python sets _credential_provider to SimpleCredentialProvider and ignores settings.credential_provider."""

    def __init__(self, settings: BotFrameworkAdapterSettings):
        super().__init__(settings)
        cp = getattr(settings, "credential_provider", None)
        self._credential_provider = (
            cp
            if cp is not None
            else SimpleCredentialProvider(settings.app_id, settings.app_password)
        )


# Initialize Bot Framework adapter for Teams
bot_adapter_settings = BotFrameworkAdapterSettings(
    BOT_APP_ID,
    BOT_APP_PASSWORD,
    channel_auth_tenant=BOT_CHANNEL_AUTH_TENANT or None,
    credential_provider=TeamsCredentialProvider(BOT_APP_ID, BOT_APP_PASSWORD, BOT_ID),
)
bot_adapter = ITSupportBotAdapter(bot_adapter_settings)

# Initialize FastAPI app
app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description="IT Support ",
    root_path=API_ROOT_PATH,  # Deployment path (e.g. /itsupportai) - not added to routes
)

# No route prefix - routes stay at /health, /chat, etc.
# When reverse proxy mounts at /itsupportai, clients use /itsupportai/health, etc.
router = APIRouter()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize pipeline on startup
pipeline = None
USER_COOKIE_NAME = "it_support_user"
_METRICS_LOCK = Lock()
_REQUEST_COUNT = defaultdict(int)  # (method, path, status_code) -> int
_REQUEST_LATENCY_SUM = defaultdict(float)  # (method, path) -> seconds sum
_REQUEST_LATENCY_COUNT = defaultdict(int)  # (method, path) -> count


def _send_teams_proactive_message(conversation_key: str, text: str) -> None:
    """Send `text` to whichever Teams conversation last saved this conversation_key.

    Registered with app.services.notifier at startup so ticket-status code
    (app/tickets/router.py) can notify a user without importing Bot Framework
    types directly.
    """
    raw = get_ticket_store().get_conversation_reference(conversation_key)
    if not raw:
        logger.warning("No stored conversation reference for %s; cannot send proactive message", conversation_key)
        return

    reference = ConversationReference().deserialize(json.loads(raw))

    async def _send(tc: TurnContext):
        await tc.send_activity(text)

    try:
        asyncio.run(bot_adapter.continue_conversation(reference, _send, BOT_APP_ID))
    except Exception as exc:
        logger.error("Proactive ticket notification failed for %s: %s", conversation_key, exc, exc_info=True)


@app.on_event("startup")
async def startup_event():
    """Initialize system on startup"""
    global pipeline

    logger.info("Starting IT Support Assistant...")

    # Ticket events (SSE) are published from a background worker thread when
    # tickets are created via chat/Teams, not just from this event loop's own
    # request handlers - register the loop so publish() can hop threads safely.
    tickets_events.set_loop(asyncio.get_running_loop())

    try:
        pipeline = get_pipeline()
        health = pipeline.check_health()

        # Configure request queue processor (serialized LLM work by default)
        request_queue = get_request_queue()
        request_queue.set_processor(lambda data: pipeline.process(**data))

        # Wire the ticket-status -> proactive Teams message hook.
        set_notifier(_send_teams_proactive_message)

        logger.info(f"Health check: {health}")
        
        if not health.get("ollama_available"):
            logger.warning("Ollama not available - system will use KB fallback")
        
        logger.info("System ready for requests")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down IT Support Assistant...")


# ==================== ENDPOINTS ====================

@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    duration = max(0.0, time.time() - started)
    path = request.url.path
    with _METRICS_LOCK:
        _REQUEST_COUNT[(request.method, path, str(response.status_code))] += 1
        _REQUEST_LATENCY_SUM[(request.method, path)] += duration
        _REQUEST_LATENCY_COUNT[(request.method, path)] += 1
    return response

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    
    if not pipeline:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    health = pipeline.check_health()
    
    return HealthResponse(
        status="healthy" if all(health.values()) else "degraded",
        ollama_available=health.get("ollama_available", False),
        model=OLLAMA_MODEL
    )


@router.get("/ready")
async def readiness_check():
    """Readiness endpoint for orchestration systems.

    The API is considered ready if the KB and classifier are available. Ollama
    may be unavailable (the pipeline will fall back to KB-only responses).
    """
    if not pipeline:
        raise HTTPException(status_code=503, detail="System not initialized")
    health = pipeline.check_health()
    if not (health.get("kb_loaded") and health.get("classifier_ready")):
        raise HTTPException(status_code=503, detail="System not ready")
    return {"status": "ready", "ollama_available": bool(health.get("ollama_available"))}


@router.get("/metrics")
async def metrics():
    """Prometheus-style metrics endpoint (no extra dependencies)."""
    lines = [
        "# HELP it_support_http_requests_total Total HTTP requests",
        "# TYPE it_support_http_requests_total counter",
    ]

    with _METRICS_LOCK:
        for (method, path, status_code), count in sorted(_REQUEST_COUNT.items()):
            lines.append(
                f'it_support_http_requests_total{{method="{method}",path="{path}",status_code="{status_code}"}} {count}'
            )

        lines.extend(
            [
                "# HELP it_support_http_request_duration_seconds HTTP request latency in seconds",
                "# TYPE it_support_http_request_duration_seconds summary",
            ]
        )

        for (method, path), total in sorted(_REQUEST_LATENCY_SUM.items()):
            count = _REQUEST_LATENCY_COUNT.get((method, path), 0)
            lines.append(
                f'it_support_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.6f}'
            )
            lines.append(
                f'it_support_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {count}'
            )

    content = "\n".join(lines) + "\n"
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/stats")
async def stats():
    """Operational stats for queue/caching throughput debugging."""
    request_queue = get_request_queue()
    return JSONResponse(
        status_code=200,
        content={
            "ollama_model": OLLAMA_MODEL,
            "queue": request_queue.get_stats(),
        },
    )


def _logs_api_enabled_or_404():
    if not ENABLE_LOGS_API:
        raise HTTPException(status_code=404, detail="Logs endpoint is disabled")


def _authorize_logs_request(request: Request) -> None:
    """Authorize logs access via shared token, or allow localhost when no token is set."""
    # If a token is configured, require it.
    if LOGS_API_TOKEN:
        token = (request.headers.get("x-logs-token") or request.query_params.get("token") or "").strip()
        if token != LOGS_API_TOKEN:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized (send x-logs-token header or ?token=... query param)",
            )
        return

    # If no token is configured, only allow local access.
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client is not None else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Forbidden (local access only)")


def _tail_file(path: Path, lines: int) -> str:
    # Simple portable tail implementation (read all lines, keep last N).
    # Log files are rotated; we keep bounded output by limiting lines.
    dq: deque[str] = deque(maxlen=max(1, int(lines)))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            dq.append(ln.rstrip("\n"))
    return "\n".join(dq) + ("\n" if dq else "")


@router.get("/logs/tail")
async def logs_tail(request: Request, lines: int = LOGS_API_DEFAULT_LINES):
    """Return the last N lines from the current log file (guarded)."""
    _logs_api_enabled_or_404()
    _authorize_logs_request(request)

    safe_lines = max(1, min(int(lines), int(LOGS_API_MAX_LINES)))
    path = Path(LOG_FILE)
    if not path.is_absolute():
        # Match how the logger creates the path when running from the project root.
        path = (Path(__file__).resolve().parents[1] / path).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    try:
        content = _tail_file(path, safe_lines)
        return PlainTextResponse(content=content, status_code=200)
    except Exception as exc:
        logger.error("Failed to read logs: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read logs")


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, http_response: Response):
    """
    Main chat endpoint for IT support
    
    Processes user issue through:
    1. Text cleaning
    2. Category classification
    3. KB lookup
    4. LLM response generation
    
    Includes:
    - Rate limiting per user
    - Request queueing for high traffic
    - Response caching
    """
    
    if not pipeline:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    user_input = request.message.strip()

    # Swagger UI often uses "string" as the default example value; treat it as unset to avoid
    # accidentally reusing a shared conversation across unrelated tests.
    conversation_id = (request.conversation_id or "").strip()
    if not conversation_id or conversation_id.lower() == "string":
        conversation_id = None
    
    # Validate input
    if not is_valid_input(user_input):
        raise HTTPException(
            status_code=400,
            detail="Invalid input - please provide a clear IT issue description"
        )
    
    # User isolation: prefer explicit user_id; otherwise use a stable cookie.
    user_id = (
        request.user_id
        or http_request.headers.get("x-user-id")
        or http_request.cookies.get(USER_COOKIE_NAME)
    )
    if not user_id:
        user_id = f"anon-{uuid4()}"
        http_response.set_cookie(
            USER_COOKIE_NAME,
            user_id,
            max_age=int(SESSION_EXPIRY_MINUTES * 60),
            httponly=True,
            samesite="lax",
        )
    
    # Check rate limit
    rate_limiter = get_rate_limiter()
    if not rate_limiter.is_allowed(user_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait before sending another request."
        )
    
    logger.info(f"Incoming request from {user_id}: {user_input[:50]}...")
    
    try:
        request_queue = get_request_queue()

        payload = {
            "user_input": user_input,
            "conversation_id": conversation_id,
            "user_id": user_id,
        }

        # Optional: return immediately and let the client poll for results.
        if bool(getattr(request, "defer_response", False)):
            request_id = request_queue.submit_async(payload)
            stats = request_queue.get_stats()
            wait = stats.get("estimated_wait_seconds", 0)
            return ChatResponse(
                conversation_id=conversation_id or "",
                user_input=user_input,
                category="PENDING",
                response=(
                    "Got it — I’m checking this now. "
                    + (f"Estimated wait: ~{wait}s." if isinstance(wait, int) and wait > 0 else "")
                ).strip(),
                confidence=0.0,
                is_follow_up=False,
                request_id=request_id,
                status="pending",
            )

        # Process through the request queue to provide backpressure under load
        result = request_queue.submit(payload)
        
        if result["status"] == "error":
            logger.error(f"Pipeline error: {result.get('error')}")
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        # Return response
        logger.info(f"Response generated for category: {result['category']}")
        
        return ChatResponse(
            conversation_id=result["conversation_id"],
            user_input=result["user_input"],
            category=result["category"],
            response=result["response"],
            confidence=result.get("confidence", 0.0),
            is_follow_up=result.get("is_follow_up", False),
            status=result.get("status") or "success",
        )
        
    except HTTPException:
        raise
    except OverflowError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except FuturesTimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out while waiting in the queue")
    except Exception as e:
        logger.error(f"Endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/chat/result/{request_id}")
async def chat_result(request_id: str):
    """Poll for a deferred /chat request result."""
    request_queue = get_request_queue()
    result = request_queue.get_async_result(request_id)
    status = result.get("status")
    if status == "pending":
        return {"status": "pending"}
    if status == "error":
        raise HTTPException(status_code=404, detail=result.get("error") or "Unknown request_id")
    return result


async def _handle_teams_activity(turn_context: TurnContext):
    """Process incoming Teams activity through the IT support pipeline."""
    activity = turn_context.activity
    text = (activity.text or "").strip() if getattr(activity, "text", None) else ""

    user_id = None
    if getattr(activity, "from_property", None) is not None:
        user_id = getattr(activity.from_property, "id", None)
    user_id = user_id or f"teams-user-{uuid4()}"

    user_name = None
    if getattr(activity, "from_property", None) is not None:
        user_name = getattr(activity.from_property, "name", None) or getattr(activity.from_property, "aad_object_id", None)
    user_name = (user_name or "").strip() or None

    conversation_id = None
    if getattr(activity, "conversation", None) is not None:
        conversation_id = getattr(activity.conversation, "id", None)
    conversation_id = conversation_id or ""

    if conversation_id:
        db_conversation_id = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:12]
    else:
        db_conversation_id = str(uuid4())[:12]

    # Persist the conversation reference on every turn so a ticket status change
    # (e.g. resolved, hours later) can proactively message this user even though
    # they aren't actively chatting at that moment.
    try:
        ref = TurnContext.get_conversation_reference(activity)
        get_ticket_store().save_conversation_reference(db_conversation_id, json.dumps(ref.serialize()))
    except Exception as exc:
        logger.warning("Failed to persist conversation reference for %s: %s", db_conversation_id, exc)

    if not text:
        response_text = "I can help with IT support. Please describe your issue."
    elif not is_valid_input(text):
        response_text = "Please provide a clear IT issue description."
    else:
        rate_limiter = get_rate_limiter()
        if not rate_limiter.is_allowed(user_id):
            response_text = "Rate limit exceeded. Please wait before sending another request."
        elif not pipeline:
            response_text = "Service temporarily unavailable."
        else:
            logger.info(f"Teams message from {user_id}: {text[:50]}...")
            try:
                request_queue = get_request_queue()
                payload = {
                    "user_input": text,
                    "conversation_id": db_conversation_id,
                    "user_id": user_id,
                    "user_name": user_name,
                }

                # Always send an immediate acknowledgement to avoid "40 sec silence" in Teams,
                # then post the real answer as a proactive follow-up message.
                request_id = request_queue.submit_async(payload)
                stats = request_queue.get_stats()
                wait = stats.get("estimated_wait_seconds", 0)
                response_text = (
                    "Got it — I’m checking this now."
                    + (f" Estimated wait: ~{wait}s." if isinstance(wait, int) and wait > 0 else "")
                ).strip()

                conversation_reference: ConversationReference = TurnContext.get_conversation_reference(activity)

                def _background_send() -> None:
                    try:
                        # Poll until done (bounded).
                        deadline = time.time() + 120  # seconds
                        result = {"status": "pending"}
                        while time.time() < deadline:
                            result = request_queue.get_async_result(request_id)
                            if result.get("status") != "pending":
                                break
                            time.sleep(0.5)

                        final_text = None
                        if result.get("status") == "pending":
                            final_text = "Sorry — this is taking longer than expected. Please try again in a moment."
                        elif result.get("status") == "error":
                            final_text = "Sorry — something went wrong while I was checking that. Please contact IT Support."
                        else:
                            final_text = (result or {}).get("response") or "Sorry — I couldn't generate a response."

                        async def _send_proactive(tc: TurnContext):
                            await tc.send_activity(final_text)

                        asyncio.run(
                            bot_adapter.continue_conversation(
                                conversation_reference,
                                _send_proactive,
                                BOT_APP_ID,
                            )
                        )
                    except Exception as exc:
                        logger.error("Proactive follow-up send failed: %s", exc, exc_info=True)

                Thread(target=_background_send, name=f"teams-followup-{request_id[:8]}", daemon=True).start()

            except Exception as e:
                logger.error(f"Bot message error: {e}", exc_info=True)
                response_text = "Internal server error occurred."

    await turn_context.send_activity(response_text)


@router.post("/api/messages")
async def bot_messages(request: Request):
    """
    Microsoft Bot Framework messages endpoint.

    Uses the Bot Framework adapter to validate incoming requests and route messages
    through the IT support processing pipeline.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        auth_header = request.headers.get("authorization", "")

    try:
        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"error": "Missing Authorization header for Bot Framework endpoint."},
            )

        body = await request.json()
        activity = Activity().deserialize(body)
        invoke_response = await bot_adapter.process_activity(activity, auth_header, _handle_teams_activity)
        if invoke_response is not None:
            status_code = getattr(invoke_response, "status", 200)
            body = getattr(invoke_response, "body", None)
            if body is not None:
                return JSONResponse(status_code=status_code, content=body)
            return JSONResponse(status_code=status_code)

        return JSONResponse(status_code=200, content={"status_code": 200})
    except JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON payload for Bot Framework activity."},
        )
    except PermissionError as e:
        logger.warning("Bot adapter auth failed: %s", e)
        return JSONResponse(status_code=401, content={"error": str(e)})
    except Exception as e:
        logger.error(f"Bot adapter processing failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions"""
    if exc.status_code in {401, 403, 404}:
        logger.warning("HTTP %s: %s", exc.status_code, exc.detail)
    else:
        logger.error("HTTP %s: %s", exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
        },
    )


app.include_router(router)
app.include_router(tickets_router)




# ==================== RUN ====================

if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting server on {API_HOST}:{API_PORT}")
    
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        reload=False  # Set to True for development
    )
