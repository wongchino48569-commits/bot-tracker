import requests
import asyncio
from datetime import datetime, timezone
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = "8807718291:AAG5NNzMF5dtlwMj9_fsm-fG0nL0I1nuJrM"
TELEGRAM_CHAT_ID = "-1003734394227"
HELIUS_API_KEY = "ba8f7f3b-e484-46b2-b3be-c9fec23cc177"

WALLETS = {
    "Stigman": "8fsKLLtvKNanL4ginCaiRS6UfeemY11rSf8U8fN1dJw4",
    "Cupseyy": "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",
    "Yp12": "7cQjAvzJsmdePPMk8TiW8hYHHhCfdNtEaaNK3o46YP12",
    "71pA": "71pAfN1nJhcLaezSRYvwdsNFE9PnY9hNfcxB4nZ1gdAp",
    "JCF": "JCFpfkrCAoovfRtkAdCde2TvPZHCmQgK5mm8Hc2LKWMZ",
    "9L32": "9L32VYiZ8AD67gaH283dPfQwviQ6AFXuzaisWR92toTT",
    "8EMY": "8EmYYBEN6a4xE92gLsAKVZHtmC5Ga4eNxXp1c9E8jiWg",
    "CYA": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
    "nyhrox": "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC"
}

STABLE_TOKENS = [
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
]

MIN_USD = 150
bot_aktif = True
tx_history = {wallet: set() for wallet in WALLETS.values()}
sol_price_cache = {"price": 150, "last_update": 0}
recap_sent = False
hourly_slot = {}
last_hour = -1
open_positions = {}
daily_stats = {name: {"buy": 0, "sell": 0, "spent": 0, "pnl": 0} for name in WALLETS.keys()}

def get_sol_price():
    try:
        now = datetime.now(timezone.utc).timestamp()
        if now - sol_price_cache["last_update"] < 300:
            return sol_price_cache["price"]
        url = "https://api.dexscreener.com/tokens/v1/solana/So11111111111111111111111111111111111111112"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            price = float(data[0].get("priceUsd", 150))
            sol_price_cache["price"] = price
            sol_price_cache["last_update"] = now
            return price
    except:
        pass
    return sol_price_cache["price"]

def get_token_info(mint):
    try:
        url = "https://api.dexscreener.com/tokens/v1/solana/" + mint
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            pair = data[0]
            name = pair.get("baseToken", {}).get("symbol", "UNKNOWN")
            price = float(pair.get("priceUsd", 0))
            mcap = pair.get("marketCap", 0)
            return name, price, mcap
    except:
        pass
    return "UNKNOWN", 0, 0

def format_mcap(mcap):
    try:
        mcap = float(mcap)
        if mcap >= 1_000_000_000:
            return "$" + str(round(mcap/1_000_000_000, 2)) + "B"
        elif mcap >= 1_000_000:
            return "$" + str(round(mcap/1_000_000, 2)) + "M"
        elif mcap >= 1_000:
            return "$" + str(round(mcap/1_000, 2)) + "K"
        return "$" + str(round(mcap, 2))
    except:
        return "?"

def get_transactions(wallet):
    url = "https://api.helius.xyz/v0/addresses/" + wallet + "/transactions?api-key=" + HELIUS_API_KEY + "&limit=10"
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except:
        return []

