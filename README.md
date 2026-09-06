# ZCAT Sentinel

> Simplest zero-dependency local real-time scanner for Solana Token-2022 mechanism telemetry.

---

## Overview

**ZCAT Sentinel** is a lightweight, zero-dependency Python service that runs locally and serves a real-time monitoring interface at `http://127.0.0.1:8765`.

- **No `pip` installs** required (pure Python standard library).
- **No wallet connection or private key signing**.
- **Optional public wallet tracking** for on-chain payout receipts.

---

## Quickstart

### macOS / Linux / Windows

1. Run the script:
   ```bash
   python3 zcat_sentinel.py
   ```
2. Your browser will automatically open:
   ```
   http://127.0.0.1:8765
   ```

### macOS Shortcut
- Double-click `run_zcat_sentinel.command` in this folder (on first run, macOS may require right-click → **Open**).

---

## Live Data Sources

- **DexScreener Token-Pairs API**: Primary market telemetry
- **GeckoTerminal API**: Independent quality-control & pool data
- **Solana Mainnet RPC**: Token-2022 mint state & optional public wallet receipts
- **Jupiter Keyless Quote API**: Executable value of configured positions
- **StonkFun First-Party Reward Endpoints**: Direct reward ledger checks
- **SolEnrich Stonk Yield Endpoint**: Secondary reward-ledger QC
- **Kraken / Coinbase**: Fallback native reward-asset pricing

---

## Default Configuration

| Parameter | Value |
| :--- | :--- |
| **Default Token** | `ZCAT` (`HcRLc9VDgjLeK154xDawfb1dmVJ98DoSqcwTHGqiDeJR`) |
| **Reward Asset** | `ZEC` (`A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS`) |
| **Default Position** | `225,600 ZCAT` |
| **Clean Reward Baseline** | `0.00246 ZEC/hour` |

*Note: The "mechanical reward proxy" is intentionally labeled as a model, not an observed payout forecast. The scanner refuses to invent unavailable StonkFun cumulative reward fields.*
