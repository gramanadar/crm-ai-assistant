# Internal Document & CRM Assistant

An internal assistant that answers from company documents with citations, classifies support requests, looks up CRM records, and writes to the CRM only after a person approves the exact payload.

## Run it locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data
```

For a machine without Docker, point the database at SQLite in `.env`:

```bash
DATABASE_URL=sqlite:///./data/assistant.db
```

Then start the API and the UI:

```bash
uvicorn app.main:app --reload --port 8000
```

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000. The API is http://localhost:8000 and `GET /health` returns `{"status":"ok"}`.

Sign in with a dev role:

| Role | Token |
| --- | --- |
| user | `dev-user-token` |
| reviewer | `dev-reviewer-token` |
| approver (also the operator in v1) | `dev-approver-token` |

The default `LLM_PROVIDER=fake` and `EMBEDDING_PROVIDER=local` answer from the indexed text without an API key. Set `LLM_PROVIDER=claude` or `openai` and the matching key when you want a live model. Details are in `docs/SETUP.md`.

## Run it with Docker

```bash
cp .env.example .env
docker compose up --build
```

That starts the API, PostgreSQL 16 with pgvector, n8n, and the UI. Migrations run when the API container starts.

## Tests

```bash
pytest
```

CI runs the same suite with the fake provider, so it does not need a live model key. Live evaluations are optional:

```bash
python evals/rag_eval.py
python evals/classify_eval.py
```

## Layout

`app/api`, `app/rag`, `app/classify`, `app/agent`, `app/actions`, `app/crm`, `app/llm`, and `app/db` hold the backend. `evals/`, `tests/`, `n8n/`, and `docs/` hold the evaluation sets, tests, workflow export, and operator notes.
