"""Measure live Aave V3 Arbitrum liquidation flow over a recent window."""
import sys
import time

sys.path.insert(0, ".")

from zero.keccak import topic_of
from zero.rpc import Rpc

RPC = "https://arb1.arbitrum.io/rpc"
POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
# LiquidationCall(address indexed collateralAsset, address indexed debtAsset,
#                 address indexed user, uint256 debtToCover,
#                 uint256 liquidatedCollateralAmount, uint256 liquidator)
TOPIC0 = "0x" + topic_of(
    "LiquidationCall(address,address,address,uint256,uint256,uint256)"
).hex()

from zero.rpc import UrllibTransport

rpc = Rpc(RPC, transport=UrllibTransport(RPC, timeout=30.0))


def call(method, params):
    return rpc.call(method, params)


head = rpc.block_number()
window_blocks = int(sys.argv[1]) if len(sys.argv) > 1 else 57600  # ~4h
start = head - window_blocks
chunk = 3000
events = []
failures = 0
for lo in range(start, head, chunk):
    hi = min(lo + chunk - 1, head)
    for attempt in range(3):
        try:
            logs = call("eth_getLogs", [{
                "address": POOL,
                "topics": [TOPIC0],
                "fromBlock": hex(lo),
                "toBlock": hex(hi),
            }])
            events.extend(logs)
            break
        except Exception as exc:
            failures += 1
            if attempt == 2:
                print(f"chunk {lo}-{hi} failed: {exc}", file=sys.stderr)
            time.sleep(1)

print(f"blocks {start}..{head} (~{window_blocks * 0.25 / 3600:.1f}h), "
      f"{len(events)} LiquidationCall events, {failures} failed chunks")

liquidators = {}
pairs = {}
sizes = []
for ev in events:
    liq = "0x" + ev["topics"][3][-40:]
    collateral = "0x" + ev["topics"][0 + 1][-40:]
    debt = "0x" + ev["topics"][2][-40:]
    liquidators[liq] = liquidators.get(liq, 0) + 1
    pairs[(collateral, debt)] = pairs.get((collateral, debt), 0) + 1
    sizes.append(int(ev["data"], 16) if ev.get("data") else 0)

print(f"distinct liquidators: {len(liquidators)}")
for liq, n in sorted(liquidators.items(), key=lambda kv: -kv[1])[:10]:
    print(f"  {liq}  {n}")
print("collateral/debt pairs:")
for (c, d), n in sorted(pairs.items(), key=lambda kv: -kv[1])[:10]:
    print(f"  {c} -> {d}  {n}")
if sizes:
    print(f"liquidatedCollateralAmount raw: min={min(sizes)} "
          f"median={sorted(sizes)[len(sizes)//2]} max={max(sizes)}")

# blocks between consecutive liquidations
if len(events) > 1:
    blocks = sorted(int(ev["blockNumber"], 16) for ev in events)
    gaps = [b - a for a, b in zip(blocks, blocks[1:])]
    print(f"blocks between liquidations: median={sorted(gaps)[len(gaps)//2]} "
          f"min={min(gaps)}")