const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

/**
 * Fetch the verification payload server-side.
 *
 * `record: true` means opening the page counts as a scan -- that is deliberate.
 * The first scan is what anchors time and coarse region, and a jar being
 * scanned in two distant places is the clone signal. Making people press a
 * button first would lose exactly the signal we need.
 */
export async function fetchVerification(serial, { secret, geohash } = {}) {
  const params = new URLSearchParams();
  if (secret) params.set("secret", secret);
  if (geohash) params.set("geohash", geohash);

  const url = `${API_BASE}/api/verify/${encodeURIComponent(serial)}${
    params.toString() ? `?${params}` : ""
  }`;

  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return { error: `api returned ${res.status}`, serial };
    return await res.json();
  } catch (err) {
    // A verification page that shows a stack trace to a shopper is worse than
    // one that says plainly that it could not reach the service.
    return { error: "unreachable", serial, detail: String(err) };
  }
}

export { API_BASE };
