import { Fragment } from "react";
import Link from "next/link";
import { fetchVerification } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The consumer verification page.
 *
 * Rendered entirely on the server. The only interactive element is the
 * scratch-off code form, which is a plain GET form and therefore works with
 * JavaScript disabled, on a two-year-old budget phone, on a bad connection.
 *
 * The hardest design constraint here is honesty. There are three distinct
 * states and they must not be blurred into a reassuring green tick:
 *
 *   verified   -- a Merkle proof checked against the on-chain root. Proven.
 *   known      -- this serial was issued, but nobody has entered the hidden
 *                 code, so we can show the journey and say nothing stronger.
 *   unknown    -- we never issued this serial. That is not the same as
 *                 "counterfeit", and the page must not say it is.
 */

const ROLE_LABEL = {
  beekeeper: "Beekeeper",
  collection_centre: "Collection centre",
  fpo: "Producer company",
  processor: "Processor",
  lab: "Laboratory",
  brand: "Brand",
  regulator: "Regulator",
};

const STEP_LABEL = {
  mint: "Harvest declared",
  transfer: "Custody transferred",
  process: "Processed",
  merge: "Blended",
  pack: "Packed",
  split: "Split",
  test: "Lab tested",
  sell: "Sold",
};

function Verdict({ data }) {
  const critical = (data.warnings || []).some((w) => w.severity === "critical");
  const warn = (data.warnings || []).some((w) => w.severity === "warn");

  if (!data.known) {
    return (
      <div className="verdict bad">
        <h2>Not in the registry</h2>
        <p>
          No jar with this code was issued on AuraBee. That does not by itself
          prove the honey is fake — it may simply be from a producer who is not
          on the platform — but nothing here can be verified.
        </p>
      </div>
    );
  }
  if (critical) {
    return (
      <div className="verdict bad">
        <h2>Something is wrong with this seal</h2>
        <p>
          This jar exists in our records, but the checks below did not pass.
          Treat it with caution and consider reporting it to the seller.
        </p>
      </div>
    );
  }
  if (data.verified) {
    return (
      <div className="verdict ok">
        <h2>Verified genuine</h2>
        <p>
          The code under the seal was checked against this batch&apos;s record on
          the blockchain. This jar is one of {data.chain ? "a" : "a"} sealed
          batch traced back to the hives below.
          {warn ? " One advisory is shown below." : ""}
        </p>
      </div>
    );
  }
  return (
    <div className="verdict idle">
      <h2>This jar was issued by AuraBee</h2>
      <p>
        Its journey is shown below. To prove <em>this particular jar</em> is
        genuine rather than a copied label, enter the code hidden under the
        scratch-off panel.
      </p>
    </div>
  );
}

function SecretForm({ serial, tried }) {
  return (
    <div className="card">
      <h3>Prove this jar</h3>
      <form className="secret" action={`/verify/${encodeURIComponent(serial)}`} method="get">
        <input
          name="code"
          placeholder="CODE UNDER SEAL"
          maxLength={12}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          aria-label="Code under the scratch-off seal"
        />
        <button type="submit">Check</button>
      </form>
      <p className="hint">
        {tried
          ? "That code did not match. The printed serial can be photographed; the hidden code cannot."
          : "Anyone can photograph a label. Only the person holding the jar can scratch off the panel."}
      </p>
    </div>
  );
}

function Origins({ origins }) {
  if (!origins?.length) return null;
  return (
    <div className="card">
      <h3>Origin</h3>
      {origins.map((o, i) => (
        <div className="origin" key={i}>
          <div className="row">
            <span className="place">
              {o.district}, {o.state}
            </span>
            <span className="pct">{o.pct.toFixed(1)}%</span>
          </div>
          <div className="who">
            {o.beekeeper}
            {o.apiary ? ` · ${o.apiary}` : ""}
          </div>
          <div className="bar">
            <i style={{ width: `${Math.max(2, o.pct)}%` }} />
          </div>
        </div>
      ))}
      {origins.length > 1 && (
        <p className="hint">
          Percentages are computed from the recorded blend, in descending order —
          the disclosure EU honey labelling rules require from June 2026.
        </p>
      )}
    </div>
  );
}

