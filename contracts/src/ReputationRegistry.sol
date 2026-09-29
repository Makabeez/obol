// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title ReputationRegistry (v2, Arc mainnet)
/// @notice Public, on-chain record of how well paid x402 services actually deliver,
///         written by the Obol buyer agent after it pays them in USDC on Arc.
///         Every write carries an evidence reference (the settlement tx hash for
///         EIP-3009 "exact" payments, or the Circle Gateway transfer id for batched
///         payments) so anyone can check the score against a real payment.
/// @dev    v2 changes vs the Lepton (testnet) version:
///         - evidence ref on every write (hash stored, full string in the event)
///         - provider URI stored on-chain, so readers need no off-chain mapping
///         - updatedBlock stored, so a reader can fetch the exact write tx with a
///           single-block eth_getLogs (no indexer, works under RPC range limits)
///         - two-step ownership transfer
contract ReputationRegistry {
    struct Record {
        uint16 scoreBps; // delivered quality, 0..10000
        uint64 calls; // paid calls the score is based on
        uint64 updatedAt; // block timestamp of last write
        uint64 updatedBlock; // block number of last write
        bool retired; // Obol stopped buying: confidently not worth it
        bytes32 evidenceHash; // keccak256(bytes(evidenceRef)) of the last write
    }

    uint16 public constant MAX_SCORE_BPS = 10_000;

    address public owner;
    address public pendingOwner;

    mapping(bytes32 => Record) private _records;
    mapping(bytes32 => string) private _uris;
    mapping(bytes32 => bool) private _known;
    bytes32[] private _providerIds;

    event ReputationUpdated(bytes32 indexed providerId, uint16 scoreBps, uint64 calls, string evidenceRef);
    event ProviderRetired(bytes32 indexed providerId, uint16 scoreBps, uint64 calls, string evidenceRef);
    event ProviderDescribed(bytes32 indexed providerId, string uri);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotPendingOwner();
    error BadScore();
    error ZeroProvider();
    error RetiredProvider();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    // ---------------------------------------------------------------- owner

    function transferOwnership(address newOwner) external onlyOwner {
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert NotPendingOwner();
        emit OwnershipTransferred(owner, msg.sender);
        owner = msg.sender;
        pendingOwner = address(0);
    }

    // ---------------------------------------------------------------- writes

    /// @notice Attach a human/agent-readable URI (the x402 endpoint) to a provider id.
    function describe(bytes32 providerId, string calldata uri) external onlyOwner {
        if (providerId == bytes32(0)) revert ZeroProvider();
        _track(providerId);
        _uris[providerId] = uri;
        emit ProviderDescribed(providerId, uri);
    }

    /// @notice Record (or update) a provider's delivered-quality score.
    function recordScore(bytes32 providerId, uint16 scoreBps, uint64 calls, string calldata evidenceRef)
        external
        onlyOwner
    {
        Record storage r = _write(providerId, scoreBps, calls, evidenceRef);
        if (r.retired) revert RetiredProvider();
        emit ReputationUpdated(providerId, scoreBps, calls, evidenceRef);
    }

    /// @notice Mark a provider retired: Obol is confident it isn't worth buying.
    function retire(bytes32 providerId, uint16 scoreBps, uint64 calls, string calldata evidenceRef)
        external
        onlyOwner
    {
        Record storage r = _write(providerId, scoreBps, calls, evidenceRef);
        r.retired = true;
        emit ProviderRetired(providerId, scoreBps, calls, evidenceRef);
    }

    /// @notice Give a retired provider another chance (e.g. after it fixed its service).
    function reinstate(bytes32 providerId) external onlyOwner {
        _records[providerId].retired = false;
    }

    // ---------------------------------------------------------------- reads

    function scoreOf(bytes32 providerId) external view returns (Record memory) {
        return _records[providerId];
    }

    function uriOf(bytes32 providerId) external view returns (string memory) {
        return _uris[providerId];
    }

    function providerCount() external view returns (uint256) {
        return _providerIds.length;
    }

    function providerIdAt(uint256 i) external view returns (bytes32) {
        return _providerIds[i];
    }

    // ---------------------------------------------------------------- internal

    function _write(bytes32 providerId, uint16 scoreBps, uint64 calls, string calldata evidenceRef)
        private
        returns (Record storage r)
    {
        if (providerId == bytes32(0)) revert ZeroProvider();
        if (scoreBps > MAX_SCORE_BPS) revert BadScore();
        _track(providerId);
        r = _records[providerId];
        r.scoreBps = scoreBps;
        r.calls = calls;
        r.updatedAt = uint64(block.timestamp);
        r.updatedBlock = uint64(block.number);
        r.evidenceHash = keccak256(bytes(evidenceRef));
    }

    function _track(bytes32 providerId) private {
        if (!_known[providerId]) {
            _known[providerId] = true;
            _providerIds.push(providerId);
        }
    }
}
