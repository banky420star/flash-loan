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

    error ActiveRun();
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
            (bool ok, bytes memory revertData) = steps[i].target.call{value: steps[i].value}(
                steps[i].data
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

    receive() external payable {}
}
