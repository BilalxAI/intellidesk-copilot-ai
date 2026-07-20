"""
Request queue and caching for high-traffic scenarios.

Handles:
- Request queueing when LLM is busy
- LRU response caching for common questions
- Rate limiting per user
"""

import logging
import time
import hashlib
from typing import Callable, Dict, Optional, Any, Tuple
from collections import deque, OrderedDict
from threading import Lock, Thread
from queue import Queue, Full
from concurrent.futures import Future
from uuid import uuid4

from app.config import (
    MAX_QUEUE_SIZE,
    QUEUE_TIMEOUT_SECONDS,
    CACHE_TTL_SECONDS,
    ENABLE_CACHING,
    REQUEST_WORKERS,
)

logger = logging.getLogger(__name__)


class RequestQueue:
    """Thread-safe request queue + response cache.

    The queue is processed by one or more worker threads that call a configured
    processor function. This provides backpressure when the LLM is slow/busy.
    """

    def __init__(self, max_size: int = MAX_QUEUE_SIZE, workers: int = 1):
        self.max_size = max_size
        self._queue: "Queue[Tuple[Future, Dict[str, Any]]]" = Queue(maxsize=max_size)
        self._processor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
        self._workers: list[Thread] = []
        self._lock = Lock()
        self._in_flight = 0
        self._processed = 0
        self._avg_process_seconds = 3.0
        
        # Cache: key -> (response, timestamp); OrderedDict for LRU eviction
        self._cache: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

        # Async jobs: request_id -> (future, created_at)
        self._jobs: Dict[str, Tuple[Future, float]] = {}
        self._jobs_ttl_seconds = 10 * 60  # 10 minutes

        for index in range(max(1, int(workers))):
            worker = Thread(target=self._worker_loop, name=f"request-queue-{index}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def set_processor(self, processor: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        """Set the function invoked by queue worker(s) for each submitted task."""
        self._processor = processor

    def submit(self, data: Dict[str, Any], timeout_seconds: int = QUEUE_TIMEOUT_SECONDS) -> Dict[str, Any]:
        """Submit a task to the queue and wait for the result (bounded by timeout)."""
        if self._processor is None:
            raise RuntimeError("RequestQueue processor is not configured")

        future: Future = Future()
        try:
            self._queue.put_nowait((future, data))
        except Full as exc:
            raise OverflowError("Request queue is full") from exc

        try:
            return future.result(timeout=timeout_seconds)
        except Exception:
            # If the request times out, cancel so workers can skip it if not started.
            future.cancel()
            raise

    def submit_async(self, data: Dict[str, Any]) -> str:
        """Submit a task to the queue and return a request id immediately."""
        if self._processor is None:
            raise RuntimeError("RequestQueue processor is not configured")

        request_id = str(uuid4())
        future: Future = Future()
        try:
            self._queue.put_nowait((future, data))
        except Full as exc:
            raise OverflowError("Request queue is full") from exc

        with self._lock:
            self._jobs[request_id] = (future, time.time())
            self._purge_expired_jobs_locked()
        return request_id

    def get_async_result(self, request_id: str) -> Dict[str, Any]:
        """Get async result if ready; otherwise return pending."""
        rid = (request_id or "").strip()
        if not rid:
            return {"status": "error", "error": "Missing request_id"}

        with self._lock:
            self._purge_expired_jobs_locked()
            entry = self._jobs.get(rid)

        if not entry:
            return {"status": "error", "error": "Unknown or expired request_id"}

        future, _created_at = entry
        if future.cancelled():
            return {"status": "error", "error": "Request was cancelled"}
        if not future.done():
            return {"status": "pending"}

        try:
            result = future.result(timeout=0)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        finally:
            # One-shot: remove after retrieval to keep memory bounded.
            with self._lock:
                self._jobs.pop(rid, None)

        if isinstance(result, dict):
            return result
        return {"status": "error", "error": "Invalid async result type"}

    def _purge_expired_jobs_locked(self) -> None:
        now = time.time()
        expired = [rid for rid, (_f, ts) in self._jobs.items() if (now - ts) > self._jobs_ttl_seconds]
        for rid in expired:
            fut, _ts = self._jobs.pop(rid, (None, 0.0))
            try:
                if fut is not None and not fut.done():
                    fut.cancel()
            except Exception:
                pass

    def _worker_loop(self) -> None:
        while True:
            future, data = self._queue.get()
            if future.cancelled():
                self._queue.task_done()
                continue

            processor = self._processor
            if processor is None:
                future.set_exception(RuntimeError("RequestQueue processor is not configured"))
                self._queue.task_done()
                continue

            started = time.time()
            with self._lock:
                self._in_flight += 1

            try:
                result = processor(data)
                if not future.cancelled():
                    future.set_result(result)
            except Exception as exc:
                if not future.cancelled():
                    future.set_exception(exc)
            finally:
                duration = max(0.0, time.time() - started)
                with self._lock:
                    self._in_flight = max(0, self._in_flight - 1)
                    self._processed += 1
                    # simple rolling average (10% new sample)
                    self._avg_process_seconds = (self._avg_process_seconds * 0.9) + (duration * 0.1)
                self._queue.task_done()

    def get_cache_key(self, user_input: str, category: str) -> str:
        """Generate cache key from input and category."""
        # Normalize input
        normalized = user_input.lower().strip()[:100]
        key_string = f"{category}:{normalized}"
        return hashlib.md5(key_string.encode()).hexdigest()

    def get_cached(self, user_input: str, category: str) -> Optional[str]:
        """Get cached response if available and not expired."""
        if not ENABLE_CACHING:
            return None
            
        cache_key = self.get_cache_key(user_input, category)
        
        with self._lock:
            if cache_key in self._cache:
                response, timestamp = self._cache[cache_key]
                age = time.time() - timestamp
                
                if age < CACHE_TTL_SECONDS:
                    logger.info(f"Cache hit for key {cache_key[:8]}...")
                    self._cache_hits += 1
                    self._cache.move_to_end(cache_key, last=True)
                    return response
                else:
                    # Expired - remove
                    self._cache.pop(cache_key, None)

            self._cache_misses += 1
                    
        return None

    def set_cached(self, user_input: str, category: str, response: str):
        """Cache a response."""
        if not ENABLE_CACHING:
            return
            
        cache_key = self.get_cache_key(user_input, category)
        
        with self._lock:
            # Evict oldest if cache is full
            if len(self._cache) >= 1000:
                oldest, _ = self._cache.popitem(last=False)
                logger.info(f"Cache evicted: {oldest[:8]}")

            self._cache[cache_key] = (response, time.time())
            self._cache.move_to_end(cache_key, last=True)
            logger.info(f"Cached response for key {cache_key[:8]}")

    def get_stats(self) -> Dict:
        """Get queue statistics."""
        with self._lock:
            queued = self._queue.qsize()
            estimated_wait = int(round(queued * self._avg_process_seconds))
            return {
                "queue_size": queued,
                "in_flight": self._in_flight,
                "processed": self._processed,
                "workers": len(self._workers),
                "estimated_wait_seconds": estimated_wait,
                "cache_size": len(self._cache),
                "cache_hit_rate": self._calculate_cache_hit_rate(),
            }

    def _calculate_cache_hit_rate(self) -> float:
        """Calculate cache hit rate (simplified)."""
        total = self._cache_hits + self._cache_misses
        if total <= 0:
            return 0.0
        return round(self._cache_hits / total, 4)


class RateLimiter:
    """Per-user rate limiting."""

    def __init__(self, max_requests_per_minute: int = 10):
        self.max_per_minute = max_requests_per_minute
        self._user_requests: Dict[str, deque] = {}
        self._lock = Lock()

    def is_allowed(self, user_id: str) -> bool:
        """Check if user is allowed to make request."""
        now = time.time()
        minute_ago = now - 60
        
        with self._lock:
            if user_id not in self._user_requests:
                self._user_requests[user_id] = deque()
            
            # Clean old requests
            user_queue = self._user_requests[user_id]
            while user_queue and user_queue[0] < minute_ago:
                user_queue.popleft()
            
            # Check limit
            if len(user_queue) >= self.max_per_minute:
                logger.warning(f"Rate limit exceeded for user {user_id}")
                return False
            
            # Add current request
            user_queue.append(now)
            return True

    def get_remaining(self, user_id: str) -> int:
        """Get remaining requests for user this minute."""
        now = time.time()
        minute_ago = now - 60
        
        with self._lock:
            if user_id not in self._user_requests:
                return self.max_per_minute
            
            user_queue = self._user_requests[user_id]
            while user_queue and user_queue[0] < minute_ago:
                user_queue.popleft()
            
            return max(0, self.max_per_minute - len(user_queue))


# Global instances
_request_queue = None
_rate_limiter = None


def get_request_queue() -> RequestQueue:
    """Get or create request queue instance."""
    global _request_queue
    if _request_queue is None:
        _request_queue = RequestQueue(workers=REQUEST_WORKERS)
    return _request_queue


def get_rate_limiter() -> RateLimiter:
    """Get or create rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        from app.config import RATE_LIMIT_PER_MINUTE
        _rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)
    return _rate_limiter
