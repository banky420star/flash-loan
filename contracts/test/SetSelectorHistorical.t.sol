// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface VmH {
    function prank(address) external;
    function roll(uint256) external;
    function load(address, bytes32) external view returns (bytes32);
}

interface IZeroH {
    function setSelector(address target, bytes4 selector, bool allowed) external;
    function owner() external view returns (address);
    function allowedRouter(address) external view returns (bool);
}

contract SetSelectorHistorical {
    VmH internal constant vm = VmH(
        address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant EX = 0x78de834B65d26d35993E0c7cE5F2fc1Ca7e461c9;
    address constant HOT = 0xC78096CE520d4D676e9B26DeA1027bF32b390A2C;

    function test_atFailureBlock() external {
        vm.roll(506760764); // block where the tx mined and reverted
        emit LogAddr("owner", IZeroH(EX).owner());
        emit LogBool("router allowed at block", IZeroH(EX).allowedRouter(0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45));
        vm.prank(HOT);
        IZeroH(EX).setSelector(0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45, 0x04e45aaf, true);
    }

    event LogAddr(string, address);
    event LogBool(string, bool);
}
