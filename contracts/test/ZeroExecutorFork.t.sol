// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroExecutor} from "../src/ZeroExecutor.sol";

interface VmHardenedFork {
    function deal(address account, uint256 newBalance) external;
}

interface IERC20Hardened {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHHardened is IERC20Hardened {
    function deposit() external payable;
}

interface IAavePoolHardened {
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
}

interface IPoolAddressesProviderHardened {
    function getPool() external view returns (address);
}

interface ISwapRouter02Hardened {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

contract ZeroExecutorForkTest {
    VmHardenedFork internal constant vm = VmHardenedFork(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address internal constant USDC =
        0xaf88d065e77c8cC2239327C5EDb3A432268e5831;
    address internal constant WETH =
        0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address internal constant SWAP_ROUTER_02 =
        0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;

    uint256 internal constant MANIPULATION_WETH = 1_000 ether;
    uint256 internal constant FLASH_AMOUNT = 100_000e6;
    uint256 internal constant MIN_PROFIT = 10e6;

    function testAllowlistedDirectRouteRepaysAndProfits() external {
        address pool = IPoolAddressesProviderHardened(AAVE_PROVIDER).getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");
        require(SWAP_ROUTER_02.code.length > 0, "ROUTER_MISSING");

        vm.deal(address(this), MANIPULATION_WETH + 1 ether);
        IWETHHardened(WETH).deposit{value: MANIPULATION_WETH}();
        require(IERC20Hardened(WETH).approve(SWAP_ROUTER_02, MANIPULATION_WETH),
                "MANIPULATION_APPROVE");
        ISwapRouter02Hardened(SWAP_ROUTER_02).exactInputSingle(
            ISwapRouter02Hardened.ExactInputSingleParams({
                tokenIn: WETH, tokenOut: USDC, fee: 500,
                recipient: address(this), amountIn: MANIPULATION_WETH,
                amountOutMinimum: 0, sqrtPriceLimitX96: 0
            }));

        ZeroExecutor executor = new ZeroExecutor(pool);
        executor.setToken(USDC, true);
        executor.setToken(WETH, true);
        executor.setRouter(SWAP_ROUTER_02, true);
        executor.setSelector(
            SWAP_ROUTER_02,
            ISwapRouter02Hardened.exactInputSingle.selector,
            true);
        executor.setMaxLoan(USDC, FLASH_AMOUNT);
        executor.setMaxSteps(2);

        uint256 premium =
            (FLASH_AMOUNT * uint256(IAavePoolHardened(pool).FLASHLOAN_PREMIUM_TOTAL()) + 5_000)
                / 10_000;
        uint256 requiredFinal = FLASH_AMOUNT + premium + MIN_PROFIT;

        ZeroExecutor.Step[] memory steps = new ZeroExecutor.Step[](2);
        steps[0] = ZeroExecutor.Step({
            kind: ZeroExecutor.StepKind.SwapExactInputSingle,
            target: SWAP_ROUTER_02,
            tokenIn: USDC,
            tokenOut: WETH,
            account: address(0),
            amount: FLASH_AMOUNT,
            limit: 1,
            fee: 500,
            recipientMode: 1
        });
        steps[1] = ZeroExecutor.Step({
            kind: ZeroExecutor.StepKind.SwapExactInputSingle,
            target: SWAP_ROUTER_02,
            tokenIn: WETH,
            tokenOut: USDC,
            account: address(0),
            amount: 0,
            limit: requiredFinal,
            fee: 3000,
            recipientMode: 0
        });

        uint256 realized = executor.run(
            USDC, FLASH_AMOUNT, MIN_PROFIT, block.timestamp + 60, steps);
        require(realized >= MIN_PROFIT, "MIN_PROFIT_NOT_REALIZED");
        require(IERC20Hardened(USDC).balanceOf(address(executor)) == realized,
                "POST_REPAY_BALANCE_MISMATCH");
    }

    receive() external payable {}
}
