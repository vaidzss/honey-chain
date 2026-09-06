import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

/**
 * Landing page. In the field nobody arrives here -- the QR on the jar points
 * straight at /verify/<serial>. This exists for the case where the code is
 * printed but the QR is damaged, scratched, or the camera will not focus, which
 * on a honey jar in a village shop is not a rare event.
 */

async function lookup(formData) {
  "use server";
  const serial = String(formData.get("serial") || "").trim().toUpperCase();
  if (!serial) return;
  redirect(`/verify/${encodeURIComponent(serial)}`);
}

export default function Home() {
  return (
    <>
      <div className="verdict idle">
        <h2>Check a jar of honey</h2>
        <p>
          Scan the QR code on the label, or type the serial printed beside it to
          see where this honey came from and whether it can be verified.
        </p>
      </div>

      <div className="card">
        <h3>Serial number</h3>
        <form className="secret" action={lookup}>
          <input
            name="serial"
            placeholder="AB-XXXXXXXX-00000"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            aria-label="Jar serial number"
          />
          <button type="submit">Look up</button>
        </form>
        <p className="hint">
          The serial is printed next to the QR code. The separate code hidden
          under the scratch-off panel proves you are holding the jar.
        </p>
      </div>

      <div className="card">
        <h3>What gets checked</h3>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.7 }}>
          <li>Which apiaries the honey came from, and in what proportion</li>
          <li>Every custody handover from hive to jar, recorded on a blockchain</li>
          <li>
            Whether the declared harvest was consistent with what hive sensors
            actually measured
          </li>
          <li>Whether this seal has been scanned somewhere it should not have been</li>
        </ul>
      </div>
    </>
  );
}
