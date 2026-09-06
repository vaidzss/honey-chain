"use client";

import { useEffect, useState } from "react";

/**
 * Harvest declaration with an offline queue.
 *
 * Progressive enhancement: the page renders and reads fine without this
 * component. What it adds is the ability to *record* something with no signal,
 * which is the normal case in a field.
 *
 * ## Why localStorage and not IndexedDB
 *
 * The queue holds a handful of small records — a number, a batch, a timestamp.
 * IndexedDB is the right answer for photographs and inspection logs with
 * attachments, and that is where the inspection queue will go. For this,
 * localStorage is synchronous, universally supported on the cheap Android
 * browsers this has to run on, and cannot fail asynchronously halfway through.
 *
 * ## The idempotency key is generated here, not on the server
 *
 * Each queued record carries a uuid made on the device. A phone drifting in and
 * out of signal will replay the same submission several times, and the server
 * de-duplicates on that key. Generating it server-side would defeat the point.
 */

const KEY = "aurabee.harvest.queue.v1";

function uuid() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  // older Android WebViews have getRandomValues but not randomUUID
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function readQueue() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

function writeQueue(q) {
  try {
    localStorage.setItem(KEY, JSON.stringify(q));
  } catch {
    /* storage full or blocked; the in-memory state still shows the entry */
  }
}

export default function HarvestForm({ apiaryId, apiaryName, remainingKg, actorId, t }) {
  const [kg, setKg] = useState("");
  const [queue, setQueue] = useState([]);
  const [status, setStatus] = useState(null);
  const [online, setOnline] = useState(true);

  useEffect(() => {
    setQueue(readQueue());
    setOnline(navigator.onLine);
    const on = () => {
      setOnline(true);
      flush();
    };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    flush();
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send(rec) {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/api/beekeeper/harvest`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rec),
      }
    );
    if (!res.ok) throw new Error(`api ${res.status}`);
    return res.json();
  }

  async function flush() {
    const q = readQueue();
    if (!q.length) return;
    const left = [];
    for (const rec of q) {
      try {
        await send(rec);
      } catch {
        left.push(rec); // still no signal, or the server is down: keep it
      }
    }
    writeQueue(left);
    setQueue(left);
    if (left.length < q.length) {
      setStatus({ kind: "ok", msg: t.synced.replace("{n}", q.length - left.length) });
    }
  }

  async function submit(e) {
    e.preventDefault();
    const amount = parseFloat(kg);
    if (!amount || amount <= 0) return;

    const rec = {
      client_uuid: uuid(),
      actor_id: actorId,
      apiary_id: apiaryId,
      kg: amount,
      recorded_at: new Date().toISOString(),
    };

    // Over the ceiling is refused here as well as on chain, with the reason --
    // a beekeeper should not have to wait for a transaction to revert to learn
    // the number was too large.
    if (amount > remainingKg) {
      setStatus({ kind: "bad", msg: t.overLimit.replace("{max}", remainingKg) });
      return;
    }

    try {
      await send(rec);
      setStatus({ kind: "ok", msg: t.recorded.replace("{kg}", amount) });
      setKg("");
    } catch {
      const q = [...readQueue(), rec];
      writeQueue(q);
      setQueue(q);
      setKg("");
      setStatus({ kind: "warn", msg: t.queued.replace("{kg}", amount) });
    }
  }

  return (
    <form onSubmit={submit}>
      <label
        htmlFor={`kg-${apiaryId}`}
        style={{ display: "block", fontSize: 15, margin: "16px 0 8px" }}
      >
        {t.howMuch}
      </label>
      <input
        id={`kg-${apiaryId}`}
        className="big-input"
        type="number"
        inputMode="decimal"
        min="0"
        max={remainingKg}
        step="0.5"
        value={kg}
        onChange={(e) => setKg(e.target.value)}
        placeholder={`0 – ${remainingKg} ${t.kg}`}
      />
      <button className="big-btn" type="submit" disabled={!kg}>
        {online ? t.submit : t.submitOffline}
      </button>

      {status && (
        <div
          className={`flag ${status.kind === "bad" ? "critical" : "warn"}`}
          style={{ marginTop: 12 }}
        >
          <span className="icon" aria-hidden="true">
            {status.kind === "ok" ? "✓" : status.kind === "bad" ? "✕" : "!"}
          </span>
          <span>{status.msg}</span>
        </div>
      )}

      {queue.length > 0 && (
        <div className="offline-note" style={{ marginTop: 12 }}>
          {t.pending.replace("{n}", queue.length)}
        </div>
      )}
    </form>
  );
}
