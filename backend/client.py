"""
CLI client for the active IT Support Assistant API.

Usage:
    python client.py "teams call is not working"
    python client.py --interactive
    python client.py --info
    python client.py --bench --seconds 10 --concurrency 5 --message "Teams mic not working"
"""

import sys
from typing import Optional

import requests
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

import os

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


def chat(message: str, conversation_id: Optional[str] = None, user_id: Optional[str] = None) -> Optional[dict]:
    try:
        payload = {"message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if user_id:
            payload["user_id"] = user_id
        response = requests.post(f"{API_URL}/chat", json=payload, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to {API_URL}")
        print("Start the server with: python launch.py server")
    except requests.exceptions.RequestException as exc:
        print(f"Request failed: {exc}")
    return None


def health() -> Optional[dict]:
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return None


def categories() -> Optional[dict]:
    return None


def print_response(data: dict):
    print("\n" + "=" * 60)
    print("IT SUPPORT ASSISTANT")
    print("=" * 60)
    print(f"Conversation: {data.get('conversation_id')}")
    print(f"Issue: {data.get('user_input')}")
    print(f"Category: {data.get('category')}")
    print(f"Confidence: {data.get('confidence', 0):.1%}")
    print(f"Follow-up: {data.get('is_follow_up')}")
    print("\nResponse:\n")
    print(data.get("response", "No response returned."))
    print("=" * 60 + "\n")


def interactive():
    status = health()
    if not status:
        print(f"API is not responding at {API_URL}")
        return

    print("IT Support Assistant interactive mode. Type 'quit' to exit.\n")
    conversation_id = None
    while True:
        message = input("You: ").strip()
        if message.lower() in {"quit", "exit", "q"}:
            break
        if not message:
            continue
        result = chat(message, conversation_id=conversation_id)
        if result:
            conversation_id = result.get("conversation_id")
            print_response(result)


def info():
    status = health()
    if not status:
        print(f"API is not responding at {API_URL}")
        return

    print(f"Status: {status.get('status')}")
    print(f"Ollama available: {status.get('ollama_available')}")
    print(f"Model: {status.get('model')}")
    print(f"Docs: {API_URL.rstrip('/')}/docs")


def main():
    parser = argparse.ArgumentParser(add_help=True, description="IT Support Assistant client")
    parser.add_argument("message", nargs="*", help="Message to send (default: interactive)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive chat mode")
    parser.add_argument("--info", action="store_true", help="Show /health info")
    parser.add_argument("--bench", action="store_true", help="Run a simple load test against POST /chat")
    parser.add_argument("--seconds", type=int, default=10, help="Benchmark duration in seconds")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers")
    parser.add_argument("--user-prefix", default="bench-user", help="User id prefix")
    parser.add_argument("--conversation-prefix", default="bench-convo", help="Conversation id prefix")
    parser.add_argument("--warmup", type=int, default=0, help="Warmup requests per worker before measuring")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout seconds")
    parser.add_argument("--disable-rate-limit", action="store_true", help="Send unique user_ids per request")
    args = parser.parse_args()

    if args.info:
        info()
        return

    if args.interactive or (not args.bench and len(args.message) == 0):
        interactive()
        return

    if args.bench:
        status = health()
        if not status:
            print(f"API is not responding at {API_URL}")
            return

        message = " ".join(args.message).strip() or "Teams mic is not working"
        duration_seconds = max(1, int(args.seconds))
        concurrency = max(1, int(args.concurrency))

        def _post_once(worker_id: int, seq: int, measure: bool) -> tuple[bool, float, Optional[float]]:
            user_id = f"{args.user_prefix}-{worker_id}"
            conversation_id = f"{args.conversation_prefix}-{worker_id}"
            if args.disable_rate_limit:
                user_id = f"{args.user_prefix}-{worker_id}-{seq}-{int(time.time()*1000)}"
                conversation_id = f"{args.conversation_prefix}-{worker_id}"

            payload = {"message": message, "user_id": user_id, "conversation_id": conversation_id}
            started = time.perf_counter()
            try:
                r = requests.post(f"{API_URL}/chat", json=payload, timeout=args.timeout)
                ok = r.status_code == 200
                data = r.json() if ok else {}
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                server_ms = None
                if measure and isinstance(data, dict):
                    v = data.get("total_time_ms")
                    server_ms = float(v) if v is not None else None
                return ok, elapsed_ms, server_ms
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                return False, elapsed_ms, None

        # Warmup
        if args.warmup > 0:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = []
                for wid in range(concurrency):
                    for i in range(args.warmup):
                        futs.append(ex.submit(_post_once, wid, i, False))
                for _ in as_completed(futs):
                    pass

        end_at = time.perf_counter() + duration_seconds
        client_lat_ms: list[float] = []
        server_lat_ms: list[float] = []
        ok_count = 0
        err_count = 0
        sent = 0

        def _worker_loop(worker_id: int) -> list[tuple[bool, float, Optional[float]]]:
            results: list[tuple[bool, float, Optional[float]]] = []
            seq = 0
            while time.perf_counter() < end_at:
                results.append(_post_once(worker_id, seq, True))
                seq += 1
            return results

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = [ex.submit(_worker_loop, wid) for wid in range(concurrency)]
            for fut in as_completed(futs):
                batch = fut.result()
                for ok, client_ms, server_ms in batch:
                    sent += 1
                    if ok:
                        ok_count += 1
                    else:
                        err_count += 1
                    client_lat_ms.append(client_ms)
                    if server_ms is not None:
                        server_lat_ms.append(server_ms)

        elapsed = float(duration_seconds)
        rps = ok_count / elapsed

        def pct(values: list[float], p: float) -> Optional[float]:
            if not values:
                return None
            values_sorted = sorted(values)
            k = int(round((p / 100.0) * (len(values_sorted) - 1)))
            return values_sorted[max(0, min(len(values_sorted) - 1, k))]

        print("\n" + "=" * 60)
        print("BENCH RESULTS")
        print("=" * 60)
        print(f"API: {API_URL}")
        print(f"Duration: {duration_seconds}s  Concurrency: {concurrency}")
        print(f"Sent: {sent}  OK: {ok_count}  Errors: {err_count}")
        print(f"Throughput: {rps:.2f} req/s")
        if client_lat_ms:
            print(f"Client latency ms: p50={pct(client_lat_ms,50):.0f} p95={pct(client_lat_ms,95):.0f} avg={statistics.mean(client_lat_ms):.0f}")
        if server_lat_ms:
            print(f"Server time ms:    p50={pct(server_lat_ms,50):.0f} p95={pct(server_lat_ms,95):.0f} avg={statistics.mean(server_lat_ms):.0f}")
        print("=" * 60 + "\n")
        return

    # Single message mode
    result = chat(" ".join(args.message))
    if result:
        print_response(result)


if __name__ == "__main__":
    main()
