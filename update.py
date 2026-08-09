# -*- coding: utf-8 -*-
"""
信息驾驶舱 - 数据采集与页面构建脚本
=====================================
数据源(全部免费、国内可访问、无需密钥):
  A股指数/涨跌榜/全球指数 : 东方财富 push2 API
  日本新番(今日放送)      : Bangumi (bgm.tv) calendar API
  俄乌战线地图+战况       : ISW (Institute for the Study of War) 每日评估
  AI/硬件/小岛秀夫/TWICE/日本动画产业新闻 : Bing News RSS (按关键词检索)
  HuggingFace 趋势模型    : huggingface.co/api/trending

输出:
  dashboard.html  (自包含单文件, 数据内嵌, 双击即可打开)
  data.json       (原始数据, 供调试/二次使用)

用法:
  python update.py
"""
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape

BASE = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=8))          # 北京时间
JST = timezone(timedelta(hours=9))         # 东京时间
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
WARNINGS = []


# ---------------------------------------------------------------- helpers
def http_get(url, timeout=30, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()


def norm_diff(diff):
    if diff is None:
        return []
    if isinstance(diff, dict):
        return list(diff.values())
    return diff


def fmt_yi(v):
    """金额(元) -> 亿 字符串"""
    try:
        f = float(v)
        if f >= 1e8:
            return "%.0f亿" % (f / 1e8)
        return "%.0f万" % (f / 1e4)
    except Exception:
        return "--"


def parse_pub(pub, now):
    if not pub:
        return None
    try:
        t = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
        t = t.replace(tzinfo=timezone.utc).astimezone(TZ)
        return t
    except Exception:
        try:
            t = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
            return t.astimezone(TZ)
        except Exception:
            return None


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        WARNINGS.append("[%s] %s" % (getattr(fn, "__name__", "fetch"), e))
        return None


# ---------------------------------------------------------------- A股(腾讯行情 + 新浪行情)
def safe_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _pfx(code):
    """股票代码 -> 腾讯行情前缀 (sh/sz/bj)"""
    if code[0] == "6":          # 60x/68x -> 沪市
        return "sh" + code
    if code[0] in "032":        # 00x/30x/20x -> 深市(含创业板300/301)
        return "sz" + code
    return "bj" + code          # 4x/8x -> 北交所


def tencent_quotes(codes):
    """腾讯行情接口: http://qt.gtimg.cn/q=code1,code2... (GBK), 每批最多6个代码"""
    out = []
    for i in range(0, len(codes), 6):
        chunk = codes[i:i + 6]
        url = "http://qt.gtimg.cn/q=" + ",".join(chunk)
        raw = http_get(url, timeout=20).decode("gbk", "ignore")
        for m in re.finditer(r'v_\w+="([^"]*)"', raw):
            f = m.group(1).split("~")
            if len(f) < 33:
                continue
            try:
                price = float(f[3])
            except Exception:
                continue
            amount_wan = safe_float(f[37]) if len(f) > 37 else 0.0
            out.append({
                "name": f[1], "code": f[2],
                "price": round(price, 2),
                "pct": round(safe_float(f[32]), 2),
                "chg": round(safe_float(f[31]), 2),
                "amount": ("%.0f亿" % (amount_wan / 1e4)) if amount_wan else "--",
            })
    return out


def sina_movers(asc, n=6):
    """新浪行情中心: 按涨跌幅排序的沪深A股榜单 (asc=0 涨幅榜 / asc=1 跌幅榜)"""
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodeData?" + urllib.parse.urlencode(
               {"page": "1", "num": n, "sort": "changepercent", "asc": asc, "node": "hs_a"}))
    raw = http_get(url, timeout=20).decode("utf-8", "ignore")
    arr = json.loads(raw)
    out = []
    for r in arr:
        try:
            out.append({"name": r["name"], "code": r["code"],
                        "pct": round(float(r["changepercent"]), 2),
                        "price": round(float(r["trade"]), 2)})
        except Exception:
            continue
    return out


def fetch_ashare():
    codes = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000688", "bj899050"]
    indices = tencent_quotes(codes)
    gainers, losers = sina_movers(0), sina_movers(1)
    return {"indices": indices, "gainers": gainers, "losers": losers,
            "status": market_status()}


def fetch_global_indices():
    return tencent_quotes(["hkHSI", "usDJI", "usIXIC", "usINX"])


def market_status():
    now = datetime.now(TZ)
    wd = now.weekday()
    if wd >= 5:
        return {"state": "休市", "label": "周末休市", "date": now.strftime("%Y-%m-%d")}
    hm = now.hour * 60 + now.minute
    if 9 * 60 + 30 <= hm <= 11 * 60 + 30 or 13 * 60 <= hm <= 15 * 60:
        return {"state": "交易中", "label": "盘中实时行情", "date": now.strftime("%Y-%m-%d")}
    if hm < 9 * 60 + 30:
        return {"state": "未开盘", "label": "今日尚未开盘", "date": now.strftime("%Y-%m-%d")}
    return {"state": "已收盘", "label": "今日已收盘", "date": now.strftime("%Y-%m-%d")}


# ---------------------------------------------------------------- 日本新番
def fetch_anime():
    raw = http_get("https://api.bgm.tv/calendar", timeout=30,
                   headers={"User-Agent": "InfoDashboard/1.0 (personal dashboard)"})
    cal = json.loads(raw)
    jst = datetime.now(JST)
    wid = jst.weekday() + 1  # 1=Mon ... 7=Sun (Bangumi 定义)
    items = []
    for day in cal:
        if day.get("weekday", {}).get("id") == wid:
            for it in day.get("items", []):
                sc = (it.get("rating") or {}).get("score")
                items.append({
                    "id": it.get("id"),
                    "name": it.get("name", ""),
                    "name_cn": it.get("name_cn") or "",
                    "score": round(float(sc), 2) if sc else None,
                    "img": (it.get("images") or {}).get("common", ""),
                    "air_date": it.get("air_date") or "",
                    "rank": it.get("rank"),
                })
            break
    items.sort(key=lambda x: -(x["score"] or 0))
    return {"date": jst.strftime("%m-%d"), "weekday": "月火水木金土日"[jst.weekday()],
            "items": items[:22]}


# ---------------------------------------------------------------- 俄乌战线
def fetch_isw():
    """ISW 每日俄乌攻势评估: 抓标题/摘要/控制区地图(可能被 Cloudflare 限流, 失败返回 None)"""
    url = "https://www.understandingwar.org/backgrounder/russian-offensive-campaign-assessment"
    txt = ""
    for attempt in range(3):
        try:
            txt = http_get(url, timeout=45, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }).decode("utf-8", "ignore")
            break
        except Exception:
            if attempt < 2:
                time.sleep(4)

    def og(p):
        m = (re.search(r'property="og:%s"\s+content="([^"]*)"' % p, txt)
             or re.search(r'content="([^"]*)"\s+property="og:%s"' % p, txt))
        return m.group(1).strip() if m else ""

    title = og("title")
    desc = og("description")
    if not title:
        m = re.search(r"<title>([^<]*)</title>", txt)
        title = m.group(1) if m else "ISW 俄乌战况评估"
    title = re.sub(r"\s*\|\s*Institute for the Study of War\s*$", "", title)

    # 从正文图片里找真正的"评估控制区地形图"(webp/png, alt 含 Control of Terrain)
    map_img, map_url = "", url
    for m in re.finditer(r'<img[^>]+>', txt, re.I):
        tag = m.group(0)
        if re.search(r'control\s+of\s+terrain', tag, re.I):
            sm = re.search(r'src="([^"]+)"', tag)
            if sm:
                src = sm.group(1)
                map_img = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)  # 缩略图 -> 原图
                break
    mm = re.search(r'https://understandingwar\.org/map/[^"\'<>\s]*(?:control|terrain)[^"\'<>\s]*', txt, re.I)
    if mm:
        map_url = mm.group(0)

    body = ""
    clean = re.sub(r"<script[^>]*>.*?</script>", " ", txt, flags=re.S | re.I)
    clean = re.sub(r"<style[^>]*>.*?</style>", " ", clean, flags=re.S | re.I)
    art = re.search(r"<article[^>]*>(.*?)</article>", clean, flags=re.S | re.I)
    scope = art.group(1) if art else clean
    for p in re.findall(r"<p[^>]*>(.*?)</p>", scope, re.S):
        t = unescape(re.sub(r"<[^>]+>", "", p))
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 90 and not t.startswith("#"):
            if t.lower().startswith("this assessment provides") and not body:
                continue  # 跳过免责声明段
            body = body + (" " if body else "") + t
            if len(body) >= 500:
                break
    summary = re.sub(r"\s+", " ", unescape(body or desc)).strip()[:620]
    if not map_img and not summary:
        return None

    return {"title": title[:120], "summary": summary,
            "map_url": map_url, "url": url,
            "links": [
                {"name": "ISW 评估原文", "url": url},
                {"name": "ISW 官方地图页", "url": map_url},
                {"name": "实时战线地图 Liveuamap", "url": "https://liveuamap.com/"},
                {"name": "深州地图 DeepStateMap", "url": "https://deepstatemap.live/en"},
            ]}


