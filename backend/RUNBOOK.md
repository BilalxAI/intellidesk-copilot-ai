# Runbook (Local + Production)

## Local (Windows) - SQLite

Install:

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

Run Ollama (optional, API still works with KB fallback):

```powershell
ollama pull LiquidAI/lfm2.5-350m
ollama serve
```

Start API:

```powershell
.\venv\Scripts\python.exe launch.py server
```

Endpoints:

- Docs: `http://127.0.0.1:8000/docs`
- Health: `GET /health`
- Ready: `GET /ready`
- Metrics: `GET /metrics`
- Logs (optional): `GET /logs/tail`

Set `API_ROOT_PATH=/itsupportai` to serve all routes under that prefix (e.g. `/itsupportai/docs`, `/itsupportai/health`). Leave it empty for root-path access (`/docs`, `/health`).

CLI client:

```powershell
.\venv\Scripts\python.exe client.py --interactive
```

Run tests:

```powershell
.\venv\Scripts\python.exe test_assistant.py
```

## Production (Docker Compose) - PostgreSQL

Start:

```powershell
docker compose -f docker-compose.prod.yml up --build
```

Useful environment variables:

- `POSTGRES_PASSWORD` (used by `docker-compose.prod.yml`)
- `OLLAMA_MODEL` (defaults to `LiquidAI/lfm2.5-350m`)
- `DATABASE_URL` default is `sqlite:///data/it_support.db` (for an absolute path in Linux containers, use `sqlite:////data/it_support.db`)
- `REQUEST_WORKERS` (default `1`) number of request-queue worker threads (restart required)
- `INCLUDE_RESPONSE_TIME_IN_API` (default `True`) include `total_time_ms`/`llm_time_ms` in `POST /chat`
- `INCLUDE_RESPONSE_TIME_IN_TEXT` (default `False`) append a timing footer to returned `response` text (API + Teams)
- `MAX_STEPS_FIRST_RESPONSE` (default `6`) max steps returned for a new issue response
- `MAX_STEPS_FOLLOW_UP` (default `3`) max steps returned for follow-up responses
- `RETURN_ALL_KB_STEPS` (default `False`) bypass LLM formatting and return all matched KB steps
- `NEXT_STEPS_CHUNK_SIZE` (default `3`) when user asks "what next", return the next N KB steps
- `KB_STEPS_RESPONSE_MODE` (default `True`) return KB steps directly (LLM not allowed to rewrite steps)
- `ENABLE_LOGS_API` (default `False`) enable `GET /logs/tail` (guarded)
- `LOGS_API_TOKEN` (recommended) shared secret; send as header `x-logs-token`

Verify:

- `GET http://localhost:8000/ready`
- `GET http://localhost:8000/metrics`

## Teams Bot -> Backend contract (handover)

The Teams bot service should call this backend:

- Endpoint: `POST /chat`
- Body:
  - `message`: user text (`activity.text`)
  - `conversation_id`: Teams thread id (`activity.conversation.id`)
  - `user_id`: Teams user id (prefer `activity.from.aadObjectId`, else `activity.from.id`)

The bot service posts the backend’s `response` string back to Teams as the bot reply.

If the backend is reverse-proxied under a mount path, the bot should call the mounted URL, for example `/itsupportai/chat`, while the backend route itself remains `/chat`.

## Production (Non-Docker) - PostgreSQL

Install optional dependency:

```powershell
.\venv\Scripts\pip.exe install -r requirements-prod.txt
```

Set `DATABASE_URL`:

```powershell
$env:DATABASE_URL = "postgresql://it_support:YOURPASS@localhost:5432/it_support"
```

Start API:

```powershell
.\venv\Scripts\python.exe launch.py server
```

## Production (MySQL) - schema managed by DevOps

1) Ask DevOps/DBA to create tables from: `db/mysql_schema.sql`
2) Install optional dependency:

```powershell
.\venv\Scripts\pip.exe install -r requirements-prod.txt
```

3) Set `DATABASE_URL` (DevOps provides connection string):

```powershell
$env:DATABASE_URL = "mysql://USER:PASSWORD@HOST:3306/DBNAME"
```

## Maintenance checklist

- Monitor `GET /metrics` for latency and error rate.
- Review `GET /stats` for queue size and cache hit rate.
- Ensure `SESSION_EXPIRY_HOURS` is set to a sensible value for your channel (Teams vs web).
- For Postgres, ensure backups and retention are configured.
