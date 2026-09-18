// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Fork {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IAavePoolFork {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

/// @notice Simulation-only executor for local Anvil forks.
/// @dev This contract is intentionally generic so a fork candidate can replay
///      arbitrary protocol calls atomically. It is NOT a production executor.
contract ZeroForkExecutor {
    struct Step {
        address target;
        uint256 value;
        bytes data;
    }

    address private constant MSG_SENDER_SENTINEL = address(1);

    error ActiveRun();
    error InvalidSwapCalldata();
    error InvalidAddress();
    error UnauthorizedPool();
    error UnauthorizedInitiator();
    error WrongAsset();
    error StepFailed(uint256 index, bytes revertData);
    error ApprovalFailed();
    error MinimumProfitNotMet(uint256 balance, uint256 requiredBalance);

    event ForkSimulation(
        address indexed asset,
        uint256 amount,
        uint256 realizedProfit,
        uint256 endingBalance
    );

    address public immutable pool;

    bool private active;
    address private activeAsset;
    uint256 private baselineBalance;
    uint256 private requestedMinProfit;

    constructor(address pool_) {
        if (pool_ == address(0)) revert InvalidAddress();
        pool = pool_;
    }

    function run(
        address asset,
        uint256 amount,
        uint256 minProfit,
        Step[] calldata steps
    ) external returns (uint256 realizedProfit) {
        if (active) revert ActiveRun();
        if (asset == address(0)) revert InvalidAddress();

        baselineBalance = IERC20Fork(asset).balanceOf(address(this));
        activeAsset = asset;
        requestedMinProfit = minProfit;
        active = true;

        IAavePoolFork(pool).flashLoanSimple(
            address(this), asset, amount, abi.encode(steps), 0
        );

        active = false;
        uint256 endingBalance = IERC20Fork(asset).balanceOf(address(this));
        uint256 requiredEndingBalance = baselineBalance + minProfit;
        if (endingBalance < requiredEndingBalance) {
            revert MinimumProfitNotMet(endingBalance, requiredEndingBalance);
        }

        realizedProfit = endingBalance - baselineBalance;
        emit ForkSimulation(asset, amount, realizedProfit, endingBalance);

        activeAsset = address(0);
        baselineBalance = 0;
        requestedMinProfit = 0;
    }

    /// @notice Aave V3 flashLoanSimple callback.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        if (msg.sender != pool) revert UnauthorizedPool();
        if (initiator != address(this)) revert UnauthorizedInitiator();
        if (!active || asset != activeAsset) revert WrongAsset();

        Step[] memory steps = abi.decode(params, (Step[]));
        for (uint256 i = 0; i < steps.length; ++i) {
            bytes memory data = _resolveSwapRecipient(steps[i].data);
            (bool ok, bytes memory revertData) = steps[i].target.call{value: steps[i].value}(
                data
            );
            if (!ok) revert StepFailed(i, revertData);
        }

        uint256 amountOwed = amount + premium;
        uint256 currentBalance = IERC20Fork(asset).balanceOf(address(this));
        uint256 requiredBalance = baselineBalance + amountOwed + requestedMinProfit;
        if (currentBalance < requiredBalance) {
            revert MinimumProfitNotMet(currentBalance, requiredBalance);
        }

        if (!IERC20Fork(asset).approve(pool, 0)) revert ApprovalFailed();
        if (!IERC20Fork(asset).approve(pool, amountOwed)) revert ApprovalFailed();
        return true;
    }

    /// @dev Sushi's Arbitrum V3 router (classic periphery SwapRouter) does not
    ///      map the 0x…1 msg.sender sentinel — tokens sent there burn. Python
    ///      encodes the unwind leg with the sentinel as a placeholder, so the
    ///      executor rewrites the recipient word (word 3, both exactInputSingle
    ///      calldata layouts) to its own address before the call.
    function _resolveSwapRecipient(bytes memory data)
        private view returns (bytes memory)
    {
        if (data.length < 4 + 4 * 32) return data;
        bytes4 selector = bytes4(data);
        // 7-field SwapRouter02 struct and 8-field Sushi deadline struct both
        // place the recipient at word 3 (byte offset 100).
        if (selector != 0x04e45aaf && selector != 0x414bf389) return data;
        uint256 recipientWord;
        assembly {
            recipientWord := mload(add(add(data, 0x20), 100))
        }
        if (address(uint160(recipientWord)) != MSG_SENDER_SENTINEL) return data;
        assembly {
            mstore(add(add(data, 0x20), 100), address())
        }
        return data;
    }

    receive() external payable {}
}