# ---------------------------------------------------------------- 新闻聚合(新浪/IT之家/机核 + 本地关键词过滤)
SINA_POOLS = [("153", "2516"), ("153", "2515")]


def sina_roll(pageid, lid, num=60):
    url = "https://feed.mix.sina.com.cn/api/roll/get?" + urllib.parse.urlencode(
        {"pageid": pageid, "lid": lid, "num": num, "page": "1"})
    raw = http_get(url, timeout=20)
    d = json.loads(raw.decode("utf-8", "ignore"))
    out = []
    for it in (d.get("result", {}).get("data") or []):
        title = (it.get("title") or "").strip()
        if not title:
            continue
        t = datetime.fromtimestamp(int(it.get("ctime") or 0), tz=TZ)
        out.append({"title": title[:110], "link": it.get("url") or "",
                    "time": t.strftime("%m-%d %H:%M"),
                    "src": (it.get("media_name") or "")[:14]})
    return out


def rss_feed(url, limit=40, default_src="RSS"):
    raw = http_get(url, timeout=25)
    root = ET.fromstring(raw)
    now = datetime.now(TZ)
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        link = it.findtext("link") or ""
        t = parse_pub(it.findtext("pubDate") or "", now)
        src = (it.findtext("source") or "").strip()
        out.append({"title": title[:110], "link": link,
                    "time": t.strftime("%m-%d %H:%M") if t else "",
                    "src": src[:14] or default_src})
        if len(out) >= limit:
            break
    return out


