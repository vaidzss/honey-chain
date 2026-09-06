// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Registry} from "./Registry.sol";

/**
 * @title YieldOracle
 * @notice The bridge between hive telemetry and what the chain will accept.
 *
 * This is the contract that makes AuraBee different from every other honey
 * traceability project. A chain on its own faithfully records lies: declare
 * 3,000 kg from 50 hives and a naive system writes it down forever. Here, the
 * AI yield forecaster derives a distribution from sentinel-hive telemetry and
 * publishes its P90 as a **ceiling on mintable kilograms** for that apiary and
 * season.
 *
 * Why P90 and not P50: the point is to catch fraud, not to punish a good year.
 * A P50 cap would reject roughly half of all honest declarations and the system
 * would be abandoned within one season. P90 says "under this model, given this
 * telemetry, more than X is a 1-in-10 event" -- defensible to a beekeeper, an
 * auditor, or a judge.
 *
 * Envelopes are immutable once published. A model that wants to revise its
 * forecast publishes a *new revision*, and the history stays on chain, because
 * an oracle that can silently rewrite its own past claims is not an oracle.
 */
contract YieldOracle {
    struct Envelope {
        uint256 maxGrams;     // the P90 ceiling; grams, so no floating point
        uint256 p50Grams;     // carried for display and audit, not enforcement
        uint64 windowStart;
        uint64 windowEnd;
        bytes32 evidenceHash; // Merkle root over the telemetry window + features
        uint16 coverageBps;   // how much was measured vs extrapolated, 0..10000
        uint32 revision;
        string modelVersion;
        bool exists;
    }

    Registry public immutable registry;

    /// apiaryId => season => envelope
    mapping(bytes32 => mapping(bytes32 => Envelope)) private _envelopes;
    mapping(address => bool) public isOracle;

    event OracleSet(address indexed oracle, bool allowed);
    event EnvelopePublished(
        bytes32 indexed apiaryId,
        bytes32 indexed season,
        uint256 maxGrams,
        uint16 coverageBps,
        uint32 revision,
        bytes32 evidenceHash,
        string modelVersion
    );

    error NotOracle();
    error NotAdmin();
    error BadWindow();
    error NoEvidence();
    error CoverageOutOfRange();

    modifier onlyOracle() {
        if (!isOracle[msg.sender]) revert NotOracle();
        _;
    }

    modifier onlyAdmin() {
        if (!registry.hasRole(msg.sender, Registry.Role.Admin)) revert NotAdmin();
        _;
    }

    constructor(Registry registry_) {
        registry = registry_;
        isOracle[msg.sender] = true;
        emit OracleSet(msg.sender, true);
    }

    function setOracle(address oracle, bool allowed) external onlyAdmin {
        isOracle[oracle] = allowed;
        emit OracleSet(oracle, allowed);
    }

    /**
     * @notice Publish (or revise) a yield envelope.
     * @dev `evidenceHash` is mandatory. An envelope without a pointer to the
     *      telemetry that produced it is an assertion, not evidence, and the
     *      whole design rests on being able to re-derive the number later.
     */
    function publishEnvelope(
        bytes32 apiaryId,
        bytes32 season,
        uint256 maxGrams_,
        uint256 p50Grams,
        uint64 windowStart,
        uint64 windowEnd,
        bytes32 evidenceHash,
        uint16 coverageBps,
        string calldata modelVersion
    ) external onlyOracle {
        if (windowEnd <= windowStart) revert BadWindow();
        if (evidenceHash == bytes32(0)) revert NoEvidence();
        if (coverageBps > 10000) revert CoverageOutOfRange();

        Envelope storage prev = _envelopes[apiaryId][season];
        uint32 nextRevision = prev.exists ? prev.revision + 1 : 1;

        _envelopes[apiaryId][season] = Envelope({
            maxGrams: maxGrams_,
            p50Grams: p50Grams,
            windowStart: windowStart,
            windowEnd: windowEnd,
            evidenceHash: evidenceHash,
            coverageBps: coverageBps,
            revision: nextRevision,
            modelVersion: modelVersion,
            exists: true
        });

        emit EnvelopePublished(
            apiaryId, season, maxGrams_, coverageBps, nextRevision, evidenceHash, modelVersion
        );
    }

    function getEnvelope(bytes32 apiaryId, bytes32 season)
        external
        view
        returns (Envelope memory)
    {
        return _envelopes[apiaryId][season];
    }

    /// @notice Mint ceiling in grams. Zero when no envelope exists, which means
    ///         "nothing may be minted here yet" rather than "unlimited".
    function maxGrams(bytes32 apiaryId, bytes32 season) external view returns (uint256) {
        return _envelopes[apiaryId][season].maxGrams;
    }

    function exists(bytes32 apiaryId, bytes32 season) external view returns (bool) {
        return _envelopes[apiaryId][season].exists;
    }
}
