#!/usr/bin/env python3
"""ZCAT Sentinel — zero-dependency local real-time scanner.

Run:  python3 zcat_sentinel.py
Then open http://127.0.0.1:8765 (the script opens it automatically).

All outbound HTTP requests use the user-requested User-Agent:
OpenAI File Downloader, XaiImageApiFetch/1.0
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

UA = "OpenAI File Downloader, XaiImageApiFetch/1.0"
DEFAULT_TOKEN = "HcRLc9VDgjLeK154xDawfb1dmVJ98DoSqcwTHGqiDeJR"
DEFAULT_REWARD = "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
STONK_OPERATOR = "5CEbueQnq1Ym2uSSx2xXds3jQAqT1BDnkA59RZobSPAG"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
RPC_URL = "https://api.mainnet-beta.solana.com"
HOST = "127.0.0.1"
PORT = 8765

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "zcat_sentinel_history.jsonl"
STATE_FILE = BASE_DIR / "zcat_sentinel_state.json"

CACHE: dict[str, tuple[float, Any]] = {}
CACHE_LOCK = threading.Lock()
HISTORY = deque(maxlen=5000)
LAST_HISTORY_WRITE = 0.0


def now_ts() -> float:
    return time.time()


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or now_ts(), tz=timezone.utc).isoformat()


def n(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def request_json(url: str, *, method: str = "GET", body: Any = None, timeout: int = 8) -> Any:
    data = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8"))


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> dict[str, Any]:
    t = now_ts()
    with CACHE_LOCK:
        old = CACHE.get(key)
    if old and t - old[0] < ttl:
        return {"ok": True, "stale": False, "age": t - old[0], "data": old[1]}
    try:
        value = fn()
        with CACHE_LOCK:
            CACHE[key] = (t, value)
        return {"ok": True, "stale": False, "age": 0, "data": value}
    except Exception as e:
        if old:
            return {"ok": True, "stale": True, "age": t - old[0], "data": old[1], "error": str(e)}
        return {"ok": False, "stale": False, "error": str(e)}


def rpc(method: str, params: list[Any], timeout: int = 10) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    out = request_json(RPC_URL, method="POST", body=payload, timeout=timeout)
    if out.get("error"):
        raise RuntimeError(f"RPC {method}: {out['error']}")
    return out.get("result")


def rpc_batch(method: str, param_sets: list[list[Any]], timeout: int = 20) -> list[Any]:
    if not param_sets:
        return []
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
        for i, params in enumerate(param_sets)
    ]
    out = request_json(RPC_URL, method="POST", body=payload, timeout=timeout)
    if not isinstance(out, list):
        raise RuntimeError(f"RPC batch {method}: unexpected response")
    by_id = {row.get("id"): row for row in out}
    results = []
    for i in range(len(param_sets)):
        row = by_id.get(i, {})
        results.append(None if row.get("error") else row.get("result"))
    return results


def dex_pairs(mint: str) -> list[dict[str, Any]]:
    return request_json(f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}")


def choose_pair(pairs: list[dict[str, Any]], token: str, reward: str) -> dict[str, Any] | None:
    exact = []
    for p in pairs or []:
        b = (p.get("baseToken") or {}).get("address")
        q = (p.get("quoteToken") or {}).get("address")
        if {b, q} == {token, reward}:
            exact.append(p)
    pool = exact or [p for p in (pairs or []) if token in {
        (p.get("baseToken") or {}).get("address"), (p.get("quoteToken") or {}).get("address")
    }]
    if not pool:
        return None
    return max(pool, key=lambda p: n((p.get("liquidity") or {}).get("usd"), 0) or 0)


def normalize_dex_pair(p: dict[str, Any] | None, token: str, reward: str) -> dict[str, Any] | None:
    if not p:
        return None
    base = p.get("baseToken") or {}
    quote = p.get("quoteToken") or {}
    price_usd = n(p.get("priceUsd"))
    price_native = n(p.get("priceNative"))
    token_is_base = base.get("address") == token
    reward_is_base = base.get("address") == reward
    reward_usd = None
    token_usd = None
    if token_is_base:
        token_usd = price_usd
        if quote.get("address") == reward and price_usd is not None and price_native not in (None, 0):
            reward_usd = price_usd / price_native
    elif reward_is_base:
        reward_usd = price_usd
        if quote.get("address") == token and price_usd is not None and price_native not in (None, 0):
            token_usd = price_usd / price_native
    return {
        "pairAddress": p.get("pairAddress"),
        "dexId": p.get("dexId"),
        "url": p.get("url"),
        "baseSymbol": base.get("symbol"),
        "quoteSymbol": quote.get("symbol"),
        "tokenPriceUsd": token_usd,
        "rewardPriceUsdFromPair": reward_usd,
        "marketCap": (n(p.get("marketCap")) or n(p.get("fdv"))) if token_is_base else None,
        "fdv": n(p.get("fdv")) if token_is_base else None,
        "liquidity": n((p.get("liquidity") or {}).get("usd")),
        "volume5m": n((p.get("volume") or {}).get("m5")),
        "volume1h": n((p.get("volume") or {}).get("h1")),
        "volume6h": n((p.get("volume") or {}).get("h6")),
        "volume24h": n((p.get("volume") or {}).get("h24")),
        "change1h": n((p.get("priceChange") or {}).get("h1")),
        "change6h": n((p.get("priceChange") or {}).get("h6")),
        "change24h": n((p.get("priceChange") or {}).get("h24")),
        "tx1h": p.get("txns", {}).get("h1"),
        "tx24h": p.get("txns", {}).get("h24"),
    }


def gecko_pools(mint: str) -> dict[str, Any]:
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
    return request_json(url)


def rel_address(d: dict[str, Any], side: str) -> str | None:
    rid = (((d.get("relationships") or {}).get(side) or {}).get("data") or {}).get("id")
    if not rid:
        return None
    return rid.split("_", 1)[-1]


def choose_gecko(obj: dict[str, Any], token: str, reward: str) -> dict[str, Any] | None:
    rows = obj.get("data") or []
    exact = [d for d in rows if {rel_address(d, "base_token"), rel_address(d, "quote_token")} == {token, reward}]
    candidates = exact or [d for d in rows if token in {rel_address(d, "base_token"), rel_address(d, "quote_token")}]
    if not candidates:
        return None
    return max(candidates, key=lambda d: n((d.get("attributes") or {}).get("reserve_in_usd"), 0) or 0)


def normalize_gecko(d: dict[str, Any] | None, token: str, reward: str) -> dict[str, Any] | None:
    if not d:
        return None
    a = d.get("attributes") or {}
    base_addr = rel_address(d, "base_token")
    quote_addr = rel_address(d, "quote_token")
    token_price = None
    reward_price = None
    if base_addr == token:
        token_price = n(a.get("base_token_price_usd"))
        if quote_addr == reward:
            reward_price = n(a.get("quote_token_price_usd"))
    elif quote_addr == token:
        token_price = n(a.get("quote_token_price_usd"))
        if base_addr == reward:
            reward_price = n(a.get("base_token_price_usd"))
    vol = a.get("volume_usd") or {}
    change = a.get("price_change_percentage") or {}
    return {
        "pairAddress": (d.get("id") or "").split("_", 1)[-1],
        "name": a.get("name"),
        "tokenPriceUsd": token_price,
        "rewardPriceUsdFromPair": reward_price,
        "marketCap": (n(a.get("market_cap_usd")) or n(a.get("fdv_usd"))) if base_addr == token else None,
        "fdv": n(a.get("fdv_usd")) if base_addr == token else None,
        "liquidity": n(a.get("reserve_in_usd")),
        "volume5m": n(vol.get("m5")),
        "volume1h": n(vol.get("h1")),
        "volume6h": n(vol.get("h6")),
        "volume24h": n(vol.get("h24")),
        "change1h": n(change.get("h1")),
        "change6h": n(change.get("h6")),
        "change24h": n(change.get("h24")),
    }


def find_reward_stable_price(pairs: list[dict[str, Any]], reward: str) -> dict[str, Any] | None:
    stable_symbols = {"USDC", "USDT", "USD1", "PYUSD"}
    rows = []
    for p in pairs or []:
        b = p.get("baseToken") or {}
        q = p.get("quoteToken") or {}
        if b.get("address") == reward and q.get("symbol") in stable_symbols:
            rows.append(p)
    if not rows:
        return None
    p = max(rows, key=lambda x: n((x.get("liquidity") or {}).get("usd"), 0) or 0)
    return {
        "priceUsd": n(p.get("priceUsd")),
        "liquidity": n((p.get("liquidity") or {}).get("usd")),
        "volume24h": n((p.get("volume") or {}).get("h24")),
        "dexId": p.get("dexId"),
        "pairAddress": p.get("pairAddress"),
        "url": p.get("url"),
    }


def native_price(symbol: str) -> dict[str, Any]:
    s = symbol.upper().strip()
    kraken_pair = {"BTC": "XBTUSD", "WBTC": "XBTUSD", "ZEC": "ZECUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}.get(s, f"{s}USD")
    errors = []
    try:
        out = request_json(f"https://api.kraken.com/0/public/Ticker?pair={urllib.parse.quote(kraken_pair)}")
        if not out.get("error"):
            result = out.get("result") or {}
            if result:
                row = next(iter(result.values()))
                return {"priceUsd": n((row.get("c") or [None])[0]), "source": "Kraken"}
    except Exception as e:
        errors.append(f"Kraken: {e}")
    cb_sym = "BTC" if s in {"BTC", "WBTC"} else s
    try:
        out = request_json(f"https://api.coinbase.com/v2/prices/{urllib.parse.quote(cb_sym)}-USD/spot")
        return {"priceUsd": n(((out.get("data") or {}).get("amount"))), "source": "Coinbase"}
    except Exception as e:
        errors.append(f"Coinbase: {e}")
    raise RuntimeError("; ".join(errors))


def contract_state(mint: str) -> dict[str, Any]:
    acc = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    epoch = rpc("getEpochInfo", [{"commitment": "confirmed"}])
    if not acc or not acc.get("value"):
        raise RuntimeError("mint account not found")
    value = acc["value"]
    data = value.get("data") or {}
    parsed = data.get("parsed") or {}
    info = parsed.get("info") or {}
    extensions = info.get("extensions") or parsed.get("extensions") or []
    tf = None
    for ext in extensions:
        if str(ext.get("extension", "")).lower() == "transferfeeconfig":
            tf = ext.get("state") or ext
            break
    current_epoch = int(epoch.get("epoch", 0)) if epoch else 0
    decimals = int(info.get("decimals", 0) or 0)
    supply_raw = n(info.get("supply"))
    supply = supply_raw / (10 ** decimals) if supply_raw is not None else None
    result: dict[str, Any] = {
        "programOwner": value.get("owner"),
        "isToken2022": value.get("owner") == TOKEN_2022_PROGRAM,
        "decimals": decimals,
        "supply": supply,
        "currentEpoch": current_epoch,
        "transferFee": None,
    }
    if tf:
        old = tf.get("olderTransferFee") or {}
        new = tf.get("newerTransferFee") or {}
        newer_epoch = int(n(new.get("epoch"), 0) or 0)
        active = new if current_epoch >= newer_epoch else old
        bps = int(n(active.get("transferFeeBasisPoints"), 0) or 0)
        max_raw = n(active.get("maximumFee"))
        max_tokens = max_raw / (10 ** decimals) if max_raw is not None else None
        cap_binds_above = (max_tokens / (bps / 10000.0)) if max_tokens is not None and bps > 0 else None
        state_small = {
            "transferFeeConfigAuthority": tf.get("transferFeeConfigAuthority"),
            "withdrawWithheldAuthority": tf.get("withdrawWithheldAuthority"),
            "withheldAmount": tf.get("withheldAmount"),
            "olderTransferFee": old,
            "newerTransferFee": new,
        }
        result["transferFee"] = {
            "bps": bps,
            "percent": bps / 100.0,
            "maximumFeeRaw": active.get("maximumFee"),
            "maximumFeeTokens": max_tokens,
            "capBindsAboveTokens": cap_binds_above,
            "configAuthority": tf.get("transferFeeConfigAuthority"),
            "withdrawAuthority": tf.get("withdrawWithheldAuthority"),
            "withheldAmountRaw": tf.get("withheldAmount"),
            "activeSchedule": "newer" if active is new else "older",
            "stateHash": hashlib.sha256(json.dumps(state_small, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }
    return result


def jupiter_quote(mint: str, position: float, decimals: int) -> dict[str, Any]:
    amount = int(round(position * (10 ** decimals)))
    q = urllib.parse.urlencode({
        "inputMint": mint,
        "outputMint": USDC,
        "amount": str(amount),
        "slippageBps": "50",
        "instructionVersion": "V2",
    })
    out = request_json(f"https://lite-api.jup.ag/swap/v1/quote?{q}")
    if out.get("error"):
        raise RuntimeError(out.get("error"))
    routes = []
    for r in out.get("routePlan") or []:
        si = r.get("swapInfo") or {}
        if si.get("label"):
            routes.append(si.get("label"))
    return {
        "positionTokens": position,
        "outUsd": n(out.get("outAmount"), 0) / 1_000_000,
        "minimumUsdAt50bps": n(out.get("otherAmountThreshold"), 0) / 1_000_000,
        "priceImpactPct": (n(out.get("priceImpactPct"), 0) or 0) * 100.0,
        "route": " → ".join(dict.fromkeys(routes)),
        "contextSlot": out.get("contextSlot"),
    }


def token_balance_amount(row: dict[str, Any], owner: str, mint: str) -> float:
    if row.get("mint") != mint or row.get("owner") != owner:
        return 0.0
    ui = row.get("uiTokenAmount") or {}
    amt = n(ui.get("uiAmountString"))
    if amt is not None:
        return amt
    raw = n(ui.get("amount"), 0) or 0
    dec = int(ui.get("decimals", 0) or 0)
    return raw / (10 ** dec)


def wallet_rewards(wallet: str, reward: str, distributor: str = STONK_OPERATOR) -> dict[str, Any]:
    accounts = rpc("getTokenAccountsByOwner", [wallet, {"mint": reward}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    rows = (accounts or {}).get("value") or []
    token_accounts = [r.get("pubkey") for r in rows if r.get("pubkey")]
    balance = 0.0
    for r in rows:
        info = (((r.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        amt = ((info.get("tokenAmount") or {}).get("uiAmountString"))
        balance += n(amt, 0) or 0
    sigs: dict[str, dict[str, Any]] = {}
    for ta in token_accounts[:3]:
        for s in rpc("getSignaturesForAddress", [ta, {"limit": 80, "commitment": "confirmed"}]) or []:
            if s.get("signature"):
                sigs[s["signature"]] = s
    cutoff = now_ts() - 8 * 3600
    selected = [s for s in sigs.values() if (s.get("blockTime") or 0) >= cutoff and not s.get("err")]
    selected.sort(key=lambda s: s.get("blockTime") or 0)
    txs = []
    signatures = [s["signature"] for s in selected[-80:]]
    for i in range(0, len(signatures), 20):
        chunk = signatures[i:i+20]
        param_sets = [[sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}] for sig in chunk]
        try:
            txs.extend(zip(chunk, rpc_batch("getTransaction", param_sets, timeout=25)))
        except Exception:
            for sig in chunk:
                try:
                    txs.append((sig, rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}], timeout=15)))
                except Exception:
                    pass
    receipts = []
    for sig, tx in txs:
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = sum(token_balance_amount(x, wallet, reward) for x in (meta.get("preTokenBalances") or []))
        post = sum(token_balance_amount(x, wallet, reward) for x in (meta.get("postTokenBalances") or []))
        delta = post - pre
        if delta <= 1e-12:
            continue
        keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
        pubkeys = set()
        for k in keys:
            if isinstance(k, str):
                pubkeys.add(k)
            elif isinstance(k, dict) and k.get("pubkey"):
                pubkeys.add(k["pubkey"])
        block_time = tx.get("blockTime") or next((s.get("blockTime") for s in selected if s.get("signature") == sig), None)
        receipts.append({
            "signature": sig,
            "time": block_time,
            "amount": delta,
            "attributed": distributor in pubkeys,
        })
    receipts.sort(key=lambda r: r.get("time") or 0)
    attributed = [r for r in receipts if r["attributed"]]
    source = attributed if attributed else []
    out: dict[str, Any] = {
        "wallet": wallet,
        "rewardBalance": balance,
        "rewardTokenAccounts": token_accounts,
        "receipts8h": receipts,
        "attributedReceipts8h": attributed,
        "velocityConfidence": "operator-attributed" if attributed else "unresolved",
        "velocity3h": None,
        "velocity6h": None,
        "medianPayout": None,
        "medianIntervalMinutes": None,
        "lastPayout": None,
    }
    if source:
        t = now_ts()
        out["velocity3h"] = sum(r["amount"] for r in source if (r.get("time") or 0) >= t - 3*3600) / 3.0
        out["velocity6h"] = sum(r["amount"] for r in source if (r.get("time") or 0) >= t - 6*3600) / 6.0
        amts = [r["amount"] for r in source]
        out["medianPayout"] = median(amts)
        times = [r["time"] for r in source if r.get("time")]
        intervals = [(b-a)/60 for a,b in zip(times, times[1:]) if b > a]
        out["medianIntervalMinutes"] = median(intervals)
        out["lastPayout"] = source[-1]
    return out


def compact_interesting(obj: Any, max_fields: int = 45) -> dict[str, Any]:
    hits: dict[str, Any] = {}
    words = ("reward", "distribut", "payout", "quote", "holder", "yield", "fee", "volume", "mint", "symbol")
    def walk(x: Any, path: str = "") -> None:
        if len(hits) >= max_fields:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                p = f"{path}.{k}" if path else str(k)
                if not isinstance(v, (dict, list)) and any(w in p.lower() for w in words):
                    hits[p] = v
                walk(v, p)
        elif isinstance(x, list):
            for i, v in enumerate(x[:30]):
                walk(v, f"{path}[{i}]")
    walk(obj)
    return hits


def stonkfun_rewards(mint: str) -> dict[str, Any]:
    base = "https://www.stonkfun.xyz"
    candidates = [
        f"{base}/api/rewards?mint={urllib.parse.quote(mint)}",
        f"{base}/api/rewards?tokenMint={urllib.parse.quote(mint)}",
        f"{base}/api/public/v1/tokens/{urllib.parse.quote(mint)}/rewards",
        f"{base}/api/public/v1/tokens/{urllib.parse.quote(mint)}",
    ]
    errors = []
    def one(url: str):
        return url, request_json(url, timeout=6)
    with ThreadPoolExecutor(max_workers=len(candidates)) as ex:
        futures = {ex.submit(one, u): u for u in candidates}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                used, obj = fut.result()
                return {"endpoint": used, "interesting": compact_interesting(obj), "rawType": type(obj).__name__}
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise RuntimeError(" | ".join(errors))


def solenrich(mint: str) -> dict[str, Any]:
    url = "https://api.solenrich.com/entrypoints/stonk-yield/invoke"
    obj = request_json(url, method="POST", body={"input": {"mint": mint, "format": "json"}}, timeout=15)
    return {"interesting": compact_interesting(obj), "rawType": type(obj).__name__}


def load_history() -> None:
    if not HISTORY_FILE.exists():
        return
    try:
        lines = HISTORY_FILE.read_text(errors="ignore").splitlines()[-5000:]
        for line in lines:
            try:
                HISTORY.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass


def write_history(row: dict[str, Any]) -> None:
    global LAST_HISTORY_WRITE
    t = now_ts()
    if t - LAST_HISTORY_WRITE < 55:
        return
    LAST_HISTORY_WRITE = t
    HISTORY.append(row)
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


def history_near(seconds_ago: int) -> dict[str, Any] | None:
    target = now_ts() - seconds_ago
    best = None
    best_gap = float("inf")
    for r in HISTORY:
        ts = r.get("ts") or 0
        gap = abs(ts - target)
        if gap < best_gap:
            best, best_gap = r, gap
    return best if best_gap < max(300, seconds_ago * 0.25) else None


def contract_changed(current_hash: str | None) -> bool:
    if not current_hash:
        return False
    old = {}
    if STATE_FILE.exists():
        try:
            old = json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    changed = bool(old.get("feeStateHash") and old.get("feeStateHash") != current_hash)
    try:
        STATE_FILE.write_text(json.dumps({"feeStateHash": current_hash, "updated": iso()}, indent=2))
    except Exception:
        pass
    return changed


def build_snapshot(token: str, reward: str, native_symbol: str, position: float, wallet: str, baseline: float, pool_fee_pct: float) -> dict[str, Any]:
    # Independent sources run concurrently so one slow API does not stall the scanner.
    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "dex": lambda: cached(f"dex:{token}", 12, lambda: dex_pairs(token)),
        "gecko": lambda: cached(f"gecko:{token}", 55, lambda: gecko_pools(token)),
        "contract": lambda: cached(f"contract:{token}", 240, lambda: contract_state(token)),
        "rewardpairs": lambda: cached(f"rewardpairs:{reward}", 25, lambda: dex_pairs(reward)),
        "native": lambda: cached(f"native:{native_symbol}", 25, lambda: native_price(native_symbol)),
        "stonk": lambda: cached(f"stonk:{token}", 28, lambda: stonkfun_rewards(token)),
        "enrich": lambda: cached(f"enrich:{token}", 120, lambda: solenrich(token)),
    }
    if wallet:
        jobs["wallet"] = lambda: cached(f"wallet:{wallet}:{reward}", 45, lambda: wallet_rewards(wallet, reward))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {"ok": False, "stale": False, "error": str(e)}

    dex_res = results["dex"]
    dex = normalize_dex_pair(choose_pair(dex_res["data"], token, reward), token, reward) if dex_res.get("ok") else None
    gecko_res = results["gecko"]
    gecko = normalize_gecko(choose_gecko(gecko_res["data"], token, reward), token, reward) if gecko_res.get("ok") else None
    contract_res = results["contract"]
    contract = contract_res.get("data") if contract_res.get("ok") else None
    fee = (contract or {}).get("transferFee") or {}
    decimals = int((contract or {}).get("decimals", 9) or 9)
    # If the token is quoted rather than base in a pool, derive MC from live price × on-chain supply.
    supply = (contract or {}).get("supply")
    if dex and dex.get("marketCap") is None and dex.get("tokenPriceUsd") is not None and supply:
        dex["marketCap"] = dex["tokenPriceUsd"] * supply
    if gecko and gecko.get("marketCap") is None and gecko.get("tokenPriceUsd") is not None and supply:
        gecko["marketCap"] = gecko["tokenPriceUsd"] * supply

    # Jupiter depends on the mint decimals, so it runs after the contract read.
    jup_res = cached(f"jup:{token}:{position}:{decimals}", 55, lambda: jupiter_quote(token, position, decimals))
    jup = jup_res.get("data") if jup_res.get("ok") else None

    reward_pairs_res = results["rewardpairs"]
    reward_stable = find_reward_stable_price(reward_pairs_res["data"], reward) if reward_pairs_res.get("ok") else None
    wrapped = (reward_stable or {}).get("priceUsd") or (dex or {}).get("rewardPriceUsdFromPair") or (gecko or {}).get("rewardPriceUsdFromPair")
    native_res = results["native"]
    native = (native_res.get("data") or {}).get("priceUsd") if native_res.get("ok") else None
    basis = pct(wrapped, native)
    stonk_res = results["stonk"]
    enrich_res = results["enrich"]
    wallet_res = results.get("wallet")
    wallet_data = wallet_res.get("data") if wallet_res and wallet_res.get("ok") else None

    mc = (dex or {}).get("marketCap")
    liq = (dex or {}).get("liquidity")
    v1 = (dex or {}).get("volume1h")
    v24 = (dex or {}).get("volume24h")
    turnover = (v24 / mc) if mc and v24 is not None else None
    liq_mc = (liq / mc) if mc and liq is not None else None
    one_way = None
    round_trip = None
    if fee.get("bps") is not None:
        tax = fee.get("bps", 0) / 10000.0
        pool = pool_fee_pct / 100.0
        one_way = 1 - (1-tax)*(1-pool)
        round_trip = 1 - ((1-tax)*(1-pool))**2

    headline_position = position * ((dex or {}).get("tokenPriceUsd") or 0) if dex else None
    execution_haircut = None
    if jup and headline_position and headline_position > 0:
        execution_haircut = 1 - jup["outUsd"] / headline_position

    divergences = {
        "pricePct": pct((dex or {}).get("tokenPriceUsd"), (gecko or {}).get("tokenPriceUsd")),
        "marketCapPct": pct((dex or {}).get("marketCap"), (gecko or {}).get("marketCap")),
        "liquidityPct": pct((dex or {}).get("liquidity"), (gecko or {}).get("liquidity")),
        "volume24hPct": pct((dex or {}).get("volume24h"), (gecko or {}).get("volume24h")),
    }

    hist1h = history_near(3600)
    liq_change_1h = pct(liq, hist1h.get("liquidity") if hist1h else None)
    volume_change_1h = pct(v24, hist1h.get("volume24h") if hist1h else None)

    alerts = []
    def alert(key: str, level: str, text: str) -> None:
        alerts.append({"key": key, "level": level, "text": text})

    if liq_mc is not None and liq_mc < 0.05:
        alert("liqmc", "warn", f"Liquidity/MC is {liq_mc*100:.2f}% (<5%).")
    if liq_change_1h is not None and liq_change_1h <= -30:
        alert("liqdrain", "critical", f"Primary-pool liquidity is down {abs(liq_change_1h):.1f}% vs ~1h ago.")
    if divergences["pricePct"] is not None and abs(divergences["pricePct"]) >= 3:
        alert("pricediv", "warn", f"DexScreener vs Gecko price divergence is {abs(divergences['pricePct']):.1f}%.")
    if divergences["marketCapPct"] is not None and abs(divergences["marketCapPct"]) >= 10:
        alert("mcdiv", "warn", f"DexScreener vs Gecko market-cap divergence is {abs(divergences['marketCapPct']):.1f}%.")
    if basis is not None and abs(basis) >= 1:
        alert("basis", "critical", f"Wrapped/native {native_symbol} basis is {basis:+.2f}% (>|1%|).")
    if fee:
        if fee.get("bps") != 300:
            alert("taxbps", "critical", f"Token-2022 transfer fee is {fee.get('bps')} bps, expected 300 bps.")
        if fee.get("configAuthority") not in (None, "", "11111111111111111111111111111111"):
            alert("authority", "critical", "Transfer-fee config authority is not null.")
        if contract_changed(fee.get("stateHash")):
            alert("contractchange", "critical", "Token-2022 transfer-fee state changed since the prior scanner observation.")
    if wallet_data and wallet_data.get("velocity6h") is not None and baseline > 0:
        if wallet_data["velocity6h"] <= baseline * 0.5:
            alert("rewardvelocity", "critical", f"6h attributed reward velocity is {wallet_data['velocity6h']:.6g}/h, >50% below configured baseline {baseline:.6g}/h.")
        medint = wallet_data.get("medianIntervalMinutes")
        if medint is not None and medint >= 100:
            alert("cadence", "warn", f"Median attributed payout interval is {medint:.0f} minutes.")
    if execution_haircut is not None and execution_haircut >= 0.05:
        level = "critical" if execution_haircut >= 0.10 else "warn"
        alert("execution", level, f"Jupiter executable value is {execution_haircut*100:.1f}% below headline position value.")
    if not stonk_res["ok"]:
        alert("stonkapi", "info", "StonkFun reward endpoint not readable; network reward totals remain unresolved.")

    # Mechanical, explicitly model-only, position payout proxy.
    mechanical = None
    if v24 is not None and fee.get("bps") and (contract or {}).get("supply") and wrapped:
        tax_pct = fee["bps"] / 10000.0
        share = position / contract["supply"]
        usd_day = v24 * tax_pct * share
        mechanical = {
            "usdPerDay": usd_day,
            "rewardPerDay": usd_day / wrapped if wrapped else None,
            "rewardPerHour": usd_day / wrapped / 24 if wrapped else None,
            "assumption": "primary-pair volume × live Token-2022 tax × position / total supply; not an observed payout forecast",
        }

    # Regime: intentionally simple and auditable.
    regime = "INSUFFICIENT HISTORY"
    ch6 = (dex or {}).get("change6h")
    pace = (v1 * 24 / v24) if v1 is not None and v24 not in (None, 0) else None
    wallet_bad = bool(wallet_data and wallet_data.get("velocity6h") is not None and baseline > 0 and wallet_data["velocity6h"] <= baseline*0.5)
    if basis is not None and abs(basis) >= 1:
        regime = "DISLOCATION"
    elif ch6 is not None and ch6 <= -15 and ((liq_change_1h is not None and liq_change_1h < -15) or (pace is not None and pace < .6)):
        regime = "UNWIND"
    elif wallet_bad or (ch6 is not None and ch6 >= 0 and pace is not None and pace < .55):
        regime = "EXHAUSTION"
    elif ch6 is not None and ch6 >= 10 and pace is not None and pace >= 1:
        regime = "EXPANSION"
    elif turnover is not None and turnover >= .15:
        regime = "MONETIZATION"

    snap = {
        "timestamp": iso(),
        "token": token,
        "rewardMint": reward,
        "nativeSymbol": native_symbol.upper(),
        "position": position,
        "regime": regime,
        "dex": dex,
        "dexSource": {k: dex_res.get(k) for k in ("ok", "stale", "age", "error") if k in dex_res},
        "gecko": gecko,
        "geckoSource": {k: gecko_res.get(k) for k in ("ok", "stale", "age", "error") if k in gecko_res},
        "contract": contract,
        "contractSource": {k: contract_res.get(k) for k in ("ok", "stale", "age", "error") if k in contract_res},
        "stonkfun": stonk_res.get("data") if stonk_res["ok"] else None,
        "stonkfunSource": {k: stonk_res.get(k) for k in ("ok", "stale", "age", "error") if k in stonk_res},
        "solenrich": enrich_res.get("data") if enrich_res["ok"] else None,
        "solenrichSource": {k: enrich_res.get(k) for k in ("ok", "stale", "age", "error") if k in enrich_res},
        "jupiter": jup,
        "jupiterSource": {k: jup_res.get(k) for k in ("ok", "stale", "age", "error") if k in jup_res},
        "wallet": wallet_data,
        "walletSource": ({k: wallet_res.get(k) for k in ("ok", "stale", "age", "error") if k in wallet_res} if wallet_res else None),
        "rewardAsset": {
            "wrappedPriceUsd": wrapped,
            "wrappedStablePool": reward_stable,
            "nativePriceUsd": native,
            "nativeSource": (native_res.get("data") or {}).get("source") if native_res["ok"] else None,
            "basisPct": basis,
        },
        "metrics": {
            "turnoverMc": turnover,
            "liquidityMc": liq_mc,
            "oneWayFeeHurdle": one_way,
            "roundTripFeeHurdle": round_trip,
            "headlinePositionUsd": headline_position,
            "executablePositionUsd": jup.get("outUsd") if jup else None,
            "executionHaircut": execution_haircut,
            "liqChange1hPct": liq_change_1h,
            "volume24Change1hPct": volume_change_1h,
            "volumePace": pace,
            "divergence": divergences,
        },
        "mechanicalRewardProxy": mechanical,
        "alerts": alerts,
    }

    if dex:
        write_history({
            "ts": now_ts(),
            "price": dex.get("tokenPriceUsd"),
            "marketCap": mc,
            "liquidity": liq,
            "volume1h": v1,
            "volume24h": v24,
            "turnoverMc": turnover,
            "liquidityMc": liq_mc,
            "wrapped": wrapped,
            "native": native,
            "basis": basis,
            "regime": regime,
        })
    return snap


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<title>ZCAT Sentinel</title>
<style>
:root{--bg:#080b0d;--panel:#0e1317;--panel2:#12191e;--line:#21303a;--txt:#eaf2f5;--muted:#82919a;--good:#47d18c;--warn:#f1b84b;--bad:#ff626e;--cyan:#61d7e8}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% -20%,#16242c 0,#080b0d 42%);color:var(--txt);font:14px/1.42 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;min-height:100vh}
.wrap{max-width:1220px;margin:0 auto;padding:20px}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:16px}.title{font-size:22px;letter-spacing:.08em;font-weight:800}.sub{color:var(--muted);font-size:12px;margin-top:5px}.live{display:flex;align-items:center;gap:8px;color:var(--good);font-size:12px}.dot{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 14px currentColor}.controls{display:grid;grid-template-columns:2fr 2fr 1fr 1fr 1fr auto;gap:8px;background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:12px;margin-bottom:14px}input,button{font:inherit}input{width:100%;background:#090d10;border:1px solid var(--line);color:var(--txt);padding:8px 9px;border-radius:7px;outline:none}input:focus{border-color:#385b69}.lab{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:0 0 4px 2px}.field{min-width:0}button{border:1px solid #31515e;background:#102029;color:#dff8ff;padding:8px 13px;border-radius:7px;cursor:pointer;align-self:end}button:hover{background:#17313c}
.regime{display:flex;justify-content:space-between;align-items:center;background:var(--panel);border:1px solid var(--line);padding:12px 14px;border-radius:12px;margin-bottom:14px}.regime strong{font-size:17px;letter-spacing:.06em}.stamp{font-size:11px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:11px;padding:12px;min-height:96px}.k{font-size:9px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted)}.v{font-size:21px;font-weight:750;margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s{font-size:11px;color:var(--muted);margin-top:4px}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.cyan{color:var(--cyan)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.box{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:13px}.box h3{font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin:0 0 10px;color:#bac8ce}.rows{display:grid;grid-template-columns:1fr auto;gap:7px 16px}.rows div:nth-child(odd){color:var(--muted)}.rows div:nth-child(even){text-align:right;max-width:420px;overflow:hidden;text-overflow:ellipsis}.alerts{display:flex;flex-direction:column;gap:7px}.alert{padding:9px 11px;border-radius:8px;border-left:3px solid var(--warn);background:#17150e}.alert.critical{border-color:var(--bad);background:#1c0f12}.alert.info{border-color:#59717b;background:#0e1518;color:#a8b6bc}.srcs{display:flex;gap:7px;flex-wrap:wrap}.src{font-size:10px;border:1px solid var(--line);border-radius:99px;padding:4px 7px;color:var(--muted)}.src.ok{color:var(--good);border-color:#214b39}.src.stale{color:var(--warn);border-color:#5a4a22}.src.fail{color:var(--bad);border-color:#5b252b}
pre{white-space:pre-wrap;word-break:break-word;background:#080c0f;border:1px solid #19252c;border-radius:8px;padding:9px;max-height:210px;overflow:auto;color:#9dafb7;font-size:10px}details{margin-top:10px}summary{cursor:pointer;color:var(--muted);font-size:11px}.foot{font-size:10px;color:#687780;padding:4px 2px 20px}.spark{height:54px;margin-top:8px;width:100%}.spark polyline{fill:none;stroke:var(--cyan);stroke-width:2;vector-effect:non-scaling-stroke}.spark .base{stroke:#1d2a31;stroke-width:1}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}.controls{grid-template-columns:1fr 1fr}.controls .wide{grid-column:span 2}.top{flex-direction:column}.v{font-size:18px}}@media(max-width:520px){.wrap{padding:11px}.grid{grid-template-columns:1fr 1fr;gap:7px}.card{padding:10px;min-height:88px}.controls{grid-template-columns:1fr}.controls .wide{grid-column:auto}.title{font-size:18px}}
</style>
</head>
<body><div class="wrap">
<div class="top"><div><div class="title">ZCAT SENTINEL <span class="cyan">/ LIVE</span></div><div class="sub">DEX → TOKEN-2022 → REWARD ASSET → WALLET → EXECUTABLE EXIT</div></div><div class="live"><span class="dot"></span><span id="liveText">starting…</span></div></div>
<div class="controls">
 <div class="field wide"><div class="lab">Token mint</div><input id="token" value="HcRLc9VDgjLeK154xDawfb1dmVJ98DoSqcwTHGqiDeJR"></div>
 <div class="field wide"><div class="lab">Reward mint</div><input id="reward" value="A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS"></div>
 <div class="field"><div class="lab">Position</div><input id="position" type="number" value="225600"></div>
 <div class="field"><div class="lab">Native reward</div><input id="native" value="ZEC"></div>
 <div class="field"><div class="lab">Baseline reward / h</div><input id="baseline" type="number" step="0.000001" value="0.00246"></div>
 <button id="refresh">REFRESH</button>
 <div class="field wide" style="grid-column:1/-2"><div class="lab">Wallet (optional — enables direct payout tracking)</div><input id="wallet" placeholder="Solana wallet address"></div>
 <button id="notify">NOTIFY</button>
</div>
<div class="regime"><div><div class="k">Regime</div><strong id="regime">—</strong></div><div class="stamp" id="stamp">—</div></div>
<div class="grid">
 <div class="card"><div class="k">Market cap</div><div class="v" id="mc">—</div><div class="s" id="mcq">DexScreener primary pair</div></div>
 <div class="card"><div class="k">Liquidity</div><div class="v" id="liq">—</div><div class="s" id="liqmc">—</div></div>
 <div class="card"><div class="k">Volume 1h / 24h</div><div class="v" id="vol">—</div><div class="s" id="turnover">—</div></div>
 <div class="card"><div class="k">ZCAT price</div><div class="v" id="price">—</div><div class="s" id="chg">—</div></div>
 <div class="card"><div class="k">Transfer tax</div><div class="v" id="tax">—</div><div class="s" id="authority">—</div></div>
 <div class="card"><div class="k">Max fee behavior</div><div class="v" id="maxfee">—</div><div class="s" id="capbind">—</div></div>
 <div class="card"><div class="k">Wrapped / native basis</div><div class="v" id="basis">—</div><div class="s" id="rewardpx">—</div></div>
 <div class="card"><div class="k">Executable position</div><div class="v" id="exec">—</div><div class="s" id="haircut">Jupiter 50 bps quote</div></div>
</div>
<div class="two">
 <div class="box"><h3>Reward engine</h3><div class="rows" id="rewardRows"></div></div>
 <div class="box"><h3>Wallet payout tape</h3><div class="rows" id="walletRows"></div></div>
</div>
<div class="two">
 <div class="box"><h3>Alerts — only mechanism changes matter</h3><div class="alerts" id="alerts"></div></div>
 <div class="box"><h3>Source health</h3><div class="srcs" id="sources"></div><details><summary>First-party / QC raw fields</summary><pre id="raw">—</pre></details></div>
</div>
<div class="foot">Zero-dependency local scanner. Data are live API/RPC observations only; unavailable fields remain unavailable. Mechanical reward proxy is explicitly not a forecast. History is stored locally beside the script.</div>
</div>
<script>
const $=id=>document.getElementById(id);let timer=null,lastAlertKeys=new Set();
const money=x=>x==null?'—':(Math.abs(x)>=1e9?'$'+(x/1e9).toFixed(2)+'B':Math.abs(x)>=1e6?'$'+(x/1e6).toFixed(2)+'M':Math.abs(x)>=1e3?'$'+(x/1e3).toFixed(1)+'K':'$'+Number(x).toFixed(2));
const num=(x,d=2)=>x==null?'—':Number(x).toLocaleString(undefined,{maximumFractionDigits:d});
const pc=(x,d=2)=>x==null?'—':(x*100).toFixed(d)+'%';const pcn=(x,d=2)=>x==null?'—':Number(x).toFixed(d)+'%';
function row(a,b){return `<div>${a}</div><div>${b}</div>`}
function src(name,o){let c=!o||!o.ok?'fail':o.stale?'stale':'ok';let t=!o||!o.ok?'FAIL':o.stale?'STALE':'LIVE';return `<span class="src ${c}">${name} ${t}</span>`}
function notifyNew(alerts){const keys=new Set(alerts.map(a=>a.key));for(const a of alerts){if(!lastAlertKeys.has(a.key)&&Notification.permission==='granted'){new Notification('ZCAT Sentinel: '+a.level,{body:a.text})}}lastAlertKeys=keys}
async function scan(){
 $('liveText').textContent='scanning…'; const q=new URLSearchParams({token:$('token').value.trim(),reward:$('reward').value.trim(),native:$('native').value.trim(),position:$('position').value||'225600',baseline:$('baseline').value||'0.00246',wallet:$('wallet').value.trim()});
 try{const r=await fetch('/api/snapshot?'+q);const d=await r.json();render(d);$('liveText').textContent='live · 15s';}catch(e){$('liveText').textContent='scanner error';console.error(e)}
 clearTimeout(timer);timer=setTimeout(scan,15000)
}
function render(d){const x=d.dex||{},m=d.metrics||{},c=d.contract||{},f=c.transferFee||{},ra=d.rewardAsset||{},j=d.jupiter||{},w=d.wallet||{},mech=d.mechanicalRewardProxy||{};
 $('regime').textContent=d.regime||'—';$('regime').className=(d.regime==='UNWIND'||d.regime==='DISLOCATION'||d.regime==='EXHAUSTION')?'bad':(d.regime==='EXPANSION'?'good':'warn');$('stamp').textContent=new Date(d.timestamp).toLocaleString();
 $('mc').textContent=money(x.marketCap);$('liq').textContent=money(x.liquidity);$('liqmc').textContent='Liquidity / MC '+pc(m.liquidityMc)+' · 1h Δ '+pcn(m.liqChange1hPct);
 $('vol').textContent=money(x.volume1h)+' / '+money(x.volume24h);$('turnover').textContent='Turnover / MC '+pc(m.turnoverMc)+' · pace '+num(m.volumePace,2)+'×';
 $('price').textContent=x.tokenPriceUsd==null?'—':'$'+Number(x.tokenPriceUsd).toPrecision(5);$('chg').textContent='1h '+pcn(x.change1h)+' · 6h '+pcn(x.change6h)+' · 24h '+pcn(x.change24h);
 $('tax').textContent=f.bps==null?'—':f.bps+' bps';$('tax').className=f.bps===300?'v good':'v bad';$('authority').textContent='config authority '+(f.configAuthority==null?'NULL ✓':String(f.configAuthority).slice(0,8)+'…');
 $('maxfee').textContent=f.maximumFeeTokens==null?'—':num(f.maximumFeeTokens,2)+' tokens';$('capbind').textContent=f.capBindsAboveTokens==null?'—':'cap binds above ~'+num(f.capBindsAboveTokens,0)+' tokens / transfer';
 $('basis').textContent=ra.basisPct==null?'—':(ra.basisPct>=0?'+':'')+ra.basisPct.toFixed(2)+'%';$('basis').className='v '+(ra.basisPct!=null&&Math.abs(ra.basisPct)>=1?'bad':'good');$('rewardpx').textContent='wrapped '+money(ra.wrappedPriceUsd)+' · native '+money(ra.nativePriceUsd)+' '+(ra.nativeSource||'');
 $('exec').textContent=j.outUsd==null?'—':money(j.outUsd);$('haircut').textContent=j.outUsd==null?'Jupiter unavailable':'headline '+money(m.headlinePositionUsd)+' · haircut '+pc(m.executionHaircut)+' · impact '+pcn(j.priceImpactPct,2);
 $('rewardRows').innerHTML=row('Primary-pair 3% tax proxy',x.volume24h==null?'—':money(x.volume24h*(f.bps||300)/10000)+'/day')+row('1% pool-fee notional',x.volume24h==null?'—':money(x.volume24h*.01)+'/day')+row('One-way fee hurdle',pc(m.oneWayFeeHurdle))+row('Round-trip fee hurdle',pc(m.roundTripFeeHurdle))+row('Position mechanical proxy',mech.rewardPerHour==null?'—':num(mech.rewardPerHour,6)+' '+d.nativeSymbol+'/h')+row('StonkFun first-party',d.stonkfunSource?.ok?'LIVE':'UNRESOLVED');
 if(d.wallet){$('walletRows').innerHTML=row('Reward balance',num(w.rewardBalance,8)+' '+d.nativeSymbol)+row('3h attributed velocity',w.velocity3h==null?'—':num(w.velocity3h,7)+' /h')+row('6h attributed velocity',w.velocity6h==null?'—':num(w.velocity6h,7)+' /h')+row('Median payout',w.medianPayout==null?'—':num(w.medianPayout,8))+row('Median interval',w.medianIntervalMinutes==null?'—':num(w.medianIntervalMinutes,0)+' min')+row('Attribution',w.velocityConfidence||'—');}else{$('walletRows').innerHTML='<div style="grid-column:1/-1;color:var(--muted)">Paste a public wallet above to enable direct on-chain payout tracking. No signing or wallet connection.</div>'}
 const alerts=d.alerts||[];$('alerts').innerHTML=alerts.length?alerts.map(a=>`<div class="alert ${a.level}">${a.text}</div>`).join(''):'<div class="alert info">No active meaningful-change trigger beyond configured structural warnings.</div>';notifyNew(alerts);
 $('sources').innerHTML=src('DEX',d.dexSource)+src('GECKO',d.geckoSource)+src('RPC',d.contractSource)+src('JUP',d.jupiterSource)+src('STONKFUN',d.stonkfunSource)+(d.walletSource?src('WALLET',d.walletSource):'');
 $('raw').textContent=JSON.stringify({stonkfun:d.stonkfun,solenrich:d.solenrich,divergence:m.divergence,contract:f},null,2);
}
$('refresh').onclick=scan;$('notify').onclick=async()=>{if('Notification'in window){await Notification.requestPermission();$('notify').textContent=Notification.permission.toUpperCase()}};scan();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/snapshot":
            q = urllib.parse.parse_qs(parsed.query)
            token = (q.get("token") or [DEFAULT_TOKEN])[0].strip()
            reward = (q.get("reward") or [DEFAULT_REWARD])[0].strip()
            native = (q.get("native") or ["ZEC"])[0].strip() or "ZEC"
            wallet = (q.get("wallet") or [""])[0].strip()
            try:
                position = float((q.get("position") or ["225600"])[0])
                baseline = float((q.get("baseline") or ["0.00246"])[0])
                pool_fee = float((q.get("poolFee") or ["1"])[0])
                data = build_snapshot(token, reward, native, position, wallet, baseline, pool_fee)
                self._send(200, json.dumps(data, separators=(",", ":"), default=str).encode(), "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e), "timestamp": iso()}).encode(), "application/json")
            return
        if parsed.path == "/api/history":
            self._send(200, json.dumps(list(HISTORY)[-500:], separators=(",", ":")).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")


def main() -> None:
    global PORT
    ap = argparse.ArgumentParser(description="Zero-dependency ZCAT real-time mechanism scanner")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    PORT = args.port
    load_history()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"ZCAT Sentinel running at {url}")
    print("Ctrl-C to stop. History is written to", HISTORY_FILE)
    if not args.no_open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
