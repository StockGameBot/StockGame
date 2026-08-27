# Alpaca Setup

Stock Game uses [Alpaca](https://alpaca.markets/) **market data** for US equities (IEX on the free tier). Crypto is not supported.

## Create keys

1. Sign up / log in at [Alpaca](https://app.alpaca.markets/).
2. Open API keys, box on the far right, halfway down, for your account (paper trading keys are fine for this bot).
3. Copy the **API Key ID** and **Secret Key**.
4. Add them to `.env`:

   ```env
   ALPACA_API_KEY=...
   ALPACA_SECRET_KEY=...
   ```

## What the bot uses Alpaca for

- Looking up US equity symbols when buying
- Fetching latest prices on the scheduled game update loop
- Checking whether the US equity market is open (clock), with a local-hours fallback if the clock call fails

Without keys, the Discord bot can still start, but price updates will fail until `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are set.

## Smoke test

Start the bot with valid keys in `.env` and check logs for a successful scheduled update or run `/update` (moderator) once. You should see price fetches without “Alpaca credentials missing” or repeated 401 errors.

## Troubleshooting

| Problem | Things to try |
|---------|----------------|
| “Alpaca credentials missing” in logs | Set both key vars in `.env` and restart; no empty spaces/quotes issues |
| 401 / unauthorized | Wrong key/secret pair; regenerate keys in the Alpaca dashboard |
| 429 / rate limited | Free tier is limited (~200 market-data requests/min). The client batches and sleeps between batches; reduce polling or wait and retry |
| Symbol not found | Confirm it is a US equity Alpaca knows about; class shares may use `.` vs `-` (e.g. `BRK.B` / `BRK-B`) — the bot maps these |
| Prices never change | Market closed; update loop not running; Alpaca errors in the error log; keys missing |
| Paper vs live confusion | Market **data** is separate from paper/live **trading**. Paper keys are enough for this bot’s price reads |

## Notes

- Prefer Alpaca’s free IEX data for this project; do not expect crypto symbols.
- Keep secrets out of git. `.env` should stay gitignored.
