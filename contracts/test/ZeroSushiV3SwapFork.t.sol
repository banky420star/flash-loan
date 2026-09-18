// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Fork-time evidence that Sushi V3 on Arbitrum executes the exact
// SwapRouter02 interface the production ZeroExecutor encodes, so the
// unwind leg may target Sushi pools the same way it targets Uniswap V3.

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmSushi {
    function deal(address account, uint256 newBalance) external;
    function envBytes(string calldata name) external view returns (bytes memory value);
    function toString(uint256 value) external pure returns (string memory stringifiedValue);
}

interface IERC20Sushi {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHSushi is IERC20Sushi {
    function deposit() external payable;
}

interface ISushiFactory {
    function getPool(address tokenA, address tokenB, uint24 fee)
        external view returns (address);
}

interface ISushiRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external returns (uint256 amountOut);
}

interface ISushiQuoterV2 {
    struct QuoteExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint24 fee;
        uint160 sqrtPriceLimitX96;
    }

    function quoteExactInputSingle(QuoteExactInputSingleParams memory params)
        external
        returns (uint256 amountOut, uint160 sqrtPriceX96After,
                 uint32 initializedTicksCrossed, uint256 gasEstimate);
}

interface IPoolAddressesProviderSushiFork {
    function getPool() external view returns (address);
}

interface IERC20TransferSushi {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract WethFaucet {
    IERC20TransferSushi public immutable token;

    constructor(address token_) {
        token = IERC20TransferSushi(token_);
    }

    function pay(address recipient, uint256 amount) external {
        require(IERC20TransferSushi(address(token)).transfer(recipient, amount),
            "FAUCET_TRANSFER");
    }
}

contract ZeroSushiV3SwapForkTest {
    VmSushi internal constant vm = VmSushi(
        address(uint160(uint256(keccak256("hevm cheat code"))))
    );

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;

    address internal constant WETH =
        0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address internal constant USDC_E =
        0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8;
    address internal constant SUSHI_FACTORY =
        0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e;
    address internal constant SUSHI_ROUTER =
        0x8A21F6768C1f8075791D08546Dadf6daA0bE820c;
    address internal constant SUSHI_QUOTER =
        0x0524E833cCD057e4d7A296e3aaAb9f7675964Ce1;
    uint256 internal constant SWAP_IN = 0.05 ether;

    function testExactInputSingleThroughSushiRouterMatchesQuoter() external {
        require(WETH.code.length > 0, "WETH_MISSING");
        // Pick the deepest pool across fee tiers, as the scanner does.
        uint24[4] memory tiers = [uint24(100), 500, 3000, 10000];
        uint24 fee = 0;
        uint256 bestQuote = 0;
        for (uint256 i = 0; i < tiers.length; i++) {
            address pool = ISushiFactory(SUSHI_FACTORY).getPool(
                WETH, USDC_E, tiers[i]);
            if (pool == address(0)) continue;
            try ISushiQuoterV2(SUSHI_QUOTER).quoteExactInputSingle(
                ISushiQuoterV2.QuoteExactInputSingleParams({
                    tokenIn: WETH, tokenOut: USDC_E, amountIn: SWAP_IN,
                    fee: tiers[i], sqrtPriceLimitX96: 0
                })
            ) returns (uint256 quoted, uint160, uint32, uint256) {
                if (quoted > bestQuote) {
                    bestQuote = quoted;
                    fee = tiers[i];
                }
            } catch {
                continue;
            }
        }
        require(fee != 0, "NO_SUSHI_WETH_USDC_POOL");

        vm.deal(address(this), 1 ether);
        IWETHSushi(WETH).deposit{value: SWAP_IN}();
        IERC20Sushi(WETH).approve(SUSHI_ROUTER, SWAP_IN);

        uint256 amountOut;
        try ISushiRouter02(SUSHI_ROUTER).exactInputSingle(
            ISushiRouter02.ExactInputSingleParams({
                tokenIn: WETH, tokenOut: USDC_E, fee: fee,
                recipient: address(this), deadline: block.timestamp + 60,
                amountIn: SWAP_IN,
                amountOutMinimum: 0, sqrtPriceLimitX96: 0
            })
        ) returns (uint256 out) {
            amountOut = out;
        } catch {
            revert("ROUTER_SWAP_REVERTED");
        }
        require(amountOut > 0, "ROUTER_SWAP_NO_OUTPUT");
        // Quoter and router must agree closely on the same pool state.
        uint256 spread = amountOut > bestQuote
            ? amountOut - bestQuote : bestQuote - amountOut;
        require(spread * 100 <= amountOut, "QUOTE_ROUTER_DIVERGENCE");
        require(IERC20Sushi(USDC_E).balanceOf(address(this)) == amountOut,
            "BALANCE_MISMATCH");
    }

    // Python-encoded sushi unwind leg (sentinel recipient placeholder) must
    // execute through the fork executor, which rewrites the recipient to the
    // executor itself before the call — Sushi's router has no sentinel.
    function testPythonEncodedSushiLegThroughForkExecutor() external {
        address pool = IPoolAddressesProviderSushiFork(AAVE_PROVIDER)
            .getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        WethFaucet faucet = new WethFaucet(WETH);
        vm.deal(address(this), 1 ether);
        IWETHSushi(WETH).deposit{value: SWAP_IN}();
        require(IERC20TransferSushi(WETH).transfer(address(faucet), SWAP_IN),
            "FUND_FAUCET");
        faucet.pay(address(executor), SWAP_IN);

        ZeroForkExecutor.Step[] memory steps =
            new ZeroForkExecutor.Step[](2);
        steps[0] = ZeroForkExecutor.Step({
            target: WETH, value: 0,
            data: vm.envBytes("ZERO_APPROVE_DATA")
        });
        steps[1] = ZeroForkExecutor.Step({
            target: SUSHI_ROUTER, value: 0,
            data: vm.envBytes("ZERO_SWAP_DATA")
        });

        // Small USDC.e flash loan the unwind must cover plus premium.
        uint256 flashAmount = 100_000_000; // 100 USDC.e
        uint256 realized = executor.run(USDC_E, flashAmount, 1, steps);
        require(realized > 0, "NO_REALIZED_PROFIT");
        require(IERC20Sushi(WETH).balanceOf(address(executor)) == 0,
            "WETH_NOT_SPENT");
    }

    receive() external payable {}
}