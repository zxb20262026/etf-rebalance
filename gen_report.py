#!/usr/bin/env python3
"""
ETF组合调仓周报 — 数据采集 + 分析 + HTML生成
双频推送：周日20:00 + 周三20:00
"""
import urllib.request, ssl, json, re, time, os, sys

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

def get(url, enc="utf-8", t=10):
    req = urllib.request.Request(url, headers=H)
    return urllib.request.urlopen(req, timeout=t, context=ssl_ctx).read().decode(enc, errors="replace")

# ═══════════════════════════════════
# 持仓配置
# ═══════════════════════════════════
PORTFOLIO = [
    {"code":"159819","name":"人工智能ETF易方达","dir":"大模型/AI","market":"sz"},
    {"code":"159516","name":"半导体设备ETF国泰","dir":"算力芯片","market":"sz"},
    {"code":"159995","name":"芯片ETF华夏","dir":"芯片半导体","market":"sz"},
    {"code":"562500","name":"机器人ETF华夏","dir":"机器人","market":"sh"},
    {"code":"159227","name":"航空航天ETF华夏","dir":"商业航天","market":"sz"},
    {"code":"512710","name":"军工龙头ETF富国","dir":"军工","market":"sh"},
    {"code":"516520","name":"智能驾驶ETF华泰柏瑞","dir":"自动驾驶","market":"sh"},
]

# ═══════════════════════════════════
# 数据采集
# ═══════════════════════════════════
def fetch_all():
    results = []
    for p in PORTFOLIO:
        code = p["code"]
        mkt = p["market"]
        entry = {"code": code, "name": p["name"], "dir": p["dir"]}
        try:
            # 新浪实时行情
            raw = get(f"https://hq.sinajs.cn/list={mkt}{code}", "gbk")
            m = re.search(r'"([^"]*)"', raw)
            if m:
                f = m.group(1).split(",")
                if len(f) >= 6:
                    entry["price"] = float(f[3])
                    prev = float(f[2])
                    entry["change_pct"] = round((entry["price"]/prev-1)*100, 2)
                    entry["high"] = float(f[4])
                    entry["low"] = float(f[5])
        except Exception as e:
            entry["error"] = str(e)[:60]

        # 腾讯K线 → 多周期收益 + 均线
        try:
            fc = f"{mkt}{code}"
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={fc},day,,,120,qfq"
            d = json.loads(get(url))
            klines = d["data"][fc].get("qfqday", []) or d["data"][fc].get("day", [])
            closes = []
            for k in klines:
                if isinstance(k, list) and len(k) >= 3:
                    closes.append(float(k[2]))
            if closes:
                N = len(closes)
                latest = closes[-1]
                if not entry.get("price"): entry["price"] = latest
                ret = {}
                if N >= 5:   ret["1w"] = round((latest/closes[-5]-1)*100, 2)
                if N >= 22:  ret["1m"] = round((latest/closes[-22]-1)*100, 2)
                if N >= 66:  ret["3m"] = round((latest/closes[-66]-1)*100, 2)
                # YTD
                ytd_close = next((c for k, c in zip(klines, closes) if k[0] >= "2026-01-01"), None)
                if ytd_close and ytd_close > 0:
                    ret["ytd"] = round((latest/ytd_close-1)*100, 2)
                entry["returns"] = ret
                # 均线
                if N >= 20:
                    ma20 = round(sum(closes[-20:])/20, 4)
                    entry["ma20_dev"] = round((latest/ma20-1)*100, 2)
                if N >= 60:
                    ma60 = round(sum(closes[-60:])/60, 4)
                    entry["ma60_dev"] = round((latest/ma60-1)*100, 2)
        except Exception as e:
            entry["kline_error"] = str(e)[:60]

        # 规模
        try:
            js = get(f"https://fund.eastmoney.com/pingzhongdata/{code}.js")
            m = re.search(r'Data_fluctuationScale\s*=\s*(\{.*?\});', js, re.DOTALL)
            if m:
                d = json.loads(m.group(1))
                series = d.get("series", [])
                if series:
                    entry["aum"] = round(series[-1].get("y", 0), 2)
        except:
            pass

        results.append(entry)
    return results

# ═══════════════════════════════════
# 市场环境
# ═══════════════════════════════════
def fetch_market():
    env = {}
    try:
        # 沪深300
        raw = get("https://hq.sinajs.cn/list=sh000300", "gbk")
        f = raw.split('"')[1].split(",")
        env["hs300_price"] = float(f[3])
        env["hs300_prev"] = float(f[2])
        env["hs300_chg"] = round((env["hs300_price"]/env["hs300_prev"]-1)*100, 2)
    except: pass
    try:
        # 两市成交额 (上证+深证)
        raw = get("https://hq.sinajs.cn/list=sh000001,sz399001", "gbk")
        sum_vol = 0
        for line in raw.strip().split("\n"):
            if '"' in line:
                parts = line.split('"')[1].split(",")
                if len(parts) > 9:
                    sum_vol += float(parts[9]) if parts[9] else 0
        env["volume"] = round(sum_vol / 100000000, 1)  # 亿
    except: pass
    try:
        # YTD (腾讯K线)
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000300,day,2025-12-01,,300,qfq"
        d = json.loads(get(url))
        klines = d["data"]["sh000300"].get("qfqday", []) or d["data"]["sh000300"].get("day", [])
        for k in klines:
            if k[0] >= "2026-01-01":
                env["hs300_ytd"] = round((env["hs300_price"]/float(k[2])-1)*100, 2)
                break
    except: pass
    return env

