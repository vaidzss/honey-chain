# Security

## Reporting

Open a private security advisory on this repository, or raise an issue with no
technical detail and we will move it somewhere private. Please do not post a
working exploit in a public issue.

## Secrets

**No secret belongs in this repository.** `.env` is git-ignored; `.env.example`
holds placeholders and local-only development values, and every value in it that
would be a genuine credential is left **empty on purpose**.

Generate any of them with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### The values that matter, and what each protects

| Variable | What it protects | If it leaks |
|---|---|---|
| `SEAL_HMAC_KEY` | Server-side pepper for jar seal secrets | A stolen database dump becomes forged verification for **every jar in circulation**. The seal secret is short by necessity — a person reads it off a scratch panel — so the pepper is what makes the stored hash useless to an attacker |
| `DEVICE_HMAC_KEY` | Telemetry frame signatures | Telemetry bounds minting, so a forged frame is a forged yield ceiling. This is the oracle the whole design rests on |
| `RELAYER_PRIVATE_KEY` | The account that submits transactions | Full write access to the ledger under our identity |
| `ORACLE_PRIVATE_KEY` | The account that signs yield envelopes | The ability to set any apiary's mint ceiling to anything |
| `JWT_SECRET` | Session signing | Impersonation of any actor |

`SEAL_HMAC_KEY` and `DEVICE_HMAC_KEY` **fail closed**: the services refuse to
start without them unless `AURABEE_ENV=development` is set explicitly. There is
no hardcoded fallback, because a fallback published in a public repository is
not a fallback — it is the key.

### Local development keys

For a local chain, run `npx hardhat node` and paste two of the private keys it
prints into your `.env`. Those accounts are well-known and funded only on your
own machine. They are deliberately **not** checked in, so that no scanner, and
no reader, has to work out whether a key in this repository is real.

## Deployment flags

| Flag | Default | Why |
|---|---|---|
| `AURABEE_ENV` | unset → strict | Only `development` enables throwaway signing keys |
| `ALLOW_TIME_TRAVEL` | `false` | Disables frame-freshness checking so the simulator's simulated timestamps are accepted. Replay protection rests on the monotonic `seq` counter and holds either way, but this must never be on in production. It defaults to off because a flag documented as "never in production" should not be something you inherit by forgetting it |

## Threat model, briefly

The design assumes **the broker is the least trustworthy hop**, which is why
frames are signed at the device rather than protected only by TLS. A compromised
broker can drop or delay telemetry; it cannot forge a higher yield ceiling.

It assumes the **database may be dumped**, which is why seal secrets are
peppered and hashed rather than stored, and why every ledger claim is
independently re-derivable from chain state alone
(`python scripts/verify_ledger.py`).

It does **not** assume the beekeeper is honest. That is the entire point of
yield-bound issuance, and the limits of what it can achieve are set out in
[docs/13-perspective.md](docs/13-perspective.md).

## Known limitations in this build

- The deployer account holds admin, relayer and oracle rights simultaneously.
  Fine for a local chain, wrong for a real one — these must be separate signers
  with separate custody before any deployment.
- The GS1 company prefix is a **development placeholder**. Real GTINs and GLNs
  require a licensed prefix from GS1 India.
- Actor authentication is not yet implemented at the API boundary. The consoles
  trust their caller, which is correct for a single-machine demo and not for
  anything else.
