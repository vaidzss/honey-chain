// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Registry} from "./Registry.sol";

/**
 * @title Attestation
 * @notice Lab reports, certificates and inspections, as signed hashes.
 *
 * The document itself never goes on chain -- a NABL lab report is a PDF and
 * chains are the worst possible filesystem. What goes on chain is the hash, the
 * issuer, and a small structured summary the consumer page can render without
 * fetching anything (C4 percentage, moisture, verdict).
 *
 * `summary` is a compact bytes32-keyed map rather than a string: a consumer
 * page that has to parse free text to decide whether honey passed is a page
 * that will eventually parse it wrong.
 *
 * Attestations are additive and never edited. A lab that revises a result
 * issues a new attestation; revoking is explicit and leaves the original
 * visible. An issuer able to silently rewrite a past result is not an issuer.
 */
contract Attestation {
    enum SubjectType { None, Batch, Actor, Apiary, Hive }
    enum Kind { None, LabReport, OrganicCert, FssaiLicence, GiTag, Inspection, ExportDossier }

    struct Record {
        SubjectType subjectType;
        bytes32 subjectId;
        Kind kind;
        address issuer;
        bytes32 docHash;    // sha256 of the file held in object storage
        uint64 issuedAt;
        uint64 expiresAt;   // 0 = no expiry
        bool passed;
        bool revoked;
    }

    Registry public immutable registry;

    Record[] private _records;
    /// subjectId => indices into _records
    mapping(bytes32 => uint256[]) private _bySubject;
    /// recordIndex => key => value, for the small structured summary
    mapping(uint256 => mapping(bytes32 => int256)) public metric;

    event Attested(
        uint256 indexed recordId,
        bytes32 indexed subjectId,
        Kind indexed kind,
        address issuer,
        bytes32 docHash,
        bool passed
    );
    event MetricSet(uint256 indexed recordId, bytes32 indexed key, int256 value);
    event Revoked(uint256 indexed recordId, string reason);

    error NotAuthorised();
    error UnknownRecord();
    error AlreadyRevoked();
    error KeyValueMismatch();

    constructor(Registry registry_) {
        registry = registry_;
    }

    /// @dev Only a Lab, Regulator or Admin may attest. A brand attesting to the
    ///      purity of its own honey is a press release, not evidence.
    function _requireIssuer(address issuer) private view {
        if (!registry.canActAs(msg.sender, issuer)) revert NotAuthorised();
        Registry.Role r = registry.roleOf(issuer);
        if (r != Registry.Role.Lab && r != Registry.Role.Regulator && r != Registry.Role.Admin) {
            revert NotAuthorised();
        }
    }

    /**
     * @param metricKeys   e.g. keccak("c4_pct_x100"), keccak("moisture_x100")
     * @param metricValues scaled integers -- there is no float on chain, and
     *                     "18.2% moisture" must survive the round trip exactly
     */
    function attest(
        SubjectType subjectType,
        bytes32 subjectId,
        Kind kind,
        address issuer,
        bytes32 docHash,
        uint64 expiresAt,
        bool passed,
        bytes32[] calldata metricKeys,
        int256[] calldata metricValues
    ) external returns (uint256 recordId) {
        _requireIssuer(issuer);
        if (metricKeys.length != metricValues.length) revert KeyValueMismatch();

        _records.push(
            Record({
                subjectType: subjectType,
                subjectId: subjectId,
                kind: kind,
                issuer: issuer,
                docHash: docHash,
                issuedAt: uint64(block.timestamp),
                expiresAt: expiresAt,
                passed: passed,
                revoked: false
            })
        );
        recordId = _records.length - 1;
        _bySubject[subjectId].push(recordId);

        for (uint256 i = 0; i < metricKeys.length; i++) {
            metric[recordId][metricKeys[i]] = metricValues[i];
            emit MetricSet(recordId, metricKeys[i], metricValues[i]);
        }

        emit Attested(recordId, subjectId, kind, issuer, docHash, passed);
    }

    function revoke(uint256 recordId, string calldata reason) external {
        if (recordId >= _records.length) revert UnknownRecord();
        Record storage rec = _records[recordId];
        if (rec.revoked) revert AlreadyRevoked();
        // only the original issuer, or a regulator, may revoke
        if (!registry.canActAs(msg.sender, rec.issuer)
            && registry.roleOf(msg.sender) != Registry.Role.Regulator) {
            revert NotAuthorised();
        }
        rec.revoked = true;
        emit Revoked(recordId, reason);
    }

    function getRecord(uint256 recordId) external view returns (Record memory) {
        if (recordId >= _records.length) revert UnknownRecord();
        return _records[recordId];
    }

    function recordsFor(bytes32 subjectId) external view returns (uint256[] memory) {
        return _bySubject[subjectId];
    }

    /// @notice Whether a subject currently holds a valid, unrevoked, unexpired
    ///         passing attestation of a given kind.
    function hasValid(bytes32 subjectId, Kind kind) external view returns (bool) {
        uint256[] storage ids = _bySubject[subjectId];
        for (uint256 i = 0; i < ids.length; i++) {
            Record storage rec = _records[ids[i]];
            if (rec.kind != kind || rec.revoked || !rec.passed) continue;
            if (rec.expiresAt != 0 && rec.expiresAt < block.timestamp) continue;
            return true;
        }
        return false;
    }

    function totalRecords() external view returns (uint256) {
        return _records.length;
    }
}
