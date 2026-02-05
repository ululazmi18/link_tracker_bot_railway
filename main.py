from flask import Flask
import subprocess
import os

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "✅ Link Tracker Bot LIVE!"

if __name__ == "__main__":
    # BOT DI PROSES TERPISAH = 100% WORK!
    subprocess.Popen(["python", "link_tracker_bot.py"])
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
