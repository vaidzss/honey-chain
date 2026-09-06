import Link from "next/link";
import HarvestForm from "../HarvestForm";
import { API_BASE } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The harvest declaration screen.
 *
 * This is the screen that decides whether yield-bounding reads as help or as an
 * accusation, and it is the reason the whole idea is presentable to a
 * beekeeper at all.
 *
 * A naive implementation lets someone type 800 kg and then shows a red error.
 * That tells a rural producer the system thinks they are lying. This one shows
 * the allowed amount *first*, with where it came from — how many of their hives
 * carry sensors, what those sensors measured, how much of the season is already
 * recorded. The constraint becomes information rather than a verdict.
 *
 * The honesty about coverage matters too. "Sensors on 3 of 18 hives" is a
 * weaker claim than the number alone implies, and hiding that would be the
 * beginning of overclaiming.
 */

const T = {
  en: {
    back: "Back",
    title: "Record a harvest",
    youCanRecord: "You can record up to",
    kg: "kg",
    ofSeason: "of this season recorded so far",
    expected: "Expected this season",
    measured: "Measured hives",
    ofHives: "of",
    howMuch: "How much did you harvest?",
    submit: "Record harvest",
    noEnvelope: "Not available yet",
    whyCap:
      "This limit comes from what the sensors on your hives actually measured. If you harvested more than this, tell your Madhu Mitra — a supervisor can record the extra amount.",
    submitOffline: "Record (will send later)",
    recorded: "Recorded {kg} kg.",
    queued: "No signal. {kg} kg saved on this phone and will be sent automatically.",
    synced: "{n} saved record(s) sent.",
    overLimit: "That is more than the {max} kg this apiary can record. Tell your Madhu Mitra.",
    pending: "{n} record(s) waiting to be sent.",
    offline:
      "No signal? Record it anyway. It will be saved on this phone and sent when you have network.",
  },
  hi: {
    back: "वापस",
    title: "फसल दर्ज करें",
    youCanRecord: "आप दर्ज कर सकते हैं",
    kg: "किलो",
    ofSeason: "इस मौसम में अब तक दर्ज",
    expected: "इस मौसम की उम्मीद",
    measured: "सेंसर वाली पेटियाँ",
    ofHives: "में से",
    howMuch: "आपने कितनी फसल ली?",
    submit: "फसल दर्ज करें",
    noEnvelope: "अभी उपलब्ध नहीं",
    whyCap:
      "यह सीमा आपकी पेटियों के सेंसर की माप से आती है। अगर आपने इससे ज्यादा निकाला है, तो अपने मधु मित्र को बताएं — पर्यवेक्षक अतिरिक्त मात्रा दर्ज कर सकते हैं।",
    submitOffline: "दर्ज करें (बाद में भेजेंगे)",
    recorded: "{kg} किलो दर्ज हो गया।",
    queued: "नेटवर्क नहीं। {kg} किलो फोन में सुरक्षित है, नेटवर्क आने पर भेज दिया जाएगा।",
    synced: "{n} सुरक्षित रिकॉर्ड भेज दिए गए।",
    overLimit: "यह {max} किलो की सीमा से ज्यादा है। अपने मधु मित्र को बताएं।",
    pending: "{n} रिकॉर्ड भेजे जाने बाकी हैं।",
    offline:
      "नेटवर्क नहीं है? फिर भी दर्ज करें। यह फोन में सुरक्षित रहेगा और नेटवर्क आने पर भेज दिया जाएगा।",
  },
};

async function getContext(actorId) {
  try {
    const res = await fetch(
      `${API_BASE}/api/beekeeper/${encodeURIComponent(actorId)}/harvest-context`,
      { cache: "no-store" }
    );
    if (!res.ok) return { error: `api ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

export default async function Harvest({ params, searchParams }) {
  const { actorId } = await params;
  const sp = await searchParams;
  const lang = sp?.lang === "hi" ? "hi" : "en";
  const t = T[lang];
  const suffix = lang === "hi" ? "?lang=hi" : "";

  const d = await getContext(actorId);
  const apiaries = d.apiaries ?? [];

  return (
    <>
      <div className="bk-top">
        <div>
          <h2>{t.title}</h2>
          <p className="who">
            <Link href={`/beekeeper/${actorId}${suffix}`}>&larr; {t.back}</Link>
          </p>
        </div>
      </div>

      {apiaries.map((a) => {
        if (!a.has_envelope) {
          return (
            <div className="card" key={a.apiary_id}>
              <h3>{a.apiary}</h3>
              <p className="hint">
                {lang === "hi" ? a.message_hi : a.message_en}
              </p>
            </div>
          );
        }
        const used = a.max_kg > 0 ? (a.already_declared_kg / a.max_kg) * 100 : 0;
        return (
          <div key={a.apiary_id}>
            {/* The allowed number, before any input box. */}
            <div className="envelope">
              <div className="allowed">
                {a.remaining_kg} <small>{t.kg}</small>
              </div>
              <p className="cap">{t.youCanRecord}</p>

              <div className="meter">
                <i style={{ width: `${Math.min(100, Math.max(0, used))}%` }} />
              </div>
              <div className="meter-labels">
                <span>
                  {a.already_declared_kg} {t.kg} {t.ofSeason}
                </span>
                <span>
                  {a.max_kg} {t.kg}
                </span>
              </div>

              <dl className="kv" style={{ marginTop: 16 }}>
                <dt>{t.expected}</dt>
                <dd>
                  {a.expected_kg} {t.kg}
                </dd>
                <dt>{t.measured}</dt>
                <dd>
                  {a.sentinel_count} {t.ofHives} {a.hive_count}
                </dd>
              </dl>
            </div>

            <div className="card">
              <h3>{a.apiary}</h3>
              <p style={{ marginTop: 0, fontSize: 15 }}>
                {lang === "hi" ? a.message_hi : a.message_en}
              </p>

              <HarvestForm
                apiaryId={a.apiary_id}
                apiaryName={a.apiary}
                remainingKg={a.remaining_kg}
                actorId={actorId}
                t={t}
              />

              <p className="hint">{t.whyCap}</p>
              <div className="offline-note">{t.offline}</div>
            </div>
          </div>
        );
      })}

      {apiaries.length === 0 && (
        <div className="card">
          <p className="hint">{t.noEnvelope}</p>
        </div>
      )}
    </>
  );
}
