"use client";

import { useEffect, useState } from "react";
import { api } from "../api";

export default function ReviewPage() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [category, setCategory] = useState("billing");
  const [text, setText] = useState("");
  const [classified, setClassified] = useState(null);

  async function load() {
    const payload = await api("/v1/requests?status=review");
    setRows(payload.data.requests);
  }
  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function decide(id, decision) {
    setError("");
    try {
      await api(`/v1/requests/${id}/review`, {
        method: "POST",
        body: JSON.stringify({ decision, category: decision === "correct" ? category : null }),
      });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function classify(event) {
    event.preventDefault();
    setError("");
    try {
      const payload = await api("/v1/requests", { method: "POST", body: JSON.stringify({ text }) });
      setClassified(payload.data);
      setText("");
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <h1>Review queue</h1>
      <p className="lede">Unclear requests stay here with the model’s reason.</p>
      <form className="card" onSubmit={classify}>
        <label htmlFor="request">New request</label>
        <textarea id="request" rows={3} value={text} onChange={(event) => setText(event.target.value)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button type="submit">Classify</button>
        </div>
      </form>
      {classified && (
        <section className="card">
          <span className={`badge ${classified.status}`}>{classified.status}</span> {classified.category || "no category"} · confidence {classified.confidence ?? "—"}
          <p>{classified.reason}</p>
        </section>
      )}
      {error && <p className="error">{error}</p>}
      {rows.length === 0 && <div className="card">The queue is empty.</div>}
      {rows.map((row) => (
        <article className="card" key={row.id}>
          <p>{row.text}</p>
          <p>
            <span className="badge review">{row.status}</span> {row.category || "no category"} · confidence {row.confidence ?? "—"}
          </p>
          <p>{row.reason || row.validation_error}</p>
          <div className="row">
            <button type="button" onClick={() => decide(row.id, "confirm")}>Confirm</button>
            <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Corrected category">
              {["billing", "technical", "account", "sales", "other"].map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
            <button type="button" className="secondary" onClick={() => decide(row.id, "correct")}>Correct</button>
          </div>
        </article>
      ))}
    </>
  );
}