# ═══════════════════════════════════
# 分析引擎
# ═══════════════════════════════════
def analyze(data, env):
    # 强度评分 1-10
    for e in data:
        score = 5
        chg = e.get("change_pct") or 0
        r = e.get("returns") or {}
        ma20d = e.get("ma20_dev") or 0
        ma60d = e.get("ma60_dev") or 0

        if chg > 3: score += 2
        elif chg > 1: score += 1
        elif chg < -2: score -= 2
        elif chg < -1: score -= 1

        r1m = r.get("1m") or 0
        if r1m > 10: score += 2
        elif r1m > 3: score += 1
        elif r1m < -5: score -= 2
        elif r1m < -2: score -= 1

        if ma20d > 0 and ma60d > 0: score += 1  # 多头排列
        elif ma20d < 0 and ma60d < 0: score -= 1  # 空头排列

        score = max(1, min(10, score))
        e["strength_score"] = score
        e["strength_label"] = "🔥🔥🔥" if score >= 7 else ("🔥🔥" if score >= 5 else ("🔥" if score >= 4 else ("🟡" if score >= 3 else "⚠️")))

        # 均线排列
        if ma20d > 0 and ma60d > 0:
            e["align"] = "多头排列 ✅"
        elif ma20d < 0 and ma60d < 0:
            e["align"] = "空头排列 ⚠️"
        else:
            e["align"] = "均线纠缠 ↔️"

    # 按强度排序
    data.sort(key=lambda x: -(x.get("strength_score", 0)))

    # 生成建议
    for e in data:
        score = e["strength_score"]
        if score >= 7:
            e["action"] = "🔼 加仓"
            e["action_detail"] = "强度榜首，均线多头，趋势加速"
            e["action_pct"] = "+3~5%"
        elif score >= 5:
            e["action"] = "➡️ 持有"
            e["action_detail"] = "趋势健康，继续持有观察"
            e["action_pct"] = "—"
        elif score >= 4:
            e["action"] = "🟡 观察"
            e["action_detail"] = "方向未明，等均线给出信号"
            e["action_pct"] = "—"
        elif score >= 2:
            e["action"] = "🔽 减仓"
            e["action_detail"] = "持续走弱，减仓等右侧信号"
            e["action_pct"] = "-3~5%"
        else:
            e["action"] = "🚫 止损"
            e["action_detail"] = "弱者恒弱，止损换强"
            e["action_pct"] = "-5~10%"

    return data

# ═══════════════════════════════════
# HTML 生成
# ═══════════════════════════════════
def generate(analyzed, env):
    now = time.strftime("%Y-%m-%d %H:%M")
    rows = ""
    for e in analyzed:
        r = e.get("returns", {})
        fmt = lambda v: f"{v:+.1f}%" if v is not None else "—"
        fmt2 = lambda v: f"{v:+.2f}%" if v is not None else "—"
        rows += f"""<tr>
            <td>{analyzed.index(e)+1}</td>
            <td class="code">{e['code']}</td>
            <td>{e['name']}</td>
            <td>{e['dir']}</td>
            <td class="num">{fmt2(e.get('change_pct'))}</td>
            <td class="num">{fmt(r.get('1w'))}</td>
            <td class="num">{fmt(r.get('1m'))}</td>
            <td class="num">{fmt(r.get('3m'))}</td>
            <td class="num">{fmt2(e.get('ma20_dev'))}</td>
            <td>{e['align']}</td>
            <td class="strength">{e['strength_label']} {e['strength_score']}/10</td>
            <td class="action">{e['action']}</td>
            <td class="detail">{e.get('action_detail','')}</td>
        </tr>"""

    # 市场环境信号
    hs_chg = env.get("hs300_chg") or 0
    hs_ytd = env.get("hs300_ytd") or 0
    vol = env.get("volume") or 0
    env_tag = "🔥 成长强" if hs_ytd > 0 else ("🟡 震荡" if hs_ytd > -10 else "💧 弱势")
    vol_tag = "🔥 活跃" if vol > 10000 else ("🟡 正常" if vol > 6000 else "💤 缩量")

    # 方向统计
    hold_count = len([e for e in analyzed if e['action'] in ('🔼 加仓','➡️ 持有')])
    cut_count = len([e for e in analyzed if e['action'] in ('🔽 减仓','🚫 止损')])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ETF组合调仓周报</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:900px;margin:0 auto;padding:20px}}
