"""Executor: führt Käufe/Verkäufe aus – Paper (Spielgeld, echte Kurse) oder Live (Jupiter + RPC)."""
from __future__ import annotations

import base64
import os

from ..models import LAMPORTS, SOL_MINT, Fill, Market
from .base import Agent

TX_FEE_SOL = 0.000005


class BaseExecutor(Agent):
    name = "Executor"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._dec: dict[str, int] = {}

    async def decimals(self, mint: str, hint: int | None = None) -> int:
        if mint in self._dec:
            return self._dec[mint]
        d = None
        if self.ctx.rpc is not None:
            try:
                d = await self.ctx.rpc.decimals(mint)
            except Exception:
                d = None
        if d is None:
            d = int(hint) if hint is not None else 6   # pump.fun-Token haben 6 Dezimalstellen
        self._dec[mint] = d
        return d

    async def buy(self, m: Market, size_sol: float, decimals_hint: int | None = None) -> Fill:
        raise NotImplementedError

    async def sell(self, pos: dict, tokens: float, reason: str, price_hint: float) -> Fill:
        raise NotImplementedError


class PaperExecutor(BaseExecutor):
    """Simuliert Trades. Nutzt echte Jupiter-Quotes, sonst DexScreener-Preis, plus Sicherheitsabschlag."""
    name = "Executor (Paper)"

    def _fees(self) -> float:
        return TX_FEE_SOL + self.cfg.execution.priority_fee_lamports / LAMPORTS * 0.5

    def _cash(self) -> float:
        return float(self.db.get("paper_cash", self.cfg.paper_start_sol))

    async def buy(self, m: Market, size_sol: float, decimals_hint: int | None = None) -> Fill:
        ex = self.cfg.execution
        dec = await self.decimals(m.mint, decimals_hint)
        fee = self._fees()
        spend = size_sol - fee
        tokens = None
        q = await self.ctx.jup.quote(SOL_MINT, m.mint, int(spend * LAMPORTS), ex.slippage_bps) if self.ctx.jup else None
        if q:
            if self.ctx.jup.impact_pct(q) > ex.max_buy_impact_pct:
                raise RuntimeError(f"Preiseinfluss {self.ctx.jup.impact_pct(q):.1f}% zu hoch")
            tokens = int(q["outAmount"]) / 10 ** dec
        if not tokens:
            tokens = spend / m.price_sol
        tokens *= 1 - ex.paper_extra_slippage_pct / 100
        self.db.set("paper_cash", self._cash() - size_sol)
        return Fill(sol_amount=size_sol, token_amount=tokens, price=size_sol / tokens, fee_sol=fee,
                    signature="paper")

    async def sell(self, pos: dict, tokens: float, reason: str, price_hint: float) -> Fill:
        ex = self.cfg.execution
        dec = await self.decimals(pos["mint"])
        fee = self._fees()
        sol = None
        q = await self.ctx.jup.quote(pos["mint"], SOL_MINT, int(tokens * 10 ** dec), ex.slippage_bps) \
            if self.ctx.jup else None
        if q:
            sol = int(q["outAmount"]) / LAMPORTS
        if sol is None:
            sol = tokens * price_hint
        sol = max(0.0, sol * (1 - ex.paper_extra_slippage_pct / 100) - fee)
        self.db.set("paper_cash", self._cash() + sol)
        return Fill(sol_amount=sol, token_amount=tokens, price=sol / tokens if tokens else 0, fee_sol=fee,
                    signature="paper")


class LiveExecutor(BaseExecutor):
    """Echte Swaps: Jupiter baut die Transaktion, wir signieren lokal und senden über den eigenen RPC."""
    name = "Executor (Live)"

    def __init__(self, ctx, portfolio):
        super().__init__(ctx)
        from solders.keypair import Keypair  # nur im Live-Modus nötig

        key = os.environ.get("SOLANA_PRIVATE_KEY", "").strip()
        if not key:
            raise RuntimeError("SOLANA_PRIVATE_KEY fehlt (Base58-Secret der Bot-Wallet)")
        self.kp = Keypair.from_base58_string(key)
        self.pubkey = str(self.kp.pubkey())
        self.pf = portfolio

    async def _send(self, quote: dict) -> str:
        from solders.transaction import VersionedTransaction

        b64 = await self.ctx.jup.swap_tx(quote, self.pubkey, self.cfg.execution.priority_fee_lamports)
        raw = VersionedTransaction.from_bytes(base64.b64decode(b64))
        signed = VersionedTransaction(raw.message, [self.kp])
        sig = await self.ctx.rpc.send_raw(bytes(signed))
        ok = await self.ctx.rpc.confirm(sig, self.cfg.execution.confirm_timeout_s)
        if not ok:
            raise RuntimeError(f"Transaktion nicht bestätigt: {sig}")
        return sig

    async def buy(self, m: Market, size_sol: float, decimals_hint: int | None = None) -> Fill:
        ex = self.cfg.execution
        dec = await self.decimals(m.mint, decimals_hint)
        q = await self.ctx.jup.quote(SOL_MINT, m.mint, int(size_sol * LAMPORTS), ex.slippage_bps)
        if not q:
            raise RuntimeError("keine Jupiter-Route für Kauf")
        if self.ctx.jup.impact_pct(q) > ex.max_buy_impact_pct:
            raise RuntimeError(f"Preiseinfluss {self.ctx.jup.impact_pct(q):.1f}% zu hoch")
        sol_before = await self.ctx.rpc.sol_balance(self.pubkey)
        tok_before = await self.ctx.rpc.token_balance_raw(self.pubkey, m.mint)
        sig = await self._send(q)
        sol_after = await self.ctx.rpc.sol_balance(self.pubkey)
        tok_after = await self.ctx.rpc.token_balance_raw(self.pubkey, m.mint)
        self.pf.invalidate()
        tokens = (tok_after - tok_before) / 10 ** dec or int(q["outAmount"]) / 10 ** dec
        spent = max(sol_before - sol_after, size_sol)
        return Fill(sol_amount=spent, token_amount=tokens, price=spent / tokens, fee_sol=spent - size_sol,
                    signature=sig)

    async def sell(self, pos: dict, tokens: float, reason: str, price_hint: float) -> Fill:
        ex = self.cfg.execution
        dec = await self.decimals(pos["mint"])
        held = await self.ctx.rpc.token_balance_raw(self.pubkey, pos["mint"])
        raw = min(int(tokens * 10 ** dec), held)
        if raw <= 0:
            raise RuntimeError("keine Token in der Wallet")
        q = await self.ctx.jup.quote(pos["mint"], SOL_MINT, raw, ex.slippage_bps)
        if not q:
            raise RuntimeError("keine Jupiter-Route für Verkauf")
        sol_before = await self.ctx.rpc.sol_balance(self.pubkey)
        sig = await self._send(q)
        sol_after = await self.ctx.rpc.sol_balance(self.pubkey)
        self.pf.invalidate()
        got = max(sol_after - sol_before, 0.0)
        sold = raw / 10 ** dec
        return Fill(sol_amount=got, token_amount=sold, price=got / sold if sold else 0, signature=sig)
