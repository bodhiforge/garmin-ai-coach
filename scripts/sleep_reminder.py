#!/usr/bin/env python3
"""Sleep enforcement reminders."""
import sys
import requests
import yaml
from pathlib import Path

config_path = Path(__file__).parent.parent / "config.yaml"
config = yaml.safe_load(config_path.read_text())

BOT_TOKEN = config["telegram"]["bot_token"]
CHAT_ID = config["telegram"]["chat_id"]

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg}
    )

mode = sys.argv[1] if len(sys.argv) > 1 else "warn"

if mode == "warn":
    send(
        "🔔 11:00pm — time to wrap up.\n\n"
        "Save your work and get ready to shut down.\n"
        "⚠️ MacP will force shutdown at 11:30pm, no matter what."
    )
elif mode == "sleep":
    send(
        "🚨 12:30am — put the phone down.\n\n"
        "Go to sleep. Tomorrow's problems can wait."
    )
elif mode == "shutdown":
    import subprocess
    send("💀 11:30pm — MacP is shutting down now.")
    subprocess.run([
        "ssh", "-o", "ConnectTimeout=5",
        "bosstation@100.102.71.47",
        "osascript -e 'tell app \"System Events\" to shut down'"
    ])
