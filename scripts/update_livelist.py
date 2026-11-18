#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Livelist auto updater.

Fetch Chinese-language IPTV playlists from several public sources, keep only
CCTV / satellite (卫视) / selected Hong Kong channels (凤凰卫视、凤凰资讯、
星空卫视), plus Taiwan-region channels that pass a reliability gate. Stream
URLs are validated concurrently with IPv6 preference, then CCTV.m3u /
CNTV.m3u / IPTV.m3u and a STATUS.md report are written. Standard library only.

Usage:
    python scripts/update_livelist.py
    python scripts/update_livelist.py --source-file sample1.m3u --skip-validate
"""

import argparse
import concurrent.futures
import datetime
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Livelist-AutoUpdate/1.0"

# Public upstream playlists. iptv-org publishes per-country files under streams/.
SOURCES = [
    ("iptv-org CN", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u"),
    ("iptv-org HK(China)", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/hk.m3u"),
    ("iptv-org TW(China)", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/tw.m3u"),
    ("iptv-org MO(China)", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/mo.m3u"),
    ("fanmingming IPv6", "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u"),
    ("Guovin iptv-api", "https://guovin.github.io/iptv-api/result.m3u"),
]
EPG_URL = "https://epg.112114.xyz/pp.xml"

# Names/groups that must never be mixed into the general lists.
ADULT_RE = re.compile(r"(成人|十八|18\+|xxx|adult|porn|色情|成人视频|日本成人)", re.I)
META_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s", re.I)
CCTV_RE = re.compile(r"(cctv|央视|中央|cntv)", re.I)
# 仅保留大陆卫视 + 指定的中国香港频道，不再纳入其他港台频道
CNTV_RE = re.compile(
    r"(卫视|卫星|凤凰卫视|凤凰资讯|星空卫视|phoenix\s*(?:tv|info)|\bifeng\b|star\s*chinese)",
    re.I,
)
# 台湾地区频道先进入验活，只有满足下列稳定性门槛才写入列表。
TW_RE = re.compile(
    r"(台湾|台視|台视|中視|中视|華視|华视|民視|民视|三立|中天|東森|东森|"
    r"TVBS|公視|公视|年代|壹电视|鏡电视|镜电视|MOMO|非凡|寰宇|"
    r"\bTTV\b|\bCTV\b|\bPTS\b|\bFTV\b|\bEBC\b|\bERA\b|\bGTV\b|\bSETN\b|\bMOMOTV\b)",
    re.I,
)
TW_MIN_URLS = 1
TW_MAX_LATENCY_MS = 1500
# 运营商资源：移动 / 联通，选择接口时尽量两者都保留。
ISP_MOBILE_RE = re.compile(r"(移动|cmcc|china\s*mobile)", re.I)
ISP_UNICOM_RE = re.compile(r"(联通|cucc|china\s*unicom)", re.I)
OTHER_GROUP_RE = re.compile(r"^(其他|其它|未分类|other|uncategorized)?$", re.I)
GOODTV_RE = re.compile(r"(good\s*tv|好电视)", re.I)
DISPLAY_GROUPS = ("央视", "卫视", "港澳台", "央视付费")
GROUP_ORDER = dict((name, i) for i, name in enumerate(DISPLAY_GROUPS))
MAX_OUTPUT_URLS = 10

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
NOISE_RE = re.compile(r"[\s\-_（）()【】\[\]·.、,，]+")
SUFFIX_RE = re.compile(r"(高清|超清|蓝光|频道|直播|hd|uhd|fhd|4k|1080p|720p)$", re.I)


class Channel(object):
    __slots__ = ("name", "tvg_id", "tvg_logo", "group", "urls", "source",
                 "region", "fastest_ms", "url_isps", "display_group")

    def __init__(self, name, tvg_id, tvg_logo, group, url, source, region="",
                 fastest_ms=None, isp="", display_group=""):
        self.name = name
        self.tvg_id = tvg_id
        self.tvg_logo = tvg_logo
        self.group = group
        self.urls = [url]
        self.source = source
        self.region = region
        self.fastest_ms = fastest_ms
        self.url_isps = [isp]
        self.display_group = display_group

    def key(self):
        k = NOISE_RE.sub("", self.name).upper()
        return SUFFIX_RE.sub("", k) or k.upper()


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def is_allowed(ch):
    """CCTV, mainland satellite, three named HK channels, or TW candidates."""
    if ch.region == "TW":
        return True
    text = " ".join((ch.name, ch.group, ch.source) + tuple(ch.urls))
    if ISP_MOBILE_RE.search(text) or ISP_UNICOM_RE.search(text):
        return True
    for pattern in (CCTV_RE, CNTV_RE):
        if pattern.search(ch.name) or pattern.search(ch.group):
            return True
    return False


def is_tw_source(source):
    upper = source.upper()
    return "TW" in upper or "TAIWAN" in upper or "台湾" in source


def detect_isp(*parts):
    """Return 移动 / 联通 / empty by scanning names, groups, sources and URLs."""
    text = " ".join(p or "" for p in parts)
    if ISP_MOBILE_RE.search(text):
        return "移动"
    if ISP_UNICOM_RE.search(text):
        return "联通"
    return ""


def is_other_group(ch):
    """True for the catch-all '其他' / empty group that should be dropped."""
    return bool(OTHER_GROUP_RE.match((ch.group or "").strip()))


def fix_uncategorized(ch):
    """Promote TW / ISP channels out of '其他' before the category filter."""
    if not is_other_group(ch):
        return ""
    if ch.region == "TW":
        ch.group = "台湾地区"
        return "tw"
    text = " ".join((ch.name, ch.source) + tuple(ch.urls))
    if ISP_MOBILE_RE.search(text) or ISP_UNICOM_RE.search(text):
        ch.group = "运营商"
        return "isp"
    return ""


def classify_display_group(ch):
    """Map a channel into the four display groups requested by the user."""
    text = " ".join((ch.name, ch.group))
    if (u"付费" in text or "pay" in text.lower()) and CCTV_RE.search(text):
        return u"央视付费"
    if CCTV_RE.search(text):
        return u"央视"
    lower = text.lower()
    if (ch.region == "TW" or u"港澳台" in ch.group or u"凤凰" in text
            or u"星空" in text or u"翡翠" in text or u"本港" in text
            or "tvb" in lower):
        return u"港澳台"
    if CNTV_RE.search(text):
        return u"卫视"
    return ""


def is_ipv6(url):
    """Return True when the stream URL points to an IPv6 literal host."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return ":" in host


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_m3u(text, source):
    """Return list of Channel parsed from an M3U document."""
    lines = [l.strip() for l in text.splitlines()]
    result = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        url = ""
        for j in range(i + 1, min(i + 6, len(lines))):
            nxt = lines[j]
            if not nxt:
                continue
            if nxt.startswith("#"):
                break
            url = nxt
            break
        if not url or not url.startswith(("http://", "https://")):
            continue
        attrs = dict(ATTR_RE.findall(line))
        name = line.split(",", 1)[1].strip() if "," in line else attrs.get("tvg-name", "").strip()
        name = name or attrs.get("tvg-name", "").strip()
        name = re.sub(r"\[[^\]]*Geo-blocked[^\]]*\]", "", name, flags=re.I).strip()
        group = attrs.get("group-title", "其他")
        if not name or META_RE.match(name) or "更新时间" in group:
            continue
        if ADULT_RE.search(name) or ADULT_RE.search(group):
            continue
        if GOODTV_RE.search(name) or GOODTV_RE.search(group) or GOODTV_RE.search(url):
            continue
        region = "TW" if (is_tw_source(source) or TW_RE.search(name) or TW_RE.search(group)) else ""
        isp = detect_isp(name, group, source, url)
        result.append(Channel(
            name=name,
            tvg_id=attrs.get("tvg-id", ""),
            tvg_logo=attrs.get("tvg-logo", ""),
            group=group,
            url=url,
            source=source,
            region=region,
            isp=isp,
        ))
    return result


