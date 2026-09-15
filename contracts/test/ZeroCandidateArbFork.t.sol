// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmCandidateFork {
    function deal(address account, uint256 newBalance) external;
}

interface IERC20Candidate {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHCandidate is IERC20Candidate {
    function deposit() external payable;
}

interface IAavePoolCandidate {
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
}

interface IPoolAddressesProviderCandidate {
    function getPool() external view returns (address);
}

interface ISwapRouter02Candidate {
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
        external
        payable
        returns (uint256 amountOut);
}

contract ZeroCandidateArbForkTest {
    VmCandidateFork internal constant vm = VmCandidateFork(
        address(uint160(uint256(keccak256("hevm cheat code"))))
    );

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address internal constant USDC =
        0xaf88d065e77c8cc2239327c5edb3a432268e5831;
    address internal constant WETH =
        0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address internal constant SWAP_ROUTER_02 =
        0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;

    address internal constant MSG_SENDER = address(1);
    address internal constant ADDRESS_THIS = address(2);

    uint256 internal constant MANIPULATION_WETH = 1_000 ether;
    uint256 internal constant FLASH_AMOUNT = 100_000e6;
    uint256 internal constant MIN_PROFIT = 10e6;

    function testCandidateRouteRepaysAndProfitsOnRealPools() external {
        address pool = IPoolAddressesProviderCandidate(AAVE_PROVIDER).getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");
        require(USDC.code.length > 0, "USDC_MISSING");
        require(WETH.code.length > 0, "WETH_MISSING");
        require(SWAP_ROUTER_02.code.length > 0, "ROUTER_MISSING");

        // Create a deterministic, fork-local price discrepancy using a real
        // Uniswap V3 swap from this test account. No tokens are transferred to
        // the executor; its eventual profit must come from the two pools.
        vm.deal(address(this), MANIPULATION_WETH + 1 ether);
        IWETHCandidate(WETH).deposit{value: MANIPULATION_WETH}();
        require(
            IERC20Candidate(WETH).approve(SWAP_ROUTER_02, MANIPULATION_WETH),
            "MANIPULATION_APPROVE"
        );
        ISwapRouter02Candidate(SWAP_ROUTER_02).exactInputSingle(
            ISwapRouter02Candidate.ExactInputSingleParams({
                tokenIn: WETH,
                tokenOut: USDC,
                fee: 500,
                recipient: address(this),
                amountIn: MANIPULATION_WETH,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            })
        );

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        uint256 premium =
            (FLASH_AMOUNT * uint256(IAavePoolCandidate(pool).FLASHLOAN_PREMIUM_TOTAL()) + 5_000)
                / 10_000;
        uint256 requiredFinal = FLASH_AMOUNT + premium + MIN_PROFIT;

        ZeroForkExecutor.Step[] memory steps = new ZeroForkExecutor.Step[](3);
        steps[0] = ZeroForkExecutor.Step({
            target: USDC,
            value: 0,
            data: abi.encodeCall(IERC20Candidate.approve, (SWAP_ROUTER_02, FLASH_AMOUNT))
        });
        steps[1] = ZeroForkExecutor.Step({
            target: SWAP_ROUTER_02,
            value: 0,
            data: abi.encodeCall(
                ISwapRouter02Candidate.exactInputSingle,
                (ISwapRouter02Candidate.ExactInputSingleParams({
                    tokenIn: USDC,
                    tokenOut: WETH,
                    fee: 500,
                    recipient: ADDRESS_THIS,
                    amountIn: FLASH_AMOUNT,
                    amountOutMinimum: 1,
                    sqrtPriceLimitX96: 0
                }))
            )
        });
        steps[2] = ZeroForkExecutor.Step({
            target: SWAP_ROUTER_02,
            value: 0,
            data: abi.encodeCall(
                ISwapRouter02Candidate.exactInputSingle,
                (ISwapRouter02Candidate.ExactInputSingleParams({
                    tokenIn: WETH,
                    tokenOut: USDC,
                    fee: 3000,
                    recipient: MSG_SENDER,
                    amountIn: 0,
                    amountOutMinimum: requiredFinal,
                    sqrtPriceLimitX96: 0
                }))
            )
        });

        uint256 realized = executor.run(USDC, FLASH_AMOUNT, MIN_PROFIT, steps);
        require(realized >= MIN_PROFIT, "MIN_PROFIT_NOT_REALIZED");
        require(
            IERC20Candidate(USDC).balanceOf(address(executor)) == realized,
            "POST_REPAY_BALANCE_MISMATCH"
        );
    }

    receive() external payable {}
}
