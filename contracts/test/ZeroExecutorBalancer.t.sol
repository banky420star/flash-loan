// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Balancer V2 Vault as a second flash-loan source: same step pipeline, no
// premium. The vault path must settle only against the vault and keep every
// Aave-side guard intact.

import {ZeroExecutor} from "../src/ZeroExecutor.sol";

interface VmBalancer {
    function expectRevert(bytes4 selector) external;
    function prank(address sender) external;
}

interface IFlashReceiverBalancer {
    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata userData
    ) external;
}

contract MockTokenBalancer {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount; return true;
    }
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(balanceOf[from] >= amount, "BAL");
        require(allowance[from][msg.sender] >= amount, "ALLOW");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount; balanceOf[to] += amount; return true;
    }
}

contract MockBalancerVault {
    MockTokenBalancer public immutable token;
    constructor(MockTokenBalancer token_) { token = token_; }
    // Balancer V2 Vault: 0-fee flash loan, receiveFlashLoan callback.
    function flashLoan(address recipient, address[] calldata tokens,
                       uint256[] calldata amounts, bytes calldata userData)
        external
    {
        require(tokens.length == 1 && amounts.length == 1, "SHAPE");
        MockTokenBalancer(tokens[0]).mint(recipient, amounts[0]);
        IFlashReceiverBalancer(recipient).receiveFlashLoan(
            tokens, amounts, new uint256[](1), userData);
        require(MockTokenBalancer(tokens[0]).transferFrom(
            recipient, address(this), amounts[0]), "REPAY");
    }
}

contract MockRouterBalancer {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut)
    {
        MockTokenBalancer(params.tokenOut).mint(msg.sender, params.amountOutMinimum);
        return params.amountOutMinimum;
    }
}

contract ZeroExecutorBalancerTest {
    VmBalancer internal constant vm = VmBalancer(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    MockTokenBalancer token;
    MockBalancerVault vault;
    MockRouterBalancer router;
    ZeroExecutor executor;

    function setUp() external {
        token = new MockTokenBalancer();
        vault = new MockBalancerVault(token);
        router = new MockRouterBalancer();
        // Aave-side pool is never exercised in this file; a non-zero
        // placeholder satisfies the constructor's validation only.
        executor = new ZeroExecutor(address(this));
    }

    function _configure() internal {
        executor.setBalancerVault(address(vault));
        executor.setToken(address(token), true);
        executor.setRouter(address(router), true);
        executor.setSelector(address(router),
            MockRouterBalancer.exactInputSingle.selector, true);
        executor.setMaxLoan(address(token), 1_000);
        executor.setMaxSteps(4);
    }

    function _swapStep(uint256 amountIn, uint256 minOut)
        internal view returns (ZeroExecutor.Step memory)
    {
        return ZeroExecutor.Step({
            kind: ZeroExecutor.StepKind.SwapExactInputSingle,
            target: address(router), tokenIn: address(token), tokenOut: address(token),
            account: address(0), amount: amountIn, limit: minOut,
            fee: 500, recipientMode: 0
        });
    }

    function testBalancerVaultMustBeSetBeforeUse() external {
        executor.setToken(address(token), true);
        executor.setMaxLoan(address(token), 100);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.InvalidAddress.selector);
        executor.runBalancer(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testOnlyOwnerPinsVault() external {
        vm.expectRevert(ZeroExecutor.NotOwner.selector);
        vm.prank(address(0xbeef));
        executor.setBalancerVault(address(vault));
    }

    function testWrongVaultCallbackReverts() external {
        address[] memory tokens = new address[](1);
        tokens[0] = address(token);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 1;
        vm.expectRevert(ZeroExecutor.UnauthorizedPool.selector);
        executor.receiveFlashLoan(tokens, amounts, new uint256[](1), "");
    }

    function testBalancerRunRealizesProfitAndRepaysZeroPremium() external {
        _configure();
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](1);
        steps[0] = _swapStep(100, 10);
        uint256 profit = executor.runBalancer(
            address(token), 100, 10, block.timestamp + 1, steps);
        require(profit == 10, "PROFIT");
        require(token.balanceOf(address(vault)) == 100, "REPAY_AMOUNT");
        require(token.allowance(address(executor), address(vault)) == 0,
            "VAULT_ALLOWANCE");
    }
}