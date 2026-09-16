// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmLiquidationFork {
    function deal(address account, uint256 newBalance) external;
    function mockCall(address callee, bytes calldata data, bytes calldata returnData) external;
}

interface IERC20Liquidation {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHLiquidation is IERC20Liquidation {
    function deposit() external payable;
}

interface IPoolAddressesProviderLiquidation {
    function getPool() external view returns (address);
    function getPriceOracle() external view returns (address);
}

interface IAaveOracleLiquidation {
    function getAssetPrice(address asset) external view returns (uint256);
}

interface IAavePoolLiquidation {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function borrow(address asset, uint256 amount, uint256 interestRateMode,
                    uint16 referralCode, address onBehalfOf) external;
    function liquidationCall(address collateralAsset, address debtAsset,
                             address user, uint256 debtToCover,
                             bool receiveAToken) external;
    function getUserAccountData(address user) external view returns (
        uint256 totalCollateralBase, uint256 totalDebtBase,
        uint256 availableBorrowsBase, uint256 currentLiquidationThreshold,
        uint256 ltv, uint256 healthFactor);
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
    function flashLoanSimple(address receiverAddress, address asset,
                             uint256 amount, bytes calldata params,
                             uint16 referralCode) external;
}

interface ISwapRouter02Liquidation {
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

contract ZeroLiquidationForkTest {
    VmLiquidationFork internal constant vm = VmLiquidationFork(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address internal constant USDC =
        0xaf88d065e77c8cC2239327C5EDb3A432268e5831;
    address internal constant WETH =
        0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address internal constant SWAP_ROUTER_02 =
        0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address internal constant MSG_SENDER = address(1);

    uint256 internal constant SUPPLY_WETH = 20 ether;
    uint256 internal constant BORROW_USDC = 5_000e6;
    uint256 internal constant FLASH_AMOUNT = 1_000e6;
    uint256 internal constant UNWIND_WETH = 1.5 ether;
    uint256 internal constant MIN_PROFIT = 10e6;

    function testFlashFundedLiquidationRepaysAndProfits() external {
        address pool = IPoolAddressesProviderLiquidation(AAVE_PROVIDER).getPool();
        address oracle = IPoolAddressesProviderLiquidation(AAVE_PROVIDER).getPriceOracle();
        require(pool.code.length > 0 && oracle.code.length > 0, "AAVE_MISSING");

        vm.deal(address(this), SUPPLY_WETH + 1 ether);
        IWETHLiquidation(WETH).deposit{value: SUPPLY_WETH}();
        require(IERC20Liquidation(WETH).approve(pool, SUPPLY_WETH), "WETH_APPROVE");
        IAavePoolLiquidation(pool).supply(WETH, SUPPLY_WETH, address(this), 0);
        IAavePoolLiquidation(pool).borrow(USDC, BORROW_USDC, 2, 0, address(this));

        vm.mockCall(
            oracle,
            abi.encodeWithSelector(IAaveOracleLiquidation.getAssetPrice.selector, WETH),
            abi.encode(uint256(200e8))
        );
        (,,,,, uint256 healthFactor) =
            IAavePoolLiquidation(pool).getUserAccountData(address(this));
        require(healthFactor < 1e18, "POSITION_NOT_LIQUIDATABLE");

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        uint256 premium =
            (FLASH_AMOUNT * uint256(IAavePoolLiquidation(pool).FLASHLOAN_PREMIUM_TOTAL()) + 5_000)
                / 10_000;
        uint256 requiredFinal = FLASH_AMOUNT + premium + MIN_PROFIT;

        ZeroForkExecutor.Step[] memory steps = new ZeroForkExecutor.Step[](4);
        steps[0] = ZeroForkExecutor.Step({
            target: USDC, value: 0,
            data: abi.encodeCall(IERC20Liquidation.approve, (pool, FLASH_AMOUNT))
        });
        steps[1] = ZeroForkExecutor.Step({
            target: pool, value: 0,
            data: abi.encodeCall(
                IAavePoolLiquidation.liquidationCall,
                (WETH, USDC, address(this), FLASH_AMOUNT, false))
        });
        steps[2] = ZeroForkExecutor.Step({
            target: WETH, value: 0,
            data: abi.encodeCall(
                IERC20Liquidation.approve, (SWAP_ROUTER_02, UNWIND_WETH))
        });
        steps[3] = ZeroForkExecutor.Step({
            target: SWAP_ROUTER_02, value: 0,
            data: abi.encodeCall(
                ISwapRouter02Liquidation.exactInputSingle,
                (ISwapRouter02Liquidation.ExactInputSingleParams({
                    tokenIn: WETH,
                    tokenOut: USDC,
                    fee: 500,
                    recipient: MSG_SENDER,
                    amountIn: UNWIND_WETH,
                    amountOutMinimum: requiredFinal,
                    sqrtPriceLimitX96: 0
                })))
        });

        uint256 realized = executor.run(USDC, FLASH_AMOUNT, MIN_PROFIT, steps);
        require(realized >= MIN_PROFIT, "MIN_PROFIT_NOT_REALIZED");
        require(IERC20Liquidation(USDC).balanceOf(address(executor)) == realized,
                "POST_REPAY_BALANCE_MISMATCH");
    }

    receive() external payable {}
}
