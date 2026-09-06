"""Chain gateway.

Everything that touches the blockchain goes through here. Two reasons for a
single choke point:

 1. **The relayer is the custodial signer.** Beekeepers do not hold keys, so the
    platform signs on their behalf and passes the actor address explicitly. That
    is a real trust concession and it should live in one auditable place rather
    than scattered across route handlers.
 2. **Unit discipline.** The chain speaks grams as integers; the rest of the
    system speaks kilograms as floats. Every conversion happens here, so a
    rounding mistake cannot quietly become a mint ceiling error.

The address book (contract addresses + ABIs) is written by
`packages/contracts/scripts/deploy.js`, so the API can never run against a
stale ABI it compiled itself.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import ROUND_DOWN, Decimal
from functools import cached_property
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3Exception

log = logging.getLogger("aurabee.chain")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENTS = REPO_ROOT / "packages" / "contracts" / "deployments"

GRAMS_PER_KG = 1000


class ChainError(RuntimeError):
    """A contract rejected the call. `reason` carries the custom error name when
    we could decode one, because 'execution reverted' helps nobody."""

    def __init__(self, message: str, reason: str | None = None, data: Any = None):
        super().__init__(message)
        self.reason = reason
        self.data = data


def kg_to_grams(kg: float | Decimal) -> int:
    """Kilograms to whole grams, rounding DOWN.

    Down, not nearest: this feeds a fraud ceiling, and the rounding should never
    invent a gram that telemetry did not support.
    """
    return int((Decimal(str(kg)) * GRAMS_PER_KG).to_integral_value(rounding=ROUND_DOWN))


def grams_to_kg(grams: int) -> float:
    return grams / GRAMS_PER_KG


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$", re.I)


def to_bytes32(value: str) -> bytes:
    """Map an application id (a UUID, a batch code, a season label) onto bytes32.

    A dashed UUID is 36 characters and does not fit, but its hex form is exactly
    32 -- so dashes are stripped rather than the whole thing hashed. This is not
    cosmetic: hashing makes the on-chain identifier **one-way**, so an
    independent verifier reading the chain cannot recover which apiary an
    envelope belongs to and has to be handed the mapping by us. Keeping the
    preimage makes the ledger self-describing and the proofs checkable without
    trusting our database.

    Anything else too long to fit is still hashed, because truncating would
    silently collide and a collision here means two apiaries sharing a ceiling.
    """
    if _UUID_RE.match(value):
        value = value.replace("-", "")
    raw = value.encode()
    if len(raw) <= 32:
        return raw.ljust(32, b"\x00")
    return Web3.keccak(raw)


def from_bytes32(value: bytes) -> str:
    """Inverse of `to_bytes32` for values that fit. Re-inserts UUID dashes so
    the recovered id matches what the database holds."""
    text = value.rstrip(b"\x00").decode(errors="replace")
    if _UUID_HEX_RE.match(text):
        return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"
    return text


class ChainClient:
    def __init__(self, rpc_url: str | None = None, network: str | None = None,
                 private_key: str | None = None):
        self.rpc_url = rpc_url or os.getenv("CHAIN_RPC_URL", "http://localhost:8545")
        self.network = network or os.getenv("CHAIN_NETWORK", "localhost")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

        key = private_key or os.getenv("RELAYER_PRIVATE_KEY")
        if not key:
            raise ChainError("RELAYER_PRIVATE_KEY not set")
        self.account = Account.from_key(key)

        book_path = DEPLOYMENTS / f"{self.network}.json"
        if not book_path.exists():
            raise ChainError(
                f"no address book at {book_path}. Deploy first: npm run chain:deploy"
            )
        self.book = json.loads(book_path.read_text())
        self.chain_id = self.book["chainId"]

    # ------------------------------------------------------------------ #
    def _contract(self, name: str):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(self.book["addresses"][name]),
            abi=self.book["abis"][name],
        )

    @cached_property
    def registry(self):
        return self._contract("Registry")

    @cached_property
    def oracle(self):
        return self._contract("YieldOracle")

    @cached_property
    def batches(self):
        return self._contract("HoneyBatch")

    @cached_property
    def seals(self):
        return self._contract("SealRegistry")

    @cached_property
    def attestations(self):
        return self._contract("Attestation")

    # ------------------------------------------------------------------ #
    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def contracts_live(self) -> bool:
        """True when the address book still points at deployed code.

        Redeploying moves every address, and a long-running API holding a stale
        book will call into an empty address and raise BadFunctionCallOutput on
        every read. Checking for bytecode turns a confusing 500 into a clear
        "chain unavailable".
        """
        try:
            return len(self.w3.eth.get_code(self.batches.address)) > 2
        except Exception:
            return False

    def _safe_read(self, fn, default=None):
        """Chain reads must never take down consumer verification.

        A jar whose provenance we hold in Postgres should still render its
        journey when the RPC node is down or the contracts have moved; it just
        cannot be marked *verified*. Failing closed on display would punish the
        consumer for our outage.
        """
        try:
            return fn()
        except (Web3Exception, ContractLogicError, ValueError, OSError) as exc:
            log.warning("chain read failed (%s); degrading", type(exc).__name__)
            return default

    def send(self, fn, *, gas: int | None = None) -> dict:
        """Sign and send as the relayer, then wait for the receipt.

        A revert is simulated with eth_call *first*. Two reasons: the custom
        error comes back cleanly instead of buried in an RPC envelope, and a
        doomed transaction never burns a nonce. An `ExceedsYieldEnvelope` revert
        is the system working correctly, so the UI has to be able to say exactly
        that rather than surfacing a raw JSON-RPC blob.
        """
        try:
            fn.call({"from": self.account.address})
        except (ContractLogicError, Web3Exception) as exc:
            reason = self._decode_error(exc)
            raise ChainError(str(exc), reason=reason, data=self._decode_args(exc)) from exc

        try:
            tx = fn.build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "chainId": self.chain_id,
                "gas": gas or 3_000_000,
                "gasPrice": self.w3.eth.gas_price,
            })
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        except (ContractLogicError, Web3Exception) as exc:
            # Simulation passed but broadcast reverted: a genuine race, e.g.
            # someone else consumed the remaining envelope in between.
            raise ChainError(str(exc), reason=self._decode_error(exc)) from exc

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise ChainError(f"transaction reverted: {tx_hash.hex()}")
        return {
            "tx_hash": tx_hash.hex(),
            "block": receipt["blockNumber"],
            "gas_used": receipt["gasUsed"],
        }

    # Custom errors we raise deliberately and want to name in the UI.
    KNOWN_ERRORS = (
        "ExceedsYieldEnvelope", "MassNotConserved", "LossTooHigh", "NoEnvelope",
        "NotAuthorised", "BatchExists", "BatchNotActive", "InsufficientBalance",
        "TransfersDisabled", "AlreadyIssued", "AlreadyRegistered", "NotAdmin",
        "NotOracle", "UnknownBatch", "EmptyBlend", "NoJars", "CoverageOutOfRange",
        "OverPacking", "NoPassingLabReport",
    )

    @classmethod
    def _decode_error(cls, exc: Exception) -> str | None:
        text = str(exc)
        for name in cls.KNOWN_ERRORS:
            if name in text:
                return name
        return None

    @staticmethod
    def _decode_args(exc: Exception) -> list[int] | None:
        """Pull the numeric arguments out of e.g. ExceedsYieldEnvelope(791500,
        137008), so the caller can say "you asked for 791.5 kg, 137.0 kg is
        left" instead of "reverted"."""
        text = str(exc)
        start, end = text.find("("), text.rfind(")")
        if start == -1 or end <= start:
            return None
        try:
            return [int(p.strip()) for p in text[start + 1:end].split(",") if p.strip().isdigit()]
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def envelope(self, apiary_id: str, season: str) -> dict | None:
        e = self.oracle.functions.getEnvelope(
            to_bytes32(apiary_id), to_bytes32(season)
        ).call()
        if not e[8]:  # exists
            return None
        return {
            "max_kg": grams_to_kg(e[0]),
            "p50_kg": grams_to_kg(e[1]),
            "window_start": e[2],
            "window_end": e[3],
            "evidence_hash": "0x" + e[4].hex(),
            "coverage_bps": e[5],
            "revision": e[6],
            "model_version": e[7],
        }

    def remaining_kg(self, apiary_id: str, season: str) -> float:
        return grams_to_kg(
            self.batches.functions.remainingInSeason(
                to_bytes32(apiary_id), to_bytes32(season)
            ).call()
        )

    def batch(self, batch_code: str) -> dict | None:
        b = self._safe_read(
            lambda: self.batches.functions.getBatch(to_bytes32(batch_code)).call())
        if b is None or b[8] == 0:  # unreachable, or Status.None
            return None
        kinds = ["None", "Raw", "Processed", "Blend", "Packed"]
        statuses = ["None", "Active", "Packed", "Flagged", "Void"]
        return {
            "apiary_id": from_bytes32(b[0]),
            "season": from_bytes32(b[1]),
            "floral_source": from_bytes32(b[2]),
            "original_owner": b[3],
            "minted_kg": grams_to_kg(b[4]),
            "surplus_kg": grams_to_kg(b[5]),
            "harvest_ts": b[6],
            "kind": kinds[b[7]],
            "status": statuses[b[8]],
        }

    def composition(self, batch_code: str) -> list[dict]:
        comps = self._safe_read(
            lambda: self.batches.functions.getComposition(to_bytes32(batch_code)).call(),
            default=[])
        return [
            {"source_batch": from_bytes32(c[0]), "kg": grams_to_kg(c[1])} for c in comps
        ]

    def seal_batch(self, batch_code: str) -> dict | None:
        s = self._safe_read(
            lambda: self.seals.functions.getSealBatch(to_bytes32(batch_code)).call())
        if s is None or not s[5]:
            return None
        return {
            "seal_root": "0x" + s[0].hex(),
            "jar_count": s[1],
            "net_weight_g": s[2],
            "issued_at": s[3],
            "issuer": s[4],
        }

    def verify_seal(self, batch_code: str, serial: str, secret: str,
                    proof: list[str]) -> bool:
        return bool(self._safe_read(lambda: self.seals.functions.verifySeal(
            to_bytes32(batch_code), to_bytes32(serial), to_bytes32(secret),
            [bytes.fromhex(p[2:] if p.startswith("0x") else p) for p in proof],
        ).call(), default=False))

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def register_actor(self, address: str, role: int, name: str,
                       agristack_hash: bytes = b"\x00" * 32) -> dict:
        return self.send(self.registry.functions.registerActor(
            Web3.to_checksum_address(address), role, agristack_hash, name))

    def register_apiary(self, apiary_id: str, owner: str, geohash: str,
                        hive_count: int, sentinel_count: int) -> dict:
        return self.send(self.registry.functions.registerApiary(
            to_bytes32(apiary_id), Web3.to_checksum_address(owner),
            to_bytes32(geohash), hive_count, sentinel_count))

    def publish_envelope(self, apiary_id: str, season: str, p90_kg: float,
                         p50_kg: float, window_start: int, window_end: int,
                         evidence_hash: bytes, coverage_bps: int,
                         model_version: str) -> dict:
        return self.send(self.oracle.functions.publishEnvelope(
            to_bytes32(apiary_id), to_bytes32(season),
            kg_to_grams(p90_kg), kg_to_grams(p50_kg),
            window_start, window_end, evidence_hash, coverage_bps, model_version))

    def mint_harvest(self, batch_code: str, apiary_id: str, season: str, kg: float,
                     floral_source: str, harvest_ts: int) -> dict:
        return self.send(self.batches.functions.mintHarvest(
            to_bytes32(batch_code), to_bytes32(apiary_id), to_bytes32(season),
            kg_to_grams(kg), to_bytes32(floral_source), harvest_ts))

    def transfer_custody(self, batch_code: str, from_addr: str, to_addr: str,
                         kg: float) -> dict:
        return self.send(self.batches.functions.transferCustody(
            to_bytes32(batch_code), Web3.to_checksum_address(from_addr),
            Web3.to_checksum_address(to_addr), kg_to_grams(kg)))

    def process_batch(self, input_code: str, output_code: str, input_kg: float,
                      output_kg: float, processor: str) -> dict:
        return self.send(self.batches.functions.processBatch(
            to_bytes32(input_code), to_bytes32(output_code),
            kg_to_grams(input_kg), kg_to_grams(output_kg),
            Web3.to_checksum_address(processor)))

    def blend(self, output_code: str, input_codes: list[str], input_kgs: list[float],
              output_kg: float, processor: str) -> dict:
        return self.send(self.batches.functions.blend(
            to_bytes32(output_code), [to_bytes32(c) for c in input_codes],
            [kg_to_grams(k) for k in input_kgs], kg_to_grams(output_kg),
            Web3.to_checksum_address(processor)))

    def attest_lab_report(self, batch_code: str, lab_addr: str, doc_hash: bytes,
                          passed: bool = True, metrics: dict[str, float] | None = None,
                          expires_at: int = 0) -> dict:
        """Anchor a NABL lab result against a batch.

        Seals cannot be issued without one of these passing, so this is a gate
        and not a nicety. Metrics are scaled x100 and stored as integers --
        "0.40% C4" must survive the round trip exactly, and there is no float
        on chain.
        """
        keys, values = [], []
        for name, value in (metrics or {}).items():
            keys.append(Web3.keccak(text=f"{name}_x100"))
            values.append(int(round(value * 100)))
        return self.send(self.attestations.functions.attest(
            1,                      # SubjectType.Batch
            to_bytes32(batch_code),
            1,                      # Kind.LabReport
            Web3.to_checksum_address(lab_addr),
            doc_hash,
            expires_at,
            passed,
            keys,
            values,
        ))

    def has_valid_lab_report(self, batch_code: str) -> bool:
        return self.attestations.functions.hasValid(to_bytes32(batch_code), 1).call()

    def issue_seals(self, batch_code: str, seal_root: bytes, jar_count: int,
                    net_weight_g: int, issuer: str) -> dict:
        return self.send(self.seals.functions.issueSeals(
            to_bytes32(batch_code), seal_root, jar_count, net_weight_g,
            Web3.to_checksum_address(issuer)))


_client: ChainClient | None = None


def get_chain() -> ChainClient:
    global _client
    if _client is None:
        _client = ChainClient()
    return _client
