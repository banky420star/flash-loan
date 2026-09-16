// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmLiveLiquidation {
    function envAddress(string calldata name) external view returns (address value);
    function envUint(string calldata name) external view returns (uint256 value);
    function envBytes(string calldata name) external view returns (bytes memory value);
    function envString(string calldata name) external view returns (string memory value);
    function toString(uint256 value) external pure returns (string memory stringifiedValue);
    function writeFile(string calldata path, string calldata data) external;
}

interface IPoolAddressesProviderLiveLiquidation {
    function getPool() external view returns (address);
}

contract ZeroLiveLiquidationForkTest {
    VmLiveLiquidation internal constant vm = VmLiveLiquidation(
        address(uint160(uint256(keccak256("hevm cheat code"))))
    );

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;

    function testExactLiquidationCalldataOnDetectionBlock() external {
        address asset = vm.envAddress("ZERO_ASSET");
        uint256 loanAmount = vm.envUint("ZERO_LOAN_RAW");
        uint256 minProfit = vm.envUint("ZERO_MIN_PROFIT_RAW");
        string memory resultPath = vm.envString("ZERO_RESULT_PATH");

        address pool = IPoolAddressesProviderLiveLiquidation(AAVE_PROVIDER).getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");
        require(asset.code.length > 0, "ASSET_MISSING");

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        ZeroForkExecutor.Step[] memory steps = new ZeroForkExecutor.Step[](4);
        steps[0] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP0_TARGET"), value: 0,
            data: vm.envBytes("ZERO_STEP0_DATA")
        });
        steps[1] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP1_TARGET"), value: 0,
            data: vm.envBytes("ZERO_STEP1_DATA")
        });
        steps[2] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP2_TARGET"), value: 0,
            data: vm.envBytes("ZERO_STEP2_DATA")
        });
        steps[3] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP3_TARGET"), value: 0,
            data: vm.envBytes("ZERO_STEP3_DATA")
        });

        uint256 gasBefore = gasleft();
        uint256 realized = executor.run(asset, loanAmount, minProfit, steps);
        uint256 gasUsed = gasBefore - gasleft();
        require(realized >= minProfit, "LIQUIDATION_MIN_PROFIT_NOT_REALIZED");

        vm.writeFile(resultPath, string.concat(
            "{\"realized_raw\":\"", vm.toString(realized),
            "\",\"gas_used\":\"", vm.toString(gasUsed), "\"}"
        ));
    }
}
