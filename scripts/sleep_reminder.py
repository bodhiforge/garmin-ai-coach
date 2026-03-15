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
        "🔔 11:00 了。\n\n"
        "收工时间到。保存好工作，准备关电脑。\n"
        "⚠️ 11:30 MacP 会自动关机，不管你在干嘛。"
    )
elif mode == "sleep":
    send(
        "🚨 12:30 了。\n\n"
        "手机放下，去睡觉。\n"
        "明天的事明天再说。"
    )
elif mode == "shutdown":
    import subprocess
    send("💀 11:30 到了。MacP 正在关机。")
    subprocess.run([
        "ssh", "-o", "ConnectTimeout=5",
        "bosstation@100.102.71.47",
        "osascript -e 'tell app \"System Events\" to shut down'"
    ])
