// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface VmG {
    function roll(uint256) external;
}

interface IZeroG {
    function allowedSelector(address, bytes4) external view returns (bool);
    function allowedRouter(address) external view returns (bool);
    function maxLoan(address) external view returns (uint256);
}

contract GetterProbe {
    VmG internal constant vm = VmG(
        address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant EX = 0x78de834B65d26d35993E0c7cE5F2fc1Ca7e461c9;

    function test_getters() external {
        bool router = IZeroG(EX).allowedRouter(0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45);
        emit LogB("allowedRouter", router);
        bool sel = IZeroG(EX).allowedSelector(0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45, 0x04e45aaf);
        emit LogB("allowedSelector", sel);
        uint256 ml = IZeroG(EX).maxLoan(0xaf88d065e77c8cC2239327C5EDb3A432268e5831);
        emit LogU("maxLoan USDC", ml);
    }

    event LogB(string, bool);
    event LogU(string, uint256);
}
