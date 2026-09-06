// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {Registry} from "./Registry.sol";
import {YieldOracle} from "./YieldOracle.sol";

/**
 * @title HoneyBatch
 * @notice Batches as ERC-1155 balances denominated in **grams**, with the two
 *         invariants the whole system rests on.
 *
 * ## Invariant 1 -- yield-bound issuance
 * A harvest may only be minted up to the yield envelope the oracle derived from
 * hive telemetry, counted cumulatively per apiary per season. Minting ten
 * batches that each sit under the cap must not add up to more than the cap;
 * `_mintedBySeason` is what makes that true.
 *
 * ## Invariant 2 -- mass conservation
 * Honey does not multiply in a processing shed. Every process and blend burns
 * its inputs and mints an output that cannot exceed them, with the difference
 * recorded as a declared loss and bounded by `maxLossBps`. A processor who
 * consistently declares an implausible loss becomes a fraud case rather than a
 * silent leak.
 *
 * ## Why ERC-1155 transfers are disabled
 * These tokens are custody records, not tradeable assets. A bare
 * `safeTransferFrom` would move honey with no counterparty role check and no
 * provenance event, which is precisely the hole the project exists to close.
 * Movement goes through `transferCustody`. We deliberately break strict
 * ERC-1155 transferability and say so rather than pretending otherwise.
 *
 * Units: grams throughout. Integer arithmetic only -- there is no float in
 * Solidity and "0.1 kg" problems have no place in a fraud-detection ceiling.
 */
