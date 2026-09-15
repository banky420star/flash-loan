// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ZeroForkExecutor} from "../src/ZeroForkExecutor.sol";

interface VmFork {
    function deal(address account, uint256 newBalance) external;
}

interface IERC20Test {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IWETHToken is IERC20Test {
    function deposit() external payable;
}

interface IPoolAddressesProviderFork {
    function getPool() external view returns (address);
}

contract WethFaucet {
    IERC20Test public immutable token;

    constructor(address token_) {
        token = IERC20Test(token_);
    }

    function pay(address recipient, uint256 amount) external {
        require(token.transfer(recipient, amount), "FAUCET_TRANSFER");
    }
}

contract ZeroForkExecutorForkTest {
    VmFork internal constant vm = VmFork(
        address(uint160(uint256(keccak256("hevm cheat code"))))
    );

    address internal constant AAVE_PROVIDER =
        0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address internal constant WETH =
        0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;

    function testRealAaveFlashLoanRepaysOnArbitrumFork() external {
        address pool = IPoolAddressesProviderFork(AAVE_PROVIDER).getPool();
        require(pool.code.length > 0, "AAVE_POOL_MISSING");
        require(WETH.code.length > 0, "WETH_MISSING");

        ZeroForkExecutor executor = new ZeroForkExecutor(pool);
        WethFaucet faucet = new WethFaucet(WETH);

        vm.deal(address(this), 1 ether);
        IWETHToken(WETH).deposit{value: 0.1 ether}();
        require(IERC20Test(WETH).transfer(address(faucet), 0.05 ether), "FUND_FAUCET");

        ZeroForkExecutor.Step[] memory steps = new ZeroForkExecutor.Step[](1);
        steps[0] = ZeroForkExecutor.Step({
            target: address(faucet),
            value: 0,
            data: abi.encodeCall(WethFaucet.pay, (address(executor), 0.01 ether))
        });

        uint256 realized = executor.run(WETH, 1 ether, 0, steps);
        require(realized > 0, "NO_POST_REPAY_BALANCE");
        require(IERC20Test(WETH).balanceOf(address(executor)) == realized, "BALANCE_MISMATCH");
    }

    receive() external payable {}
}
