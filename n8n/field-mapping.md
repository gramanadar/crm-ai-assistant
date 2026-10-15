# CRM field mapping

Mapping lives in the backend `ActionPayload` schema. n8n does not validate fields.

| CRM field | Source | Validation |
| --- | --- | --- |
| external_id | Action idempotency key | Sent with every write. A repeat returns the original record. |
| record_id | CRM lookup snapshot | Required. Re-checked with a lookup immediately before write. |
| kind | Approver-confirmed payload | `note` or `task` |
| title | Draft, then approver edit | 1–200 characters |
| body | Draft, includes the idempotency marker | 1–4000 characters |
| due_date | Approver input for tasks | `YYYY-MM-DD` |
| priority | Approver input for tasks | `low`, `normal`, or `high` |
| customer_email | Classification `extracted.customer_email` | Optional. Used to find the record, not written by itself. |
| order_id | Classification `extracted.order_id` | Optional context for the note body. |

The payload hash covers `kind`, `record_id`, `title`, `body`, `due_date`, and `priority`. Execution refuses the write when the stored payload no longer matches that hash.
