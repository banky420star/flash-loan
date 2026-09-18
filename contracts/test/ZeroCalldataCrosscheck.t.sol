// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Cross-check: the Python live gate's executor calldata must be byte-identical
// to Solidity's encoding of the same call. ZERO_RUN_AAVE and ZERO_RUN_BALANCER
// are hex payloads produced by zero.live_executor.build_run_calldata; this
// fork test re-encodes the same arguments natively and asserts equality.

interface VmCross {
    function envBytes(string calldata name) external view returns (bytes memory value);
}

contract ZeroCalldataCrosscheck {
    VmCross internal constant vm = VmCross(
        address(uint160(uint256(keccak256("hevm cheat code")))));

    enum StepKind { SwapExactInputSingle, AaveLiquidation }

    struct Step {
        StepKind kind;
        address target;
        address tokenIn;
        address tokenOut;
        address account;
        uint256 amount;
        uint256 limit;
        uint24 fee;
        uint8 recipientMode;
    }

    // Mirrors the sample the Python side encodes (kept in sync with
    // scripts/encode_calldata_sample.py).
    Step[] sample;

    constructor() {
        sample.push(Step({
            kind: StepKind.SwapExactInputSingle,
            target: 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45,
            tokenIn: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1,
            tokenOut: 0xaf88d065e77c8cC2239327C5EDb3A432268e5831,
            account: address(1),
            amount: 25_000_000_000000,
            limit: 1_000_000_000000,
            fee: 3000,
            recipientMode: 1
        }));
    }

    function _sampleSteps() internal view returns (Step[] memory steps) {
        steps = new Step[](1);
        steps[0] = sample[0];
    }


    function testPythonAaveCalldataMatchesSolidity() external view {
        bytes memory py = vm.envBytes("ZERO_RUN_AAVE");
        bytes memory native = abi.encodeWithSelector(
            ZeroExecutor_run.selector(),
            0xaf88d065e77c8cC2239327C5EDb3A432268e5831,
            uint256(25_000_000_000000),
            uint256(500),
            uint256(1_700_000_000),
            _sampleSteps()
        );
        require(keccak256(py) == keccak256(native),
                "python aave calldata != solidity");
    }

    function testPythonBalancerCalldataMatchesSolidity() external view {
        bytes memory py = vm.envBytes("ZERO_RUN_BALANCER");
        bytes memory native = abi.encodeWithSelector(
            ZeroExecutor_runBalancer.selector(),
            0xaf88d065e77c8cC2239327C5EDb3A432268e5831,
            uint256(25_000_000_000000),
            uint256(500),
            uint256(1_700_000_000),
            _sampleSteps()
        );
        require(keccak256(py) == keccak256(native),
                "python balancer calldata != solidity");
    }
}

library ZeroExecutor_run {
    function selector() internal pure returns (bytes4) {
        return bytes4(keccak256("run(address,uint256,uint256,uint256,(uint8,address,address,address,uint256,uint256,uint24,uint8)[])"));
    }
}

library ZeroExecutor_runBalancer {
    function selector() internal pure returns (bytes4) {
        return bytes4(keccak256("runBalancer(address,uint256,uint256,uint256,(uint8,address,address,address,uint256,uint256,uint24,uint8)[])"));
    }
}