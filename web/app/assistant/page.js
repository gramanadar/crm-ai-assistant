"use client";

import { useState } from "react";
import { api } from "../api";

export default function AssistantPage() {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [turns, setTurns] = useState([]);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    const text = message;
    setMessage("");
    try {
      const payload = await api("/v1/agent", {
        method: "POST",
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      setConversationId(payload.data.conversation_id);
      setTurns((current) => [...current, { text, ...payload.data }]);
    } catch (err) {
      setError(err.message);
    }
  }

  async function submitDraft(id) {
    await api(`/v1/actions/${id}/submit`, { method: "POST" });
    setTurns((current) =>
      current.map((turn) => ({
        ...turn,
        drafts: (turn.drafts || []).map((draft) => (draft.id === id ? { ...draft, status: "PENDING_APPROVAL" } : draft)),
      })),
    );
  }

  return (
    <>
      <h1>Assistant</h1>
      <p className="lede">Look up a sandbox contact such as ada@example.com, search documents, or draft a note.</p>
      {turns.map((turn, index) => (
        <article className="card" key={index}>
          <p><strong>You.</strong> {turn.text}</p>
          {(turn.tool_activity || []).map((item, itemIndex) => (
            <p key={itemIndex}>{item.summary}</p>
          ))}
          <p>{turn.answer}</p>
          {(turn.drafts || []).map((draft) => (
            <div key={draft.id}>
              <p>Draft {draft.type} for {draft.target_label || "unconfirmed record"} · {draft.status}</p>
              {draft.status === "DRAFT" && draft.lookup_confirmed && (
                <button type="button" onClick={() => submitDraft(draft.id)}>Submit for approval</button>
              )}
            </div>
          ))}
        </article>
      ))}
      {error && <p className="error">{error}</p>}
      <form className="card" onSubmit={submit}>
        <label htmlFor="message">Message</label>
        <textarea id="message" rows={3} value={message} onChange={(event) => setMessage(event.target.value)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button type="submit">Send</button>
        </div>
      </form>
    </>
  );
}
