// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";

contract ReputationRegistryTest is Test {
    ReputationRegistry reg;
    address agent = address(this);
    address stranger = address(0xBEEF);
    bytes32 constant P = keccak256("cra-fees-estimate");

    event ReputationUpdated(bytes32 indexed providerId, uint16 scoreBps, uint64 calls, string evidenceRef);
    event ProviderRetired(bytes32 indexed providerId, uint16 scoreBps, uint64 calls, string evidenceRef);

    function setUp() public {
        reg = new ReputationRegistry();
    }

    function test_recordStoresEverything() public {
        vm.roll(1234);
        vm.warp(99);
        vm.expectEmit(true, false, false, true);
        emit ReputationUpdated(P, 8_900, 42, "0xabc");
        reg.recordScore(P, 8_900, 42, "0xabc");
        ReputationRegistry.Record memory r = reg.scoreOf(P);
        assertEq(r.scoreBps, 8_900);
        assertEq(r.calls, 42);
        assertEq(r.updatedAt, 99);
        assertEq(r.updatedBlock, 1234);
        assertEq(r.evidenceHash, keccak256(bytes("0xabc")));
        assertFalse(r.retired);
        assertEq(reg.providerCount(), 1);
        assertEq(reg.providerIdAt(0), P);
    }

    function test_describeThenRecordTracksOnce() public {
        reg.describe(P, "https://api.cra-agent.tech/v1/paid/fees/estimate");
        reg.recordScore(P, 5_000, 1, "ref");
        assertEq(reg.providerCount(), 1);
        assertEq(reg.uriOf(P), "https://api.cra-agent.tech/v1/paid/fees/estimate");
    }

    function test_retireThenRecordReverts() public {
        vm.expectEmit(true, false, false, true);
        emit ProviderRetired(P, 0, 3, "no-payment:http-500");
        reg.retire(P, 0, 3, "no-payment:http-500");
        assertTrue(reg.scoreOf(P).retired);
        vm.expectRevert(ReputationRegistry.RetiredProvider.selector);
        reg.recordScore(P, 9_000, 4, "x");
        reg.reinstate(P);
        reg.recordScore(P, 9_000, 4, "x");
        assertEq(reg.scoreOf(P).scoreBps, 9_000);
    }

    function test_onlyOwnerWrites() public {
        vm.startPrank(stranger);
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.recordScore(P, 1, 1, "");
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.retire(P, 1, 1, "");
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.describe(P, "x");
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.reinstate(P);
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.transferOwnership(stranger);
        vm.stopPrank();
    }

    function test_twoStepOwnership() public {
        reg.transferOwnership(stranger);
        assertEq(reg.owner(), agent); // not yet
        vm.expectRevert(ReputationRegistry.NotPendingOwner.selector);
        reg.acceptOwnership(); // wrong caller
        vm.prank(stranger);
        reg.acceptOwnership();
        assertEq(reg.owner(), stranger);
        assertEq(reg.pendingOwner(), address(0));
        vm.expectRevert(ReputationRegistry.NotOwner.selector);
        reg.recordScore(P, 1, 1, "");
    }

    function test_rejectsZeroProviderAndBadScore() public {
        vm.expectRevert(ReputationRegistry.ZeroProvider.selector);
        reg.recordScore(bytes32(0), 1, 1, "");
        vm.expectRevert(ReputationRegistry.BadScore.selector);
        reg.recordScore(P, 10_001, 1, "");
    }

    function testFuzz_scoreBounds(uint16 s, uint64 calls, string calldata ev) public {
        if (s > 10_000) {
            vm.expectRevert(ReputationRegistry.BadScore.selector);
            reg.recordScore(P, s, calls, ev);
        } else {
            reg.recordScore(P, s, calls, ev);
            assertEq(reg.scoreOf(P).scoreBps, s);
            assertEq(reg.scoreOf(P).evidenceHash, keccak256(bytes(ev)));
        }
    }
}