def parse_tx(tx):
    try:
        sig = tx.get("signature", "")
        if tx.get("type", "") != "SWAP":
            return None
        token_transfers = tx.get("tokenTransfers", [])
        native_transfers = tx.get("nativeTransfers", [])
        fee_payer = tx.get("feePayer", "")
        tx_time = tx.get("timestamp", 0)
        token_in = None
        token_out = None
        amount_in = 0
        amount_out = 0
        for t in token_transfers:
            mint = t.get("mint", "")
            amount = t.get("tokenAmount", 0)
            if t.get("toUserAccount") == fee_payer:
                token_in = mint
                amount_in = amount
            elif t.get("fromUserAccount") == fee_payer:
                token_out = mint
                amount_out = amount
        sol_in = sum(t.get("amount", 0) for t in native_transfers if t.get("toUserAccount") == fee_payer) / 1e9
        sol_out = sum(t.get("amount", 0) for t in native_transfers if t.get("fromUserAccount") == fee_payer) / 1e9
        sol_price = get_sol_price()
        min_sol = MIN_USD / sol_price
        if sol_out > min_sol and token_in and token_in not in STABLE_TOKENS:
            return {"sig": sig, "action": "BUY", "mint": token_in, "sol": round(sol_out, 4), "amount": amount_in, "usd": round(sol_out * sol_price, 2), "time": tx_time}
        elif token_out and token_out not in STABLE_TOKENS:
            return {"sig": sig, "action": "SELL", "mint": token_out, "sol": round(sol_in, 4), "amount": amount_out, "usd": round(sol_in * sol_price, 2), "time": tx_time}
        return None
    except:
        return None

async def send_notif(app, name, parsed, token_name, price, mcap):
    utc_tz = timezone.utc
    wib_tz = pytz.timezone("Asia/Jakarta")
    tx_time_utc = datetime.fromtimestamp(parsed["time"], tz=utc_tz)
    tx_time_wib = tx_time_utc.astimezone(wib_tz)
    waktu = tx_time_utc.strftime("%H:%M:%S UTC") + " | " + tx_time_wib.strftime("%H:%M:%S WIB")
    if parsed["action"] == "BUY":
        emoji = "🟢"
        action_text = "BUY 🚀"
        sol_text = "Spent: " + str(parsed["sol"]) + " SOL (approx $" + str(parsed["usd"]) + ")"
    else:
        emoji = "🔴"
        action_text = "SELL 💰"
        sol_text = "Received: " + str(parsed["sol"]) + " SOL (approx $" + str(parsed["usd"]) + ")"
    amount_fmt = str(int(float(parsed["amount"]))) if parsed["amount"] else "?"
    price_fmt = "$" + str(round(price, 8)) if price > 0 else "?"
    mcap_fmt = format_mcap(mcap)
    pesan = emoji + " *Wallet Alert!*\n"
    pesan += "━━━━━━━━━━━━━━━\n"
    pesan += "👤 *Wallet:* " + name + "\n"
    pesan += "📊 *Action:* " + action_text + "\n"
    pesan += "🪙 *Token:* " + token_name + "\n"
    pesan += "📦 *Amount:* " + amount_fmt + " " + token_name + "\n"
    pesan += "💵 *" + sol_text + "*\n"
    pesan += "💲 *Price:* " + price_fmt + "\n"
    pesan += "📈 *MCap:* " + mcap_fmt + "\n"
    pesan += "🕐 *Time:* " + waktu + "\n"
    pesan += "━━━━━━━━━━━━━━━\n"
    pesan += "🔗 [Solscan](https://solscan.io/tx/" + parsed["sig"] + ") | 📊 [DexScreener](https://dexscreener.com/solana/" + parsed["mint"] + ")"
    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=pesan, parse_mode="Markdown")

async def send_recap(app):
    tanggal = datetime.now(timezone.utc).strftime("%d %B %Y")
    pesan = "📊 *Daily Recap - " + tanggal + "*\n"
    pesan += "━━━━━━━━━━━━━━━\n"
    ada_data = False
    for name, stats in daily_stats.items():
        if stats["buy"] == 0 and stats["sell"] == 0:
            continue
        ada_data = True
        pnl = stats["pnl"]
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_text = "+$" + str(round(pnl, 2)) if pnl >= 0 else "-$" + str(round(abs(pnl), 2))
        pesan += "\n🐋 *" + name + "*\n"
        pesan += "• BUY: " + str(stats["buy"]) + "x | SELL: " + str(stats["sell"]) + "x\n"
        pesan += "• Total Spent: $" + str(round(stats["spent"], 2)) + "\n"
        pesan += "• Total PnL: " + pnl_emoji + " " + pnl_text + "\n"
    if not ada_data:
        pesan += "\n_Tidak ada transaksi hari ini._\n"
    pesan += "\n━━━━━━━━━━━━━━━"
    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=pesan, parse_mode="Markdown")
    for name in daily_stats:
        daily_stats[name] = {"buy": 0, "sell": 0, "spent": 0, "pnl": 0}

