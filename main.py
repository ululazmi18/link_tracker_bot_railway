import os, threading, time
from flask import Flask
import link_tracker_bot  # Import skripmu

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "✅ Link Tracker Bot FULLY ACTIVE! 🟢"

def run_bot():
    print("🚀 Starting Pyrogram bot...")
    link_tracker_bot.app.start()  # ← INI YANG BENAR!
    print("✅ Pyrogram bot running!")

if __name__ == "__main__":
    # Start bot di background
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    time.sleep(3)  # Tunggu bot siap
    
    # Web server Railway
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
