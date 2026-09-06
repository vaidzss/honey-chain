import Link from "next/link";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The FPO / collection centre console.
 *
 * An FPO aggregates supply, so the question it needs answered is per member:
 * how much can this beekeeper still legitimately declare this season, and is
 * any of their stock at risk. Headroom is shown rather than hidden, because an
 * FPO planning procurement needs to know how much verified honey the cluster
 * can still produce.
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

export default async function FpoConsole({ params }) {
  const { actorId } = await params;
  const d = await get(`/api/fpo/${encodeURIComponent(actorId)}/console`);
  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>Console unavailable</h2>
        <p>Could not load this FPO. The API may be down.</p>
      </div>
    );
  }
  const s = d.summary;
  const usedPct = s.season_ceiling_kg
    ? (s.declared_kg / s.season_ceiling_kg) * 100
    : 0;

  return (
    <div className="wide-content">
      <nav className="consolenav">
        <Link href={`/console/fpo/${actorId}`} className="on">FPO</Link>
        <Link href="/console/admin">KVIC</Link>
        <Link href="/metrics">Model metrics</Link>
      </nav>

      <div className="bk-top">
        <div>
          <h2>{d.fpo.name}</h2>
          <p className="who">
            {d.fpo.district}, {d.fpo.state} — supply aggregation
          </p>
        </div>
      </div>

      <div className="grid4">
        <div className="metric">
          <div className="v">{s.members}</div>
          <div className="k">member beekeepers</div>
        </div>
        <div className="metric">
          <div className="v">
            {s.sentinels}/{s.hives}
          </div>
          <div className="k">instrumented hives</div>
        </div>
        <div className="metric">
          <div className="v">{s.declared_kg}</div>
          <div className="k">kg declared this season</div>
        </div>
        <div className="metric">
          <div className="v">{s.headroom_kg}</div>
          <div className="k">kg headroom remaining</div>
        </div>
      </div>

      <div className="card">
        <h3>Season capacity</h3>
        <div className="meter">
          <i style={{ width: `${Math.min(100, usedPct)}%` }} />
        </div>
        <div className="meter-labels">
          <span>{s.declared_kg} kg declared</span>
          <span>{s.season_ceiling_kg} kg verified ceiling</span>
        </div>
        <p className="hint">
          The ceiling is the sum of each member apiary&apos;s telemetry-derived
          P90. It is what the cluster can sell as verified honey this season, and
          it is enforced on chain rather than by policy.
        </p>
      </div>

      <div className="card">
        <h3>Members</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Beekeeper</th>
              <th className="num">Hives</th>
              <th className="num">Sensors</th>
              <th className="num">Ceiling kg</th>
              <th className="num">Declared kg</th>
              <th className="num">Headroom</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {d.members.map((m) => {
              const ceiling = Number(m.ceiling_kg) || 0;
              const declared = Number(m.declared_kg) || 0;
              const head = Math.max(0, ceiling - declared);
              return (
                <tr key={m.actor_id} className={m.open_alerts > 0 ? "warn" : ""}>
                  <td>
                    <Link href={`/beekeeper/${m.actor_id}`}>{m.name}</Link>
                  </td>
                  <td className="num">{m.hives}</td>
                  <td className="num">{m.sentinels}</td>
                  <td className="num">{ceiling.toFixed(0)}</td>
                  <td className="num">{declared.toFixed(0)}</td>
                  <td className="num">{head.toFixed(0)}</td>
                  <td>
                    {m.open_alerts > 0 ? (
                      <span className="pill warn">{m.open_alerts}</span>
                    ) : (
                      <span className="pill ok">clear</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Lots in custody</h3>
        {d.holding.length ? (
          <table className="tbl">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Producer</th>
                <th>Floral</th>
                <th className="num">kg</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {d.holding.map((l) => (
                <tr key={l.batch_code}>
                  <td className="mono">
                    <Link href={`/proof/${l.batch_code}`}>{l.batch_code}</Link>
                  </td>
                  <td>{l.producer ?? "—"}</td>
                  <td>{l.floral_source ?? "—"}</td>
                  <td className="num">{Number(l.current_kg ?? 0).toFixed(1)}</td>
                  <td>
                    <span className="pill mute">{l.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="hint">
            No lots currently held. In the seeded demo flow honey moves straight
            from beekeeper to processor.
          </p>
        )}
      </div>
    </div>
  );
}
