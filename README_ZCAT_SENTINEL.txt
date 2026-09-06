ZCAT SENTINEL — simplest local real-time scanner

MAC / WINDOWS / LINUX
1. Put zcat_sentinel.py anywhere.
2. Run: python3 zcat_sentinel.py
3. It opens http://127.0.0.1:8765 automatically.

MAC shortcut
- Put run_zcat_sentinel.command in the same folder as zcat_sentinel.py.
- Double-click it. macOS may require right-click > Open the first time.

No pip installs. No wallet connection. No signing.
Paste a PUBLIC Solana wallet into the UI only if you want on-chain payout tracking.

Live sources used by the script:
- DexScreener token-pairs API (primary market telemetry)
- GeckoTerminal API (independent QC)
- Solana mainnet RPC (Token-2022 mint state + optional wallet receipts)
- Jupiter keyless quote API (executable value of the configured position)
- StonkFun first-party reward endpoint candidates (shown as unresolved if the public route/schema is unavailable)
- SolEnrich Stonk yield endpoint as secondary reward-ledger QC
- Kraken, Coinbase fallback for native reward-asset price

Default token:
ZCAT  HcRLc9VDgjLeK154xDawfb1dmVJ98DoSqcwTHGqiDeJR
Reward asset:
ZEC   A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS
Default position: 225,600 ZCAT
Default clean reward baseline: 0.00246 ZEC/hour

Important: the "mechanical reward proxy" is intentionally labeled as a model, not an observed payout forecast.
The scanner refuses to invent unavailable StonkFun cumulative reward fields.
