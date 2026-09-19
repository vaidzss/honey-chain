/**
 * Browser check of the running stack.
 *
 *   node scripts/e2e_check.js
 *
 * Drives the real pages with Playwright against localhost:3001, asserting the
 * things the demo actually claims: a genuine jar verifies only once its
 * scratch-off code is given, a guessed code is refused, an unknown serial is
 * neither verified nor called fake, and the proof, metrics and console pages
 * render real values.
 *
 * Exits non-zero if any assertion fails, so it can gate a release the same way
 * scripts/demo_flow.py gates the chain guarantees. Screenshots land in
 * scripts/_screens/ for eyeballing.
 */
const { chromium } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const WEB = process.env.WEB_BASE || "http://localhost:3001";
const SERIAL = process.env.DEMO_SERIAL || "AB-IT22033B-00042";
const CODE = process.env.DEMO_CODE || "SNPQ3KAV";
const GTIN = process.env.DEMO_GTIN || "08901234000335";
const BLEND = process.env.DEMO_BATCH || "AB-SIT-22033-B";
const BEEKEEPER = process.env.DEMO_BEEKEEPER || "";

const SHOTS = path.join(__dirname, "_screens");
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function record(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  const body = async () => (await page.locator("body").innerText()).replace(/\s+/g, " ");

  // 1. an issued jar, before the scratch-off code is given
  console.log("\nconsumer verification");
  await page.goto(`${WEB}/verify/${SERIAL}`, { waitUntil: "networkidle" });
  let t = await body();
  record("issued jar is found but not yet proven",
    /issued by AuraBee/i.test(t) && !/Verified genuine/i.test(t),
    t.match(/This jar was issued by AuraBee/i) ? "shows the scratch-off prompt" : "");
  await shot(page, "01-verify-unproven");

  // 2. the real code proves it
  await page.fill('input[name="code"]', CODE);
  // Wait for the form's own navigation to land. Asserting before it settles
  // let the next goto() race it, which failed intermittently.
  await Promise.all([
    page.waitForURL(/[?&]code=/, { waitUntil: "networkidle", timeout: 30000 }),
    page.keyboard.press("Enter"),
  ]);
  t = await body();
  record("correct scratch-off code verifies the jar", /Verified genuine/i.test(t));
  record("journey shows the real apiary", /Ramesh/i.test(t), "Ramesh Apiary in the journey");
  await shot(page, "02-verify-genuine");

  // 3. a photographed label carries the serial but not the code
  await page.goto(`${WEB}/verify/${SERIAL}?code=WRONGCODE`, { waitUntil: "networkidle" });
  t = await body();
  record("guessed code does not verify", !/Verified genuine/i.test(t));
  await shot(page, "03-verify-cloned");

  // 4. an unknown serial is honest about what it does and does not know
  await page.goto(`${WEB}/verify/AB-NOPE-00000`, { waitUntil: "networkidle" });
  t = await body();
  record("unknown serial is not called fake",
    /not in the registry/i.test(t) && /not on the platform/i.test(t));
  await shot(page, "04-verify-unknown");

  // 5. GS1 Digital Link resolves to the same jar
  console.log("\nstandards and proof");
  await page.goto(`${WEB}/01/${GTIN}/21/${SERIAL}`, { waitUntil: "networkidle" });
  t = await body();
  record("GS1 Digital Link URL resolves", !/404|not found/i.test(t) && t.length > 200);
  await shot(page, "05-gs1-digital-link");

  // 6. proof page shows both sides of each claim
  await page.goto(`${WEB}/proof/${BLEND}`, { waitUntil: "networkidle" });
  t = await body();
  record("proof page states the issuance claim", /bounded by/i.test(t));
  record("proof page shows mass conservation", /conserv/i.test(t));
  record("proof page names the lab gate", /lab/i.test(t));
  record("proof page lists contract addresses", /0x[0-9a-fA-F]{6}/.test(t));
  await shot(page, "06-proof");

  // 7. metrics page reads from the artefacts
  console.log("\ndashboards");
  await page.goto(`${WEB}/metrics`, { waitUntil: "networkidle" });
  t = await body();
  record("metrics page renders model results", /accuracy|recall|AUC/i.test(t));
  await shot(page, "07-metrics");

  // 8. KVIC console
  await page.goto(`${WEB}/console/admin`, { waitUntil: "networkidle" });
  t = await body();
  record("KVIC console renders cluster figures", /hive|apiar|batch/i.test(t));
  await shot(page, "08-console-admin");

  // 9. beekeeper app
  if (BEEKEEPER) {
    await page.goto(`${WEB}/beekeeper/${BEEKEEPER}`, { waitUntil: "networkidle" });
    t = await body();
    record("beekeeper app renders hives", /hive/i.test(t));
    await shot(page, "09-beekeeper");
    await page.goto(`${WEB}/beekeeper/${BEEKEEPER}?lang=hi`, { waitUntil: "networkidle" });
    t = await body();
    record("beekeeper app renders in Hindi", /[ऀ-ॿ]/.test(t),
      "Devanagari present");
    await shot(page, "10-beekeeper-hindi");
  }

  // 10. phone width, since this is what a consumer actually uses
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const p2 = await phone.newPage();
  await p2.goto(`${WEB}/verify/${SERIAL}`, { waitUntil: "networkidle" });
  const overflow = await p2.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1);
  record("verify page does not scroll sideways on a phone", !overflow);
  await p2.screenshot({ path: path.join(SHOTS, "11-verify-phone.png"), fullPage: true });

  // 11. dark mode is a full token set, not an afterthought; a token defined
  //     only in the light block renders one theme's text on the other's ground
  const dark = await browser.newContext({
    colorScheme: "dark", viewport: { width: 900, height: 1200 } });
  const p3 = await dark.newPage();
  await p3.goto(`${WEB}/verify/${SERIAL}?code=${CODE}`, { waitUntil: "networkidle" });
  const theme = await p3.evaluate(() => {
    const s = getComputedStyle(document.body);
    const lum = (c) => {
      const [r, g, b] = c.match(/\d+/g).map(Number).map((v) => {
        v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const bg = lum(s.backgroundColor), fg = lum(s.color);
    const ratio = (Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05);
    return { bgLum: bg, ratio };
  });
  record("dark mode paints a dark ground", theme.bgLum < 0.2,
    `body luminance ${theme.bgLum.toFixed(3)}`);
  record("dark mode body text stays legible", theme.ratio >= 7,
    `contrast ${theme.ratio.toFixed(1)}:1`);
  await p3.screenshot({ path: path.join(SHOTS, "12-verify-dark.png"), fullPage: true });

  record("no browser console errors", consoleErrors.length === 0,
    consoleErrors.slice(0, 2).join(" | "));

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    console.log("failed: " + failed.map((f) => f.name).join("; "));
    process.exit(1);
  }
  console.log(`screenshots in ${SHOTS}`);
})().catch((e) => { console.error(e); process.exit(1); });
