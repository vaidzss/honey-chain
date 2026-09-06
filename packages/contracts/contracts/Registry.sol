// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title Registry
 * @notice Identities and apiaries for the AuraBee honey chain.
 *
 * Two design points worth stating explicitly:
 *
 * 1. **Custodial by design.** Rural beekeepers do not hold private keys, and
 *    pretending otherwise is how this class of project dies in the field. Each
 *    actor has an address, but an authorised relayer may act on their behalf.
 *    The relayer cannot invent actors or change roles -- only an admin can --
 *    so the blast radius of a compromised relayer is bounded to acting as
 *    already-registered actors, which is auditable after the fact via events.
 *
 * 2. **No PII on chain.** `agristackHash` is a salted hash of the AgriStack
 *    Farmer ID, never the ID itself. A chain is permanent; PII on a permanent
 *    ledger is a liability that cannot be withdrawn.
 */
contract Registry {
    enum Role {
        None,
        Beekeeper,
        CollectionCentre,
        FPO,
        Processor,
        Lab,
        Brand,
        Regulator,
        Admin
    }

    struct Actor {
        Role role;
        bytes32 agristackHash; // salted hash, never the raw Farmer ID
        bool active;
        string name;
    }

    struct Apiary {
        address owner;
        bytes32 geohash;
        uint32 hiveCount;
        uint32 sentinelCount; // instrumented hives; drives envelope coverage
        bool active;
    }

    mapping(address => Actor) private _actors;
    mapping(bytes32 => Apiary) private _apiaries;
    mapping(address => bool) public isRelayer;

    event ActorRegistered(address indexed actor, Role role, string name);
    event ActorStatusChanged(address indexed actor, bool active);
    event ApiaryRegistered(bytes32 indexed apiaryId, address indexed owner, uint32 hiveCount);
    event ApiaryUpdated(bytes32 indexed apiaryId, uint32 hiveCount, uint32 sentinelCount);
    event RelayerSet(address indexed relayer, bool allowed);

    error NotAdmin();
    error NotAuthorised();
    error UnknownActor();
    error UnknownApiary();
    error AlreadyRegistered();

    modifier onlyAdmin() {
        if (_actors[msg.sender].role != Role.Admin || !_actors[msg.sender].active) {
            revert NotAdmin();
        }
        _;
    }

    /// @dev The deployer bootstraps as the first admin. Every other actor is
    ///      registered by an admin; there is no self-registration.
    constructor() {
        _actors[msg.sender] = Actor({
            role: Role.Admin,
            agristackHash: bytes32(0),
            active: true,
            name: "genesis-admin"
        });
        isRelayer[msg.sender] = true;
        emit ActorRegistered(msg.sender, Role.Admin, "genesis-admin");
        emit RelayerSet(msg.sender, true);
    }

    // ------------------------------------------------------------------ //
    // actors
    // ------------------------------------------------------------------ //
    function registerActor(
        address actor,
        Role role,
        bytes32 agristackHash,
        string calldata name
    ) external onlyAdmin {
        if (_actors[actor].role != Role.None) revert AlreadyRegistered();
        _actors[actor] = Actor(role, agristackHash, true, name);
        emit ActorRegistered(actor, role, name);
    }

    function setActorActive(address actor, bool active) external onlyAdmin {
        if (_actors[actor].role == Role.None) revert UnknownActor();
        _actors[actor].active = active;
        emit ActorStatusChanged(actor, active);
    }

    function setRelayer(address relayer, bool allowed) external onlyAdmin {
        isRelayer[relayer] = allowed;
        emit RelayerSet(relayer, allowed);
    }

    function getActor(address actor) external view returns (Actor memory) {
        return _actors[actor];
    }

    function roleOf(address actor) external view returns (Role) {
        Actor storage a = _actors[actor];
        return a.active ? a.role : Role.None;
    }

    function hasRole(address actor, Role role) public view returns (bool) {
        Actor storage a = _actors[actor];
        return a.active && a.role == role;
    }

    /**
     * @notice True when `caller` may act as `actor`.
     * @dev Either the actor itself, or a whitelisted relayer holding that
     *      actor's custodial key. This is the single choke point for the
     *      custodial model; every contract that authorises an action routes
     *      through it rather than reimplementing the check.
     */
    function canActAs(address caller, address actor) public view returns (bool) {
        if (!_actors[actor].active) return false;
        return caller == actor || isRelayer[caller];
    }

    function requireActingAs(address caller, address actor, Role role) external view {
        if (!canActAs(caller, actor)) revert NotAuthorised();
        if (_actors[actor].role != role) revert NotAuthorised();
    }

    // ------------------------------------------------------------------ //
    // apiaries
    // ------------------------------------------------------------------ //
    function registerApiary(
        bytes32 apiaryId,
        address owner,
        bytes32 geohash,
        uint32 hiveCount,
        uint32 sentinelCount
    ) external onlyAdmin {
        if (_apiaries[apiaryId].owner != address(0)) revert AlreadyRegistered();
        if (!hasRole(owner, Role.Beekeeper)) revert NotAuthorised();
        _apiaries[apiaryId] = Apiary(owner, geohash, hiveCount, sentinelCount, true);
        emit ApiaryRegistered(apiaryId, owner, hiveCount);
    }

    function updateApiary(bytes32 apiaryId, uint32 hiveCount, uint32 sentinelCount)
        external
        onlyAdmin
    {
        Apiary storage ap = _apiaries[apiaryId];
        if (ap.owner == address(0)) revert UnknownApiary();
        ap.hiveCount = hiveCount;
        ap.sentinelCount = sentinelCount;
        emit ApiaryUpdated(apiaryId, hiveCount, sentinelCount);
    }

    function getApiary(bytes32 apiaryId) external view returns (Apiary memory) {
        return _apiaries[apiaryId];
    }

    function apiaryOwner(bytes32 apiaryId) external view returns (address) {
        return _apiaries[apiaryId].owner;
    }
}