contract HoneyBatch is ERC1155 {
    enum Kind { None, Raw, Processed, Blend, Packed }
    enum Status { None, Active, Packed, Flagged, Void }

    struct Batch {
        bytes32 apiaryId;      // zero for blends spanning several apiaries
        bytes32 season;
        bytes32 floralSource;
        address originalOwner;
        uint256 mintedGrams;
        uint256 surplusGrams;  // minted above the envelope, under regulator sign-off
        uint64 harvestTs;
        Kind kind;
        Status status;
    }

    /// A constituent of a blend. This is what generates the EU 2024/1438
    /// country/region-of-origin declaration with descending percentages.
    struct Component {
        bytes32 sourceBatchId;
        uint256 grams;
    }

    Registry public immutable registry;
    YieldOracle public immutable oracle;

    mapping(bytes32 => Batch) private _batches;
    mapping(bytes32 => Component[]) private _composition;
    mapping(bytes32 => uint256) public totalSupplyOf;

    /// apiaryId => season => grams already minted. The cumulative cap.
    mapping(bytes32 => mapping(bytes32 => uint256)) private _mintedBySeason;

    /// Maximum declarable processing loss, in basis points. Filtering and
    /// settling genuinely lose a few percent; 15% is generous and still bounds
    /// "the rest evaporated" as an excuse for a leak.
    uint16 public maxLossBps = 1500;

    event BatchMinted(
        bytes32 indexed batchId,
        bytes32 indexed apiaryId,
        bytes32 indexed season,
        address owner,
        uint256 grams,
        uint256 surplusGrams
    );
    event CustodyTransferred(
        bytes32 indexed batchId, address indexed from, address indexed to, uint256 grams
    );
    event BatchProcessed(
        bytes32 indexed inputBatchId,
        bytes32 indexed outputBatchId,
        uint256 inputGrams,
        uint256 outputGrams,
        uint256 lossGrams
    );
    event BatchBlended(bytes32 indexed outputBatchId, uint256 outputGrams, uint256 inputCount);
    event BatchPacked(bytes32 indexed batchId, uint32 jars);
    event BatchFlagged(bytes32 indexed batchId, string reason);
    event MaxLossBpsChanged(uint16 bps);

    error NotAuthorised();
    error NotAdmin();
    error UnknownBatch();
    error BatchExists();
    error BatchNotActive();
    error ExceedsYieldEnvelope(uint256 requested, uint256 remaining);
    error NoEnvelope();
    error MassNotConserved(uint256 outputGrams, uint256 inputGrams);
    error LossTooHigh(uint256 lossBps, uint16 maxBps);
    error InsufficientBalance();
    error TransfersDisabled();
    error EmptyBlend();

    modifier onlyAdmin() {
        if (!registry.hasRole(msg.sender, Registry.Role.Admin)) revert NotAdmin();
        _;
    }

    constructor(Registry registry_, YieldOracle oracle_, string memory uri_) ERC1155(uri_) {
        registry = registry_;
        oracle = oracle_;
    }

    // ------------------------------------------------------------------ //
    // minting -- invariant 1
    // ------------------------------------------------------------------ //

    /**
     * @notice Declare a harvest. Reverts if it would breach the yield envelope.
     * @dev This revert is the point of the whole system. It is also the demo:
     *      declare 800 kg against a 520 kg envelope and watch the transaction
     *      fail on chain rather than be quietly recorded.
     */
    function mintHarvest(
        bytes32 batchId,
        bytes32 apiaryId,
        bytes32 season,
        uint256 grams,
        bytes32 floralSource,
        uint64 harvestTs
    ) external {
        address owner = registry.apiaryOwner(apiaryId);
        if (owner == address(0) || !registry.canActAs(msg.sender, owner)) revert NotAuthorised();
        if (_batches[batchId].status != Status.None) revert BatchExists();
        if (!oracle.exists(apiaryId, season)) revert NoEnvelope();

        uint256 cap = oracle.maxGrams(apiaryId, season);
        uint256 used = _mintedBySeason[apiaryId][season];
        uint256 remaining = cap > used ? cap - used : 0;
        if (grams > remaining) revert ExceedsYieldEnvelope(grams, remaining);

        _createBatch(batchId, apiaryId, season, floralSource, owner, grams, 0, harvestTs, Kind.Raw);
        _mintedBySeason[apiaryId][season] = used + grams;
    }

    /**
     * @notice Mint above the envelope, with a regulator taking responsibility.
     * @dev A genuinely exceptional season is a real thing, and a system that
     *      calls every good year fraud will be abandoned by the honest majority
     *      before it ever catches a cheat. So the escape hatch exists -- but it
     *      needs a Regulator, it records exactly how much was unbacked by
     *      telemetry, and that number renders as a visible caveat on the
     *      consumer page. Permitted, attributed, and visible.
     */
    function mintSurplus(
        bytes32 batchId,
        bytes32 apiaryId,
        bytes32 season,
        uint256 grams,
        bytes32 floralSource,
        uint64 harvestTs
    ) external {
        if (!registry.hasRole(msg.sender, Registry.Role.Regulator)) revert NotAuthorised();
        address owner = registry.apiaryOwner(apiaryId);
        if (owner == address(0)) revert NotAuthorised();
        if (_batches[batchId].status != Status.None) revert BatchExists();

        uint256 cap = oracle.maxGrams(apiaryId, season);
        uint256 used = _mintedBySeason[apiaryId][season];
        uint256 remaining = cap > used ? cap - used : 0;
        uint256 surplus = grams > remaining ? grams - remaining : 0;

        _createBatch(
            batchId, apiaryId, season, floralSource, owner, grams, surplus, harvestTs, Kind.Raw
        );
        _mintedBySeason[apiaryId][season] = used + grams;
    }

    function _createBatch(
        bytes32 batchId,
        bytes32 apiaryId,
        bytes32 season,
        bytes32 floralSource,
        address owner,
        uint256 grams,
        uint256 surplus,
        uint64 harvestTs,
        Kind kind
    ) private {
        _batches[batchId] = Batch({
            apiaryId: apiaryId,
            season: season,
            floralSource: floralSource,
            originalOwner: owner,
            mintedGrams: grams,
            surplusGrams: surplus,
            harvestTs: harvestTs,
            kind: kind,
            status: Status.Active
        });
        totalSupplyOf[batchId] += grams;
        _mint(owner, uint256(batchId), grams, "");
        emit BatchMinted(batchId, apiaryId, season, owner, grams, surplus);
    }

    // ------------------------------------------------------------------ //
    // custody
    // ------------------------------------------------------------------ //
    function transferCustody(bytes32 batchId, address from, address to, uint256 grams) external {
        if (!registry.canActAs(msg.sender, from)) revert NotAuthorised();
        if (registry.roleOf(to) == Registry.Role.None) revert NotAuthorised();
        if (_batches[batchId].status != Status.Active) revert BatchNotActive();
        if (balanceOf(from, uint256(batchId)) < grams) revert InsufficientBalance();

        _safeTransferFrom(from, to, uint256(batchId), grams, "");
        emit CustodyTransferred(batchId, from, to, grams);
    }

    // ------------------------------------------------------------------ //
    // processing and blending -- invariant 2
    // ------------------------------------------------------------------ //

    /**
     * @notice Consume part of one batch to produce another (filtering, settling).
     * @dev Mass conservation: outputGrams <= inputGrams, and the implied loss
     *      must sit within maxLossBps.
     */
    function processBatch(
        bytes32 inputBatchId,
        bytes32 outputBatchId,
        uint256 inputGrams,
        uint256 outputGrams,
        address processor
    ) external {
        if (!registry.canActAs(msg.sender, processor)) revert NotAuthorised();
        if (!registry.hasRole(processor, Registry.Role.Processor)) revert NotAuthorised();
        if (_batches[inputBatchId].status != Status.Active) revert BatchNotActive();
        if (_batches[outputBatchId].status != Status.None) revert BatchExists();
        if (balanceOf(processor, uint256(inputBatchId)) < inputGrams) revert InsufficientBalance();

        if (outputGrams > inputGrams) revert MassNotConserved(outputGrams, inputGrams);
        uint256 loss = inputGrams - outputGrams;
        uint256 lossBps = inputGrams == 0 ? 0 : (loss * 10000) / inputGrams;
        if (lossBps > maxLossBps) revert LossTooHigh(lossBps, maxLossBps);

        _burn(processor, uint256(inputBatchId), inputGrams);
        totalSupplyOf[inputBatchId] -= inputGrams;

        Batch storage src = _batches[inputBatchId];
        _batches[outputBatchId] = Batch({
            apiaryId: src.apiaryId,
            season: src.season,
            floralSource: src.floralSource,
            originalOwner: processor,
            mintedGrams: outputGrams,
            surplusGrams: 0,
            harvestTs: src.harvestTs,
            kind: Kind.Processed,
            status: Status.Active
        });
        totalSupplyOf[outputBatchId] += outputGrams;
        _mint(processor, uint256(outputBatchId), outputGrams, "");
        _composition[outputBatchId].push(Component(inputBatchId, inputGrams));

        if (balanceOf(processor, uint256(inputBatchId)) == 0 && totalSupplyOf[inputBatchId] == 0) {
            src.status = Status.Void;
        }
        emit BatchProcessed(inputBatchId, outputBatchId, inputGrams, outputGrams, loss);
    }

    /**
     * @notice Combine several batches into one blend.
     * @dev Records every constituent and its mass. Walking this graph off-chain
     *      is what produces the EU origin declaration -- countries in descending
     *      order with percentages -- without anyone hand-filling a form.
     */
    function blend(
        bytes32 outputBatchId,
        bytes32[] calldata inputBatchIds,
        uint256[] calldata inputGrams,
        uint256 outputGrams,
        address processor
    ) external {
        if (!registry.canActAs(msg.sender, processor)) revert NotAuthorised();
        if (!registry.hasRole(processor, Registry.Role.Processor)) revert NotAuthorised();
        if (inputBatchIds.length == 0 || inputBatchIds.length != inputGrams.length) {
            revert EmptyBlend();
        }
        if (_batches[outputBatchId].status != Status.None) revert BatchExists();

        uint256 totalIn;
        for (uint256 i = 0; i < inputBatchIds.length; i++) {
            bytes32 id = inputBatchIds[i];
            if (_batches[id].status != Status.Active) revert BatchNotActive();
            if (balanceOf(processor, uint256(id)) < inputGrams[i]) revert InsufficientBalance();

            _burn(processor, uint256(id), inputGrams[i]);
            totalSupplyOf[id] -= inputGrams[i];
            totalIn += inputGrams[i];
            _composition[outputBatchId].push(Component(id, inputGrams[i]));

            if (totalSupplyOf[id] == 0) _batches[id].status = Status.Void;
        }

        if (outputGrams > totalIn) revert MassNotConserved(outputGrams, totalIn);
        uint256 lossBps = totalIn == 0 ? 0 : ((totalIn - outputGrams) * 10000) / totalIn;
        if (lossBps > maxLossBps) revert LossTooHigh(lossBps, maxLossBps);

        _batches[outputBatchId] = Batch({
            apiaryId: bytes32(0), // a blend has no single origin, by definition
            season: bytes32(0),
            floralSource: bytes32(0),
            originalOwner: processor,
            mintedGrams: outputGrams,
            surplusGrams: 0,
            harvestTs: uint64(block.timestamp),
            kind: Kind.Blend,
            status: Status.Active
        });
        totalSupplyOf[outputBatchId] += outputGrams;
        _mint(processor, uint256(outputBatchId), outputGrams, "");
        emit BatchBlended(outputBatchId, outputGrams, inputBatchIds.length);
    }

    function markPacked(bytes32 batchId, uint32 jars, address packer) external {
        if (!registry.canActAs(msg.sender, packer)) revert NotAuthorised();
        Batch storage b = _batches[batchId];
        if (b.status != Status.Active) revert BatchNotActive();
        b.status = Status.Packed;
        b.kind = Kind.Packed;
        emit BatchPacked(batchId, jars);
    }

    // ------------------------------------------------------------------ //
    // administration
    // ------------------------------------------------------------------ //
    function flagBatch(bytes32 batchId, string calldata reason) external {
        Registry.Role r = registry.roleOf(msg.sender);
        if (r != Registry.Role.Regulator && r != Registry.Role.Admin) revert NotAuthorised();
        if (_batches[batchId].status == Status.None) revert UnknownBatch();
        _batches[batchId].status = Status.Flagged;
        emit BatchFlagged(batchId, reason);
    }

    function setMaxLossBps(uint16 bps) external onlyAdmin {
        require(bps <= 5000, "implausible loss ceiling");
        maxLossBps = bps;
        emit MaxLossBpsChanged(bps);
    }

    // ------------------------------------------------------------------ //
    // views
    // ------------------------------------------------------------------ //
    function getBatch(bytes32 batchId) external view returns (Batch memory) {
        return _batches[batchId];
    }

    function getComposition(bytes32 batchId) external view returns (Component[] memory) {
        return _composition[batchId];
    }

    function mintedInSeason(bytes32 apiaryId, bytes32 season) external view returns (uint256) {
        return _mintedBySeason[apiaryId][season];
    }

    function remainingInSeason(bytes32 apiaryId, bytes32 season) external view returns (uint256) {
        uint256 cap = oracle.maxGrams(apiaryId, season);
        uint256 used = _mintedBySeason[apiaryId][season];
        return cap > used ? cap - used : 0;
    }

    // ------------------------------------------------------------------ //
    // ERC-1155 transfer entrypoints, deliberately closed
    // ------------------------------------------------------------------ //
    function safeTransferFrom(address, address, uint256, uint256, bytes memory)
        public
        pure
        override
    {
        revert TransfersDisabled();
    }

    function safeBatchTransferFrom(
        address, address, uint256[] memory, uint256[] memory, bytes memory
    ) public pure override {
        revert TransfersDisabled();
    }
}
