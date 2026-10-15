# Operations

## Add or reload documents

Upload on the Documents screen or `POST /v1/documents`. A file whose SHA-256 already exists is not processed again. After changing chunk size, overlap, or the embedding model, call `POST /v1/documents/{id}/reindex`. The new settings are read at reindex time.

Pages or sections with almost no extractable text are listed as warnings. Those pages are not indexed. OCR is not part of the pipeline.

A corrupt file is stored with status `failed` and an error detail. It does not count toward the ten-document cap.

## Change categories

Set `CATEGORIES` to the five agreed labels and restart the API. Update `app/classify/examples.json` with example-split rows only. Do not paste the held-out file `evals/datasets/classify_test.json` into the prompt. Routing uses `CLASSIFY_CONFIDENCE_THRESHOLD` (currently **0.75**). Pick a replacement from `tradeoff_curve` in the classification report, then set the variable. No code change is required.

`CLASSIFY_DOUBLE_CHECK=true` classifies twice and sends disagreements to review. Leave it off until the eval shows the extra tokens are worth it.

## Switch provider or model

Set `LLM_PROVIDER` to `claude` or `openai`. Set the per-task model variables. Run `python evals/rag_eval.py` and `python evals/classify_eval.py` and compare `delta_vs_previous` before keeping a cheaper classification model. Usage rows store the model that was actually called.

## Read usage

The Usage screen calls `GET /v1/usage?from=&to=&group_by=task|model|day`. Export downloads the same totals as CSV. Cost is computed when the call is stored. Editing `PRICE_TABLE_JSON` changes later estimates and does not rewrite stored token counts.

`DAILY_TOKEN_CEILING` blocks new ask, classify, and agent calls when today's tokens reach the ceiling. Approval and execution of drafts already created still run.

## Provider retention

Confirm the provider's data-retention setting against internal policy before sending production documents. Record the decision here when the client answers. Until then, use the fake provider or a non-production key.

## Failure handling

| What happened | What you should see |
| --- | --- |
| Lookup times out | One retry, then "I couldn't reach the CRM…", and no record details |
| Lookup returns 500 repeatedly | Circuit opens for 60 seconds. Later calls fail fast with "CRM unavailable" |
| Lookup returns not found | "No record found for that email." |
| Approve or execute clicked twice | One CRM record and the original `crm_result_id` |
| Write times out after the CRM saved it | Status `UNKNOWN`, then verification, then `SUCCEEDED`. No second write |
| Write times out and the record is absent | Stays `UNKNOWN` until one retry with the same idempotency key |
| n8n delivers the same event twice | Second delivery ignored |
| Payload changed after approval | Execution refused. Status returns to `PENDING_APPROVAL` |
| Invalid draft field | Validation error. Nothing is sent |
| Model output is invalid, refused, or truncated | One retry, then `not_found` or the review queue |
| Question the documents do not cover | `not_found` with an explanation |

## Roles

`user` can ask questions and use the assistant. `reviewer` can resolve the review queue. `approver` can approve, reject, edit, and execute CRM actions, and can upload documents and open Usage. In v1 the operator is the approver. A person can hold more than one role by joining them in `AUTH_TOKENS` (`reviewer+approver:token`).

## Logs

Logs are JSON with `request_id`, task, user, and timing. Document text, request bodies, and CRM payloads are omitted unless `DEBUG_LOG_BODIES=true` outside production. Secrets are redacted.
