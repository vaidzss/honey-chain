import Link from "next/link";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The beekeeper's home screen.
 *
 * Server-rendered, and the language toggle is a plain link with `?lang=hi`
 * rather than client-side i18n. That is not laziness — it means the page works
 * with JavaScript disabled, on a browser too old for a modern bundle, and it
 * ships nothing extra to a phone on 2G.
 *
 * Ordering is deliberate: alerts first, hives second. A beekeeper opening this
 * in a field wants to know what needs doing today, not to browse an inventory.
 */

const T = {
  en: {
    title: "My hives",
    hives: "hives",
    monitored: "with sensors",
    attention: "need attention",
    needsDoing: "Needs doing",
    allWell: "Nothing needs attention today.",
    allWellSub: "Sensors are reporting normally on your monitored hives.",
    yourHives: "Your hives",
    record: "Record a harvest",
    noSensor: "no sensor",
    unmonitored:
      "Hives without a sensor are estimated from the monitored ones nearby.",
    confidence: "confidence",
  },
  hi: {
    title: "मेरी पेटियाँ",
    hives: "पेटियाँ",
    monitored: "सेंसर वाली",
    attention: "ध्यान चाहिए",
    needsDoing: "करने के लिए",
    allWell: "आज कुछ करने की जरूरत नहीं।",
    allWellSub: "सेंसर वाली पेटियाँ सामान्य हैं।",
    yourHives: "आपकी पेटियाँ",
    record: "फसल दर्ज करें",
    noSensor: "सेंसर नहीं",
    unmonitored:
      "बिना सेंसर वाली पेटियों का अनुमान पास की सेंसर वाली पेटियों से लगाया जाता है।",
    confidence: "विश्वास",
  },
};

async function getDashboard(actorId) {
  try {
    const res = await fetch(
      `${API_BASE}/api/beekeeper/${encodeURIComponent(actorId)}/dashboard`,
      { cache: "no-store" }
    );
    if (!res.ok) return { error: `api ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

function LangToggle({ actorId, lang }) {
  return (
    <div className="lang">
      <Link href={`/beekeeper/${actorId}`} className={lang === "en" ? "on" : ""}>
        EN
      </Link>
      <Link
        href={`/beekeeper/${actorId}?lang=hi`}
        className={lang === "hi" ? "on" : ""}
      >
        हिं
      </Link>
    </div>
  );
}

export default async function BeekeeperHome({ params, searchParams }) {
  const { actorId } = await params;
  const sp = await searchParams;
  const lang = sp?.lang === "hi" ? "hi" : "en";
  const t = T[lang];

  const d = await getDashboard(actorId);

  if (d.error) {
    return (
      <div className="verdict warn">
        <h2>{lang === "hi" ? "जुड़ नहीं पाए" : "Cannot reach the server"}</h2>
        <p>
          {lang === "hi"
            ? "आपकी पेटियों की जानकारी अभी नहीं मिल पा रही। नेटवर्क आने पर दोबारा खुलेगी।"
            : "Your hive data cannot be loaded right now. It will appear when you have signal."}
        </p>
      </div>
    );
  }

  const s = d.summary;

  return (
    <>
      <div className="bk-top">
        <div>
          <h2>{t.title}</h2>
          <p className="who">
            {d.beekeeper.name} · {d.beekeeper.district}
          </p>
        </div>
        <LangToggle actorId={actorId} lang={lang} />
      </div>

      <div className="stats">
        <div className="stat">
          <b>{s.hives}</b>
          <span>{t.hives}</span>
        </div>
        <div className="stat">
          <b>{s.monitored}</b>
          <span>{t.monitored}</span>
        </div>
        <div className={`stat ${s.critical ? "bad" : s.needs_attention ? "warn" : ""}`}>
          <b>{s.needs_attention}</b>
          <span>{t.attention}</span>
        </div>
      </div>

      {/* Alerts first. This is the reason the app exists. */}
      {d.alerts.length > 0 ? (
        <>
          <div className="card" style={{ padding: 0, border: 0, background: "none" }}>
            <h3 style={{ marginBottom: 10 }}>{t.needsDoing}</h3>
          </div>
          {d.alerts.map((a) => (
            <div key={a.id} className={`alert ${a.severity}`}>
              <div className="hive">{a.label}</div>
              <div className="what">
                {lang === "hi" ? a.advice_hi || a.advice_en : a.advice_en}
              </div>
              <div className="meta">
                {t.confidence} {Math.round((a.confidence ?? 0) * 100)}%
              </div>
            </div>
          ))}
        </>
      ) : (
        <div className="verdict ok">
          <h2>{t.allWell}</h2>
          <p>{t.allWellSub}</p>
        </div>
      )}

      <div className="card">
        <h3>{t.yourHives}</h3>
        <div className="hives">
          {d.hives.map((h) => (
            <div className="hive" key={h.hive_id}>
              <div className="label">
                <span
                  className={`dot ${
                    h.has_alert ? "alert" : h.monitored ? "ok" : "none"
                  }`}
                />
                {h.label}
              </div>
              <div className="sub">
                {h.monitored
                  ? `${h.weight_kg?.toFixed(1) ?? "–"} kg`
                  : t.noSensor}
              </div>
            </div>
          ))}
        </div>
        <p className="hint">{t.unmonitored}</p>
      </div>

      <Link
        href={`/beekeeper/${actorId}/harvest${lang === "hi" ? "?lang=hi" : ""}`}
        className="big-btn"
        style={{ display: "block", textAlign: "center", textDecoration: "none" }}
      >
        {t.record}
      </Link>
    </>
  );
}
