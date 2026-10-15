"use client";

import { useState } from "react";
import { api } from "./api";

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const payload = await api("/v1/ask", { method: "POST", body: JSON.stringify({ question }) });
      setResult(payload.data);
    } catch (err) {
      setResult(null);
      setError(err.message);
    }
  }

  return (
    <>
      <h1>Ask</h1>
      <p className="lede">Answers come from uploaded documents. Each claim keeps its source.</p>
      <form className="card" onSubmit={submit}>
        <label htmlFor="question">Question</label>
        <textarea id="question" rows={4} value={question} onChange={(event) => setQuestion(event.target.value)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button type="submit">Ask</button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <section className="card">
          <span className={`badge ${result.status}`}>{result.status.replace("_", " ")}</span>
          {result.answer && <p>{result.answer}</p>}
          <div>
            {result.citations.map((citation) => (
              <span className="chip" key={citation.chunk_id}>
                {citation.document_title}
                {citation.heading_path ? ` · ${citation.heading_path}` : ""}
                {citation.page_from ? ` · p. ${citation.page_from}` : ""}
                <span className="tip">{citation.text}</span>
              </span>
            ))}
          </div>
          {result.status !== "answered" && result.missing_information && <p>{result.missing_information}</p>}
        </section>
      )}
    </>
  );
}
