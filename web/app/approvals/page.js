"use client";

import { useEffect, useState } from "react";
import { api } from "../api";

const EDITABLE = new Set(["DRAFT", "PENDING_APPROVAL", "APPROVED", "FAILED", "UNKNOWN"]);

export default function ApprovalsPage() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(null);

  async function load() {
    const payload = await api("/v1/actions");
    setRows(payload.data.actions);
  }
  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function act(id, action) {
    setError("");
    try {
      await api(`/v1/actions/${id}/${action}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err.message);
      await load();
    }
  }

  function startEdit(row) {
    setEditing(row.id);
    setDraft({
      kind: row.payload.kind,
      record_id: row.payload.record_id,
      title: row.payload.title,
      body: row.payload.body,
      due_date: row.payload.due_date || "",
      priority: row.payload.priority || "",
    });
  }

  async function saveEdit(id) {
    setError("");
    const payload = {
      kind: draft.kind,
      record_id: draft.record_id,
      title: draft.title,
      body: draft.body,
      due_date: draft.due_date || null,
      priority: draft.priority || null,
    };
    try {
      await api(`/v1/actions/${id}/edit`, { method: "POST", body: JSON.stringify({ payload }) });
      setEditing(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <h1>Approvals</h1>
      <p className="lede">The fields below are the payload that will be sent.</p>
      {error && <p className="error">{error}</p>}
      {rows.length === 0 && <div className="card">No drafts yet.</div>}
      {rows.map((row) => (
        <article className="card" key={row.id}>
          <p>
            <span className={`badge ${row.status}`}>{row.status}</span> {row.type} · {row.target_label || row.crm_record_id || "no target"}
          </p>
          {editing === row.id ? (
            <div className="stack">
              <label htmlFor={`title-${row.id}`}>Title</label>
              <input id={`title-${row.id}`} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
              <label htmlFor={`body-${row.id}`}>Body</label>
              <textarea id={`body-${row.id}`} rows={4} value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} />
              {draft.kind === "task" && (
                <div className="row">
                  <label htmlFor={`due-${row.id}`}>Due</label>
                  <input id={`due-${row.id}`} type="date" value={draft.due_date} onChange={(event) => setDraft({ ...draft, due_date: event.target.value })} />
                  <label htmlFor={`priority-${row.id}`}>Priority</label>
                  <select id={`priority-${row.id}`} value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value })}>
                    <option value="low">low</option>
                    <option value="normal">normal</option>
                    <option value="high">high</option>
                  </select>
                </div>
              )}
              <div className="row">
                <button type="button" onClick={() => saveEdit(row.id)}>Save edit</button>
                <button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <pre>{JSON.stringify(row.payload, null, 2)}</pre>
          )}
          {row.last_error && <p className="error">{row.last_error}</p>}
          {row.crm_result_id && <p>CRM result {row.crm_result_id}</p>}
          <div className="row">
            {EDITABLE.has(row.status) && editing !== row.id && (
              <button type="button" className="secondary" onClick={() => startEdit(row)}>Edit</button>
            )}
            {row.status === "PENDING_APPROVAL" && (
              <>
                <button type="button" onClick={() => act(row.id, "approve")}>Approve</button>
                <button type="button" className="danger" onClick={() => act(row.id, "reject")}>Reject</button>
              </>
            )}
            {row.status === "APPROVED" && <button type="button" onClick={() => act(row.id, "execute")}>Execute</button>}
            {(row.status === "FAILED" || row.status === "UNKNOWN") && (
              <button type="button" onClick={() => act(row.id, "execute")}>Retry</button>
            )}
          </div>
        </article>
      ))}
    </>
  );
}
