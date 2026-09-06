import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

/**
 * GS1 Digital Link resolver.
 *
 * A GS1 Digital Link QR encodes a real URL whose path is a GS1 grammar:
 *
 *     https://aurabee.in/01/{GTIN-14}/21/{serial}
 *
 * The value of this over a bespoke URL is that ONE code serves three readers:
 * a retail point-of-sale scanner sees a GTIN, a phone camera opens a web page,
 * and a compliance system can resolve structured product data. Retail is
 * migrating to 2D codes on exactly this basis by around 2027, so a QR that is
 * not a Digital Link is a QR that has to be reprinted.
 *
 * The GTIN is carried for the scanner and for future SKU-level lookups; the
 * serial is what identifies this jar to us, so we hand off to the verification
 * page rather than duplicating it here.
 */
export default async function DigitalLinkJar({ params, searchParams }) {
  const { serial } = await params;
  const sp = await searchParams;
  const qs = new URLSearchParams();
  if (typeof sp?.code === "string") qs.set("code", sp.code);
  redirect(`/verify/${encodeURIComponent(serial)}${qs.toString() ? `?${qs}` : ""}`);
}
