# ZCAT Sentinel

> Simplest zero-dependency local real-time scanner for Solana Token-2022 mechanism telemetry.

---

## 🚀 Live Shareable Web App

Open the live scanner directly in your browser (no installation or download required):

### **[https://21e8-miner.github.io/ZCAT_Sentinel_bundle/](https://21e8-miner.github.io/ZCAT_Sentinel_bundle/)**

---

## 📦 Direct Standalone Downloads

- **Direct Bundle (.zip)**: [ZCAT_Sentinel_bundle.zip](https://github.com/21e8-miner/ZCAT_Sentinel_bundle/releases/latest/download/ZCAT_Sentinel_bundle.zip)
- **GitHub Release (v1.0.0)**: [Release v1.0.0](https://github.com/21e8-miner/ZCAT_Sentinel_bundle/releases/tag/v1.0.0)
- **Source Code Archive**: [main.zip](https://github.com/21e8-miner/ZCAT_Sentinel_bundle/archive/refs/heads/main.zip)

---

## Overview

**ZCAT Sentinel** provides institutional-grade telemetry on Solana Token-2022 transfer fees, Jupiter executable exit quotes, DEX volumes, and reward-asset basis:

- **No `pip` installs** required (pure Python standard library or 100% browser-based).
- **No wallet connection or private key signing**.
- **Optional public wallet tracking** for on-chain payout receipts.

---

## Quickstart (Local Execution)

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
- Double-click `run_zcat_sentinel.command` in the bundle folder (on first run, macOS may require right-click → **Open**).

---

## Live Data Sources

- **DexScreener Token-Pairs API**: Primary market telemetry
- **GeckoTerminal API**: Independent quality-control & pool data
- **Solana Mainnet RPC**: Token-2022 mint state & optional public wallet receipts
- **Jupiter Keyless Quote API**: Executable value of configured positions
- **StonkFun First-Party Reward Endpoints**: Direct reward ledger checks
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
