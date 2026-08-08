# Running Legend Trade on Windows

This gets the whole platform — live charts, the analysis pipeline, backtesting,
options pricing and paper trading — running on your own Windows PC.

**No API key is required.** Crypto streams tick-by-tick from Binance or
Coinbase, and stocks, ETFs, indices, FX and futures come from Yahoo Finance,
none of which need credentials. Keys only improve equity data quality; see
Step 5 if you want that.

Total time: about 15 minutes.

---

## Step 1 — Install the two prerequisites (once)

1. **Python 3.11 or newer** — https://www.python.org/downloads/
   - On the first install screen, **tick "Add python.exe to PATH"** before
     clicking Install. Without it, the commands below won't be found.
2. **Node.js 20 or newer (LTS)** — https://nodejs.org/
   - Click through the installer with defaults.

To confirm both installed, open **Command Prompt** (press Start, type `cmd`,
Enter) and run:
```
python --version
node --version
```
You should see a version number for each. If "not recognized" appears,
reinstall the one that failed with the PATH option ticked.

---

## Step 2 — Get the code

With Git:
```
git clone https://github.com/nexarohc/Project-Legend-Trade.git
cd Project-Legend-Trade
```

No Git? Download the ZIP from the repo page on GitHub (Code → Download ZIP),
unzip it, and open Command Prompt in that folder.

---

## Step 3 — Start the backend

In Command Prompt, from the project folder:
```
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements-server.txt
python backend\run.py
```

Leave this window open — it is the server. You should see
`Legend Trade backend ready`.

---

## Step 4 — Start the interface (in a second window)

Open a **new** Command Prompt in the same project folder:
```
cd frontend
npm install
npm run dev
```

Then open your browser to **http://localhost:5173**.

You'll land on the home page. Click **Open the terminal**, create your account
(the first account on an instance is the administrator), and the terminal opens
with live charts.

---

## Step 5 — Optional: better equity data

Yahoo Finance is an undocumented endpoint and rate-limits per IP. It is fine for
personal use; for anything heavier, add one keyed provider.

**Twelve Data** is the one free tier verified to serve real-time equity
streaming rather than delayed or end-of-day data:

1. Get a free key at https://twelvedata.com/pricing (the Basic plan).
2. In the project folder, copy `.env.example` to a new file named `.env`.
3. Open `.env` in Notepad, set `TWELVEDATA_API_KEY=your-key-here`, and save.
4. Restart the backend window (Ctrl+C, then `python backend\run.py` again).

Your `.env` is git-ignored — it never gets committed or shared.

Two other free tiers, checked and found wanting: Polygon's free plan serves
end-of-day and 15-minute-delayed data only, and Finnhub's free plan returns 403
on the historical candle endpoint the charts need. Neither is a substitute.

---

## If something goes wrong

Copy the **exact** error text from the Command Prompt window — that is usually
all that's needed to diagnose it. Common ones:

- **`python` / `pip` / `node` not recognized** → the installer's PATH step was
  missed. Reinstall with "Add to PATH" ticked and open a fresh Command Prompt.
- **`npm install` errors** → check Node is version 20+ (`node --version`).
- **Charts are empty** → check the backend window for provider errors. Yahoo
  rate-limits per IP; wait a minute or add a Twelve Data key (Step 5).
- **Port already in use** → close any old backend window, or restart the PC.

---

## What this does not turn on

Live broker trading. It stays off until you set `LEGEND_ENABLE_LIVE_TRADING=true`
server-side, switch mode explicitly, and type a confirmation phrase — three
independent gates, on purpose. Paper trading works out of the box and needs no
broker account.

Read the limitations section of [`TRADING_TERMINAL.md`](./TRADING_TERMINAL.md)
before pointing any of this at real money.
