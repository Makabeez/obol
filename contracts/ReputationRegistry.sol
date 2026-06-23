// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ReputationRegistry
/// @notice On-chain record of how well paid agent-services actually deliver, written by
///         Obol as it spends. Score is delivered quality in basis points (0-10000), so any
///         other agent can read a provider's track record before paying it. Reputation is a
///         public good here: one buyer's experience priced in USDC, posted for everyone.
contract ReputationRegistry {
    struct Record {
        uint16 scoreBps;   // delivered quality, 0..10000
        uint64 calls;      // how many paid calls this score is based on
        uint64 updatedAt;  // block timestamp of last write
        bool retired;       // Obol stopped buying: confidently not worth it
    }

    address public owner;
    mapping(bytes32 => Record) private _records;
    bytes32[] private _providerIds;
    mapping(bytes32 => bool) private _known;

    event ReputationUpdated(bytes32 indexed providerId, uint16 scoreBps, uint64 calls);
    event ProviderRetired(bytes32 indexed providerId, uint16 scoreBps, uint64 calls);
    event OwnerChanged(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error BadScore();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        emit OwnerChanged(address(0), msg.sender);
    }

    function setOwner(address newOwner) external onlyOwner {
        emit OwnerChanged(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Record (or update) a provider's delivered-quality score.
    /// @param providerId keccak/id of the provider (e.g. keccak256(bytes(provider.id)))
    /// @param scoreBps   delivered quality in basis points, 0..10000
    /// @param calls      number of paid calls the score is based on
    function recordScore(bytes32 providerId, uint16 scoreBps, uint64 calls)
        external
        onlyOwner
    {
        if (scoreBps > 10000) revert BadScore();
        _track(providerId);
        Record storage r = _records[providerId];
        r.scoreBps = scoreBps;
        r.calls = calls;
        r.updatedAt = uint64(block.timestamp);
        emit ReputationUpdated(providerId, scoreBps, calls);
    }

    /// @notice Mark a provider retired: Obol is confident it isn't worth buying.
    function retire(bytes32 providerId, uint16 scoreBps, uint64 calls)
        external
        onlyOwner
    {
        if (scoreBps > 10000) revert BadScore();
        _track(providerId);
        Record storage r = _records[providerId];
        r.scoreBps = scoreBps;
        r.calls = calls;
        r.retired = true;
        r.updatedAt = uint64(block.timestamp);
        emit ProviderRetired(providerId, scoreBps, calls);
    }

    function scoreOf(bytes32 providerId) external view returns (Record memory) {
        return _records[providerId];
    }

    function providerCount() external view returns (uint256) {
        return _providerIds.length;
    }

    function providerIdAt(uint256 i) external view returns (bytes32) {
        return _providerIds[i];
    }

    function _track(bytes32 providerId) private {
        if (!_known[providerId]) {
            _known[providerId] = true;
            _providerIds.push(providerId);
        }
    }
}
