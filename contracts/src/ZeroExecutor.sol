// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Zero {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IAavePoolZero {
    function flashLoanSimple(
        address receiverAddress, address asset, uint256 amount,
        bytes calldata params, uint16 referralCode
    ) external;

    function liquidationCall(
        address collateralAsset, address debtAsset, address user,
        uint256 debtToCover, bool receiveAToken
    ) external;
}

interface IBalancerVaultZero {
    function flashLoan(
        address recipient,
        address[] calldata tokens,
        uint256[] calldata amounts,
        bytes calldata userData
    ) external;
}

interface ISwapRouter02Zero {
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

// Sushi's Arbitrum V3 router is the classic periphery SwapRouter: same
// pool interface, but its params carry a deadline and it does NOT map the
// 0x…1 msg.sender sentinel (tokens sent to the sentinel burn).
interface ISushiRouterZero {
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
        external payable returns (uint256 amountOut);
}

contract ZeroExecutor {
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

    error NotOwner();
    error NotAuthorizedCaller();
    error Paused();
    error ActiveRun();
    error InvalidAddress();
    error InvalidFlashParams();
    error InvalidRecipientMode();
    error UnauthorizedPool();
    error UnauthorizedInitiator();
    error WrongAsset();
    error TokenNotAllowed();
    error RouterNotAllowed();
    error SelectorNotAllowed();
    error TooManySteps();
    error LoanLimitExceeded();
    error Expired();
    error ApprovalFailed();
    error MinimumOutputNotMet();
    error MinimumProfitNotMet(uint256 balance, uint256 requiredBalance);

    event ExecutionCompleted(
        address indexed asset, uint256 amount,
        uint256 realizedProfit, uint256 endingBalance
    );
    event PauseChanged(bool paused);
    event CallerPermissionChanged(address indexed caller, bool allowed);

    address public immutable pool;
    address public immutable owner;
    address public balancerVault;

    mapping(address => bool) public allowedToken;
    mapping(address => bool) public allowedRouter;
    mapping(address => mapping(bytes4 => bool)) public allowedSelector;
    mapping(address => bool) public authorizedCaller;
    mapping(address => uint256) public maxLoan;

    uint256 public maxSteps = 4;
    bool public paused;

    bool private active;
    bool private viaBalancer;
    address private activeAsset;
    uint256 private baselineBalance;
    uint256 private activeAmount;
    uint256 private requestedMinProfit;

    address private constant MSG_SENDER = address(1);
    address private constant ADDRESS_THIS = address(2);
    address private constant SUSHI_V3_ROUTER =
        0x8A21F6768C1f8075791D08546Dadf6daA0bE820c;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address pool_) {
        if (pool_ == address(0)) revert InvalidAddress();
        pool = pool_;
        owner = msg.sender;
        authorizedCaller[msg.sender] = true;
    }

    function setToken(address token, bool allowed) external onlyOwner {
        if (token == address(0)) revert InvalidAddress();
        allowedToken[token] = allowed;
    }

    function setRouter(address router, bool allowed) external onlyOwner {
        if (router == address(0)) revert InvalidAddress();
        allowedRouter[router] = allowed;
    }

    function setSelector(address target, bytes4 selector, bool allowed)
        external onlyOwner
    {
        if (target == address(0)) revert InvalidAddress();
        allowedSelector[target][selector] = allowed;
    }

    function setCaller(address caller, bool allowed) external onlyOwner {
        if (caller == address(0)) revert InvalidAddress();
        authorizedCaller[caller] = allowed;
        emit CallerPermissionChanged(caller, allowed);
    }

    /// @notice Trust a Balancer V2 Vault as a second flash-loan source.
    /// @dev Balancer charges no premium (feeAmounts arrive 0), which lowers
    ///      the break-even spread versus the Aave path. The owner pins the
    ///      vault once; every flash callback still checks msg.sender.
    function setBalancerVault(address vault) external onlyOwner {
        if (vault == address(0)) revert InvalidAddress();
        if (vault == pool) revert InvalidAddress();
        balancerVault = vault;
    }

    function setMaxLoan(address asset, uint256 amount) external onlyOwner {
        if (asset == address(0)) revert InvalidAddress();
        maxLoan[asset] = amount;
    }

