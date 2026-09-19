// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface VmP {
    function prank(address) external;
}

interface IZero {
    function setSelector(address target, bytes4 selector, bool allowed) external;
    function owner() external view returns (address);
}

contract SetSelectorProbe {
    VmP internal constant vm = VmP(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    function test_probeSetSelector() external {
        address ex = 0x78de834B65d26d35993E0c7cE5F2fc1Ca7e461c9;
        address hot = 0xC78096CE520d4D676e9B26DeA1027bF32b390A2C;
        emit Log("owner", IZero(ex).owner());
        vm.prank(hot);
        IZero(ex).setSelector(
            0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45,
            0x04e45aaf, true);
    }

    event Log(string, address);
}
