import asyncio
import datetime
import signal
import sys
from ib_insync import *
import pytz
import requests

# ==================== 配置 ====================
symbols = ['SPY', 'QQQ', 'IWM', 'MSFT', 'GOOGL', 'META', 'AMZN', 'AAPL', 'TSLA', 'NVDA', 'PLTR']

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1458571606807941376/WMuf2Tm5Lp5p_S-vlqFN7TB_7Y_hA0iWS45cg-eX85GfX2QX5o03vTiKqbDZbDBlCMcu"

et_tz = pytz.timezone('US/Eastern')

shutdown_flag = False
ib_instance = None

# 开盘第一根5分钟K线范围
first_range = {sym: {'high': None, 'low': None} for sym in symbols}

# 当前正在形成的5分钟K线缓存
current_5min = {sym: {'high': -float('inf'), 'low': float('inf'), 'close': None, 'start_time': None} for sym in symbols}

# 已报警记录（可选：防止重复报警）
alerted = set()

# ==================== 时间窗检查 ====================
def is_within_monitoring_window():
    now = datetime.datetime.now(et_tz)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    start_time = market_open + datetime.timedelta(minutes=30)
    end_time = market_open + datetime.timedelta(hours=4)  # 11:30 ET
    return start_time <= now <= end_time

# ==================== Discord Webhook ====================
def send_webhook(msg):
    data = {"content": msg, "username": "开盘反弹失败警报"}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=data)
        print("Discord推送成功" if r.status_code == 204 else f"推送失败: {r.text}")
    except Exception as e:
        print(f"Discord异常: {e}")

# ==================== 实时5秒bar回调（核心） ====================
def on_realtime_bar(ticker, bar, hasNewBar):
    sym = ticker.contract.symbol
    bar_time = datetime.datetime.fromtimestamp(bar.time, et_tz)

    # 计算当前所属5分钟周期起始时间
    minute_key = bar_time.minute - (bar_time.minute % 5)
    cycle_start = bar_time.replace(minute=minute_key, second=0, microsecond=0)

    current = current_5min[sym]

    # 新5分钟周期开始 → 检查上一周期是否反弹失败
    if current['start_time'] is not None and current['start_time'] != cycle_start:
        prev_close = current['close']
        prev_high = current['high']
        prev_low = current['low']
        prev_time = current['start_time']

        # 锁定开盘第一根（9:30-9:35周期）
        if first_range[sym]['high'] is None and prev_time.hour == 9 and prev_time.minute == 30:
            first_range[sym]['high'] = prev_high
            first_range[sym]['low'] = prev_low
            print("\n" + "="*80)
            print(f"*** {sym} 开盘第一根K线锁定完成！High={prev_high:.2f} Low={prev_low:.2f} ***")
            print("="*80 + "\n")

        if first_range[sym]['high'] is not None:
            first_high = first_range[sym]['high']
            first_low = first_range[sym]['low']

            # 跳过第一根K线本身的检查
            if prev_time.hour == 9 and prev_time.minute == 30:
                pass
            else:
                print(f"\n[EVENT] {sym} {prev_time.strftime('%H:%M')} K线收盘，检查反弹失败")
                print(f"    K线: H={prev_high:.2f} L={prev_low:.2f} C={prev_close:.2f}")
                print(f"    开盘范围: H={first_high:.2f} L={first_low:.2f}")

                # 向上反弹失败
                if prev_high > first_low and prev_close <= first_low:
                    msg = f"**【向上反弹失败】** {sym} {prev_time.strftime('%H:%M')} ET\n收盘 {prev_close:.2f} ≤ 下轨 {first_low:.2f}\n曾上探 {prev_high:.2f}"
                    if sym not in alerted:  # 防止重复
                        send_webhook(msg)
                        alerted.add(sym)
                    print("[TRIGGER] 向上反弹失败！")

                # 向下反弹失败
                elif prev_low < first_high and prev_close >= first_high:
                    msg = f"**【向下反弹失败】** {sym} {prev_time.strftime('%H:%M')} ET\n收盘 {prev_close:.2f} ≥ 上轨 {first_high:.2f}\n曾下探 {prev_low:.2f}"
                    if sym not in alerted:
                        send_webhook(msg)
                        alerted.add(sym)
                    print("[TRIGGER] 向下反弹失败！")

    # 更新或初始化当前周期
    if current['start_time'] != cycle_start:
        current.update({
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'start_time': cycle_start
        })
        print(f"[NEW CYCLE] {sym} 新5分钟周期开始: {cycle_start.strftime('%H:%M')} ET")
    else:
        current['high'] = max(current['high'], bar.high)
        current['low'] = min(current['low'], bar.low)
        current['close'] = bar.close

# ==================== 优雅关闭 ====================
def signal_handler(sig, frame):
    global shutdown_flag
    print("\n[SHUTDOWN] 收到关闭信号（Ctrl+C），正在优雅退出...")
    shutdown_flag = True
    print("[WAIT] 等待异步任务结束...")

# ==================== 监控单个股票 ====================
async def monitor_symbol(ib, symbol):
    contract = Stock(symbol, 'SMART', 'USD')
    ib.qualifyContracts(contract)

    if not is_within_monitoring_window():
        return

    ticker = ib.reqRealTimeBars(contract, 5, 'TRADES', False)
    ticker.updateEvent += on_realtime_bar

    print(f"[START] {symbol} 开始实时5秒bar监控")

    try:
        while is_within_monitoring_window() and not shutdown_flag:
            await asyncio.sleep(1)
    finally:
        if ib.isConnected():
            ib.cancelRealTimeBars(ticker)
        print(f"[END] {symbol} 监控结束")

# ==================== 主函数 ====================
async def main():
    global ib_instance
    ib = IB()
    ib_instance = ib

    try:
        await ib.connectAsync('127.0.0.1', 7496, clientId=10)  # 实盘端口7496
        print("实盘账户连接成功")
    except Exception as e:
        print(f"连接失败: {e}")
        return

    if not is_within_monitoring_window():
        print("不在时间窗内，退出")
        ib.disconnect()
        return

    tasks = [monitor_symbol(ib, sym) for sym in symbols]
    await asyncio.gather(*tasks)

    ib.disconnect()
    print("[DISCONNECTED] 已安全断开 IB API 连接")

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    # util.startLoop()  # 不需要，asyncio.run会自动管理循环
    asyncio.run(main())