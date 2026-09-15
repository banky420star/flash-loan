#!/bin/zsh
# ============================================================
#  ZERO ENGINE — one-click run (SHADOW MODE, read-only)
#  No wallet. No signing. No transactions. Ever.
# ============================================================
cd "$(dirname "$0")"

echo ""
echo "   ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡"
echo ""
echo "          Z E R O   E N G I N E"
echo "        Zero-Principal DeFi Scanner"
echo "        Arbitrum One — SHADOW MODE"
echo ""
echo "   🚫 no wallet   🚫 no signing   🚫 no broadcast"
echo ""
echo "   ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡"
echo ""

echo "[1/3] verifying environment..."
python3 -m unittest discover -s tests -q 2>/dev/null && \
  echo "      ✓ unit tests passed" || echo "      ⚠ some unit tests failed"

echo "[2/3] doctor — live Arbitrum One connectivity..."
python3 -m zero.cli doctor || {
  echo "      ✗ chain unreachable — check network / rpc_url"
  exit 1
}

echo "[3/3] shadow cycle (records to zero_ledger.db, sends nothing)..."
echo ""
python3 -m zero.cli prices
echo ""
python3 -m zero.cli shadow --interval 30