"use client";

import { useEffect, useState } from "react";
import { API, api, currentRole } from "../api";

export default function UsagePage() {
  const [groupBy, setGroupBy] = useState("task");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");

  function query(nextGroup = groupBy, nextFrom = from, nextTo = to) {
    const params = new URLSearchParams({ group_by: nextGroup });
    if (nextFrom) params.set("from", nextFrom);
    if (nextTo) params.set("to", nextTo);
    return params.toString();
  }

  async function load(nextGroup = groupBy, nextFrom = from, nextTo = to) {
    const payload = await api(`/v1/usage?${query(nextGroup, nextFrom, nextTo)}`);
    setRows(payload.data.rows);
  }
  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  function exportCsv() {
    const token = currentRole().token;
    fetch(`${API}/v1/usage/export?${query()}`, { headers: { Authorization: `Bearer ${token}` } })
      .then((response) => response.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "usage.csv";
        link.click();
      });
  }

  return (
    <>
      <h1>Usage</h1>
      <p className="lede">Token counts and the cost stored at call time.</p>
      <div className="row">
        <label htmlFor="from">From</label>
        <input id="from" type="date" value={from} onChange={(event) => { setFrom(event.target.value); load(groupBy, event.target.value, to).catch((err) => setError(err.message)); }} />
        <label htmlFor="to">To</label>
        <input id="to" type="date" value={to} onChange={(event) => { setTo(event.target.value); load(groupBy, from, event.target.value).catch((err) => setError(err.message)); }} />
        <label htmlFor="group">Group by</label>
        <select id="group" value={groupBy} onChange={(event) => { setGroupBy(event.target.value); load(event.target.value).catch((err) => setError(err.message)); }}>
          <option value="task">task</option>
          <option value="model">model</option>
          <option value="day">day</option>
        </select>
        <button type="button" className="secondary" onClick={exportCsv}>Export</button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr><th>Key</th><th>Input</th><th>Output</th><th>Cache read</th><th>Cache write</th><th>Cost</th><th>Calls</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.key}</td>
                <td>{row.input_tokens}</td>
                <td>{row.output_tokens}</td>
                <td>{row.cache_read_tokens}</td>
                <td>{row.cache_write_tokens}</td>
                <td>{row.estimated_cost}</td>
                <td>{row.calls}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
