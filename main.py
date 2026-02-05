import os, threading, time
from flask import Flask
import asyncio
import link_tracker_bot

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "✅ Link Tracker Bot LIVE! Pyrogram + Flask OK"

def run_bot():
    print("🚀 Starting Pyrogram BOT...")
    # NON-BLOCKING: idle() bukan run()
    try:
        link_tracker_bot.app.idle()  # ← PYROGRAM BOT CORRECT!
    except Exception as e:
        print(f"Bot error: {e}")

if __name__ == "__main__":
    # Bot di daemon thread (NON-BLOCKING)
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Tunggu bot siap
    time.sleep(3)
    print("✅ Flask web server starting...")
    
    # Railway port
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
