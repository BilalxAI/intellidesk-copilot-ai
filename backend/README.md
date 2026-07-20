# IT Support Assistant

Local-first first-tier IT support bot.

The bot receives a user issue, classifies it into an IT support intent, retrieves approved steps from `kb.json`, and uses a small local model through Ollama to format the final response.

## Active Stack

- FastAPI backend
- Ollama local runtime
- Liquid AI `LiquidAI/lfm2.5-350m` through Ollama
- `kb.json` as the source of approved support steps
- `dataset.jsonl` as classification examples and evaluation data
- SQLite conversation database, default `data/it_support.db`

LFM2.5-350M is intentionally small. The model should format and follow instructions; it should not be trusted as the knowledge source. The knowledge source is `kb.json`.

## Knowledge Base Granularity

To avoid returning irrelevant steps, `kb.json` can optionally store per-issue entries inside a category:

```json
{
  "TEAMS_ISSUE": {
    "can't join meeting": ["Check meeting link", "Try Teams web", "Check VPN/proxy"],
    "microphone not working": ["Check Teams device settings", "Check OS microphone permissions"]
  }
}
```

The backend picks the best matching issue phrase for the user’s text, then uses the LLM to format the final response (not invent new steps).

## Request Flow

```text
POST /chat
  -> clean user text
  -> classify with dataset + keyword matcher
  -> retrieve approved KB steps
  -> ask LFM2.5 through Ollama to format the response
  -> fallback to raw KB steps if Ollama is unavailable
```

## Run Locally

Install Python dependencies:

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

Install and run Ollama, then pull the model:

```powershell
ollama pull LiquidAI/lfm2.5-350m
ollama serve
```

Start the API:

```powershell
.\venv\Scripts\python.exe launch.py server
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

## Test

```powershell
.\venv\Scripts\python.exe test_assistant.py
```

Ollama can be offline during basic testing. The pipeline will fall back to KB steps.

## API

- `GET /health`
- `GET /ready`
- `POST /chat`
- `GET /metrics`
- `GET /logs/tail` (optional; disabled by default)
- `GET /docs`

If the app is deployed behind a reverse proxy at a mount path such as `/itsupportai`, set `API_ROOT_PATH=/itsupportai`. Clients then use `/itsupportai/health`, `/itsupportai/chat`, and `/itsupportai/docs`, while the FastAPI route table remains unprefixed.

Example request:

```json
{
  "message": "Teams microphone is not working"
}
```

Example response shape:

```json
{
  "conversation_id": "c3f51e8e-4dbf-4ee1-9734-30e5af0c6f45",
  "user_input": "Teams microphone is not working",
  "category": "TEAMS_ISSUE",
  "response": "1. Close and reopen Microsoft Teams...",
  "confidence": 0.95,
  "is_follow_up": false
}
```

For follow-up messages, pass the same `conversation_id`:

```json
{
  "conversation_id": "c3f51e8e-4dbf-4ee1-9734-30e5af0c6f45",
  "message": "I tried step 1 and it still crashed"
}
```

## Conversation Storage

Conversations are stored in SQLite using `DATABASE_URL`.

Default:

```text
sqlite:///data/it_support.db
```

For Microsoft Teams:

- Use the Teams conversation id as `conversation_id`.
- Use the Teams user id as `user_id`.
- If a user starts a clearly new issue in the same Teams thread, the backend switches to the new category.
- If a user says things like `failed`, `still not working`, or `I tried step 1`, the backend treats it as a follow-up.
- Guided troubleshooting mode returns one step at a time.

SQLite is fine for local/staging. For production with multiple backend instances, move the same store interface to PostgreSQL, SQL Server, or Redis-backed session storage.

To use PostgreSQL with this repo’s built-in store, install the optional dependency:

```powershell
.\venv\Scripts\pip.exe install -r requirements-prod.txt
```

## Docker

Development (SQLite):

```powershell
docker compose up --build
```

Production example (PostgreSQL):

```powershell
docker compose -f docker-compose.prod.yml up --build
```

## Client CLI

With the API running:

```powershell
.\venv\Scripts\python.exe client.py --interactive
```

## Load testing / Throughput

- Set `REQUEST_WORKERS` in `.env` to increase parallel request processing (default `1`). Restart the API after changing it.
- Run: `.\venv\Scripts\python.exe client.py --bench --seconds 15 --concurrency 20 --disable-rate-limit`
- Check queue/worker stats: `GET http://127.0.0.1:8000/stats`

## Logs access (when you don't have server access)

Preferred in production: use DevOps log streaming (App Service logs / Container logs / `kubectl logs`) rather than exposing logs over HTTP.

If you need an HTTP-based fallback for testing, enable the guarded endpoint:

- Set `ENABLE_LOGS_API=True`
- Set `LOGS_API_TOKEN=...` and call `GET /logs/tail` with header `x-logs-token`

## Production Direction

Keep the API contract stable. For production, the Ollama client can be swapped for vLLM or another hosted runtime, but the pipeline should remain KB-grounded:

```text
API -> classifier -> KB retrieval -> model formatting -> response
```

For Microsoft Teams integration, create a Teams app/bot registration, store credentials in environment variables, and have the bot handler call this backend's `/chat` endpoint.