    function setMaxSteps(uint256 value) external onlyOwner {
        if (value == 0 || value > 8) revert TooManySteps();
        maxSteps = value;
    }

    function pause() external onlyOwner {
        paused = true;
        emit PauseChanged(true);
    }

    function unpause() external onlyOwner {
        paused = false;
        emit PauseChanged(false);
    }

    function run(
        address asset, uint256 amount, uint256 minProfit,
        uint256 deadline, Step[] calldata steps
    ) external returns (uint256 realizedProfit) {
        _beginRun(asset, amount, minProfit, deadline, steps.length);

        IAavePoolZero(pool).flashLoanSimple(
            address(this), asset, amount, abi.encode(steps), 0
        );

        realizedProfit = _endRun(asset, minProfit);
    }

    /// @notice Flash loan from the owner-pinned Balancer Vault (0 premium)
    ///         instead of Aave, running the same step pipeline.
    function runBalancer(
        address asset, uint256 amount, uint256 minProfit,
        uint256 deadline, Step[] calldata steps
    ) external returns (uint256 realizedProfit) {
        address vault = balancerVault;
        if (vault == address(0)) revert InvalidAddress();
        _beginRun(asset, amount, minProfit, deadline, steps.length);
        viaBalancer = true;

        address[] memory tokens = new address[](1);
        tokens[0] = asset;
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amount;
        IBalancerVaultZero(vault).flashLoan(
            address(this), tokens, amounts, abi.encode(steps)
        );

        viaBalancer = false;
        realizedProfit = _endRun(asset, minProfit);
    }

    function _beginRun(
        address asset, uint256 amount, uint256 minProfit,
        uint256 deadline, uint256 stepCount
    ) private {
        if (!authorizedCaller[msg.sender]) revert NotAuthorizedCaller();
        if (asset == address(0)) revert InvalidAddress();
        if (paused) revert Paused();
        if (active) revert ActiveRun();
        if (block.timestamp > deadline) revert Expired();
        if (!allowedToken[asset]) revert TokenNotAllowed();
        if (amount == 0 || maxLoan[asset] == 0 || amount > maxLoan[asset]) {
            revert LoanLimitExceeded();
        }
        if (stepCount > maxSteps) revert TooManySteps();

        baselineBalance = IERC20Zero(asset).balanceOf(address(this));
        activeAsset = asset;
        activeAmount = amount;
        requestedMinProfit = minProfit;
        active = true;
    }

    function _endRun(address asset, uint256 minProfit)
        private returns (uint256 realizedProfit)
    {
        address lender = viaBalancer ? balancerVault : pool;
        viaBalancer = false;
        active = false;
        _safeApprove(asset, lender, 0);
        uint256 endingBalance = IERC20Zero(asset).balanceOf(address(this));
        uint256 requiredEnding = baselineBalance + minProfit;
        if (endingBalance < requiredEnding) {
            revert MinimumProfitNotMet(endingBalance, requiredEnding);
        }
        realizedProfit = endingBalance - baselineBalance;
        emit ExecutionCompleted(asset, activeAmount, realizedProfit, endingBalance);
        activeAsset = address(0);
        activeAmount = 0;
        baselineBalance = 0;
        requestedMinProfit = 0;
    }

    function executeOperation(
        address asset, uint256 amount, uint256 premium,
        address initiator, bytes calldata params
    ) external returns (bool) {
        if (msg.sender != pool) revert UnauthorizedPool();
        if (initiator != address(this)) revert UnauthorizedInitiator();
        if (!active || viaBalancer || asset != activeAsset) revert WrongAsset();
        _runFlashBody(asset, amount, premium, params);
        return true;
    }

