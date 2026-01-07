import asyncio
import datetime
import signal
import sys
from ib_insync import *
import pytz
import requests

# ==================== 配置 ====================
symbols = ['SPY', 'QQQ', 'IWM', 'MSFT', 'GOOGL', 'META', 'AMEN', 'AAPL', 'TSLA', 'NVDA', 'PLTR']

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1458571606807941376/WMuf2Tm5Lp5p_S-vlqFN7TB_7Y_hA0iWS45cg-eX85GfX2QX5o03vTiKqbDZbDBlCMcu"

et_tz = pytz.timezone('US/Eastern')

shutdown_flag = False
ib_instance = None

# 开盘第一根5分钟K线范围（固定）
first_range = {sym: {'high': None, 'low': None} for sym in symbols}

# ==================== 时间窗检查 ====================
def is_within_monitoring_window():
    now = datetime.datetime.now(et_tz)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    start_time = market_open + datetime.timedelta(minutes=30)   # 10:00 ET 开始
    end_time = market_open + datetime.timedelta(hours=2)        # 11:30 ET 结束
    return start_time <= now <= end_time

# ==================== Discord Webhook ====================
def send_webhook(alert_msg):
    data = {"content": alert_msg, "username": "开盘范围反弹失败警报"}
    try:
        response = requests.post(DISCORD_WEBHOOK, json=data)
        print("Discord 推送成功" if response.status_code == 204 else f"Discord 推送失败: {response.text}")
    except Exception as e:
        print(f"Discord 发送异常: {e}")

# ==================== bar更新回调（核心逻辑） ====================
def on_bar_update(bars, hasNewBar):
    """
    当 hasNewBar == True 时，说明上一根5分钟K线刚刚收盘（完整可靠）
    此时 bars[-2] 是刚刚收盘的K线 → 用于判断反弹失败
    bars[-1] 是当前正在形成的K线 → 不参与判断
    """
    sym = bars.contract.symbol

    # 必须有第一根K线已锁定，且至少有2根bar（上一根已收盘 + 当前正在形成）
    if first_range[sym]['high'] is None or first_range[sym]['low'] is None:
        return
    if len(bars) < 2:
        return

    # 只有在新K线开始（即上一根正式收盘）时才判断
    if hasNewBar:
        closed_bar = bars[-2]  # 刚刚收盘的完整5分钟K线（用于判断）
        close = closed_bar.close
        bar_high = closed_bar.high
        bar_low = closed_bar.low
        first_high = first_range[sym]['high']
        first_low = first_range[sym]['low']

        current_time = datetime.datetime.fromtimestamp(closed_bar.time, et_tz)

        # 1. 向上反弹失败：已收盘K线整体在第一根Low下方，且收盘价 ≤ 第一根Low
        if bar_high > first_low and close <= first_low:
            msg = (f"**【向上反弹失败】** {sym}\n"
                   f"时间: {current_time.strftime('%H:%M')} ET\n"
                   f"收盘价: {close:.2f} ≤ 开盘下轨 {first_low:.2f}\n"
                   f"该K线范围: [{bar_low:.2f} - {bar_high:.2f}] 完全在开盘范围下方")
            send_webhook(msg)
            print(msg)

        # 2. 向下反弹失败：已收盘K线整体在第一根High上方，且收盘价 ≥ 第一根High
        elif bar_low < first_high and close >= first_high:
            msg = (f"**【向下反弹失败】** {sym}\n"
                   f"时间: {current_time.strftime('%H:%M')} ET\n"
                   f"收盘价: {close:.2f} ≥ 开盘上轨 {first_high:.2f}\n"
                   f"该K线范围: [{bar_low:.2f} - {bar_high:.2f}] 完全在开盘范围上方")
            send_webhook(msg)
            print(msg)

    # 锁定开盘第一根5分钟K线（当天最早的完整bar）
    if first_range[sym]['high'] is None:
        for bar in bars:
            bar_time = datetime.datetime.fromtimestamp(bar.time, et_tz)
            if bar_time.hour == 9 and 30 <= bar_time.minute < 40:
                first_range[sym]['high'] = bar.high
                first_range[sym]['low'] = bar.low
                print(f"{sym} 开盘第一根5分钟K线已锁定: High={bar.high:.2f}, Low={bar.low:.2f}")
                break

# ==================== 监控单个股票 ====================
async def monitor_symbol(ib, symbol):
    contract = Stock(symbol, 'SMART', 'USD')
    ib.qualifyContracts(contract)

    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='1 D',
        barSizeSetting='5 mins',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1,
        keepUpToDate=True
    )

    bars.updateEvent += on_bar_update

    try:
        while is_within_monitoring_window() and not shutdown_flag:
            await asyncio.sleep(10)
    finally:
        ib.cancelHistoricalData(bars)
        print(f"{symbol} 监控结束")

# ==================== 优雅关闭 ====================
def signal_handler(sig, frame):
    global shutdown_flag
    print("\n收到关闭信号，正在优雅关闭...")
    shutdown_flag = True
    if ib_instance and ib_instance.isConnected():
        ib_instance.disconnect()

# ==================== 主函数 ====================
async def main():
    global ib_instance
    ib = IB()
    ib_instance = ib

    try:
        ib.connect('127.0.0.1', 7497, clientId=10)  # 根据你的环境调整端口
        print("IB API 连接成功")
    except Exception as e:
        print(f"连接失败: {e}")
        return

    if not is_within_monitoring_window():
        print("当前不在监控时间窗（美股10:00-11:30 ET），程序退出。")
        ib.disconnect()
        return

    tasks = [monitor_symbol(ib, sym) for sym in symbols]
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    util.startLoop()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        signal_handler(None, None)