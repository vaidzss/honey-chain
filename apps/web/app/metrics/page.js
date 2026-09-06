import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata = { title: "AuraBee — model metrics" };

/**
 * The metrics page.
 *
 * Every number here is read from the `.meta.json` each training run writes, so
 * the page cannot drift from the model actually loaded. Nothing is typed in by
 * hand.
 *
 * It shows the weak numbers as prominently as the strong ones. A metrics page
 * that only displays what went well is marketing, and the first person to ask
 * "what is your worst class?" will find it in thirty seconds anyway. Better to
 * put varroa recall on the screen ourselves and be able to explain it.
 */

async function getMetrics() {
  try {
    const res = await fetch(`${API_BASE}/api/metrics`, { cache: "no-store" });
    if (!res.ok) return { error: `api ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

function pct(x) {
  return x == null ? "–" : `${(x * 100).toFixed(1)}%`;
}

function RecallBar({ label, value }) {
  const v = value ?? 0;
  const cls = v >= 0.9 ? "good" : v >= 0.7 ? "" : "poor";
  return (
    <div className="barrow">
      <span>{label}</span>
      <span className="track">
        <i className={cls} style={{ width: `${Math.max(1, v * 100)}%` }} />
      </span>
      <span className="n">{(v * 100).toFixed(1)}%</span>
    </div>
  );
}

export default async function Metrics() {
  const d = await getMetrics();
  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>Metrics unavailable</h2>
        <p>The API is not reachable. Start it and reload.</p>
      </div>
    );
  }

  const health = d.models.colony_health ?? {};
  const yieldM = d.models.yield_forecast ?? {};
  const anomaly = d.models.anomaly ?? {};
  const varroa = d.models.varroa_counter ?? {};
  const sys = d.system ?? {};

  return (
    <>
      <div className="bk-top">
        <div>
          <h2>Model metrics</h2>
          <p className="who">
            Read from each model&apos;s training artefact — not typed by hand.
            A saved copy lives in <code>metrics/</code>.
          </p>
        </div>
      </div>

      <div className="grid4">
        <div className="metric">
          <div className="v">{(sys.telemetry_rows ?? 0).toLocaleString()}</div>
          <div className="k">telemetry rows</div>
        </div>
        <div className="metric">
          <div className="v">
            {sys.sentinel_hives}/{sys.hives}
          </div>
          <div className="k">sentinel coverage {pct(sys.sentinel_coverage)}</div>
        </div>
        <div className="metric">
          <div className="v">{sys.batches ?? 0}</div>
          <div className="k">batches on chain</div>
        </div>
        <div className="metric">
          <div className="v">{(sys.seals ?? 0).toLocaleString()}</div>
          <div className="k">jars sealed</div>
        </div>
      </div>

      {/* ---------------- colony health ---------------- */}
      <div className="card">
        <h3>Colony health classifier — {health.version ?? "not trained"}</h3>
        {health.per_class_recall ? (
          <>
            <div className="grid4" style={{ marginBottom: 14 }}>
              <div className="metric">
                <div className="v">{pct(health.test_accuracy)}</div>
                <div className="k">accuracy</div>
              </div>
              <div className="metric">
                <div className="v">{health.test_hives}</div>
                <div className="k">test hives (separate run)</div>
              </div>
              <div className="metric">
                <div className="v">
                  {(health.test_rows ?? 0).toLocaleString()}
                </div>
                <div className="k">test rows</div>
              </div>
              <div className="metric">
                <div className="v">{health.classes?.length ?? 0}</div>
                <div className="k">classes</div>
              </div>
            </div>

            <h3>Recall per class</h3>
            {Object.entries(health.per_class_recall)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => (
                <RecallBar key={k} label={k} value={v} />
              ))}

            <p className="hint">
              Accuracy is the least useful number here: the test set is ~70%
              healthy, so always predicting &quot;healthy&quot; would score 0.70.
              Recall per class is what matters. <strong>Varroa is the weak
              one</strong> — it is a three-week decline that looks like
              &quot;slightly less healthy&quot;, which is why the alerting layer
              demands 0.88 confidence before varroa is allowed to interrupt a
              beekeeper.
            </p>
            {health.caveat && <div className="caveat">{health.caveat}</div>}
          </>
        ) : (
          <p className="hint">Not trained. Run <code>python ml/train_health.py</code>.</p>
        )}
      </div>

      {/* ---------------- yield ---------------- */}
      <div className="card">
        <h3>Yield forecaster — {yieldM.version ?? "not trained"}</h3>
        {yieldM.p90_coverage != null ? (
          <>
            <div className="grid4" style={{ marginBottom: 12 }}>
              <div className="metric">
                <div className="v">{pct(yieldM.p90_coverage)}</div>
                <div className="k">P90 coverage (calibrated)</div>
              </div>
              <div className="metric">
                <div className="v">{pct(yieldM.p90_coverage_uncalibrated)}</div>
                <div className="k">before calibration</div>
              </div>
              <div className="metric">
                <div className="v">×{yieldM.calibration_factor}</div>
                <div className="k">conformal factor</div>
              </div>
              <div className="metric">
                <div className="v">{yieldM.test_windows}</div>
                <div className="k">test hive-windows</div>
              </div>
            </div>
            <p className="hint">
              The P90 becomes the <strong>on-chain mint ceiling</strong>, so this
              is the one model whose error costs a real person money. The first
              fit covered only 74.6% of actual yields — a quarter of honest
              beekeepers would have been blocked. Conformal calibration on hives
              the model never saw fixed it.
            </p>
            {yieldM.caveat && <div className="caveat">{yieldM.caveat}</div>}
          </>
        ) : (
          <p className="hint">Not trained. Run <code>python ml/train_yield.py</code>.</p>
        )}
      </div>

      {/* ---------------- anomaly ---------------- */}
      <div className="card">
        <h3>Anomaly detector — {anomaly.version ?? "not trained"}</h3>
        {anomaly.auc_by_fault ? (
          <>
            <p className="hint" style={{ marginTop: 0 }}>
              Trained on healthy hives only. It answers &quot;unlike anything
              normal&quot;, which is what catches faults the classifier has no
              class for. AUC of 0.5 is a coin flip.
            </p>
            {Object.entries(anomaly.auc_by_fault)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => (
                <RecallBar key={k} label={k} value={v} />
              ))}
            <dl className="kv" style={{ marginTop: 12 }}>
              <dt>False alarm rate</dt>
              <dd>{pct(anomaly.false_alarm_rate)}</dd>
              <dt>Fitted on</dt>
              <dd>
                {(anomaly.fit_rows_healthy_only ?? 0).toLocaleString()} healthy
                windows
              </dd>
            </dl>
            {anomaly.caveat && <div className="caveat">{anomaly.caveat}</div>}
          </>
        ) : (
          <p className="hint">Not trained. Run <code>python ml/train_anomaly.py</code>.</p>
        )}
      </div>

      {/* ---------------- varroa ---------------- */}
      <div className="card">
        <h3>Varroa counter — {varroa.version}</h3>
        <dl className="kv">
          <dt>Method</dt>
          <dd style={{ textAlign: "left" }}>{varroa.method}</dd>
          <dt>Mean error (synthetic boards)</dt>
          <dd>{pct(varroa.mean_relative_error_synthetic)}</dd>
          <dt>Refuses when</dt>
          <dd style={{ textAlign: "left" }}>
            {(varroa.refuses_when ?? []).join(", ")}
          </dd>
        </dl>
        <div className="caveat">{varroa.caveat}</div>
      </div>

      {/* ---------------- chain ---------------- */}
      <div className="card">
        <h3>Ledger</h3>
        <dl className="kv">
          <dt>Network</dt>
          <dd>
            {d.chain.network} (chain id {d.chain.chain_id}){" "}
            <span className={`pill ${d.chain.contracts_live ? "ok" : "bad"}`}>
              {d.chain.contracts_live ? "live" : "unreachable"}
            </span>
          </dd>
        </dl>
        <div style={{ marginTop: 10 }}>
          {Object.entries(d.chain.addresses ?? {}).map(([k, v]) => (
            <div key={k} className="hashline">
              {k}: {v}
            </div>
          ))}
        </div>
        <p className="hint">
          Every claim made about a batch is re-checkable from these addresses.
          Run <code>python scripts/verify_ledger.py</code> — it reads chain state
          only, so an error in our database cannot make a broken ledger look
          sound.
        </p>
      </div>

      <div className="card">
        <h3>Saved results</h3>
        <p className="hint" style={{ marginTop: 0 }}>
          This page reads the models currently loaded, which is right for a
          running system and wrong for a record. <code>python
          scripts/export_metrics.py</code> writes the same data to{" "}
          <code>metrics/</code> as committed files — a summary README, one JSON
          per model, PNG charts and the ledger verification verdict — so the
          numbers are available with the stack down and can be diffed across
          runs.
        </p>
      </div>

      <div className="caveat">
        <strong>Read every number above as simulator performance.</strong> These
        models were trained and evaluated on simulated hives. That shows they
        learned the physics the simulator encodes; it is not evidence they work
        on real bees. Most public bee acoustics are <em>Apis mellifera</em>, and{" "}
        <em>Apis cerana indica</em> is a further gap with no public dataset.
      </div>
    </>
  );
}
