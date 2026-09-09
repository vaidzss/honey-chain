"""Fetch the real, licensed datasets named in ml/datasets/registry.json.

    python ml/fetch_datasets.py --list
    python ml/fetch_datasets.py beehive_sounds_nolasco
    python ml/fetch_datasets.py --all

## The licensing rule is enforced here, not promised in a README

Every registry entry must carry a `license`, a resolvable `doi`/`zenodo_record`
and an `attribution` string. An entry missing any of those is refused before a
single byte is downloaded. That is deliberate: "we only used licensed data" is
worth nothing as a sentence in a document and quite a lot as a check that runs.

Each dataset directory gets a `LICENSE.txt` and a `PROVENANCE.json` written
beside the files, so a copy that escapes this repo still says where it came
from and what may be done with it.

Downloads are resumable and every file is verified against the MD5 the Zenodo
API reports. A truncated download that silently trains a model is a worse
outcome than a crash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "ml" / "datasets" / "registry.json"
RAW = ROOT / "ml" / "datasets" / "raw"

REQUIRED_FIELDS = ("license", "attribution")
CHUNK = 1 << 20


def _api(record: int) -> dict:
    url = f"https://zenodo.org/api/records/{record}"
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path, expect_md5: str | None, size: int,
              attempts: int = 5) -> str:
    """Resumable download with retries. Returns 'cached', 'resumed' or 'downloaded'.

    Zenodo drops connections mid-stream often enough that a single pass is not
    good enough: the socket simply ends early and a naive reader thinks it is
    finished. So the loop checks the byte count against the size the API
    declared, resumes with a Range request, and only then verifies the MD5.
    A truncated archive that silently trains a model is a far worse outcome
    than a crash.
    """
    if dest.exists() and dest.stat().st_size == size:
        if expect_md5 is None or _md5(dest) == expect_md5:
            return "cached"
        dest.unlink()          # corrupt; start over

    started_with = dest.stat().st_size if dest.exists() else 0
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= size > 0:
            break
        mode, headers = "wb", {}
        if 0 < have < size:
            mode, headers = "ab", {"Range": f"bytes={have}-"}
        try:
            req = urllib.request.Request(url, headers=headers)
            t0, done = time.time(), have
            with urllib.request.urlopen(req, timeout=300) as r, dest.open(mode) as out:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if time.time() - t0 > 2:
                        pct = 100 * done / size if size else 0
                        print(f"\r    {dest.name[:46]:<46} {pct:5.1f}%",
                              end="", flush=True)
                        t0 = time.time()
        except Exception as exc:                       # noqa: BLE001
            print(f"\r    {dest.name[:46]:<46} attempt {attempt} failed: "
                  f"{type(exc).__name__}")
            time.sleep(min(30, 3 * attempt))
            continue

        got = dest.stat().st_size if dest.exists() else 0
        if got >= size > 0:
            break
        print(f"\r    {dest.name[:46]:<46} truncated at "
              f"{100 * got / size:5.1f}% (attempt {attempt}), resuming")
        time.sleep(min(30, 3 * attempt))

    got = dest.stat().st_size if dest.exists() else 0
    if size and got < size:
        raise SystemExit(
            f"{dest.name}: got {got} of {size} bytes after {attempts} attempts. "
            f"Zenodo may be degraded; re-run to resume from where it stopped.")
    print(f"\r    {dest.name[:46]:<46} 100.0%")

    if expect_md5 and _md5(dest) != expect_md5:
        dest.unlink()
        raise SystemExit(f"checksum mismatch for {dest.name}; deleted, re-run to retry")
    return "resumed" if started_with else "downloaded"


def fetch(name: str, spec: dict, *, dry_run: bool = False) -> None:
    missing = [f for f in REQUIRED_FIELDS if not spec.get(f)]
    if missing:
        raise SystemExit(
            f"REFUSING {name}: registry entry is missing {missing}. "
            "Every dataset must declare its licence and attribution before it "
            "is downloaded.")
    record = spec.get("zenodo_record")
    if not record:
        raise SystemExit(f"REFUSING {name}: no resolvable source (zenodo_record).")

    print(f"\n=== {name} ===")
    print(f"  {spec['title']}")
    print(f"  licence : {spec['license']}"
          + ("" if spec.get("commercial_use", True) else "   [NON-COMMERCIAL]"))
    print(f"  doi     : {spec.get('doi')}")

    meta = _api(record)
    keep = tuple(spec.get("include_ext") or [])
    files = [f for f in meta.get("files", [])
             if not keep or f["key"].lower().endswith(keep)]
    total = sum(f.get("size", 0) for f in files)
    print(f"  files   : {len(files)}  ({total / 1e9:.2f} GB)")

    if dry_run:
        return

    out = RAW / name
    out.mkdir(parents=True, exist_ok=True)

    (out / "LICENSE.txt").write_text(
        f"{spec['title']}\n\n"
        f"Licence: {spec['license']}\n{spec.get('license_url', '')}\n\n"
        f"Attribution (required when redistributing or publishing results):\n"
        f"{spec['attribution']}\n\n"
        + ("" if spec.get("commercial_use", True) else
           "RESTRICTION: NON-COMMERCIAL USE ONLY. "
           + spec.get("restriction", "") + "\n\n")
        + "Downloaded by ml/fetch_datasets.py. Do not redistribute without\n"
          "preserving this notice.\n",
        encoding="utf-8")

    counts = {"cached": 0, "downloaded": 0, "resumed": 0}
    for f in files:
        url = f.get("links", {}).get("self") or f.get("links", {}).get("download")
        checksum = (f.get("checksum") or "")
        md5 = checksum.split("md5:")[-1] if checksum.startswith("md5:") else None
        counts[_download(url, out / f["key"], md5, f.get("size", 0))] += 1

    (out / "PROVENANCE.json").write_text(json.dumps({
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "registry_entry": name,
        **{k: spec.get(k) for k in
           ("title", "creators", "year", "doi", "license", "license_url",
            "commercial_use", "attribution", "species", "used_for",
            "known_confound", "restriction")},
        "files": [{"name": f["key"], "size": f.get("size"),
                   "checksum": f.get("checksum")} for f in files],
    }, indent=2), encoding="utf-8")

    print(f"  -> {out}   ({counts['cached']} cached, "
          f"{counts['downloaded'] + counts['resumed']} fetched)")


def main() -> int:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", nargs="*", help="dataset keys from registry.json")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list or not (args.names or args.all):
        print("Available (all licence-checked):\n")
        for k, v in reg["datasets"].items():
            flag = "" if v.get("commercial_use", True) else "  [NON-COMMERCIAL]"
            print(f"  {k:28s} {v['license']:14s} {v['approx_gb']:5.2f} GB{flag}")
            print(f"  {'':28s} {v['used_for'][:96]}")
        print("\nRejected, with reasons (see registry.json):\n")
        for k, v in reg.get("rejected", {}).items():
            print(f"  {k:28s} {v['reason'][:100]}")
        return 0

    names = list(reg["datasets"]) if args.all else args.names
    for n in names:
        if n not in reg["datasets"]:
            raise SystemExit(f"unknown dataset {n!r}; try --list")
        fetch(n, reg["datasets"][n], dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
