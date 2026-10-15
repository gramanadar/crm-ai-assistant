"""In-process CRM used when no external sandbox URL is configured."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SandboxStore:
    records: dict[str, dict] = field(default_factory=dict)
    writes: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.records:
            self.records = {
                "cnt_ada": {
                    "id": "cnt_ada",
                    "email": "ada@example.com",
                    "name": "Ada Lovelace",
                    "case_title": "Onboarding",
                },
                "cnt_grace": {
                    "id": "cnt_grace",
                    "email": "grace@example.com",
                    "name": "Grace Hopper",
                    "case_title": "Compiler case",
                },
            }


class SandboxTransport:
    def __init__(self, store: SandboxStore, allow_write: bool, token: str = ""):
        self.store = store
        self.allow_write = allow_write
        self.token = token
        self.calls: list[dict] = []

    def request(self, method: str, path: str, *, json_body: dict | None = None, headers: dict | None = None) -> dict:
        self.calls.append({"method": method, "path": path, "headers": headers or {}, "body": json_body})
        if method != "GET" and not self.allow_write:
            raise PermissionError("read-only CRM credential cannot write")
        if method == "GET" and path.startswith("/records"):
            email = (json_body or {}).get("email")
            record_id = (json_body or {}).get("record_id")
            external_id = (json_body or {}).get("external_id")
            if external_id:
                found = self.store.writes.get(external_id)
                if not found:
                    return {"status_code": 404, "body": {"error": "not_found"}}
                return {"status_code": 200, "body": found}
            match = None
            if record_id:
                match = self.store.records.get(record_id)
            elif email:
                match = next((row for row in self.store.records.values() if row["email"].lower() == email.lower()), None)
            if not match:
                return {"status_code": 404, "body": {"error": "not_found"}}
            return {"status_code": 200, "body": match}
        if method == "POST" and path == "/writes":
            external_id = (json_body or {}).get("external_id")
            if external_id and external_id in self.store.writes:
                return {"status_code": 200, "body": self.store.writes[external_id]}
            result_id = f"crm_{len(self.store.writes) + 1}"
            saved = {"id": result_id, "external_id": external_id, "payload": json_body}
            if external_id:
                self.store.writes[external_id] = saved
            return {"status_code": 201, "body": saved}
        return {"status_code": 400, "body": {"error": "bad_request"}}
