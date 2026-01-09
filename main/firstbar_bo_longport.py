from longport.openapi import QuoteContext, Config, Period, AdjustType
from datetime import datetime, timedelta, time
import pytz
import requests
import asyncio
import signal
import sys
from dotenv import load_dotenv  # 导入库

load_dotenv()  # 加载环境变量

# ==================== 配置区域 ====================
symbols = ['SPY.US', 'QQQ.US', 'IWM.US', 'MSFT.US', 'GOOGL.US', 'META.US', 'AMZN.US', 'AAPL.US', 'TSLA.US', 'NVDA.US', 'PLTR.US']
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1458571606807941376/WMuf2Tm5Lp5p_S-vlqFN7TB_7Y_hA0iWS45cg-eX85GfX2QX5o03vTiKqbDZbDBlCMcu"

et_tz = pytz.timezone('US/Eastern')

shutdown_flag = False
alerted = set()
first_range = {} 

# ==================== 辅助函数 ====================

def get_today_market_times():
    now = datetime.now(et_tz)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    monitor_start = market_open + timedelta(minutes=30)
    monitor_end = market_open + timedelta(hours=2)
    return market_open, monitor_start, monitor_end

def send_webhook(title, description, color):
    # 【核心修复】构造一段纯文本，专门给 TTS 读
    # 比如： "注意！AAPL 向上反弹失败"
    tts_text = f"注意！{title}" 
    payload = {
        "username": "疤脸哥",
        "tts": True,               # 开启朗读
        "content": tts_text,       # <--- TTS 实际朗读的内容在这里！
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "=====Longport 实时监控====="}
            }
        ]
    }
    
    try:
        # 增加 headers 确保兼容性
        headers = {"Content-Type": "application/json"}
        requests.post(DISCORD_WEBHOOK, json=payload, headers=headers, timeout=5)
    except Exception as e:
        print(f"[Warn] Discord推送失败: {e}")

# ==================== 核心逻辑 ====================

async def get_first_candle_data(ctx):
    market_open, _, _ = get_today_market_times()
    # 转换为整数时间戳进行比较
    target_ts = int(market_open.timestamp())
    
    for sym in symbols:
        if sym in first_range and first_range[sym].get('ready'):
            continue
            
        try:
            # 获取最近几根K线
            candles = ctx.candlesticks(sym, Period.Min_5, 10, AdjustType.NoAdjust)
            
            for k in candles:
                # 兼容处理：将 k.timestamp 转为数值时间戳
                k_ts = k.timestamp.timestamp() if hasattr(k.timestamp, 'timestamp') else k.timestamp
                
                # 如果误差在60秒内，说明找到了 9:30 这根K线
                if abs(int(k_ts) - target_ts) < 60: 
                    first_range[sym] = {
                        'high': float(k.high),
                        'low': float(k.low),
                        'ready': True
                    }
                    print(f"[LOCKED] {sym} 首根K线: High={k.high}, Low={k.low}")
                    break
        except Exception as e:
            print(f"[Error] 获取 {sym} 首根K线失败: {e}")

async def monitor_stocks(ctx):
    print("监控程序已启动...")
    send_webhook("策略监控已启动", f"正在开启开盘反弹策略监控，当前时间：{datetime.now(et_tz).strftime('%Y-%m-%d %H:%M:%S')}", 3447003)
    last_processed_time = {sym: 0 for sym in symbols}

    while not shutdown_flag:
        now = datetime.now(et_tz)
        market_open, monitor_start, monitor_end = get_today_market_times()

        if now < market_open:
            print(f"等待开盘... 当前: {now.strftime('%H:%M:%S')}", end='\r')
            await asyncio.sleep(30)
            continue

        # 09:35 之后开始尝试抓取第一根K线
        if now > (market_open + timedelta(minutes=5)):
            all_ready = all(first_range.get(s, {}).get('ready') for s in symbols)
            if not all_ready:
                await get_first_candle_data(ctx)

        # 10:00 - 11:30 监控窗口
        if now < monitor_start:
            await asyncio.sleep(20)
            continue
        
        if now > monitor_end:
            print("\n监控时间已过，今日任务结束。")
            break

        for sym in symbols:
            if not first_range.get(sym, {}).get('ready'):
                continue

            try:
                k_lines = ctx.candlesticks(sym, Period.Min_5, 2, AdjustType.NoAdjust)
                if not k_lines: continue
                
                latest_candle = k_lines[-1]
                l_ts = latest_candle.timestamp.timestamp() if hasattr(latest_candle.timestamp, 'timestamp') else latest_candle.timestamp
                
                if l_ts <= last_processed_time[sym]:
                    continue 
                
                curr_open = float(latest_candle.open)
                curr_close = float(latest_candle.close)
                curr_high = float(latest_candle.high)
                curr_low = float(latest_candle.low)
                
                ref_high = first_range[sym]['high']
                ref_low = first_range[sym]['low']
                
                last_processed_time[sym] = l_ts

                # 逻辑判断
                if curr_high > ref_low and curr_close <= ref_low and curr_open < ref_low:
                    alert_id = f"{sym}_up_{l_ts}"
                    if alert_id not in alerted:
                        title = f"📉 {sym} 向上反弹失败"
                        desc = (f"**状态**: 假突破回落 (看空)\n"
                                f"**当前收盘**: {curr_close:.2f}\n"
                                f"**首根下轨**: {ref_low:.2f}\n"
                                f"**曾上探**: {curr_high:.2f}")
                        send_webhook(title, desc, 16711680) # 传入红色代码
                        alerted.add(alert_id)
                    print(f"[TRIGGER] {sym} UP FAIL")
                
                elif curr_low < ref_high and curr_close >= ref_high and curr_open > ref_high:
                    alert_id = f"{sym}_down_{l_ts}"
                    if alert_id not in alerted:
                        title = f"📈 {sym} 向下反弹失败"
                        desc = (f"**状态**: 假跌破拉回 (看多)\n"
                                f"**当前收盘**: {curr_close:.2f}\n"
                                f"**首根上轨**: {ref_high:.2f}\n"
                                f"**曾下探**: {curr_low:.2f}")
                        send_webhook(title, desc, 65280) # 传入绿色代码
                        alerted.add(alert_id)
                    print(f"[TRIGGER] {sym} DOWN FAIL")
            except Exception as e:
                print(f"Error checking {sym}: {e}")

        await asyncio.sleep(20)

# ==================== 启动部分 ====================

def signal_handler(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    print("\n[SHUTDOWN] 正在退出...")
    sys.exit(0)

async def main():
    try:
        config = Config.from_env()
        # 直接创建对象，不使用 with 语句
        ctx = QuoteContext(config)
        # send_webhook("🔧 GitHub 环境测试", "GitHub Actions 已成功启动脚本并加载环境变量。", 3447003)
        await monitor_stocks(ctx)
    except Exception as e:
        print(f"[CRITICAL] 脚本崩溃: {e}")

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    asyncio.run(main())