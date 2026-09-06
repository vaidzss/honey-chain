import Link from "next/link";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The processor console.
 *
 * A processor does not primarily need to know what it owns — it needs to know
 * what is blocking each batch. A lot that cannot be sealed is money sitting
 * still, and the commonest blocker is a missing laboratory result, which the
 * contract enforces. Better to say so here than to let them discover it when
 * the seal transaction reverts.
 */

async function get(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!res.ok) return { error: `api ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

const PILL = { packed: "ok", "ready to pack": "warn", "awaiting lab": "bad" };

export default async function ProcessorConsole({ params }) {
  const { actorId } = await params;
  const d = await get(`/api/processor/${encodeURIComponent(actorId)}/console`);
  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>Console unavailable</h2>
        <p>Could not load this processor.</p>
      </div>
    );
  }
  const s = d.summary;

  return (
    <div className="wide-content">
      <nav className="consolenav">
        <Link href={`/console/processor/${actorId}`} className="on">Processor</Link>
        <Link href="/console/admin">KVIC</Link>
        <Link href="/metrics">Model metrics</Link>
      </nav>

      <div className="bk-top">
        <div>
          <h2>{d.processor.name}</h2>
          <p className="who">Processing and packing worklist</p>
        </div>
      </div>

      <div className="grid4">
        <div className="metric">
          <div className="v">{s.kg_in_custody}</div>
          <div className="k">kg in custody</div>
        </div>
        <div className="metric">
          <div className="v">{s.awaiting_lab}</div>
          <div className="k">awaiting lab</div>
        </div>
        <div className="metric">
          <div className="v">{s.ready_to_pack}</div>
          <div className="k">ready to pack</div>
        </div>
        <div className="metric">
          <div className="v">{s.jars_issued.toLocaleString()}</div>
          <div className="k">jars issued</div>
        </div>
      </div>

      {s.awaiting_lab > 0 && (
        <div className="alert critical" style={{ marginBottom: 14 }}>
          <div className="hive">
            {s.awaiting_lab} batches blocked on laboratory results
          </div>
          <div className="what">
            The contract will not issue seals for a batch without a passing
            report from an accredited laboratory. Send samples before packing.
          </div>
        </div>
      )}

      <div className="card">
        <h3>Batches</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Batch</th>
              <th>Kind</th>
              <th className="num">kg</th>
              <th>Lab</th>
              <th className="num">Jars</th>
              <th>Next action</th>
              <th>Proof</th>
            </tr>
          </thead>
          <tbody>
            {d.batches.map((b) => (
              <tr
                key={b.batch_code}
                className={
                  b.next_action === "awaiting lab"
                    ? "bad"
                    : b.next_action === "ready to pack"
                    ? "warn"
                    : ""
                }
              >
                <td className="mono">{b.batch_code}</td>
                <td>{b.kind}</td>
                <td className="num">{Number(b.current_kg ?? 0).toFixed(1)}</td>
                <td>
                  {b.lab_passed ? (
                    <span className="pill ok">passed</span>
                  ) : (
                    <span className="pill bad">none</span>
                  )}
                </td>
                <td className="num">{b.jar_count || "—"}</td>
                <td>
                  <span className={`pill ${PILL[b.next_action] ?? "mute"}`}>
                    {b.next_action}
                  </span>
                </td>
                <td>
                  <Link href={`/proof/${b.batch_code}`}>view</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">
          Mass conservation is enforced on chain: what leaves a batch can never
          exceed what entered it, and jars issued cannot exceed the honey that
          exists. Every row links to its proof.
        </p>
      </div>
    </div>
  );
}
