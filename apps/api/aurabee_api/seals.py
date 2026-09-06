"""Per-jar seals: generation, Merkle tree, verification.

The tree must hash pairs exactly the way OpenZeppelin's `MerkleProof` does --
commutatively, by sorting each pair before hashing. Get that wrong and every
proof fails on chain with no useful error, so `leaf()` is cross-checked against
the contract's `leafOf()` in scripts/demo_flow.py rather than trusted.

Seal anatomy:

    serial : printed openly beside the QR, e.g. AB-SIT-0042-0007
    secret : 8 characters under a scratch-off or inside the cap
    leaf   : keccak256(abi.encode(batchId, serial, secret))

Only the Merkle root reaches the chain. Scanning the QR proves the jar was part
of a real packing run; entering the secret proves the scanner is physically
holding it. Someone who photographs a label in a shop gets the first and never
the second -- which is the entire reason seals are per jar rather than per batch.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from web3 import Web3

# Ambiguous glyphs removed: these get read off a scratch-off panel by a person,
# often in bad light, and 0/O and 1/I/L are where transcription errors come from.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SECRET_LEN = 8


@dataclass(frozen=True)
class Seal:
    index: int
    serial: str
    secret: str
    leaf: bytes

    @property
    def leaf_hex(self) -> str:
        return "0x" + self.leaf.hex()


def _b32(value: str) -> bytes:
    raw = value.encode()
    if len(raw) > 32:
        raise ValueError(f"{value!r} does not fit in bytes32")
    return raw.ljust(32, b"\x00")


def leaf(batch_code: str, serial: str, secret: str) -> bytes:
    """Must match SealRegistry.leafOf exactly."""
    return Web3.keccak(_b32(batch_code) + _b32(serial) + _b32(secret))


def new_secret() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(SECRET_LEN))


def generate_seals(batch_code: str, jar_count: int, prefix: str = "AB") -> list[Seal]:
    seals = []
    for i in range(1, jar_count + 1):
        # strip separators from the batch suffix so serials do not
        # come out as AB--1234-B-00042
        suffix = "".join(ch for ch in batch_code if ch.isalnum())[-8:].upper()
        serial = f"{prefix}-{suffix}-{i:05d}"
        secret = new_secret()
        seals.append(Seal(i, serial, secret, leaf(batch_code, serial, secret)))
    return seals


# --------------------------------------------------------------------------
# Merkle, sorted-pair (OpenZeppelin compatible)
# --------------------------------------------------------------------------
def _hash_pair(a: bytes, b: bytes) -> bytes:
    return Web3.keccak(a + b if a < b else b + a)


def merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        raise ValueError("cannot build a tree with no leaves")
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            # An odd node is promoted unchanged rather than duplicated. Hashing
            # a node with itself is the classic Merkle second-preimage foot-gun.
            nxt.append(_hash_pair(level[i], level[i + 1]) if i + 1 < len(level) else level[i])
        level = nxt
    return level[0]


def merkle_proof(leaves: list[bytes], index: int) -> list[bytes]:
    proof: list[bytes] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                if idx in (i, i + 1):
                    proof.append(level[i + 1] if idx == i else level[i])
                nxt.append(_hash_pair(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        idx //= 2
        level = nxt
    return proof


def verify_proof(root: bytes, leaf_hash: bytes, proof: list[bytes]) -> bool:
    """Local mirror of the on-chain check, for tests and for the verify page
    when we do not want a round trip to an RPC node."""
    computed = leaf_hash
    for sibling in proof:
        computed = _hash_pair(computed, sibling)
    return computed == root
