/** @type {import('next').NextConfig} */
const path = require("path");

module.exports = {
  reactStrictMode: true,
  // There is another package-lock.json above this repo, so Next guesses the
  // wrong workspace root and warns. Pin it to the monorepo.
  outputFileTracingRoot: path.join(__dirname, "../../"),
  // The consumer page is the one that has to load on a cheap phone over 3G in
  // a shop. Every kilobyte here is a kilobyte someone waits for.
  poweredByHeader: false,
  env: {
    API_BASE: process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000",
  },
};