.header{{margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #21262d}}
.header h1{{font-size:1.5em;color:#f0f6fc;margin-bottom:4px}}
.header .sub{{font-size:0.8em;color:#6e7681}}
.env-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}}
.env-card{{background:#131a26;border:1px solid #1e2d45;border-radius:8px;padding:12px;text-align:center}}
.env-card .label{{font-size:0.7em;color:#6e7681;margin-bottom:4px}}
.env-card .val{{font-size:1.2em;font-weight:700;color:#e6edf3}}
.env-card .tag{{font-size:0.7em;margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:0.85em;margin-bottom:20px}}
th{{background:#161b22;color:#8b949e;font-weight:600;padding:10px 6px;text-align:left;border-bottom:1px solid #21262d;font-size:0.8em}}
td{{padding:8px 6px;border-bottom:1px solid #161b22}}
tr:hover td{{background:#131a26}}
.code{{color:#58a6ff;font-weight:600;font-family:monospace}}
.num{{text-align:right;font-family:monospace}}
.strength{{font-weight:700}}
.action{{font-weight:700;font-size:0.95em}}
.detail{{font-size:0.75em;color:#8b949e;max-width:160px}}
.summary{{background:#131a26;border:1px solid #1e2d45;border-radius:8px;padding:16px;margin-bottom:20px}}
.summary h3{{font-size:0.9em;color:#e6edf3;margin-bottom:8px}}
.summary p{{font-size:0.8em;color:#8b949e;line-height:1.6}}
.r{{color:#f85149}}.g{{color:#3fb950}}.y{{color:#d29922}}
.footer{{text-align:center;font-size:0.7em;color:#484f58;margin-top:30px;padding-top:16px;border-top:1px solid #21262d}}
@media(max-width:600px){{.env-grid{{grid-template-columns:repeat(2,1fr)}}table{{font-size:0.7em}}}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 ETF组合调仓周报</h1>
  <div class="sub">{now} · 自动生成 · 仅供参考不构成投资建议</div>
</div>

<div class="env-grid">
  <div class="env-card">
    <div class="label">沪深300</div>
    <div class="val">{env.get('hs300_price','—'):.0f}</div>
    <div class="tag {('r' if hs_chg>0 else 'g' if hs_chg<0 else '')}">今日 {hs_chg:+.2f}% &nbsp; YTD {hs_ytd:+.1f}%</div>
  </div>
  <div class="env-card">
    <div class="label">市场风格</div>
    <div class="val">{env_tag}</div>
    <div class="tag">科技 vs 价值</div>
  </div>
  <div class="env-card">
    <div class="label">两市成交</div>
    <div class="val">{vol:.0f}亿</div>
    <div class="tag">{vol_tag}</div>
  </div>
  <div class="env-card">
    <div class="label">操作倾向</div>
    <div class="val">{hold_count}持{cut_count}减</div>
    <div class="tag">加/持:{hold_count} 减/止:{cut_count}</div>
  </div>
</div>

<div class="summary">
  <h3>🧠 核心逻辑</h3>
  <p>强者恒强：芯片/AI/半导体 → 加仓方向<br>
  均线纠缠：军工 → 等方向明确<br>
  弱者恒弱：自动驾驶/航空航天 → 减仓等右侧信号</p>
</div>

<table>
<thead>
<tr>
  <th>#</th><th>代码</th><th>名称</th><th>方向</th>
  <th>今日</th><th>1周</th><th>1月</th><th>3月</th>
  <th>MA20</th><th>均线</th><th>强度</th><th>建议</th><th>理由</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>

<div class="summary">
  <h3>📋 本周操作清单</h3>
  <p>{"".join(f"{e['action']} <b>{e['code']} {e['name']}</b> {e.get('action_pct','')} — {e.get('action_detail','')}<br>" for e in analyzed if e['action'] != '➡️ 持有')}</p>
  <p style="margin-top:8px;font-size:0.75em;color:#484f58">
    ⚠️ 以上为数据驱动建议，实际调仓请结合个人风险偏好和仓位管理原则
  </p>
</div>

<div class="footer">
  <p>数据来源: 新浪/腾讯/东方财富 | Generated: {now}</p>
</div>
</body>
</html>"""
    return html

# ═══════════════════════════════════
# Main
# ═══════════════════════════════════
if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "index.html")
    print(f"🔄 采集持仓数据...", flush=True)
    data = fetch_all()
    print(f"✅ {len(data)}只数据获取完成", flush=True)
    print(f"📡 市场环境...", flush=True)
    env = fetch_market()
    print(f"🧠 分析中...", flush=True)
    analyzed = analyze(data, env)
    print(f"📄 生成报告...", flush=True)
    html = generate(analyzed, env)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ {out} ({len(html)} chars)", flush=True)