def merge(channels):
    """Deduplicate by normalized name, keeping up to 12 URLs per channel."""
    merged = {}
    for ch in channels:
        k = ch.key()
        if not k:
            continue
        if k not in merged:
            merged[k] = ch
            continue
        existing = merged[k]
        if len(existing.urls) < 12 and ch.urls[0] not in existing.urls:
            existing.urls.append(ch.urls[0])
            existing.url_isps.append(ch.url_isps[0] if ch.url_isps else "")
        if not existing.tvg_logo and ch.tvg_logo:
            existing.tvg_logo = ch.tvg_logo
        if existing.group == "其他" and ch.group != "其他":
            existing.group = ch.group
        if ch.region and not existing.region:
            existing.region = ch.region
    return list(merged.values())


def probe(url, timeout):
    """Return (url, latency_ms) when the stream responds with media data."""
    start = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Range": "bytes=0-2047",
        "Accept": "*/*",
        "Connection": "close",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            ctype = resp.headers.get("Content-Type", "")
            head = resp.read(512)
        if status not in (200, 206):
            return None
        if not head:
            return None
        if head[:7] == b"#EXTM3U" and "mpegurl" not in ctype.lower():
            return None
        if "text/html" in ctype.lower() and head[:5].lower() != b"<?xml":
            return None
        return url, int((time.time() - start) * 1000)
    except Exception:
        return None


