import "./globals.css";

export const metadata = {
  title: "AuraBee — verify your honey",
  description:
    "Scan-to-verify honey provenance. Hive-to-jar traceability for KVIC beekeepers.",
  // Installable: a beekeeper adds this to their home screen once and it opens
  // like an app, with no Play Store download over a 2G connection.
  manifest: "/manifest.json",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#d98c00",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">
          <header className="brand">
            <span className="mark" aria-hidden="true">◆</span>
            <div>
              <h1>AuraBee</h1>
              <p className="sub">Honey Chain · verified provenance</p>
            </div>
          </header>
          {children}
          <p className="footer">
            Hive telemetry, blockchain custody and per-jar seals.<br />
            Built for KVIC Honey Mission beekeepers.
          </p>
        </div>
      </body>
    </html>
  );
}
