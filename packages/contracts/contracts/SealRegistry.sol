// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
import {Registry} from "./Registry.sol";
import {HoneyBatch} from "./HoneyBatch.sol";
import {Attestation} from "./Attestation.sol";

/**
 * @title SealRegistry
 * @notice Per-jar seals, anchored as a Merkle root rather than one row per jar.
 *
 * ## Why a root and not a row per jar
 * A single cluster can pack a lakh of jars in a season. Writing a storage slot
 * per jar would cost more than the honey. Instead each packing run anchors one
 * Merkle root over all its jar commitments, plus the jar count. A jar then
 * proves membership with a Merkle proof, which is O(log n) calldata and no
 * storage at all.
 *
 * The jar count is not decoration: it is what lets the platform notice that
 * more distinct seals are being scanned in the wild than were ever issued.
 *
 * ## The commitment
 *      leaf = keccak256(abi.encode(batchId, serial, secret))
 * `serial` is printed openly under the QR; `secret` is a short code under a
 * scratch-off or inside the cap. Scanning proves the jar exists; entering the
 * secret proves the scanner physically holds it. A photographed QR gets the
 * first and never the second, which is the whole point -- a batch-level QR is
 * one photocopy away from worthless.
 *
 * ## What stays off chain
 * Individual scans. Anchoring every scan would be absurd, and scan records
 * contain coarse location, which does not belong on a permanent public ledger.
 * They live in Postgres; `anchorScanEpoch` commits a periodic root so the scan
 * history cannot be quietly rewritten later.
 */
contract SealRegistry {
    struct SealBatch {
        bytes32 sealRoot;
        uint32 jarCount;
        uint32 netWeightG; // per jar, for reconciliation against batch mass
        uint64 issuedAt;
        address issuer;
        bool exists;
    }

    Registry public immutable registry;
    HoneyBatch public immutable batches;
    Attestation public immutable attestations;

    mapping(bytes32 => SealBatch) private _sealBatches;
    /// batchId => epoch => merkle root over that epoch's scan records
    mapping(bytes32 => mapping(uint32 => bytes32)) public scanEpochRoot;

    event SealsIssued(
        bytes32 indexed batchId, bytes32 sealRoot, uint32 jarCount, uint32 netWeightG
    );
    event ScanEpochAnchored(bytes32 indexed batchId, uint32 indexed epoch, bytes32 root);

    error NotAuthorised();
    error AlreadyIssued();
    error UnknownSealBatch();
    error NoJars();
    error OverPacking(uint256 claimedGrams_, uint256 availableGrams);
    error NoPassingLabReport();

    constructor(Registry registry_, HoneyBatch batches_, Attestation attestations_) {
        registry = registry_;
        batches = batches_;
        attestations = attestations_;
    }

    /**
     * @notice Anchor the seals for one packing run.
     *
     * Two gates, and they are the whole point of this function:
     *
     * **The output cap.** A packer holding 1,000 kg cannot mint seals for
     * 5,000 kg of jars. Seals are issued against mass that demonstrably
     * exists, so syrup added on the packing line ends up in jars with no
     * seal to sell under. This is the mirror of the mint ceiling at the other
     * end of the chain: inputs bounded by hive telemetry, outputs bounded by
     * verified mass.
     *
     * **The lab gate.** No passing, unexpired lab report from an independent
     * NABL lab means no seals, full stop. `Attestation` already refuses
     * attestations issued by a Brand, so a packer cannot self-certify.
     *
     * One packing run per batch. A packer running two days creates two batches
     * through `HoneyBatch.processBatch`, which keeps the cap arithmetic simple
     * and each Merkle root bound to exactly one run.
     *
     * @param sealRoot   Merkle root over keccak256(batchId, serial, secret) leaves.
     * @param jarCount   how many jars this run produced.
     * @param netWeightG net honey per jar, in grams.
     */
    function issueSeals(
        bytes32 batchId,
        bytes32 sealRoot,
        uint32 jarCount,
        uint32 netWeightG,
        address issuer
    ) external {
        if (!registry.canActAs(msg.sender, issuer)) revert NotAuthorised();
        Registry.Role r = registry.roleOf(issuer);
        if (r != Registry.Role.Processor && r != Registry.Role.Brand) revert NotAuthorised();
        if (_sealBatches[batchId].exists) revert AlreadyIssued();
        if (jarCount == 0) revert NoJars();

        if (!attestations.hasValid(batchId, Attestation.Kind.LabReport)) {
            revert NoPassingLabReport();
        }

        uint256 claimed = uint256(jarCount) * uint256(netWeightG);
        uint256 available = batches.totalSupplyOf(batchId);
        // Jars cannot hold more honey than the batch contains. Packing loses a
        // little rather than gaining, so this is a strict ceiling with no
        // upward tolerance.
        if (claimed > available) revert OverPacking(claimed, available);

        _sealBatches[batchId] = SealBatch({
            sealRoot: sealRoot,
            jarCount: jarCount,
            netWeightG: netWeightG,
            issuedAt: uint64(block.timestamp),
            issuer: issuer,
            exists: true
        });
        emit SealsIssued(batchId, sealRoot, jarCount, netWeightG);
    }

    /// @notice Commitment for a jar. Kept on chain so the off-chain issuer,
    ///         the verify page and the tests cannot drift apart on encoding.
    function leafOf(bytes32 batchId, bytes32 serial, bytes32 secret)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(batchId, serial, secret));
    }

    /// @notice Prove a jar belongs to a packing run.
    function verifySeal(
        bytes32 batchId,
        bytes32 serial,
        bytes32 secret,
        bytes32[] calldata proof
    ) external view returns (bool) {
        SealBatch storage sb = _sealBatches[batchId];
        if (!sb.exists) return false;
        return MerkleProof.verify(proof, sb.sealRoot, leafOf(batchId, serial, secret));
    }

    function anchorScanEpoch(bytes32 batchId, uint32 epoch, bytes32 root) external {
        if (!registry.isRelayer(msg.sender)) revert NotAuthorised();
        if (!_sealBatches[batchId].exists) revert UnknownSealBatch();
        scanEpochRoot[batchId][epoch] = root;
        emit ScanEpochAnchored(batchId, epoch, root);
    }

    function getSealBatch(bytes32 batchId) external view returns (SealBatch memory) {
        return _sealBatches[batchId];
    }

    /// @notice Total honey the jars claim to contain, in grams. The caller
    ///         compares this against the batch mass; a packing run claiming
    ///         more honey than the batch held is arithmetic, not opinion.
    function claimedGrams(bytes32 batchId) external view returns (uint256) {
        SealBatch storage sb = _sealBatches[batchId];
        return uint256(sb.jarCount) * uint256(sb.netWeightG);
    }
}