def validate(channels, workers, timeout, max_checks):
    tasks = []
    for idx, ch in enumerate(channels):
        for url in ch.urls:
            tasks.append((idx, url))
            if len(tasks) >= max_checks:
                break
        if len(tasks) >= max_checks:
            break
    log("开始验活: %d 个接口, 并发 %d, 超时 %ds" % (len(tasks), workers, timeout))
    good = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, url, timeout): (idx, url) for idx, url in tasks}
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if done % 200 == 0:
                log("  已检测 %d/%d" % (done, len(tasks)))
            res = fut.result()
            if not res:
                continue
            url, ms = res
            idx, _ = futures[fut]
            good.setdefault(idx, []).append((ms, url))
    kept = []
    for idx, ch in enumerate(channels):
        # IPv6 接口优先；同等协议下按响应延迟排序
        hits = sorted(good.get(idx, []), key=lambda item: (0 if is_ipv6(item[1]) else 1, item[0]))
        if not hits:
            continue
        isp_for = dict(zip(ch.urls, ch.url_isps))
        # 移动/联通都可用时各保留一条，其余按优先级补齐
        chosen = []
        for isp in ("移动", "联通"):
            for _, url in hits:
                if isp_for.get(url) == isp and url not in chosen:
                    chosen.append(url)
                    break
        for _, url in hits:
            if len(chosen) >= MAX_OUTPUT_URLS:
                break
            if url not in chosen:
                chosen.append(url)
        ch.urls = chosen[:MAX_OUTPUT_URLS]
        ch.url_isps = [isp_for.get(u, "") for u in ch.urls]
        ch.fastest_ms = min(ms for ms, _ in hits)
        kept.append(ch)
    ipv6_total = sum(1 for ch in kept for u in ch.urls if is_ipv6(u))
    mobile_total = sum(1 for ch in kept if any(i == "移动" for i in ch.url_isps))
    unicom_total = sum(1 for ch in kept if any(i == "联通" for i in ch.url_isps))
    both_total = sum(1 for ch in kept if any(i == "移动" for i in ch.url_isps)
                     and any(i == "联通" for i in ch.url_isps))
    log("验活通过: %d/%d 个频道 (IPv6 %d 条, 移动 %d / 联通 %d / 双网 %d)"
        % (len(kept), len(channels), ipv6_total, mobile_total, unicom_total, both_total))
    return kept


def extinf(ch):
    group = ch.display_group or ch.group
    return ('#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s'
            % (ch.tvg_id, ch.name, ch.tvg_logo, group, ch.name))


def write_m3u(path, channels):
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(extinf(ch))
        lines.extend(ch.urls[:MAX_OUTPUT_URLS])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    log("写入 %s (%d 个频道, %d 个接口)"
        % (os.path.basename(path), len(channels), sum(len(c.urls) for c in channels)))


