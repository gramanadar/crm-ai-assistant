# Sandbox comparison

The fault-injection suite in `tests/test_actions_and_agent.py` passes against `tests/fakes/fake_crm.py`.

No client CRM sandbox URL or credential was provided with the repository, so the suite has not been repeated against an external sandbox. Differences to check on the first client sandbox run:

- Whether the write API accepts `external_id` or only a marker inside the note body.
- The exact not-found and rate-limit status codes.
- Whether a timed-out write that succeeded is visible on `GET /records?external_id=`.

Until that run, `CRM_BASE_URL=sandbox` is the local stand-in and the duplicate-prevention rule is the idempotency key stored as `external_id`.
