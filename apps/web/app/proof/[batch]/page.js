import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The ledger proof page.
 *
 * The point of this screen is that it is useful to someone who does not believe
 * us. Each claim shows its arithmetic with both sides visible, names the
 * contract function that enforces it, and gives the transaction hash. Nothing
 * says "verified" without showing the sum.
 *
 * A dashboard that renders a green tick is not a proof — it is our assertion
 * with better typography.
 */

async function getProof(batch, serial) {
  const qs = serial ? `?serial=${encodeURIComponent(serial)}` : "";
  try {
    const res = await fetch(
      `${API_BASE}/api/batch/${encodeURIComponent(batch)}/proof${qs}`,
      { cache: "no-store" }
    );
    if (!res.ok) return { error: `api ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

export default async function Proof({ params, searchParams }) {
  const { batch } = await params;
  const sp = await searchParams;
  const d = await getProof(batch, sp?.serial);

  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>Cannot load proof</h2>
        <p>
          {d.error === "unreachable"
            ? "The API is not reachable."
            : `No proof available for ${batch}. It may not be on chain.`}
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="bk-top">
        <div>
          <h2>Ledger proof</h2>
          <p className="who mono">{d.batch_code}</p>
        </div>
        <span className={`pill ${d.all_claims_hold ? "ok" : "bad"}`}>
          {d.all_claims_hold ? "all claims hold" : "a claim is broken"}
        </span>
      </div>

      {d.origin?.district && (
        <div className="card">
          <h3>Origin</h3>
          <dl className="kv">
            <dt>Apiary</dt>
            <dd>{d.origin.apiary}</dd>
            <dt>District</dt>
            <dd>
              {d.origin.district}, {d.origin.state}
            </dd>
            {d.origin.season && (
              <>
                <dt>Season</dt>
                <dd>{d.origin.season}</dd>
              </>
            )}
            {d.gs1?.lot_gtin && (
              <>
                <dt>GTIN</dt>
                <dd className="mono">{d.gs1.lot_gtin}</dd>
              </>
            )}
          </dl>
        </div>
      )}

      <h3 style={{ margin: "18px 0 10px", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--muted)" }}>
        Claims, with their arithmetic
      </h3>
      {d.claims.map((c, i) => (
        <div className={`claim ${c.holds ? "holds" : "broken"}`} key={i}>
          <div className="title">
            <span>{c.claim}</span>
            <span className={`pill ${c.holds ? "ok" : "bad"}`}>
              {c.holds ? "holds" : "broken"}
            </span>
          </div>
          <div className="math">{c.arithmetic}</div>
          {c.how_derived && <div className="src">{c.how_derived}</div>}
          <div className="src">
            enforced by <code>{c.enforced_by}</code>
          </div>
          {c.evidence_hash && (
            <div className="hashline" style={{ marginTop: 6 }}>
              evidence {c.evidence_hash}
            </div>
          )}
          {c.seal_root && (
            <div className="hashline" style={{ marginTop: 6 }}>
              seal root {c.seal_root}
            </div>
          )}
          {c.tx && (
            <div className="hashline" style={{ marginTop: 4 }}>
              tx {c.tx}
            </div>
          )}
        </div>
      ))}

      {d.custody?.length > 0 && (
        <div className="card">
          <h3>Custody, on chain</h3>
          <table className="tbl">
            <thead>
              <tr>
                <th>Step</th>
                <th>From</th>
                <th>To</th>
                <th className="num">kg</th>
                <th className="num">loss</th>
                <th>tx</th>
              </tr>
            </thead>
            <tbody>
              {d.custody.map((c, i) => (
                <tr key={i}>
                  <td>{c.step}</td>
                  <td>{c.from ?? "—"}</td>
                  <td>{c.to ?? "—"}</td>
                  <td className="num">{c.kg.toFixed(1)}</td>
                  <td className="num">{c.loss_kg ? c.loss_kg.toFixed(1) : "—"}</td>
                  <td className="hashline">{c.tx ? c.tx.slice(0, 14) + "…" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.jar && (
        <div className="card">
          <h3>Merkle path for jar {d.jar.serial}</h3>
          <div className="hashline">leaf {d.jar.leaf}</div>
          {d.jar.merkle_path.map((p, i) => (
            <div className="hashline" key={i}>
              path[{i}] {p}
            </div>
          ))}
          <div className="hashline" style={{ marginTop: 6 }}>
            root {d.jar.root}
          </div>
          <p className="hint">
            {d.jar.note} Verify with <code>{d.jar.verify_with}</code>.
          </p>
        </div>
      )}

      <div className="card">
        <h3>Re-check this yourself</h3>
        <dl className="kv">
          <dt>Network</dt>
          <dd>
            {d.chain.network} · chain id {d.chain.chain_id}
          </dd>
        </dl>
        <div style={{ marginTop: 8 }}>
          {Object.entries(d.chain.contracts).map(([k, v]) => (
            <div key={k} className="hashline">
              {k}: {v}
            </div>
          ))}
        </div>
        <p className="hint">{d.how_to_verify_independently}</p>
      </div>
    </>
  );
}
