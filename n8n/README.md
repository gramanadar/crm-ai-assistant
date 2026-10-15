# n8n workflow

Import `workflow.json` into n8n (or start the Compose `n8n` service and import it there).

1. Set the n8n credential or environment variable `N8N_SIGNING_SECRET` to the same value as the backend.
2. Point the backend `N8N_WEBHOOK_URL` at `http://n8n:5678/webhook/assistant-events`.
3. Set `N8N_INLINE=false` so the backend does not also execute the workflow itself.
4. The workflow checks the HMAC signature and timestamp, switches on `request.classified`, `action.approved`, `action.completed`, and `action.failed`, then calls back `POST /v1/actions/{id}/n8n-result` with the same signature scheme.

`workflow_logic.py` is the tested version of those branches. The exported workflow calls the same endpoints.

Category notifications default to the email addresses in `CATEGORY_CHANNELS_JSON`. Ambiguous requests go to the review channel and include the review URL.

`error-workflow.json` posts failed n8n runs to the alert channel.