async def monitor_wallets(app):
    global recap_sent, last_hour, hourly_slot
    while True:
        try:
            if not bot_aktif:
                await asyncio.sleep(60)
                continue
            utc_now = datetime.now(timezone.utc)
            wib_hour = (utc_now.hour + 7) % 24
            if wib_hour != last_hour:
                hourly_slot = {"BUY": False, "SELL": False}
                last_hour = wib_hour
            if wib_hour == 20 and utc_now.minute == 0 and not recap_sent:
                await send_recap(app)
                recap_sent = True
            elif wib_hour != 20:
                recap_sent = False
            for name, wallet in WALLETS.items():
                txs = get_transactions(wallet)
                if not txs or not isinstance(txs, list):
                    continue
                for tx in txs[:5]:
                    sig = tx.get("signature", "")
                    if not sig or sig in tx_history[wallet]:
                        continue
                    parsed = parse_tx(tx)
                    if not parsed:
                        tx_history[wallet].add(sig)
                        continue
                    action = parsed["action"]
                    mint = parsed["mint"]
                    open_key = name + "_" + mint
                    if action == "SELL" and open_key in open_positions:
                        token_name, price, mcap = get_token_info(mint)
                        await send_notif(app, name, parsed, token_name, price, mcap)
                        daily_stats[name]["sell"] += 1
                        daily_stats[name]["pnl"] += parsed["usd"]
                        del open_positions[open_key]
                        tx_history[wallet].add(sig)
                        continue
                    if action == "BUY":
                        if not hourly_slot["BUY"]:
                            hourly_slot["BUY"] = True
                            token_name, price, mcap = get_token_info(mint)
                            await send_notif(app, name, parsed, token_name, price, mcap)
                            daily_stats[name]["buy"] += 1
                            daily_stats[name]["spent"] += parsed["usd"]
                            daily_stats[name]["pnl"] -= parsed["usd"]
                            open_positions[open_key] = parsed
                    elif action == "SELL":
                        pass  # Skip SELL tanpa open position
                    tx_history[wallet].add(sig)
            await asyncio.sleep(60)
        except Exception as e:
            print("Error: " + str(e))
            await asyncio.sleep(30)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sol_price = get_sol_price()
    status = "✅ Aktif" if bot_aktif else "⛔ Berhenti"
    pesan = "👁️ *Wallet Tracker*: " + status + "\n\n"
    pesan += "💲 *SOL Price:* $" + str(round(sol_price, 2)) + "\n"
    pesan += "🎯 *Min Trade:* $" + str(MIN_USD) + "\n\n"
    pesan += "📂 *Open Positions:* " + str(len(open_positions)) + "\n\n"
    pesan += "🐋 *Monitoring:*\n"
    for name in WALLETS.keys():
        pesan += "• " + name + "\n"
    await update.message.reply_text(pesan, parse_mode="Markdown")

async def cmd_recap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_recap(app)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_aktif
    bot_aktif = True
    await update.message.reply_text("✅ Bot aktif!")

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_aktif
    bot_aktif = False
    await update.message.reply_text("⛔ Bot berhenti!")

async def main():
    global app
    print("Wallet Tracker Bot v4")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("recap", cmd_recap))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    async with app:
        await app.start()
        await app.updater.start_polling()
        sol_price = get_sol_price()
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="👁️ *Wallet Tracker V4 AKTIF!*\n\n🐋 Monitoring 9 wallet\n💲 SOL: $" + str(round(sol_price, 2)) + "\n🎯 Min trade: $" + str(MIN_USD) + "\n📊 Notif: 1 BUY + 1 SELL per jam (siapa duluan)\n🔄 Open position dipantau lintas jam\n📋 Recap: 20:00 WIB\n\n/status /recap /start /stop",
            parse_mode="Markdown"
        )
        await monitor_wallets(app)
        await app.updater.stop()
        await app.stop()

asyncio.run(main())
