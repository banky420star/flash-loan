// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmLiveCandidate {
    function envAddress(string calldata name) external view returns (address value);
    function envUint(string calldata name) external view returns (uint256 value);
    function envBytes(string calldata name) external view returns (bytes memory value);
}

interface IPoolAddressesProviderLiveCandidate {
    function getPool() external view returns (address);
}

contract ZeroLiveCandidateForkTest {
    VmLiveCandidate internal constant vm = VmLiveCandidate(
        address(uint160(uint256(keccak256("hevm cheat code"))))
    );

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;

    function testExactPythonCandidateCalldataOnDetectionBlock() external {
        address asset = vm.envAddress("ZERO_ASSET");
        uint256 loanAmount = vm.envUint("ZERO_LOAN_RAW");
        uint256 minProfit = vm.envUint("ZERO_MIN_PROFIT_RAW");

        address pool = IPoolAddressesProviderLiveCandidate(AAVE_PROVIDER).getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");
        require(asset.code.length > 0, "ASSET_MISSING");

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        ZeroForkExecutor.Step[] memory steps = new ZeroForkExecutor.Step[](3);
        steps[0] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP0_TARGET"),
            value: 0,
            data: vm.envBytes("ZERO_STEP0_DATA")
        });
        steps[1] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP1_TARGET"),
            value: 0,
            data: vm.envBytes("ZERO_STEP1_DATA")
        });
        steps[2] = ZeroForkExecutor.Step({
            target: vm.envAddress("ZERO_STEP2_TARGET"),
            value: 0,
            data: vm.envBytes("ZERO_STEP2_DATA")
        });

        uint256 realized = executor.run(asset, loanAmount, minProfit, steps);
        require(realized >= minProfit, "CANDIDATE_MIN_PROFIT_NOT_REALIZED");
    }
}
