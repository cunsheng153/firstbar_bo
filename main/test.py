import requests
import os
from datetime import datetime
import pytz

# 尝试从环境变量获取，如果本地测试没有环境变量，则使用硬编码（仅限本地测试使用）
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1458571606807941376/WMuf2Tm5Lp5p_S-vlqFN7TB_7Y_hA0iWS45cg-eX85GfX2QX5o03vTiKqbDZbDBlCMcu"

def test_webhook():
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz).strftime('%Y-%m-%d %H:%M:%S')
    tts_text = f"spy 向上反弹失败"
    payload = {
        "username": "开盘反弹策略",
        "tts": True,  # <--- 开启文字转语音
        "content": tts_text,
        "embeds": [
            {
                "title": "✅ Discord 推送测试成功",
                "description": "@everyone如果你看到这条消息，说明 Webhook 配置正确。",
                "color": 65280,  # 绿色
                "fields": [
                    {"name": "测试时间 (ET)", "value": now_et, "inline": True},
                    {"name": "运行环境", "value": "GitHub Actions" if os.getenv("GITHUB_ACTIONS") else "本地运行", "inline": True}
                ],
                "footer": {"text": "Longport 策略监控系统"}
            }
        ]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code == 204:
            print("Successfully sent message to Discord.")
        else:
            print(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    test_webhook()