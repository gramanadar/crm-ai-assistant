# Setup

## Environment

Copy `.env.example` to `.env`. The example file contains names only. Put live keys in `.env` or the process environment, never in git.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLite for a single machine, or `postgresql+psycopg://assistant:assistant@db:5432/assistant` in Compose |
| `LLM_PROVIDER` | `fake` (default), `claude`, or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Provider credentials |
| `MODEL_ASK`, `MODEL_CLASSIFY`, `MODEL_AGENT` | Claude model per task. OpenAI equivalents are `OPENAI_MODEL_*` |
| `EMBEDDING_PROVIDER` | `local`, `openai`, or `voyage` |
| `EMBEDDING_MODEL` | Stored on every chunk. Changing it re-embeds on the next question or reindex |
| `CHUNK_TARGET_MIN_TOKENS`, `CHUNK_TARGET_MAX_TOKENS`, `CHUNK_OVERLAP_RATIO` | Chunking. Reindex after a change |
| `SIMILARITY_FLOOR`, `RETRIEVAL_TOP_K` | Retrieval |
| `CATEGORIES` | Up to five labels, comma-separated |
| `CLASSIFY_CONFIDENCE_THRESHOLD` | Requests under this confidence go to review. Default `0.75` until the client picks a point on the trade-off curve |
| `CRM_BASE_URL` | `sandbox` uses the built-in records. An `http` URL uses the client's CRM |
| `CRM_READ_TOKEN`, `CRM_WRITE_TOKEN` | Separate credentials. The agent path loads only the read token |
| `N8N_SIGNING_SECRET` | HMAC secret shared with n8n |
| `N8N_WEBHOOK_URL` | Where classified and action events are posted |
| `N8N_INLINE` | `true` runs the workflow inside the API so a laptop demo does not need n8n. Set `false` when n8n is running |
| `DAILY_TOKEN_CEILING` | `0` disables the guard |
| `PRICE_TABLE_JSON` | USD per 1M tokens, including cache read and write |
| `AUTH_TOKENS` | `role:token` pairs. Join roles with `+` |

## CRM sandbox

With `CRM_BASE_URL=sandbox` the API serves two contacts:

| Id | Email | Name |
| --- | --- | --- |
| `cnt_ada` | ada@example.com | Ada Lovelace |
| `cnt_grace` | grace@example.com | Grace Hopper |

A real CRM should expose:

- `GET /records?email=` or `?record_id=`
- `GET /records?external_id=`
- `POST /writes` with the approved payload plus `external_id`

Connect timeout is 3 seconds and read timeout is 10 seconds. There is no client sandbox credential in this repository, so `evals/reports/sandbox_comparison.md` records that the fault suite ran against the fake CRM only.

## n8n

1. Start Compose, open http://localhost:5678, and import `n8n/workflow.json` and `n8n/error-workflow.json`.
2. Set `N8N_SIGNING_SECRET` and `BACKEND_URL` (for example `http://api:8000`) in the n8n environment.
3. Set `N8N_INLINE=false` and `N8N_WEBHOOK_URL=http://n8n:5678/webhook/assistant-events`.
4. Field mapping is in `n8n/field-mapping.md`.

## First document

Sign in as the approver and upload a PDF or DOCX on the Documents screen, or:

```bash
curl -H "Authorization: Bearer dev-approver-token" \
  -F "file=@handbook.docx" \
  http://localhost:8000/v1/documents
```

The response includes pages, characters, chunk count, and warnings. The library holds ten active documents. Uploading the same bytes again returns the existing record.