function Journey({ journey }) {
  if (!journey?.length) return null;
  return (
    <div className="card">
      <h3>Journey</h3>
      <ul className="journey">
        {journey.map((j, i) => (
          <li key={i}>
            <div className="step">{STEP_LABEL[j.step] || j.step}</div>
            <div className="detail">
              {j.kg?.toFixed(1)} kg
              {j.to && j.from !== j.to ? ` · ${j.from ?? "origin"} → ${j.to}` : j.to ? ` · ${j.to}` : ""}
              {j.loss_kg > 0 ? ` · ${j.loss_kg.toFixed(1)} kg processing loss` : ""}
            </div>
            {j.at && (
              <div className="detail">
                {new Date(j.at).toLocaleDateString("en-IN", {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </div>
            )}
            {j.tx && <div className="tx">tx {j.tx.slice(0, 26)}…</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Warnings({ warnings }) {
  if (!warnings?.length) return null;
  return (
    <div className="card">
      <h3>Checks</h3>
      {warnings.map((w, i) => (
        <div className={`flag ${w.severity}`} key={i}>
          <span className="icon" aria-hidden="true">
            {w.severity === "critical" ? "✕" : "!"}
          </span>
          <span>{w.message}</span>
        </div>
      ))}
    </div>
  );
}

const METRIC_LABEL = {
  c4_pct: ["C4 sugar", "%"],
  c3_pct: ["C3 sugar", "%"],
  moisture_pct: ["Moisture", "%"],
  hmf_mg_kg: ["HMF", "mg/kg"],
};

function Lab({ lab }) {
  if (!lab?.length) return null;
  return (
    <div className="card">
      <h3>Laboratory test</h3>
      {lab.map((r, i) => {
        const s = r.summary || {};
        const rows = Object.entries(METRIC_LABEL).filter(([k]) => s[k] != null);
        return (
          <div key={i} style={{ marginBottom: i < lab.length - 1 ? 16 : 0 }}>
            <div className="row" style={{ display: "flex", justifyContent: "space-between" }}>
              <span className="place" style={{ fontWeight: 600 }}>{r.issuer}</span>
              {s.verdict && (
                <span className="pct" style={{ color: s.verdict === "pass" ? "var(--ok)" : "var(--bad)" }}>
                  {s.verdict === "pass" ? "Passed" : "Failed"}
                </span>
              )}
            </div>
            {rows.length > 0 && (
              <dl className="kv" style={{ marginTop: 10 }}>
                {rows.map(([k, [label, unit]]) => (
                  <Fragment key={k}>
                    <dt>{label}</dt>
                    <dd>{s[k]}{unit}</dd>
                  </Fragment>
                ))}
              </dl>
            )}
            <p className="hint">
              {s.method ? `${s.method}. ` : ""}
              Tested by an independent NABL-accredited laboratory, not by the
              packer. No jar of this batch could be sealed until this passed.
            </p>
          </div>
        );
      })}
    </div>
  );
}

function BatchFacts({ data }) {
  const c = data.chain;
  if (!c && !data.batch_code) return null;
  return (
    <div className="card">
      <h3>This batch</h3>
      <dl className="kv">
        <dt>Batch</dt>
        <dd className="mono">{data.batch_code}</dd>
        {data.net_weight_g && (
          <>
            <dt>Jar size</dt>
            <dd>{data.net_weight_g} g</dd>
          </>
        )}
        {c?.minted_kg != null && (
          <>
            <dt>Batch size</dt>
            <dd>{c.minted_kg.toFixed(1)} kg</dd>
          </>
        )}
        {c?.harvest_ts ? (
          <>
            <dt>Harvested</dt>
            <dd>
              {new Date(c.harvest_ts * 1000).toLocaleDateString("en-IN", {
                month: "short",
                year: "numeric",
              })}
            </dd>
          </>
        ) : null}
        {data.scan?.count != null && (
          <>
            <dt>Times scanned</dt>
            <dd>{data.scan.count}</dd>
          </>
        )}
      </dl>
      {/* Honesty about the escape hatch: if any part of this batch was minted
          above what hive telemetry supported, the consumer is told. */}
      {c?.surplus_kg > 0 && (
        <div className="flag warn" style={{ marginTop: 12 }}>
          <span className="icon" aria-hidden="true">!</span>
          <span>
            {c.surplus_kg.toFixed(1)} kg of this batch was declared above what
            hive sensors supported, approved under regulator sign-off.
          </span>
        </div>
      )}
    </div>
  );
}

export default async function VerifyPage({ params, searchParams }) {
  const { serial } = await params;
  const sp = await searchParams;
  const code = typeof sp?.code === "string" ? sp.code.trim().toUpperCase() : undefined;

  const data = await fetchVerification(serial, { secret: code || undefined });

  if (data.error) {
    return (
      <div className="verdict warn">
        <h2>Could not check this code</h2>
        <p>
          The verification service is unreachable right now. Your jar is not
          affected — please try again in a moment.
        </p>
      </div>
    );
  }

  const triedAndFailed = Boolean(code) && data.secret_ok === false;

  return (
    <>
      <Verdict data={data} />
      <p className="mono" style={{ margin: "-4px 0 16px", color: "var(--muted)" }}>
        {serial}
      </p>

      {data.known && !data.verified && (
        <SecretForm serial={serial} tried={triedAndFailed} />
      )}

      <Warnings warnings={data.warnings} />
      <Origins origins={data.origins} />
      <Lab lab={data.lab} />
      <BatchFacts data={data} />
      <Journey journey={data.journey} />

      {data.known && (
        <p className="footer">
          <Link href="/">Check another jar</Link>
        </p>
      )}
    </>
  );
}