def news_pool():
    """汇总多源新闻池, 供各板块按关键词过滤"""
    pool = []
    try:
        pool += rss_feed("https://www.ithome.com/rss/", 40, "IT之家")   # IT之家(科技/硬件)
    except Exception as e:
        WARNINGS.append("[ithome] %s" % e)
    try:
        pool += rss_feed("https://www.gcores.com/rss", 30, "机核")      # 机核(游戏/文化)
    except Exception as e:
        WARNINGS.append("[gcores] %s" % e)
    for pageid, lid in SINA_POOLS:
        try:
            pool += sina_roll(pageid, lid, 80)
        except Exception as e:
            WARNINGS.append("[sina:%s/%s] %s" % (pageid, lid, e))
    seen, uniq = set(), []
    for it in pool:
        k = it["title"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return uniq


def _match(t_low, kw):
    if kw.isascii() and kw.isalnum():
        return re.search(r"\b" + re.escape(kw) + r"\b", t_low)
    return kw in t_low


def filter_news(pool, keywords, limit, defaults=None):
    """关键词过滤 + 精选兜底, 保证板块始终有内容"""
    hits = []
    for it in pool:
        t = (it.get("title") or "").lower()
        if any(_match(t, k.lower()) for k in keywords):
            hits.append(it)
        if len(hits) >= limit:
            break
    for d_ in (defaults or []):
        if len(hits) >= limit:
            break
        hits.append(d_)
    return hits[:limit]


def fetch_hf_trending():
    try:
        raw = http_get("https://huggingface.co/api/trending", timeout=30)
    except Exception:
        raw = http_get("https://hf-mirror.com/api/trending", timeout=30)
    d = json.loads(raw)
    out = []
    for m in (d.get("recentlyTrending") or []):
        rd = m.get("repoData") or {}
        mid = rd.get("id") or ""
        if not mid:
            continue
        out.append({"id": mid, "likes": rd.get("likes", 0),
                    "downloads": rd.get("downloads", 0),
                    "pipeline": rd.get("pipeline_tag", "")})
        if len(out) >= 6:
            break
    return out


# ---------------------------------------------------------------- 精选资料(静态, 定期人工维护)
AI_MODELS = [
    {"name": "DeepSeek-V4-Pro", "vendor": "深度求索", "note": "1.6T总参/49B激活, 1M上下文; 8/6正式版登顶 OpenRouter 调用量"},
    {"name": "Kimi K3", "vendor": "月之暗面", "note": "7月中旬发布, 两万亿参数级, 1M 上下文, Agent 能力登顶全球榜单"},
    {"name": "GLM-5.2", "vendor": "智谱AI", "note": "745B/44B MoE 开源, 200K上下文; 另有 Solid 1M 无损上下文版"},
    {"name": "Qwen3.8-Max", "vendor": "阿里通义", "note": "8月正式发布, 参数规模达 2.4 万亿"},
    {"name": "MiniMax H3 / M3", "vendor": "MiniMax", "note": "H3 多模态生成模型开源, 视频编辑能力全球第一"},
    {"name": "混元 Hy3", "vendor": "腾讯", "note": "295B/21B 激活 MoE, 256K 上下文, 开源; 快慢思维融合"},
    {"name": "MiMo-V2.5-Pro", "vendor": "小米", "note": "1.02T/42B 激活, 1M 上下文, MIT 开源, 长期 Agent 任务"},
    {"name": "GPT-5.5", "vendor": "OpenAI", "note": "2026-05 发布, 800K 输入/128K 输出, $3/$20 每百万 token"},
    {"name": "Gemini 3.1 Pro", "vendor": "Google", "note": "ARC-AGI-2 达 77.1%, 推理/多模态/Agent 全面升级"},
    {"name": "Claude Sonnet 4.6", "vendor": "Anthropic", "note": "1M 上下文, 编码与电脑操作能力显著提升, 保持原价"},
]
VIDEO_MODELS = [
    {"name": "Seedance 2.5", "vendor": "字节跳动", "note": "长叙事+多模态参考+编辑能力, 音画联合生成"},
    {"name": "MiniMax H3", "vendor": "MiniMax", "note": "2K 分辨率直出, 最长15秒音画内容, 视频编辑榜第一"},
    {"name": "可灵 2.x", "vendor": "快手", "note": "国内头部文生视频, 电影级运镜"},
    {"name": "通义万相 Wan", "vendor": "阿里", "note": "开源视频生成, 2.1 系列性能领跑"},
    {"name": "Sora 2", "vendor": "OpenAI", "note": "文生视频标杆, 原生多模态叙事"},
    {"name": "Veo 3", "vendor": "Google", "note": "原生音频+视频, 专业级质感"},
    {"name": "即梦 Dreamina", "vendor": "字节", "note": "面向创作者的图像视频工具集"},
    {"name": "Vidu 2", "vendor": "生数科技", "note": "高一致性, 首尾帧控制出色"},
    {"name": "Runway Gen-4", "vendor": "Runway", "note": "专业影视工作流, 场景一致性"},
    {"name": "混元视频", "vendor": "腾讯", "note": "全链路视频生成, 与元宝深度集成"},
]
HARDWARE = [
    {"cat": "CPU", "name": "AMD Ryzen 9 9850X3D", "note": "2026 游戏CPU之王: 5.6GHz/104MB缓存, 较 i9-285K 快约27%", "link": "https://www.amd.com/zh-hans/products/processors"},
    {"cat": "CPU", "name": "Intel Nova Lake(预览)", "note": "48核(16P+32E), 18A工艺, LGA1954新插槽, 预计2026年底上市", "link": "https://www.intel.cn/"},
    {"cat": "显卡", "name": "NVIDIA RTX 5090 / RTX Spark", "note": "Blackwell 旗舰; 新增 ARM 架构 PC 芯片 RTX Spark 对标苹果 M5", "link": "https://www.nvidia.cn/"},
    {"cat": "显卡", "name": "AMD RX 9070 GRE", "note": "RDNA4, 12GB, 1440p 甜品卡, $549, 较 RTX 5060 Ti 快约21%", "link": "https://www.amd.com/zh-hans/products/graphics"},
    {"cat": "主板", "name": "AM5 平台(至2029) / X970", "note": "AMD 承诺 AM5 支持到2029; Intel 平台换 LGA1954 需换新主板", "link": "https://www.asus.com/cn/"},
    {"cat": "内存", "name": "DDR5 8000+ / CUDIMM", "note": "高频 DDR5 普及, 主动散热内存登场(降温约15℃), EXPO 一键超频", "link": "https://www.gskill.com/"},
    {"cat": "显示器", "name": "1000Hz / 5K Mini-LED", "note": "宏碁首发1000Hz电竞屏; 微星5K Mini-LED双模(5K@180Hz/2K@330Hz)", "link": "https://www.acer.com.cn/"},
    {"cat": "键盘", "name": "磁轴/光轴电竞键盘", "note": "RT 快速触发成主流, 国产客制化厂牌百花齐放", "link": "https://www.vgn.com.cn/"},
    {"cat": "耳机", "name": "旗舰降噪 / 电竞耳机", "note": "索尼 WH-1000XM 系列 / Audeze 平板振膜 / HyperX Cloud III", "link": "https://www.sony.com.cn/"},
    {"cat": "鼠标", "name": "轻量化旗舰", "note": "罗技 G Pro X Superlight 2 / 雷蛇 Viper V3 Pro / 国产 VXE", "link": "https://www.logitech.com.cn/"},
]
KOJIMA_PROFILE = {
    "name": "小岛秀夫",
    "birth": "1963-08-24 (神奈川)",
    "role": "游戏设计师·制作人, Kojima Productions 创始人",
    "icon_works": ["合金装备 MGS 系列(1987-2015)", "死亡搁浅(2019)", "死亡搁浅2: 海滩(2025-06-26, PS5)",
                   "OD (Xbox, 与 Jordan Peele 合作的恐怖作品)", "Physint (谍战新作)", "新东京工作室(2025)"],
    "links": [{"name": "KOJIMA PRODUCTIONS 官网", "url": "https://www.kojimaproductions.jp/"},
              {"name": "X @HIDEO_KOJIMA_EN", "url": "https://x.com/HIDEO_KOJIMA_EN"}],
}
TWICE_PROFILE = {
    "name": "TWICE",
    "birth": "2015-10-20 出道 (JYP Entertainment), 2026年出道10周年",
    "members": "9人: 娜琏·定延·Momo·Sana·志效·Mina·多贤·彩瑛·子瑜",
    "socials": [
        {"platform": "Instagram", "handle": "@twicetagram", "url": "https://www.instagram.com/twicetagram/"},
        {"platform": "X", "handle": "@JYPETWICE", "url": "https://x.com/JYPETWICE"},
        {"platform": "YouTube", "handle": "@TWICE", "url": "https://www.youtube.com/@TWICE"},
        {"platform": "TikTok", "handle": "@twice_tiktok_official", "url": "https://www.tiktok.com/@twice_tiktok_official"},
        {"platform": "Facebook", "handle": "JYPETWICE", "url": "https://www.facebook.com/JYPETWICE"},
    ],
    "members_socials": [
        {"name": "娜琏 Nayeon", "ig": "@nayeonyny", "url": "https://www.instagram.com/nayeonyny/"},
        {"name": "定延 Jeongyeon", "ig": "@jy_piece", "url": "https://www.instagram.com/jy_piece/"},
        {"name": "Momo 平井桃", "ig": "@momo", "url": "https://www.instagram.com/momo/"},
        {"name": "Sana 凑崎纱夏", "ig": "@m.by__sana", "url": "https://www.instagram.com/m.by__sana/"},
        {"name": "志效 Jihyo", "ig": "@_zyozyo", "url": "https://www.instagram.com/_zyozyo/"},
        {"name": "Mina 名井南", "ig": "@mina_sr_my", "url": "https://www.instagram.com/mina_sr_my/"},
        {"name": "多贤 Dahyun", "ig": "@dahhyunnee", "url": "https://www.instagram.com/dahhyunnee/"},
        {"name": "彩瑛 Chaeyoung", "ig": "@chaeyo.0", "url": "https://www.instagram.com/chaeyo.0/"},
        {"name": "子瑜 Tzuyu", "ig": "@thinkaboutzu", "url": "https://www.instagram.com/thinkaboutzu/"},
    ],
    "albums": ["《STRATEGY》(2024-12, 14th Mini)", "《THIS IS FOR》(2025, 15th Mini·世巡同名)",
               "《ENEMY》(2026, 最新专辑)"],
    "tour": "第六次世界巡演《THIS IS FOR》: 4月日本国家体育场360°舞台; 10-12月新加坡/吉隆坡/悉尼/墨尔本/高雄/香港/曼谷; 剩余44场",
    "links": [{"name": "JYP TWICE 官方", "url": "https://twice.jype.com/"},
              {"name": "YouTube 官方频道", "url": "https://www.youtube.com/@TWICE"}],
}
ANIME_STATS = [
    {"k": "市场规模(2024年度)", "v": "3.84 万亿日元", "note": "《动画产业报告2025》历史新高, 稳步迈向4万亿"},
    {"k": "海外市场占比", "v": "约 56.5%", "note": "首次持续超过国内市场, 流媒体(Netflix/Crunchyroll)驱动"},
    {"k": "电视动画年产量", "v": "约 300 部", "note": "2000年代初约100部, 制作数量持续攀升"},
    {"k": "制作市场", "v": "营收创新高", "note": "但缺工+外包成本上涨, 倒闭/停业案例增加"},
    {"k": "行业分化", "v": "赢家通吃", "note": "东宝(营业利润678亿日元新高)、索尼/Aniplex 稳健; 角川利润 -51.3%"},
    {"k": "趋势信号", "v": "串流泡沫隐忧", "note": "二次开发/海外授权成关键; MAPPA x Netflix 战略合作"},
]


# ---- 建议关注股票(综合分析: 板块主线 + 代表标的 + 关注理由; 仅供观察, 非投资建议) ----
A_SHARE_WATCH = [
    {"code": "300308", "name": "中际旭创", "theme": "AI光模块", "reason": "800G/1.6T光模块龙头, AI算力资本开支核心受益"},
    {"code": "300502", "name": "新易盛", "theme": "AI光模块", "reason": "光模块高弹性, 海外算力需求驱动"},
    {"code": "688256", "name": "寒武纪", "theme": "国产AI芯片", "reason": "国产算力核心标的, 大模型推理放量"},
    {"code": "688041", "name": "海光信息", "theme": "国产算力", "reason": "信创+国产DCU双主线"},
    {"code": "002371", "name": "北方华创", "theme": "半导体设备", "reason": "国产设备龙头, 晶圆厂扩产受益"},
    {"code": "688981", "name": "中芯国际", "theme": "晶圆代工", "reason": "国产代工龙头, 稼动率与价格回升"},
    {"code": "002230", "name": "科大讯飞", "theme": "AI应用", "reason": "大模型应用落地, 星火迭代提速"},
    {"code": "601689", "name": "拓普集团", "theme": "人形机器人", "reason": "机器人执行器核心供应商"},
    {"code": "600276", "name": "恒瑞医药", "theme": "创新药", "reason": "创新药BD出海密集, 管线兑现"},
    {"code": "688235", "name": "百济神州", "theme": "创新药", "reason": "全球化创新药企, 海外收入放量"},
    {"code": "601088", "name": "中国神华", "theme": "高股息", "reason": "高股息煤炭龙头, 防御属性强"},
    {"code": "300059", "name": "东方财富", "theme": "金融科技", "reason": "市场成交活跃, 业绩弹性大"},
]

# ---- 新闻关键词(用于本地过滤)与精选兜底 ----
AI_KW = ["AI", "大模型", "人工智能", "GPT", "Gemini", "Claude", "DeepSeek", "通义", "文心", "豆包",
         "Kimi", "智谱", "混元", "MiniMax", "ChatGPT", "智能体", "Agent", "GPU", "算力", "英伟达",
         "OpenAI", "Anthropic", "机器人", "Sora", "可灵", "视频生成", "多模态", "LLM", "开源模型",
         "HuggingFace", "芯片"]
HW_KW = ["显卡", "RTX", "RX", "CPU", "处理器", "主板", "内存", "DDR5", "显示器", "键盘", "鼠标",
         "耳机", "硬盘", "笔记本", "电竞", "OLED", "Mini-LED", "酷睿", "锐龙", "Ryzen", "Core Ultra",
         "NVIDIA", "AMD", "Intel", "英特尔", "RTX 50", "Blackwell", "磁轴"]
KOJIMA_KW = ["小岛秀夫", "死亡搁浅", "Death Stranding", "Kojima", "合金装备", "小岛工作室"]
TWICE_KW = ["TWICE", "娜琏", "志效", "定延", "子瑜", "多贤", "彩瑛", "JYP", "ONCE",
            "트와이스", "Momo", "Sana", "Mina", "水炸弹", "大巨蛋"]
ANIME_IND_KW = ["动画", "动漫", "番剧", "二次元", "新海诚", "宫崎骏", "鬼灭", "Aniplex", "角川",
                "东宝", "MAPPA", "动画产业", "anime", "漫画", "漫改", "轻小说", "声优", "剧场版",
                "OVA", "赛马娘", "龙珠", "高达", "宝可梦", "EVA"]
UKRAINE_KW = ["俄乌", "乌克兰", "俄罗斯", "前线", "战线", "泽连斯基", "普京", "俄军", "乌军",
              "基辅", "哈尔科夫", "顿巴斯", "克里米亚", "扎波罗热", "赫尔松", "顿涅茨克", "卢甘斯克",
              "黑海", "无人机", "导弹", "反攻", "库尔斯克"]

_cur = lambda title, link, src="资讯整理": {"title": title, "link": link, "time": "精选", "src": src}
ASHARE_KW = ["A股", "沪指", "深成指", "创业板", "科创50", "北证50", "北向", "涨停", "跌停", "证监会",
             "央行", "降息", "降准", "美联储", "IPO", "回购", "增持", "成交", "万亿", "牛市", "主力资金",
             "国家队", "A50", "恒生", "纳指", "道指", "标普", "半导体", "算力", "机器人", "创新药", "券商"]
A_SHARE_NEWS = [
    _cur("A股周五收盘综述: 上证指数涨1.02%收3940.04点, 科创50大涨2.51%, 两市成交合计超2.6万亿",
         "https://finance.sina.com.cn/stock/"),
    _cur("全球市场: 美股标普500再创收盘新高(纳指+1.3%), 恒生指数收涨0.54%",
         "https://finance.sina.com.cn/stock/usstock/"),
]
HARDWARE_NEWS = [
    _cur("Computex 2026: NVIDIA 发布 ARM 架构 PC 芯片 RTX Spark, 集成 Blackwell GPU, 对标苹果 M5",
         "https://new.qq.com/rain/a/20260605A09PXY00"),
    _cur("AMD 发布 Ryzen 7 7700X3D(104MB缓存,$329) 与 Radeon RX 9070 GRE(12GB,$549), 强化 1440p 游戏市场",
         "https://www.91mobiles.com/hub/computex-2026-amd-ryzen-x3d-cpus-radeon-9070-gre"),
    _cur("Intel 预览 Nova Lake 桌面平台: 最高48核(16P+32E), 18A 工艺, LGA1954 新插槽, 预计2026年底上市",
         "https://www.160.com/article/12732.html"),
    _cur("宏碁首发 1000Hz 电竞显示器; 微星 5K Mini-LED 双模电竞屏(5K@180Hz/2K@330Hz) 亮相 Computex 2026",
         "https://www.160.com/article/12732.html"),
    _cur("微星 40 周年发布 RTX 5090 LIGHTNING Z 旗舰与内置 AI 代理的 OLED 电竞显示器",
         "https://www.160.com/article/12732.html"),
    _cur("AMD 锐龙 AI Max 400 系列: 192GB 统一内存, 宣称本地可运行 300B 参数大模型",
         "https://www.160.com/article/12732.html"),
]
KOJIMA_NEWS = [
    _cur("小岛秀夫客串日剧《VIVANT》第二季第11集, 饰演警视总监\"绵贯贤\", 摘眼镜剃胡造型大反转",
         "https://dy.163.com/article/L2RI4BGS0526K1KN.html"),
    _cur("小岛工作室联名日本音频品牌 km5 推出《死亡搁浅2》限定 CD 播放器, 荧光橙致敬 BB Pod",
         "https://www.163.com/dy/article/L3ETSAA705561FY7.html"),
    _cur("小岛秀夫 X 发布\"早上好\"动态, 配图羊文学《Keep Walking》封面引玩家热议",
         "https://game.zol.com.cn/1226/12268058.html"),
]
TWICE_NEWS = [
    _cur("TWICE 出道10周年: 4月在日本国家体育场(MUFG Stadium)连开3场360°舞台演唱会, 刷新纪录",
         "https://lehoivietnam.com.vn/zh-hans/su-kien/5086-twice-mufg-stadium-concert-series"),
    _cur("娜琏确定担任 8/29 台北大巨蛋 SINGDOME 音乐电台演出嘉宾",
         "https://www.163.com/dy/article/L3DF6THD05528K16.html"),
    _cur("志效确认以 SPECIAL HEADLINER 身份加盟 9/12 高雄 WATERBOMB 水炸弹音乐节",
         "https://www.163.com/dy/article/L3DF6THD05528K16.html"),
    _cur("《THIS IS FOR》第六次世巡: 定延因健康因素缺席 10/4 菲律宾站, 世巡剩余44场",
         "https://cn.hitkn.com/a/shi-xun-huan-you-44chang-twiceding-yan-shen-ti-chu-zhuang-kuang-que-ding-que-xi-fei-lu-bin.html"),
]
ANIME_IND_NEWS = [
    _cur("《动画产业报告2025》: 市场规模达 3.84 万亿日元创历史新高, 海外占比约 56.5%",
         "https://www.info35.net/hotopics/44871.html"),
    _cur("东宝2026年2月期营收3606亿日元/营业利润678亿日元均创纪录; 角川营业利润同比下滑51.3%",
         "https://news.sohu.com/a/1031048251_761993"),
    _cur("MAPPA 与 Netflix 建立战略合作伙伴关系, 打造面向全球观众的新项目",
         "https://www.info35.net/hotopics/44871.html"),
    _cur("专家示警\"串流泡沫到顶\": KADOKAWA、TBS动画事业、Studio KAI 相继亏损或大幅减益",
         "https://www.ucmanga.com/a/ri-ben-dong-hua-ye-jing-bao-xiang-qi-da-han-jie-lian-yu-sun-zhuan-jia-shi-jing-chuan-liu-pao-mo-dao-ding-liao.html"),
]


# ---------------------------------------------------------------- 组装数据
def load_prev():
    """读取上次生成的数据, 用于不稳定数据源(ISW/HF)的兜底"""
    try:
        with open(os.path.join(BASE, "data.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def collect():
    now = datetime.now(TZ)
    prev = load_prev()
    pool = safe(news_pool) or []

    ukraine = safe(fetch_isw)
    if not ukraine or not ukraine.get("summary"):
        if prev and prev.get("ukraine") and prev["ukraine"].get("summary"):
            ukraine = prev["ukraine"]
            WARNINGS.append("[isw] 本次获取失败, 沿用上次数据(%s)" % (prev.get("generated_at") or "?"))
        elif not ukraine:
            ukraine = {"title": "ISW 数据暂不可用", "summary": "",
                       "url": "https://www.understandingwar.org/",
                       "links": [{"name": "ISW 官网", "url": "https://www.understandingwar.org/"}]}
    # 战线新闻集合: ISW 今日评估条目 + 实时新闻关键词过滤
    u_news = []
    if ukraine.get("url"):
        u_news.append({"title": ("今日战况: " + ukraine.get("title", "ISW 每日评估")) if ukraine.get("title") else "今日战况: ISW 每日评估",
                       "link": ukraine["url"], "time": "ISW每日评估", "src": "ISW"})
    u_news += filter_news(pool, UKRAINE_KW, 5, [])
    ukraine["news"] = u_news[:6]

    hf = safe(fetch_hf_trending) or []
    if not hf and prev and prev.get("ai", {}).get("hf"):
        hf = prev["ai"]["hf"]

    ashare = safe(fetch_ashare) or {"indices": [], "gainers": [], "losers": [], "status": market_status()}
    # 建议关注: 精选标的 + 实时行情合并
    try:
        qmap = {q["code"]: q for q in tencent_quotes([_pfx(w["code"]) for w in A_SHARE_WATCH])}
        ashare["watch"] = [dict(w,
                                price=qmap[w["code"]]["price"] if w["code"] in qmap else None,
                                pct=qmap[w["code"]]["pct"] if w["code"] in qmap else None)
                           for w in A_SHARE_WATCH]
    except Exception as e:
        WARNINGS.append("[watch] %s" % e)
        ashare["watch"] = []
    ashare["news"] = filter_news(pool, ASHARE_KW, 8, A_SHARE_NEWS)

    data = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "update_note": "每 2 小时由云端定时自动更新",
        "ashare": ashare,
        "global": safe(fetch_global_indices) or [],
        "anime": safe(fetch_anime) or {"date": now.strftime("%m-%d"), "weekday": "?", "items": []},
        "ukraine": ukraine,
        "ai": {
            "news_zh": filter_news(pool, AI_KW, 8, []),
            "hf": hf,
            "models": AI_MODELS,
            "video_models": VIDEO_MODELS,
        },
        "hardware": HARDWARE,
        "hw_news": filter_news(pool, HW_KW, 12, HARDWARE_NEWS),
        "kojima": {
            "news": filter_news(pool, KOJIMA_KW, 7, KOJIMA_NEWS),
            "profile": KOJIMA_PROFILE,
        },
        "twice": {
            "news": filter_news(pool, TWICE_KW, 7, TWICE_NEWS),
            "profile": TWICE_PROFILE,
        },
        "anime_industry": {
            "news": filter_news(pool, ANIME_IND_KW, 10, ANIME_IND_NEWS),
        },
    }
    return data


# ---------------------------------------------------------------- HTML 模板
TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信息驾驶舱 INFO COCKPIT</title>
<style>
:root{
  --bg:#0a0e14; --panel:#111821; --panel2:#0d141c; --line:#1d2836;
  --txt:#dce6f0; --mut:#8fa3b8; --dim:#5c6f85;
  --red:#ff4d4f; --green:#2ecc8f; --amber:#f5b942;
  --a:#ff4d4f; --anime:#f472b6; --ua:#a3e635; --ai:#38bdf8;
  --hw:#fb923c; --kj:#94a3b8; --tw:#f9a8d4; --id:#a78bfa; --info:#5c6f85;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg)}
body{font-family:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  color:var(--txt);background:
  radial-gradient(1200px 500px at 15% -10%, rgba(56,189,248,.08), transparent 60%),
  radial-gradient(1000px 500px at 90% -10%, rgba(167,139,250,.07), transparent 60%),
  var(--bg);min-height:100vh;padding:18px 20px 40px}
.wrap{max-width:1460px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin-bottom:18px;
  padding:14px 18px;background:linear-gradient(90deg,#101823,#0d131c 60%,#101823);
  border:1px solid var(--line);border-radius:14px}
.logo{display:flex;align-items:center;gap:12px}
.logo .dot{width:12px;height:12px;border-radius:50%;background:var(--ai);
  box-shadow:0 0 12px var(--ai);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
h1{font-size:22px;letter-spacing:2px;font-weight:700}
h1 small{color:var(--dim);font-size:12px;letter-spacing:3px;margin-left:10px;font-weight:400}
.hmeta{margin-left:auto;display:flex;gap:22px;flex-wrap:wrap;align-items:center;color:var(--mut);font-size:12.5px}
.hmeta b{color:var(--txt);font-weight:600}
#clock{font-family:Consolas,"JetBrains Mono",monospace;font-size:16px;color:var(--ai)}
#refreshNote{color:var(--dim);font-size:11.5px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
  border-radius:14px;padding:14px 16px;display:flex;flex-direction:column;min-width:0;
  box-shadow:0 4px 18px rgba(0,0,0,.25)}
.card>.head{display:flex;align-items:center;gap:8px;margin-bottom:10px;padding-bottom:9px;
  border-bottom:1px solid var(--line)}
.card>.head .bar{width:4px;height:15px;border-radius:2px;background:var(--ac,#38bdf8)}
.card>.head h2{font-size:15px;font-weight:700;letter-spacing:1px}
.card>.head .tag{margin-left:auto;font-size:11px;color:var(--mut);background:rgba(255,255,255,.04);
  border:1px solid var(--line);padding:2px 9px;border-radius:20px;white-space:nowrap}
.s8{grid-column:span 8}.s4{grid-column:span 4}.s12{grid-column:span 12}
@media(max-width:1180px){.s8,.s4,.s12{grid-column:span 6}}
@media(max-width:820px){.s8,.s4,.s12{grid-column:span 12}}
/* ---------- 通用 ---------- */
.mut{color:var(--mut)}.dim{color:var(--dim)}.small{font-size:12px}
.empty{padding:26px 10px;text-align:center;color:var(--dim);font-size:13px;border:1px dashed var(--line);border-radius:10px}
a{color:inherit;text-decoration:none}
.news{display:flex;flex-direction:column;gap:8px;overflow:hidden}
.news a{display:flex;gap:8px;align-items:baseline;padding:7px 9px;border-radius:8px;transition:background .15s}
.news a:hover{background:rgba(255,255,255,.05)}
.news .t{flex:1;font-size:12.8px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.news .time{color:var(--dim);font-size:11px;white-space:nowrap}
/* ---------- A股 ---------- */
.idx{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.idx .cell{flex:1 1 30%;min-width:120px;background:rgba(255,255,255,.03);border:1px solid var(--line);
  border-radius:10px;padding:8px 10px}
.idx .nm{font-size:12px;color:var(--mut);display:flex;justify-content:space-between;gap:4px}
.idx .px{font-size:19px;font-weight:700;font-family:Consolas,monospace;margin-top:2px}
.up{color:var(--red)} .down{color:var(--green)} .flat{color:var(--mut)}
.pct{font-size:12px;font-family:Consolas,monospace}
.mini{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.mini h3{font-size:12px;color:var(--mut);margin-bottom:5px;font-weight:600}
.mini table{width:100%;border-collapse:collapse;font-size:12px}
.mini td{padding:3.5px 2px;border-bottom:1px solid rgba(255,255,255,.04)}
.mini td:last-child{text-align:right;font-family:Consolas,monospace;white-space:nowrap}
.mini .nmc{display:flex;flex-direction:column}
.mini .nmc em{font-style:normal;color:var(--dim);font-size:10.5px}
.au2{display:grid;grid-template-columns:1fr 1.4fr;gap:12px;margin-top:4px}
@media(max-width:900px){.au2{grid-template-columns:1fr}}
.watch td{vertical-align:middle}
.watch .n em{display:block;font-style:normal;color:var(--dim);font-size:10px}
.watch .pxc{white-space:nowrap;font-family:Consolas,monospace;text-align:right}
.watch .d{font-size:11px;line-height:1.45;color:var(--mut)}
.glb{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;padding-top:10px;border-top:1px dashed var(--line)}
.glb span{font-size:11.5px;color:var(--mut);padding:3px 8px;border:1px solid var(--line);border-radius:20px;background:rgba(255,255,255,.02)}
.glb b{font-family:Consolas,monospace;margin:0 3px}
/* ---------- 新番 ---------- */
.anime{display:flex;flex-direction:column;gap:8px;max-height:560px;overflow:auto;padding-right:4px}
.anime::-webkit-scrollbar{width:5px}
.anime::-webkit-scrollbar-thumb{background:#263448;border-radius:3px}
.anime .it{display:flex;gap:10px;align-items:center;padding:7px;border-radius:10px;background:rgba(255,255,255,.025);border:1px solid var(--line)}
.anime img{width:44px;height:58px;object-fit:cover;border-radius:6px;background:#000;flex-shrink:0}
.anime .info{flex:1;min-width:0}
.anime .info .cn{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anime .info .jp{font-size:11.5px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anime .sc{font-size:12px;color:var(--amber);font-family:Consolas,monospace;flex-shrink:0}
/* ---------- 俄乌 ---------- */
.ua-wrap{display:grid;grid-template-columns:1.5fr 1fr;gap:14px;flex:1}
@media(max-width:1000px){.ua-wrap{grid-template-columns:1fr}}
.ua-map{position:relative;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#0b1017}
.ua-map img,.ua-map svg.handmap{width:100%;max-height:520px;object-fit:cover;display:block}
.ua-map .cap{position:absolute;left:0;right:0;bottom:0;padding:14px 12px 10px;font-size:11.5px;color:#cfe3d0;
  background:linear-gradient(transparent,rgba(6,10,8,.92))}
.ua-map a.zoom{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.55);color:#cfe3d0;font-size:11px;
  padding:4px 10px;border-radius:16px;border:1px solid rgba(255,255,255,.25)}
.ua-side{display:flex;flex-direction:column;gap:10px;min-width:0}
.ua-side h3{font-size:13.5px;line-height:1.5}
.ua-side .sum{font-size:12.3px;color:var(--mut);line-height:1.65;overflow:auto;max-height:210px;padding-right:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.legend span{font-size:10.8px;color:var(--mut);border:1px solid var(--line);border-radius:14px;padding:2.5px 8px;background:rgba(255,255,255,.02)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:1px}
.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:auto}
.links a{font-size:11.5px;color:var(--ai);border:1px solid rgba(56,189,248,.35);padding:4px 10px;border-radius:16px;background:rgba(56,189,248,.06)}
.links a:hover{background:rgba(56,189,248,.14)}
/* ---------- AI ---------- */
.ai-wrap{display:grid;grid-template-columns:1.05fr 1fr 0.95fr;gap:12px;flex:1;min-width:0}
@media(max-width:1100px){.ai-wrap{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.ai-wrap{grid-template-columns:1fr}}
.ai-wrap .col h3{font-size:12px;color:var(--mut);margin-bottom:8px;font-weight:600;letter-spacing:1px}
.tbl{font-size:11.8px;border-collapse:collapse;width:100%}
.tbl td{padding:4.5px 6px;border-bottom:1px solid rgba(255,255,255,.045);vertical-align:top}
.tbl .n{font-weight:600;white-space:nowrap}
.tbl .v{color:var(--mut);white-space:nowrap;font-size:10.8px}
.tbl .d{color:var(--mut);font-size:11px;line-height:1.4}
.hf{font-size:12px;display:flex;flex-direction:column;gap:6px;margin-top:8px}
.hf .m{display:flex;justify-content:space-between;gap:8px;padding:6px 8px;background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:8px}
.hf .m b{font-family:Consolas,monospace;font-size:11.5px;white-space:nowrap}
/* ---------- 硬件 ---------- */
.hw{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;flex:1}
@media(max-width:1100px){.hw{grid-template-columns:repeat(2,1fr)}}
.hw a{display:flex;flex-direction:column;gap:4px;padding:11px;border:1px solid var(--line);border-radius:11px;
  background:rgba(255,255,255,.028);transition:transform .12s,border-color .12s}
.hw a:hover{transform:translateY(-2px);border-color:rgba(251,146,60,.5)}
.hw .cat{font-size:10.5px;color:var(--hw);letter-spacing:1px}
.hw .nm{font-size:13px;font-weight:600;line-height:1.35}
.hw .nt{font-size:11.3px;color:var(--mut);line-height:1.5}
/* ---------- 小岛/TWICE/动画产业 ---------- */
.profile{font-size:12.3px;color:var(--mut);line-height:1.7;margin-bottom:8px}
.profile b{color:var(--txt)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px}
.chips span{font-size:11px;color:var(--txt);background:rgba(255,255,255,.045);border:1px solid var(--line);
  border-radius:15px;padding:3px 9px}
.sec{font-size:12px;color:var(--mut);margin:10px 0 6px;font-weight:600;letter-spacing:1px}
.soc{display:flex;flex-wrap:wrap;gap:6px}
.soc a{display:flex;flex-direction:column;gap:1px;min-width:100px;padding:6px 10px;border:1px solid var(--line);
  border-radius:9px;background:rgba(255,255,255,.03);transition:border-color .12s}
.soc a:hover{border-color:rgba(249,168,212,.6)}
.soc i{font-style:normal;font-size:10.5px;color:var(--mut)}
.soc b{font-size:11.8px;color:var(--tw);font-weight:600;font-family:Consolas,monospace;white-space:nowrap}
.soc.mem a{min-width:150px}
.chips a{display:inline-block;font-size:11px;color:var(--txt);background:rgba(255,255,255,.045);
  border:1px solid var(--line);border-radius:15px;padding:3px 9px;margin:2px}
.chips a:hover{border-color:var(--hw)}
.news-card{max-height:240px;overflow:auto;padding-right:3px}
.news-card::-webkit-scrollbar{width:5px}
.news-card::-webkit-scrollbar-thumb{background:#263448;border-radius:3px}
.stats{display:flex;flex-direction:column;gap:8px}
.stats .st{display:flex;gap:10px;align-items:baseline;padding:7px 9px;background:rgba(255,255,255,.03);
  border:1px solid var(--line);border-radius:9px}
.stats .k{font-size:11px;color:var(--mut);flex-shrink:0;width:86px}
.stats .v{font-size:13.5px;font-weight:700;color:var(--id);white-space:nowrap;font-family:Consolas,monospace}
.stats .n{font-size:11px;color:var(--mut);line-height:1.4}
/* ---------- 说明卡 ---------- */
.info-body{font-size:12.5px;color:var(--mut);line-height:1.9;flex:1}
.info-body b{color:var(--txt)}
.info-body .src{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.info-body .src span{font-size:10.8px;border:1px solid var(--line);border-radius:12px;padding:2px 8px;color:var(--dim)}
footer{margin-top:16px;text-align:center;color:var(--dim);font-size:11.5px;line-height:1.8}
footer code{background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:5px;padding:1px 7px;font-family:Consolas,monospace}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo"><span class="dot"></span>
      <h1>信息驾驶舱<small>INFO COCKPIT</small></h1>
    </div>
    <div class="hmeta">
      <span>数据更新 <b id="genAt"></b></span>
      <span id="clock">--:--:--</span>
      <span id="refreshNote">每30分钟自动刷新</span>
    </div>
  </header>

  <div class="grid">
    <!-- 俄乌战线 -->
    <section class="card s8" style="--ac:var(--ua)">
      <div class="head"><span class="bar"></span><h2>俄乌战线 · 综合态势</h2><span class="tag" id="uaTag">前线</span></div>
      <div class="ua-wrap">
        <div class="ua-map">
          <svg class="handmap" viewBox="0 0 220 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="俄乌战线手绘示意图">
            <defs>
              <filter id="sketch" x="-10%" y="-10%" width="120%" height="120%">
                <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="2" seed="7" result="n"/>
                <feDisplacementMap in="SourceGraphic" in2="n" scale="2.2"/>
              </filter>
            </defs>
            <g transform="rotate(-1 110 150)" filter="url(#sketch)">
              <path d="M22 68 L30 46 L58 38 L92 40 L128 38 L158 44 L182 52 L192 62 L197 76 L203 92 L207 108 L210 122 L208 138 L198 150 L186 157 L168 164 L152 170 L138 176 L128 180 L120 181 L112 177 L104 175 L96 172 L84 178 L70 182 L56 186 L42 187 L30 184 L22 178 L19 162 L18 138 L19 110 L21 86 Z"
                    fill="#24455c" stroke="#6fb1c9" stroke-width="2" stroke-linejoin="round"/>
              <path d="M196 62 L206 92 L211 118 L209 136 L198 150 L186 157 L168 164 L152 170 L138 176 L128 180 L120 181 L116 176 L124 170 L138 164 L154 156 L168 144 L178 128 L186 112 L192 94 L196 76 Z"
                    fill="#5c2230" stroke="#c05a5a" stroke-width="1.6" stroke-linejoin="round"/>
              <path d="M120 180 L126 190 L130 210 L124 228 L114 220 L104 205 L100 192 L108 184 Z"
                    fill="#5c2230" stroke="#c05a5a" stroke-width="1.6" stroke-linejoin="round"/>
              <path d="M196 74 C192 88 194 96 190 106 C186 116 182 122 176 130 C170 138 162 144 154 150 C146 156 138 162 130 168 C124 172 120 175 116 177"
                    fill="none" stroke="#e0a33a" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="6 5"/>
              <path d="M150 160 L140 148 M178 128 L168 120 M136 146 l-8 4 M164 118 l-8 4"
                    stroke="#e0a33a" stroke-width="2" stroke-linecap="round"/>
              <g font-size="9.5" fill="#c9d6e2" font-family="Microsoft YaHei, PingFang SC, sans-serif">
                <circle cx="78" cy="84" r="2.6" fill="#e8eef5"/><text x="84" y="87" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">基辅</text>
                <circle cx="188" cy="66" r="2.6" fill="#e8eef5"/><text x="174" y="60" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">哈尔科夫</text>
                <circle cx="128" cy="120" r="2.4" fill="#e8eef5"/><text x="133" y="123" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">第聂伯罗</text>
                <circle cx="142" cy="148" r="2.4" fill="#e8eef5"/><text x="128" y="152" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">扎波罗热</text>
                <circle cx="186" cy="124" r="2.4" fill="#e8eef5"/><text x="188" y="116" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">顿涅茨克</text>
                <circle cx="200" cy="104" r="2.4" fill="#e8eef5"/><text x="190" y="97" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">卢甘斯克</text>
                <circle cx="104" cy="168" r="2.4" fill="#e8eef5"/><text x="88" y="172" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">赫尔松</text>
                <circle cx="42" cy="180" r="2.4" fill="#e8eef5"/><text x="47" y="183" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">敖德萨</text>
                <circle cx="192" cy="146" r="2.4" fill="#e8eef5"/><text x="180" y="150" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">马里乌波尔</text>
                <circle cx="115" cy="213" r="2.4" fill="#e8eef5"/><text x="120" y="216" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">辛菲罗波尔</text>
                <circle cx="152" cy="50" r="2.2" fill="#e8eef5"/><text x="157" y="53" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">苏梅</text>
                <circle cx="27" cy="72" r="2.2" fill="#e8eef5"/><text x="32" y="75" style="paint-order:stroke;stroke:#0a0e14;stroke-width:2.5px">利沃夫</text>
              </g>
            </g>
          </svg>
          <div class="cap">手绘示意图 · 红=俄控 蓝=乌控 橙虚线=前线 黄箭头=近期推进（示意边界，非精确）</div>
        </div>
        <div class="ua-side" id="uaSide"></div>
      </div>
    </section>

    <!-- A股 -->
    <section class="card s8" style="--ac:var(--a)">
      <div class="head"><span class="bar"></span><h2>A股市场</h2><span class="tag" id="ashareTag">--</span></div>
      <div id="ashareBody"></div>
    </section>

    <!-- 日本新番 -->
    <section class="card s4" style="--ac:var(--anime)">
      <div class="head"><span class="bar"></span><h2>今日日本新番</h2><span class="tag" id="animeTag">--</span></div>
      <div id="animeBody"></div>
    </section>

    <!-- 小岛秀夫 -->
    <section class="card s4" style="--ac:var(--kj)">
      <div class="head"><span class="bar"></span><h2>小岛秀夫</h2><span class="tag">HIDEO KOJIMA</span></div>
      <div id="kojimaBody"></div>
    </section>

    <!-- AI -->
    <section class="card s8" style="--ac:var(--ai)">
      <div class="head"><span class="bar"></span><h2>AI 前沿 · 大模型 / 视频模型</h2><span class="tag">LLM × Video</span></div>
      <div class="ai-wrap" id="aiBody"></div>
    </section>

    <!-- TWICE -->
    <section class="card s4" style="--ac:var(--tw)">
      <div class="head"><span class="bar"></span><h2>TWICE</h2><span class="tag">SOCIAL × NEWS</span></div>
      <div id="twiceBody"></div>
    </section>

    <!-- 硬件外设 -->
    <section class="card s8" style="--ac:var(--hw)">
      <div class="head"><span class="bar"></span><h2>电脑硬件 · 外设</h2><span class="tag">新闻聚合</span></div>
      <div id="hwBody"></div>
    </section>

    <!-- 日本动画产业 -->
    <section class="card s4" style="--ac:var(--id)">
      <div class="head"><span class="bar"></span><h2>日本动画产业</h2><span class="tag">INDUSTRY</span></div>
      <div id="animeIndBody"></div>
    </section>

    <!-- 使用说明 -->
    <section class="card s12" style="--ac:var(--info)">
      <div class="head"><span class="bar"></span><h2>更新机制 · 数据源</h2><span class="tag">INFO</span></div>
      <div class="info-body" id="infoBody"></div>
    </section>
  </div>

  <footer>
    数据来源: 腾讯行情 · 新浪 · Bangumi · ISW · IT之家 · 机核 · HuggingFace &nbsp;|&nbsp;
    手动更新: <code>python C:/Users/27170/InfoDashboard/update.py</code> &nbsp;|&nbsp;
    页面每 30 分钟自动刷新
  </footer>
</div>

<script>
window.__DATA__ = __DATA_JSON__;
(function(){
const D = window.__DATA__ || {};
const $ = id => document.getElementById(id);
const esc = s => String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const pct = v => { const x=parseFloat(v); return isNaN(x)?"--":(x>0?"+":"")+x.toFixed(2)+"%"; };
const pctCls = v => { const x=parseFloat(v); return x>0?"up":(x<0?"down":"flat"); };
const newsHTML = (arr, max) => {
  if(!arr || !arr.length) return '<div class="empty">暂无新闻</div>';
  return '<div class="news">'+arr.slice(0,max||10).map(n => {
    const meta = [n.time, n.src].filter(Boolean).join(' · ');
    return '<a href="'+esc(n.link)+'" target="_blank" rel="noopener">'+
      '<span class="t">'+esc(n.title)+'</span>'+
      (meta?'<span class="time">'+esc(meta)+'</span>':'')+
    '</a>';
  }).join('')+'</div>';
};
const tableHTML = (arr, cols) => {
  if(!arr || !arr.length) return '<div class="empty">暂无数据</div>';
  return '<table class="tbl">'+arr.map(r =>
    '<tr>'+cols.map(c => c==='name' ? '<td class="n">'+esc(r[c])+'</td>'
      : c==='note'||c==='d' ? '<td class="d">'+esc(r[c])+'</td>'
      : '<td class="v">'+esc(r[c])+'</td>').join('')+'</tr>').join('')+'</table>';
};

/* 头部 */
$("genAt").textContent = D.generated_at || "--";
function tick(){ const d=new Date();
  $("clock").textContent = d.toLocaleString("zh-CN",{hour12:false}); }
tick(); setInterval(tick,1000);
setTimeout(()=>location.reload(), 30*60*1000);

/* A股(指数/涨跌榜/建议关注/市场新闻) */
(function(){
  const A = D.ashare || {};
  const st = A.status || {};
  $("ashareTag").textContent = st.label || "--";
  let idx = '<div class="idx">';
  (A.indices||[]).forEach(i=>{
    idx += '<div class="cell"><div class="nm"><span>'+esc(i.name)+'</span><span class="'+pctCls(i.pct)+'">'+pct(i.pct)+'</span></div>'+
      '<div class="px '+pctCls(i.pct)+'">'+esc(i.price)+'</div>'+
      '<div class="pct '+pctCls(i.pct)+'">'+esc(i.amount||"--")+'</div></div>';
  });
  idx += '</div>';
  let minis = '<div class="mini">';
  minis += '<div><h3>涨幅榜</h3><table>'+((A.gainers||[]).slice(0,6).map(g =>
    '<tr><td><span class="nmc">'+esc(g.name)+'<em>'+esc(g.code)+'</em></span></td>'+
    '<td class="up">'+pct(g.pct)+'</td></tr>').join(''))+'</table></div>';
  minis += '<div><h3>跌幅榜</h3><table>'+((A.losers||[]).slice(0,6).map(g =>
    '<tr><td><span class="nmc">'+esc(g.name)+'<em>'+esc(g.code)+'</em></span></td>'+
    '<td class="down">'+pct(g.pct)+'</td></tr>').join(''))+'</table></div>';
  minis += '</div>';
  const watch = A.watch || [];
  let wtable = '<div><h3 class="sec">建议关注 · 综合分析</h3>'+
    (watch.length ? '<table class="tbl watch">'+watch.map(w =>
      '<tr><td class="n">'+esc(w.name)+'<em>'+esc(w.theme)+' · '+esc(w.code)+'</em></td>'+
      '<td class="pxc '+pctCls(w.pct)+'">'+(w.price!=null?esc(w.price):"--")+'<br>'+pct(w.pct)+'</td>'+
      '<td class="d">'+esc(w.reason)+'</td></tr>').join('')+'</table>' : '<div class="empty">暂无数据</div>')+'</div>';
  let glb = '<div class="glb">'+(D.global||[]).map(g =>
    '<span>'+esc(g.name)+'<b class="'+pctCls(g.pct)+'">'+esc(g.price)+'</b><b class="'+pctCls(g.pct)+'">'+pct(g.pct)+'</b></span>'
  ).join('')+'</div>';
  const news = '<h3 class="sec">市场新闻 · 新闻集合</h3><div class="news-card" style="max-height:220px">'+newsHTML(A.news,8)+'</div>';
  $("ashareBody").innerHTML = (A.indices||[]).length ? idx + '<div class="au2">'+minis+wtable+'</div>' + glb + news
    : '<div class="empty">行情获取失败, 等待下次自动更新</div>'+(glb||"");
})();

/* 新番 */
(function(){
  const A = D.anime || {};
  $("animeTag").textContent = (A.date||"--")+" · 周"+esc(A.weekday||"?");
  const items = A.items || [];
  if(!items.length){ $("animeBody").innerHTML = '<div class="empty">今日暂无放送数据</div>'; return; }
  $("animeBody").innerHTML = '<div class="anime">'+items.map(it => {
    const img = it.img ? '<img src="'+esc(it.img)+'" loading="lazy" onerror="this.style.visibility=\'hidden\'">' : '<img style="visibility:hidden">';
    return '<a class="it" href="https://bgm.tv/subject/'+esc(it.id)+'" target="_blank" rel="noopener">'+
      img+'<div class="info"><div class="cn">'+esc(it.name_cn||it.name)+'</div>'+
      '<div class="jp">'+(it.name_cn?esc(it.name):"")+'</div></div>'+
      '<span class="sc">'+(it.score!=null?esc(it.score.toFixed?it.score.toFixed(2):it.score):"")+'</span></a>';
  }).join('')+'</div>';
})();

/* 俄乌 */
(function(){
  const U = D.ukraine || {};
  $("uaTag").textContent = "前线动态";
  const legend = '<div class="legend">'+
    '<span><i style="background:#7b2d3b"></i>俄军控制区</span>'+
    '<span><i style="background:#c05a5a"></i>俄方声称控制</span>'+
    '<span><i style="background:#2e6e8e"></i>乌军控制区</span>'+
    '<span><i style="background:#6fb1c9"></i>乌方声称控制</span>'+
    '<span><i style="background:#e0a33a"></i>近期推进方向</span>'+
    '</div>';
  const links = '<div class="links">'+((U.links||[]).map(l=>
    '<a href="'+esc(l.url)+'" target="_blank" rel="noopener">'+esc(l.name)+'</a>').join(''))+'</div>';
  const news = '<h3 class="sec">战线新闻 · 新闻集合</h3><div class="news-card" style="max-height:180px">'+newsHTML(U.news,6)+'</div>';
  $("uaSide").innerHTML =
    '<h3>'+esc(U.title||"ISW 战况评估")+'</h3>'+
    '<div class="sum">'+(U.summary?esc(U.summary):'<span class="dim">摘要暂不可用</span>')+'</div>'+
    legend + links + news;
})();

/* AI */
(function(){
  const A = D.ai || {};
  const newsCol = '<div class="col"><h3>AI 快讯 · 中文</h3>'+newsHTML(A.news_zh,8)+'</div>';
  const llmCol = '<div class="col"><h3>大模型速览 (2026-08)</h3>'+tableHTML(A.models,['name','vendor','note'])+'</div>';
  let videoCol = '<div class="col"><h3>视频生成模型</h3>'+tableHTML(A.video_models,['name','vendor','note']);
  if((A.hf||[]).length){
    videoCol += '<h3 style="margin-top:10px">HuggingFace 趋势模型</h3><div class="hf">'+
      A.hf.slice(0,5).map(m=>'<div class="m"><b>'+esc(m.id)+'</b><span class="mut">♥ '+esc(m.likes)+'</span></div>').join('')+'</div>';
  }
  videoCol += '</div>';
  $("aiBody").innerHTML = newsCol + llmCol + videoCol;
})();

/* 硬件(新闻聚合) */
(function(){
  let h = '<h3 class="sec">硬件快讯 · 新闻集合</h3><div class="news-card" style="max-height:400px">'+newsHTML(D.hw_news,12)+'</div>';
  const H = D.hardware || [];
  if(H.length){
    h += '<h3 class="sec">近期旗舰速览</h3><div class="chips">'+H.map(x=>
      '<a href="'+esc(x.link||"#")+'" target="_blank" rel="noopener"><b style="color:var(--hw)">'+esc(x.cat)+'</b> · '+esc(x.name)+'</a>'
    ).join('')+'</div>';
  }
  $("hwBody").innerHTML = h;
})();

/* 小岛秀夫 */
(function(){
  const K = D.kojima || {}; const P = K.profile || {};
  let h = '<div class="profile"><b>'+esc(P.name||"小岛秀夫")+'</b> · '+esc(P.birth||"")+'<br>'+esc(P.role||"")+'</div>';
  if((P.icon_works||[]).length){
    h += '<div class="chips">'+(P.icon_works||[]).map(w=>'<span>'+esc(w)+'</span>').join('')+'</div>';
  }
  if((P.links||[]).length){
    h += '<div class="links" style="margin:8px 0 10px">'+(P.links||[]).map(l=>'<a href="'+esc(l.url)+'" target="_blank" rel="noopener">'+esc(l.name)+'</a>').join('')+'</div>';
  }
  h += '<div class="news-card">'+newsHTML(K.news,7)+'</div>';
  $("kojimaBody").innerHTML = h;
})();

/* TWICE(官方/成员社媒 + 新闻) */
(function(){
  const T = D.twice || {}; const P = T.profile || {};
  let h = '<div class="profile"><b>'+esc(P.name||"TWICE")+'</b> · '+esc(P.birth||"")+'<br>'+esc(P.members||"")+'</div>';
  if((P.socials||[]).length){
    h += '<h3 class="sec">官方社媒</h3><div class="soc">'+(P.socials||[]).map(s=>
      '<a href="'+esc(s.url)+'" target="_blank" rel="noopener"><i>'+esc(s.platform)+'</i><b>'+esc(s.handle)+'</b></a>').join('')+'</div>';
  }
  if((P.members_socials||[]).length){
    h += '<h3 class="sec">9 位成员 · Instagram</h3><div class="soc mem">'+(P.members_socials||[]).map(m=>
      '<a href="'+esc(m.url)+'" target="_blank" rel="noopener"><i>'+esc(m.name)+'</i><b>'+esc(m.ig)+'</b></a>').join('')+'</div>';
  }
  if((P.albums||[]).length){ h += '<div class="chips">'+(P.albums||[]).map(a=>'<span>'+esc(a)+'</span>').join('')+'</div>'; }
  if(P.tour){ h += '<div class="profile" style="margin-top:6px">巡演: '+esc(P.tour)+'</div>'; }
  h += '<h3 class="sec">最新动态</h3><div class="news-card">'+newsHTML(T.news,6)+'</div>';
  $("twiceBody").innerHTML = h;
})();

/* 日本动画产业(新闻集合) */
(function(){
  const A = D.anime_industry || {};
  $("animeIndBody").innerHTML = '<h3 class="sec">产业新闻 · 新闻集合</h3><div class="news-card" style="max-height:460px">'+newsHTML(A.news,10)+'</div>';
})();

/* 说明 */
(function(){
  const srcs = ["腾讯行情 (A股/全球指数)","新浪行情 (涨跌榜)","Bangumi (今日新番)",
    "ISW (俄乌评估+地图)","新浪/IT之家/机核 (新闻)","HuggingFace (趋势模型)"];
  $("infoBody").innerHTML =
    '<b>定时更新:</b> 每 <b>2 小时</b> 自动抓取并重建本页（云端 GitHub Actions）<br>'+
    '<b>自动刷新:</b> 页面每 30 分钟重载, 打开着即可看到新数据<br>'+
    '<b>手动更新:</b> 运行 <code>python update.py</code> 后刷新页面<br>'+
    '<b>数据源:</b><div class="src">'+srcs.map(s=>'<span>'+s+'</span>').join('')+'</div>';
})();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 构建
def build(data):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TPL.replace("__DATA_JSON__", payload)
    with open(os.path.join(BASE, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(BASE, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    print("== 信息驾驶舱数据采集开始 ==")
    data = collect()
    build(data)
    print("== 构建完成 ==")
    print("生成时间:", data["generated_at"])
    print("A股指数:", len(data["ashare"]["indices"]), "| 全球指数:", len(data["global"]))
    print("今日新番:", len(data["anime"]["items"]), "| ISW战况:", "有" if data["ukraine"].get("summary") else "无",
          "| 战线新闻:", len(data["ukraine"].get("news", [])))
    print("AI新闻:", len(data["ai"]["news_zh"]), "| HF趋势:", len(data["ai"]["hf"]),
          "| 硬件快讯:", len(data["hw_news"]))
    print("小岛新闻:", len(data["kojima"]["news"]), "| TWICE新闻:", len(data["twice"]["news"]),
          "| 动画产业新闻:", len(data["anime_industry"]["news"]))
    if WARNINGS:
        print("-- 警告 --")
        for w in WARNINGS:
            print(" ", w)
    print("输出: dashboard.html / data.json @", BASE)


if __name__ == "__main__":
    main()
