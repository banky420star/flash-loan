// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Decisive probe for the "cash from minting" idea: does Renzo's Arbitrum
// xRenzoDeposit actually mint ezETH for WETH at latest block, and at what
// rate vs the pool price? No forge-std in this repo — raw cheat-code Vm.

interface VmProbe {
    function deal(address account, uint256 newBalance) external;
}

interface IERC20Probe {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHProbe is IERC20Probe {
    function deposit() external payable;
}

interface IXRenzoDepositProbe {
    function deposit(address tokenIn, uint256 amountIn, uint256 amountOutMin,
                     address recipient) external returns (uint256);
}

contract EzETHMintProbe {
    VmProbe internal constant vm = VmProbe(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant XRD = 0xf25484650484DE3d554fB0b7125e7696efA4ab99;
    address constant EZETH = 0x2416092f143378750bb29b79eD961ab195CcEea5;

    event Probe(string label, uint256 value);

    function test_mintRateProbe() external {
        emit Probe("code at deposit proxy bytes", XRD.code.length);
        vm.deal(address(this), 100 ether);
        IWETHProbe(WETH).deposit{value: 100 ether}();
        emit Probe("weth wrapped", IERC20Probe(WETH).balanceOf(address(this)));
        IERC20Probe(WETH).approve(XRD, type(uint256).max);
        try IXRenzoDepositProbe(XRD).deposit(WETH, 100 ether, 0, address(this))
        returns (uint256 minted) {
            emit Probe("minted ezETH (18dp) per 100 WETH", minted);
            emit Probe("ezETH balance after", IERC20Probe(EZETH).balanceOf(address(this)));
        } catch (bytes memory reason) {
            emit Probe("deposit reverted with bytes len", reason.length);
            // surface the revert reason in the trace
            assembly {
                revert(add(reason, 0x20), mload(reason))
            }
        }
    }
}