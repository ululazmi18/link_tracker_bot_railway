import os, threading, time
from flask import Flask
import link_tracker_bot  

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "Link Tracker Bot Aktif! 🟢"

def run_bot():
    link_tracker_bot.main()  # Sesuaikan dengan fungsi utama di skripmu

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
