// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroExecutor} from "../src/ZeroExecutor.sol";

interface VmZeroExecutor {
    function expectRevert(bytes4 selector) external;
    function expectRevert(bytes calldata revertData) external;
    function prank(address sender) external;
    function warp(uint256 timestamp) external;
}

interface IFlashReceiverMock {
    function executeOperation(address asset, uint256 amount, uint256 premium,
                              address initiator, bytes calldata params)
        external returns (bool);
}

contract MockTokenZero {
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

contract MockPoolZero {
    MockTokenZero public immutable token;
    constructor(MockTokenZero token_) { token = token_; }
    function flashLoanSimple(address receiverAddress, address asset, uint256 amount,
                             bytes calldata params, uint16) external {
        require(asset == address(token), "ASSET");
        token.mint(receiverAddress, amount);
        require(IFlashReceiverMock(receiverAddress).executeOperation(
            asset, amount, 0, msg.sender, params), "CALLBACK");
        require(token.transferFrom(receiverAddress, address(this), amount), "REPAY");
    }
    function liquidationCall(address,address,address,uint256,bool) external {}
}

contract MockRouterZero {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut)
    {
        MockTokenZero(params.tokenOut).mint(msg.sender, params.amountOutMinimum);
        return params.amountOutMinimum;
    }
}

contract ZeroExecutorTest {
    VmZeroExecutor internal constant vm = VmZeroExecutor(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    MockTokenZero token;
    MockPoolZero pool;
    MockRouterZero router;
    ZeroExecutor executor;

    function setUp() external {
        token = new MockTokenZero();
        pool = new MockPoolZero(token);
        router = new MockRouterZero();
        executor = new ZeroExecutor(address(pool));
    }

    function _configure() internal {
        executor.setToken(address(token), true);
        executor.setRouter(address(router), true);
        executor.setSelector(address(router), MockRouterZero.exactInputSingle.selector, true);
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

    function testUnauthorizedPoolCallbackReverts() external {
        vm.expectRevert(ZeroExecutor.UnauthorizedPool.selector);
        executor.executeOperation(address(token), 1, 0, address(executor), "");
    }

    function testUnauthorizedInitiatorReverts() external {
        vm.expectRevert(ZeroExecutor.UnauthorizedInitiator.selector);
        vm.prank(address(pool));
        executor.executeOperation(address(token), 1, 0, address(0xbeef), "");
    }

    function testUnauthorizedCallerCannotRun() external {
        _configure();
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.NotAuthorizedCaller.selector);
        vm.prank(address(0xbeef));
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testOnlyOwnerCanChangeAllowlist() external {
        vm.expectRevert(ZeroExecutor.NotOwner.selector);
        vm.prank(address(0xbeef));
        executor.setToken(address(token), true);
    }

    function testPausedExecutionReverts() external {
        _configure(); executor.pause();
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.Paused.selector);
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testDisallowedTokenReverts() external {
        executor.setMaxLoan(address(token), 100);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.TokenNotAllowed.selector);
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testDisallowedRouterReverts() external {
        executor.setToken(address(token), true);
        executor.setMaxLoan(address(token), 100);
        executor.setSelector(address(router), MockRouterZero.exactInputSingle.selector, true);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](1);
        steps[0] = _swapStep(0, 0);
        vm.expectRevert(ZeroExecutor.RouterNotAllowed.selector);
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testDisallowedSelectorReverts() external {
        executor.setToken(address(token), true);
        executor.setRouter(address(router), true);
        executor.setMaxLoan(address(token), 100);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](1);
        steps[0] = _swapStep(0, 0);
        vm.expectRevert(ZeroExecutor.SelectorNotAllowed.selector);
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testExcessiveStepsRevert() external {
        _configure(); executor.setMaxSteps(1);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](2);
        steps[0] = _swapStep(0, 0); steps[1] = _swapStep(0, 0);
        vm.expectRevert(ZeroExecutor.TooManySteps.selector);
        executor.run(address(token), 1, 0, block.timestamp + 1, steps);
    }

    function testExcessiveLoanReverts() external {
        _configure(); executor.setMaxLoan(address(token), 100);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.LoanLimitExceeded.selector);
        executor.run(address(token), 101, 0, block.timestamp + 1, steps);
    }

    function testExpiredDeadlineReverts() external {
        _configure(); vm.warp(1_000);
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(ZeroExecutor.Expired.selector);
        executor.run(address(token), 1, 0, 999, steps);
    }

    function testApprovalsAreResetAfterSuccessfulRun() external {
        _configure();
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](1);
        steps[0] = _swapStep(10, 1);
        uint256 profit = executor.run(address(token), 100, 1, block.timestamp + 1, steps);
        require(profit >= 1, "NO_PROFIT");
        require(token.allowance(address(executor), address(router)) == 0, "ROUTER_ALLOWANCE");
        require(token.allowance(address(executor), address(pool)) == 0, "POOL_ALLOWANCE");
    }

    function testMinimumProfitFailureReverts() external {
        _configure();
        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](0);
        vm.expectRevert(abi.encodeWithSelector(
            ZeroExecutor.MinimumProfitNotMet.selector, 100, 101));
        executor.run(address(token), 100, 1, block.timestamp + 1, steps);
    }
}
