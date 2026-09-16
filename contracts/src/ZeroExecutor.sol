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

    mapping(address => bool) public allowedToken;
    mapping(address => bool) public allowedRouter;
    mapping(address => mapping(bytes4 => bool)) public allowedSelector;
    mapping(address => bool) public authorizedCaller;
    mapping(address => uint256) public maxLoan;

    uint256 public maxSteps = 4;
    bool public paused;

    bool private active;
    address private activeAsset;
    uint256 private baselineBalance;
    uint256 private requestedMinProfit;

    address private constant MSG_SENDER = address(1);
    address private constant ADDRESS_THIS = address(2);

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
        if (!authorizedCaller[msg.sender]) revert NotAuthorizedCaller();
        if (asset == address(0)) revert InvalidAddress();
        if (paused) revert Paused();
        if (active) revert ActiveRun();
        if (block.timestamp > deadline) revert Expired();
        if (!allowedToken[asset]) revert TokenNotAllowed();
        if (amount == 0 || maxLoan[asset] == 0 || amount > maxLoan[asset]) {
            revert LoanLimitExceeded();
        }
        if (steps.length > maxSteps) revert TooManySteps();

        baselineBalance = IERC20Zero(asset).balanceOf(address(this));
        activeAsset = asset;
        requestedMinProfit = minProfit;
        active = true;

        IAavePoolZero(pool).flashLoanSimple(
            address(this), asset, amount, abi.encode(steps), 0
        );

        active = false;
        _safeApprove(asset, pool, 0);
        uint256 endingBalance = IERC20Zero(asset).balanceOf(address(this));
        uint256 requiredEnding = baselineBalance + minProfit;
        if (endingBalance < requiredEnding) {
            revert MinimumProfitNotMet(endingBalance, requiredEnding);
        }
        realizedProfit = endingBalance - baselineBalance;
        emit ExecutionCompleted(asset, amount, realizedProfit, endingBalance);
        activeAsset = address(0);
        baselineBalance = 0;
        requestedMinProfit = 0;
    }

    function executeOperation(
        address asset, uint256 amount, uint256 premium,
        address initiator, bytes calldata params
    ) external returns (bool) {
        if (msg.sender != pool) revert UnauthorizedPool();
        if (initiator != address(this)) revert UnauthorizedInitiator();
        if (!active || asset != activeAsset) revert WrongAsset();

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
        _safeApprove(asset, pool, 0);
        _safeApprove(asset, pool, amountOwed);
        return true;
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
        bytes4 selector = ISwapRouter02Zero.exactInputSingle.selector;
        if (!allowedSelector[step.target][selector]) {
            revert SelectorNotAllowed();
        }
        address recipient;
        if (step.recipientMode == 0) recipient = MSG_SENDER;
        else if (step.recipientMode == 1) recipient = ADDRESS_THIS;
        else revert InvalidRecipientMode();

        if (step.amount > 0) {
            _safeApprove(step.tokenIn, step.target, 0);
            _safeApprove(step.tokenIn, step.target, step.amount);
        }
        uint256 amountOut = ISwapRouter02Zero(step.target).exactInputSingle(
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
