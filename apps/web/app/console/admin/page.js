import Link from "next/link";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata = { title: "AuraBee — KVIC console" };

/**
 * The KVIC / regulator console.
 *
 * The headline is deliberately **scheme attribution**, not traceability.
 * KVIC has distributed 2,46,099 bee boxes and cannot currently answer "which of
 * them produced honey this season". That is the question a ministry is judged
 * on, and it is the one number here that would not exist without the rest of
 * the system.
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

export default async function AdminConsole() {
  const [d, plan] = await Promise.all([
    get("/api/admin/console"),
    get("/api/admin/testing-plan?budget_inr=8000"),
  ]);

  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>Console unavailable</h2>
        <p>The API is not reachable.</p>
      </div>
    );
  }

  const attr = d.scheme_attribution;

  return (
    <div className="wide-content">
      <nav className="consolenav">
        <Link href="/console/admin" className="on">KVIC</Link>
        <Link href="/metrics">Model metrics</Link>
      </nav>

      <div className="bk-top">
        <div>
          <h2>KVIC Honey Mission console</h2>
          <p className="who">Cluster oversight, scheme attribution and risk</p>
        </div>
      </div>

      <div className="grid4">
        <div className="metric">
          <div className="v">{d.totals.clusters}</div>
          <div className="k">clusters</div>
        </div>
        <div className="metric">
          <div className="v">{d.totals.beekeepers}</div>
          <div className="k">beekeepers</div>
        </div>
        <div className="metric">
          <div className="v">
            {d.totals.sentinels}/{d.totals.hives}
          </div>
          <div className="k">instrumented hives</div>
        </div>
        <div className="metric">
          <div className="v">{d.seals?.total_scans ?? 0}</div>
          <div className="k">consumer scans</div>
        </div>
      </div>

      {/* the number a ministry actually wants */}
      <div className="card">
        <h3>Scheme attribution — are the distributed boxes producing?</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Hive origin</th>
              <th className="num">Hives</th>
              <th className="num">Instrumented</th>
              <th className="num">Apiaries</th>
            </tr>
          </thead>
          <tbody>
            {attr.by_source.map((a) => (
              <tr key={a.source}>
                <td>
                  {a.source === "kvic_distributed" ? (
                    <strong>KVIC distributed</strong>
                  ) : (
                    a.source
                  )}
                </td>
                <td className="num">{a.hives}</td>
                <td className="num">{a.sentinels}</td>
                <td className="num">{a.apiaries}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">
          {attr.kvic_distributed_hives} of {d.totals.hives} monitored hives came
          from KVIC distribution ({(attr.share_of_monitored_hives * 100).toFixed(0)}%).
          {" "}{attr.note}
        </p>
      </div>

      <div className="grid2">
        <div className="card">
          <h3>Open hive alerts</h3>
          {d.hive_alerts_open.length ? (
            <table className="tbl">
              <tbody>
                {d.hive_alerts_open.map((a, i) => (
                  <tr key={i} className={a.severity === "critical" ? "bad" : "warn"}>
                    <td>{a.event_type}</td>
                    <td>
                      <span className={`pill ${a.severity === "critical" ? "bad" : "warn"}`}>
                        {a.severity}
                      </span>
                    </td>
                    <td className="num">{a.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="hint">No open alerts.</p>
          )}
        </div>

        <div className="card">
          <h3>Open fraud cases</h3>
          {d.fraud_cases_open.length ? (
            <table className="tbl">
              <tbody>
                {d.fraud_cases_open.map((f, i) => (
                  <tr key={i}>
                    <td>{f.kind}</td>
                    <td className="num">{f.n}</td>
                    <td className="num">{f.avg_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="hint">No open cases.</p>
          )}
        </div>
      </div>

      {/* the practical answer to "who pays for testing" */}
      {plan && !plan.error && (
        <div className="card">
          <h3>Laboratory testing plan — ₹{plan.budget_inr.toLocaleString()} budget</h3>
          <p className="hint" style={{ marginTop: 0 }}>
            Nobody can afford to test every batch, so the risk model decides
            which ones are worth it. ₹{plan.spent_inr.toLocaleString()} allocated
            across {plan.tested} of {plan.batches} batches
            {plan.downgraded_for_budget > 0 &&
              `, ${plan.downgraded_for_budget} downgraded to field screen for budget`}
            .
          </p>
          <table className="tbl">
            <thead>
              <tr>
                <th>Batch</th>
                <th className="num">Risk</th>
                <th>Test tier</th>
                <th className="num">₹</th>
                <th>Top reason</th>
              </tr>
            </thead>
            <tbody>
              {plan.plan.slice(0, 8).map((p) => (
                <tr key={p.batch_code} className={p.score > 0.55 ? "warn" : ""}>
                  <td className="mono">{p.batch_code}</td>
                  <td className="num">{p.score.toFixed(3)}</td>
                  <td>
                    <span className={`pill ${p.cost ? "warn" : "mute"}`}>{p.tier}</span>
                  </td>
                  <td className="num">{p.cost || "—"}</td>
                  <td style={{ fontSize: 12, color: "var(--muted)" }}>
                    {p.reasons?.[0]?.slice(0, 70)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <h3>Clusters</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>District</th>
              <th className="num">Beekeepers</th>
              <th className="num">Hives</th>
              <th className="num">Sensors</th>
            </tr>
          </thead>
          <tbody>
            {d.clusters.map((c) => (
              <tr key={c.code}>
                <td className="mono">{c.code}</td>
                <td>{c.name}</td>
                <td>
                  {c.district}, {c.state}
                </td>
                <td className="num">{c.beekeepers}</td>
                <td className="num">{c.hives}</td>
                <td className="num">{c.sentinels}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
