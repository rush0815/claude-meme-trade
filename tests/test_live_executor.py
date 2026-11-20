"""Live-Executor mit Fake-RPC/Jupiter: Signatur, Mengen- und Kostenberechnung."""
import asyncio
import base64

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from memebot.agents.base import Ctx
from memebot.agents.executor import LiveExecutor
from memebot.agents.risk_manager import Portfolio
from memebot.config import load_config
from memebot.db import DB
from memebot.models import Market


class FakeJup:
    def __init__(self, kp):
        self.kp = kp

    async def quote(self, i, o, amount, slip):
        return {"outAmount": "5000000000", "priceImpactPct": "0.01"}

    @staticmethod
    def impact_pct(q):
        return 1.0

    async def swap_tx(self, quote, user, prio):
        assert user == str(self.kp.pubkey())
        ix = transfer(TransferParams(from_pubkey=self.kp.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))
        msg = MessageV0.try_compile(self.kp.pubkey(), [ix], [], Hash.default())
        unsigned = VersionedTransaction.populate(msg, [Signature.default()])
        return base64.b64encode(bytes(unsigned)).decode()


class FakeRPC:
    def __init__(self, kp):
        self.kp, self.sol, self.tok, self.sent = kp, 1.0, 0, []

    async def decimals(self, mint):
        return 6

    async def sol_balance(self, pk):
        return self.sol

    async def token_balance_raw(self, owner, mint):
        return self.tok

    async def send_raw(self, raw):
        tx = VersionedTransaction.from_bytes(raw)
        assert tx.signatures[0] != Signature.default()          # wirklich signiert
        assert tx.verify_with_results() == [True]
        self.sent.append(tx)
        self.sol -= 0.1 + 0.0002                                 # Swap + Gebühren
        self.tok += 4_900_000_000
        return "SIG123"

    async def confirm(self, sig, timeout_s=60):
        return True


def test_live_buy(monkeypatch):
    kp = Keypair()
    monkeypatch.setenv("SOLANA_PRIVATE_KEY", str(kp))
    cfg = load_config()
    cfg["mode"] = "live"
    ctx = Ctx(cfg=cfg, db=DB(":memory:"), jup=FakeJup(kp), rpc=FakeRPC(kp))
    ex = LiveExecutor(ctx, Portfolio(ctx))
    m = Market(mint="MINT", symbol="T", price_sol=2e-5)
    fill = asyncio.run(ex.buy(m, 0.1))
    assert fill.signature == "SIG123"
    assert abs(fill.token_amount - 4900) < 1e-9
    assert abs(fill.sol_amount - 0.1002) < 1e-9 and fill.fee_sol > 0