    /// @notice Balancer V2 Vault flash-loan callback (0 premium).
    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata userData
    ) external {
        if (msg.sender != balancerVault) revert UnauthorizedPool();
        if (!active || !viaBalancer) revert WrongAsset();
        if (tokens.length != 1 || amounts.length != 1 || feeAmounts.length != 1) {
            revert InvalidFlashParams();
        }
        if (tokens[0] != activeAsset) revert WrongAsset();
        if (feeAmounts[0] != 0) revert InvalidFlashParams();
        _runFlashBody(tokens[0], amounts[0], 0, userData);
    }

    function _runFlashBody(
        address asset, uint256 amount, uint256 premium, bytes calldata params
    ) private {
        Step[] memory steps = abi.decode(params, (Step[]));
        if (steps.length > maxSteps) revert TooManySteps();
        for (uint256 i = 0; i < steps.length; ++i) {
            _executeStep(steps[i]);
        }

        uint256 amountOwed = amount + premium;
        uint256 currentBalance = IERC20Zero(asset).balanceOf(address(this));
        uint256 requiredBalance =
            baselineBalance + amountOwed + requestedMinProfit;
        if (currentBalance < requiredBalance) {
            revert MinimumProfitNotMet(currentBalance, requiredBalance);
        }
        _safeApprove(asset, msg.sender, 0);
        _safeApprove(asset, msg.sender, amountOwed);
    }

    function _executeStep(Step memory step) internal {
        if (step.kind == StepKind.SwapExactInputSingle) {
            _executeSwap(step);
            return;
        }
        if (step.kind == StepKind.AaveLiquidation) {
            _executeLiquidation(step);
            return;
        }
        revert SelectorNotAllowed();
    }

    function _executeSwap(Step memory step) internal {
        if (!allowedRouter[step.target]) revert RouterNotAllowed();
        if (!allowedToken[step.tokenIn] || !allowedToken[step.tokenOut]) {
            revert TokenNotAllowed();
        }
        address recipient;
        if (step.recipientMode == 0) recipient = MSG_SENDER;
        else if (step.recipientMode == 1) recipient = ADDRESS_THIS;
        else revert InvalidRecipientMode();

        if (step.amount > 0) {
            _safeApprove(step.tokenIn, step.target, 0);
            _safeApprove(step.tokenIn, step.target, step.amount);
        }
        uint256 amountOut;
        if (step.target == SUSHI_V3_ROUTER) {
            bytes4 sushiSelector = ISushiRouterZero.exactInputSingle.selector;
            if (!allowedSelector[step.target][sushiSelector]) {
                revert SelectorNotAllowed();
            }
            // Sushi lacks the msg.sender sentinel: both recipient modes
            // resolve to this executor, which custodies the output for the
            // flash-loan repay.
            amountOut = ISushiRouterZero(step.target).exactInputSingle(
                ISushiRouterZero.ExactInputSingleParams({
                    tokenIn: step.tokenIn,
                    tokenOut: step.tokenOut,
                    fee: step.fee,
                    recipient: address(this),
                    deadline: block.timestamp + 300,
                    amountIn: step.amount,
                    amountOutMinimum: step.limit,
                    sqrtPriceLimitX96: 0
                })
            );
        } else {
            bytes4 selector = ISwapRouter02Zero.exactInputSingle.selector;
            if (!allowedSelector[step.target][selector]) {
                revert SelectorNotAllowed();
            }
            amountOut = ISwapRouter02Zero(step.target).exactInputSingle(
                ISwapRouter02Zero.ExactInputSingleParams({
                    tokenIn: step.tokenIn,
                    tokenOut: step.tokenOut,
                    fee: step.fee,
                    recipient: recipient,
                    amountIn: step.amount,
                    amountOutMinimum: step.limit,
                    sqrtPriceLimitX96: 0
                })
            );
        }
        if (amountOut < step.limit) revert MinimumOutputNotMet();
        if (step.amount > 0) {
            _safeApprove(step.tokenIn, step.target, 0);
        }
    }

    function _executeLiquidation(Step memory step) internal {
        if (step.target != pool) revert RouterNotAllowed();
        if (!allowedToken[step.tokenIn] || !allowedToken[step.tokenOut]) {
            revert TokenNotAllowed();
        }
        if (step.account == address(0)) revert InvalidAddress();
        bytes4 selector = IAavePoolZero.liquidationCall.selector;
        if (!allowedSelector[pool][selector]) revert SelectorNotAllowed();

        _safeApprove(step.tokenOut, pool, 0);
        _safeApprove(step.tokenOut, pool, step.amount);
        IAavePoolZero(pool).liquidationCall(
            step.tokenIn,
            step.tokenOut,
            step.account,
            step.amount,
            false
        );
        _safeApprove(step.tokenOut, pool, 0);
    }

    function _safeApprove(address token, address spender, uint256 amount)
        internal
    {
        if (!IERC20Zero(token).approve(spender, amount)) {
            revert ApprovalFailed();
        }
    }
}
