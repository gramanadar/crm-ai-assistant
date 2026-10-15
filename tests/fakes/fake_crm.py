"""CRM transport whose faults can be switched on for tests."""

from __future__ import annotations

from app.crm.client import TransportFailure


class FakeCRM:
    def __init__(self):
        self.lookup_status = 200
        self.lookup_timeouts = 0
        self.write_mode = "ok"
        self.records = {
            "cnt_ada": {
                "id": "cnt_ada",
                "email": "ada@example.com",
                "name": "Ada Lovelace",
                "case_title": "Onboarding",
            }
        }
        self.writes: dict[str, dict] = {}
        self.calls: list[dict] = []

    def request(self, method: str, path: str, *, json_body: dict | None = None, headers: dict | None = None) -> dict:
        self.calls.append({"method": method, "path": path, "body": json_body, "headers": headers or {}})
        if method == "GET":
            return self._lookup(json_body or {})
        if method == "POST":
            return self._write(json_body or {})
        return {"status_code": 400, "body": {}}

    def _lookup(self, body: dict) -> dict:
        if self.lookup_timeouts > 0:
            self.lookup_timeouts -= 1
            raise TransportFailure("timeout")
        if self.lookup_status >= 500 or self.lookup_status == 429:
            return {"status_code": self.lookup_status, "body": {}}
        external_id = body.get("external_id")
        if external_id:
            found = self.writes.get(external_id)
            if not found:
                return {"status_code": 404, "body": {}}
            return {"status_code": 200, "body": found}
        if self.lookup_status == 404:
            return {"status_code": 404, "body": {}}
        record_id = body.get("record_id")
        email = body.get("email")
        if record_id and record_id in self.records:
            return {"status_code": 200, "body": self.records[record_id]}
        if email:
            for record in self.records.values():
                if record["email"].lower() == email.lower():
                    return {"status_code": 200, "body": record}
        return {"status_code": 404, "body": {}}

    def _write(self, body: dict) -> dict:
        external_id = body.get("external_id")
        if external_id and external_id in self.writes and self.write_mode == "ok":
            return {"status_code": 200, "body": self.writes[external_id]}
        if self.write_mode == "timeout_after_save":
            saved = {"id": f"crm_{len(self.writes) + 1}", "external_id": external_id, "payload": body}
            if external_id:
                self.writes[external_id] = saved
            self.write_mode = "ok"
            raise TransportFailure("timeout")
        if self.write_mode == "timeout_before_save":
            self.write_mode = "ok"
            raise TransportFailure("timeout")
        if self.write_mode == "server_error":
            return {"status_code": 500, "body": {}}
        saved = {"id": f"crm_{len(self.writes) + 1}", "external_id": external_id, "payload": body}
        if external_id:
            self.writes[external_id] = saved
        return {"status_code": 201, "body": saved}

    @property
    def write_calls(self) -> int:
        return sum(1 for call in self.calls if call["method"] == "POST")