def update_epg(out_dir, timeout=40):
    path = os.path.join(out_dir, "EPG.xml")
    try:
        raw = fetch(EPG_URL, timeout=timeout)
        if len(raw) < 1000 or "<" not in raw[:200]:
            raise ValueError("unexpected EPG payload")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
        log("EPG 更新成功 (%d KB)" % (len(raw) // 1024))
        return True
    except Exception as exc:
        log("EPG 更新失败, 保留旧文件: %s" % exc)
        return False


def write_status(out_dir, stats, sources):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    lines = [
        "# Livelist 自动更新状态",
        "",
        "- 最近更新: %s (北京时间)" % now.strftime("%Y-%m-%d %H:%M:%S"),
        "- 源数量: %d" % stats["sources"],
        "- 抓取频道: %d (去重后 %d)" % (stats["raw"], stats["merged"]),
        "- 验活通过: %d" % stats["valid"],
        "- CCTV: %d / 卫视(含港澳台): %d" % (stats["cctv"], stats["cntv"]),
        "- 分组: 央视 %d / 卫视 %d / 港澳台 %d / 央视付费 %d"
        % (stats["g_cctv"], stats["g_sat"], stats["g_hkmo"], stats["g_pay"]),
        "- 剔除 GoodTV: %d 个；未匹配四类分组: %d 个"
        % (stats["goodtv_dropped"], stats["group_dropped"]),
        "- 台湾地区（通过稳定性门槛）: %d 个，剔除 %d 个" % (stats["tw"], stats["tw_dropped"]),
        "- 运营商: 移动 %d / 联通 %d / 双网 %d 个频道" % (stats["mobile"], stats["unicom"], stats["both"]),
        "- 剔除“其他”类目: %d 个（台湾地区/运营商已归类保留）" % stats["other_dropped"],
        "- IPv6 接口: %d 条（同频道优先）" % stats["ipv6"],
        "- 筛选范围: CCTV + 卫视 + 凤凰卫视/凤凰资讯/星空卫视 + 稳定台湾地区频道 + 移动/联通资源",
        "",
        "## 数据源",
        "",
    ]
    for label, count, ok in sources:
        lines.append("- %s: %d 条%s" % (label, count, "" if ok else " (抓取失败)"))
    lines += [
        "",
        "## 订阅地址",
        "",
        "- `https://raw.githubusercontent.com/amygoTV/Livelist/main/IPTV.m3u`",
        "- `https://raw.githubusercontent.com/amygoTV/Livelist/main/CCTV.m3u`",
        "- `https://raw.githubusercontent.com/amygoTV/Livelist/main/CNTV.m3u`",
        "",
        "> 直播源随时可能失效，本仓库每 6 小时自动验活更新。仅供个人合法研究使用。",
        "",
    ]
    with open(os.path.join(out_dir, "STATUS.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Livelist auto updater")
    ap.add_argument("--output-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--source-file", action="append", default=[], help="Use local m3u file instead of remote sources (repeatable)")
    ap.add_argument("--skip-validate", action="store_true", help="Skip URL probing (fast dry run)")
    ap.add_argument("--max-channels", type=int, default=1500)
    ap.add_argument("--max-urls", type=int, default=MAX_OUTPUT_URLS)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--timeout", type=int, default=6)
    ap.add_argument("--max-checks", type=int, default=4000)
    ap.add_argument("--tw-min-urls", type=int, default=TW_MIN_URLS,
                    help="台湾地区频道至少需要的可用接口数 (default: 1)")
    ap.add_argument("--tw-max-latency", type=int, default=TW_MAX_LATENCY_MS,
                    help="台湾地区频道最快接口的最大延迟 ms (default: 1500)")
    ap.add_argument("--no-epg", action="store_true")
    args = ap.parse_args()

    out = os.path.abspath(args.output_dir)
    if not os.path.isdir(out):
        os.makedirs(out)

    all_channels = []
    source_stats = []
    if args.source_file:
        for path in args.source_file:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                items = parse_m3u(f.read(), os.path.basename(path))
            source_stats.append((os.path.basename(path), len(items), True))
            all_channels.extend(items)
    else:
        for label, url in SOURCES:
            try:
                items = parse_m3u(fetch(url), label)
                source_stats.append((label, len(items), True))
                log("%s: %d 条" % (label, len(items)))
                all_channels.extend(items)
            except Exception as exc:
                source_stats.append((label, 0, False))
                log("抓取失败 %s: %s" % (label, exc))

    merged = merge(all_channels)
    log("去重后 %d 个频道" % len(merged))
    merged_total = len(merged)
    before_goodtv = len(merged)
    merged = [ch for ch in merged
              if not (GOODTV_RE.search(ch.name) or GOODTV_RE.search(ch.group)
                      or any(GOODTV_RE.search(u) for u in ch.urls))]
    goodtv_dropped = before_goodtv - len(merged)
    if goodtv_dropped:
        log("剔除 GoodTV 频道 %d 个" % goodtv_dropped)
    tw_regrouped = 0
    isp_regrouped = 0
    for ch in merged:
        kind = fix_uncategorized(ch)
        if kind == "tw":
            tw_regrouped += 1
        elif kind == "isp":
            isp_regrouped += 1
    before_other = len(merged)
    merged = [ch for ch in merged if not is_other_group(ch)]
    other_dropped = before_other - len(merged)
    if other_dropped:
        log("剔除“其他”类目 %d 个（台湾地区 %d / 运营商 %d 已归类保留）"
            % (other_dropped, tw_regrouped, isp_regrouped))
    merged = [ch for ch in merged if is_allowed(ch)]
    log("按 CCTV/卫视/指定香港频道/台湾地区候选过滤后 %d 个频道" % len(merged))
    merged = merged[:args.max_channels]

    tw_dropped = 0
    if args.skip_validate:
        tw_candidates = [ch for ch in merged if ch.region == "TW"]
        if tw_candidates:
            log("已跳过验活，%d 个台湾地区频道因无法判定稳定性不纳入" % len(tw_candidates))
            tw_dropped = len(tw_candidates)
        kept = [ch for ch in merged if ch.region != "TW"]
        log("已跳过验活 (--skip-validate)")
    else:
        kept = validate(merged, args.workers, args.timeout, args.max_checks)
        for ch in kept:
            ch.urls = ch.urls[:args.max_urls]
            ch.url_isps = ch.url_isps[:args.max_urls]
        tw_all = [ch for ch in kept if ch.region == "TW"]
        tw_ok_ids = set()
        for ch in tw_all:
            good = (len(ch.urls) >= args.tw_min_urls
                    and ch.fastest_ms is not None
                    and ch.fastest_ms <= args.tw_max_latency)
            if good:
                tw_ok_ids.add(id(ch))
        tw_dropped = len(tw_all) - len(tw_ok_ids)
        if tw_dropped:
            log("台湾地区频道因稳定性不足剔除 %d 个（要求 ≥%d 条可用接口且最快延迟 ≤%dms）"
                % (tw_dropped, args.tw_min_urls, args.tw_max_latency))
        if tw_all:
            kept = [ch for ch in kept if ch.region != "TW" or id(ch) in tw_ok_ids]

    for ch in kept:
        ch.display_group = classify_display_group(ch)
    before_group = len(kept)
    kept = [ch for ch in kept if ch.display_group]
    group_dropped = before_group - len(kept)
    if group_dropped:
        log("按央视/卫视/港澳台/央视付费四类分组剔除 %d 个未匹配频道" % group_dropped)
    kept.sort(key=lambda c: GROUP_ORDER.get(c.display_group, 99))

    cctv = [c for c in kept if c.display_group in ("央视", "央视付费")]
    cntv = [c for c in kept if c.display_group in ("卫视", "港澳台")]
    write_m3u(os.path.join(out, "IPTV.m3u"), kept)
    write_m3u(os.path.join(out, "CCTV.m3u"), cctv)
    write_m3u(os.path.join(out, "CNTV.m3u"), cntv)

    if not args.no_epg:
        update_epg(out)

    write_status(out, {
        "sources": len(source_stats),
        "raw": len(all_channels),
        "merged": merged_total,
        "valid": len(kept),
        "cctv": len(cctv),
        "cntv": len(cntv),
        "tw": sum(1 for ch in kept if ch.region == "TW"),
        "tw_dropped": tw_dropped,
        "other_dropped": other_dropped,
        "goodtv_dropped": goodtv_dropped,
        "group_dropped": group_dropped,
        "g_cctv": sum(1 for ch in kept if ch.display_group == "央视"),
        "g_sat": sum(1 for ch in kept if ch.display_group == "卫视"),
        "g_hkmo": sum(1 for ch in kept if ch.display_group == "港澳台"),
        "g_pay": sum(1 for ch in kept if ch.display_group == "央视付费"),
        "mobile": sum(1 for ch in kept if any(i == "移动" for i in ch.url_isps)),
        "unicom": sum(1 for ch in kept if any(i == "联通" for i in ch.url_isps)),
        "both": sum(1 for ch in kept if any(i == "移动" for i in ch.url_isps)
                     and any(i == "联通" for i in ch.url_isps)),
        "ipv6": sum(1 for ch in kept for u in ch.urls if is_ipv6(u)),
    }, source_stats)
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
