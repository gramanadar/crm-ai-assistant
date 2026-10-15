"use client";

import { useEffect, useState } from "react";
import { api } from "../api";

export default function DocumentsPage() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);

  async function load() {
    const payload = await api("/v1/documents");
    setRows(payload.data.documents);
  }
  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function upload(event) {
    event.preventDefault();
    setError("");
    const body = new FormData();
    body.append("file", file);
    try {
      await api("/v1/documents", { method: "POST", body });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <h1>Documents</h1>
      <p className="lede">PDF and DOCX only. Ten active documents.</p>
      <form className="card" onSubmit={upload}>
        <input type="file" accept=".pdf,.docx" onChange={(event) => setFile(event.target.files?.[0] || null)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button type="submit">Upload</button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr><th>Title</th><th>Status</th><th>Pages</th><th>Characters</th><th>Chunks</th><th>Warnings</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.title}</td>
                <td><span className={`badge ${row.status}`}>{row.status}</span></td>
                <td>{row.report.pages}</td>
                <td>{row.report.characters}</td>
                <td>{row.report.chunks}</td>
                <td>{(row.warnings || []).join(" ") || row.error_detail || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
