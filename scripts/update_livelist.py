#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Livelist auto updater.

Fetch Chinese-language IPTV playlists from several public sources, validate
stream URLs concurrently, then write CCTV.m3u / CNTV.m3u / IPTV.m3u and a
STATUS.md report. Uses only the Python standard library.

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
CNTV_RE = re.compile(r"(卫视|卫星|tvb|翡翠|本港|有线|now\s*宽频|华视|民视|三立|中天|东森)", re.I)

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
NOISE_RE = re.compile(r"[\s\-_（）()【】\[\]·.、,，]+")
SUFFIX_RE = re.compile(r"(高清|超清|蓝光|频道|直播|hd|uhd|fhd|4k|1080p|720p)$", re.I)


class Channel(object):
    __slots__ = ("name", "tvg_id", "tvg_logo", "group", "urls", "source")

    def __init__(self, name, tvg_id, tvg_logo, group, url, source):
        self.name = name
        self.tvg_id = tvg_id
        self.tvg_logo = tvg_logo
        self.group = group
        self.urls = [url]
        self.source = source

    def key(self):
        k = NOISE_RE.sub("", self.name).upper()
        return SUFFIX_RE.sub("", k) or k.upper()


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


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
        result.append(Channel(
            name=name,
            tvg_id=attrs.get("tvg-id", ""),
            tvg_logo=attrs.get("tvg-logo", ""),
            group=group,
            url=url,
            source=source,
        ))
    return result


def merge(channels):
    """Deduplicate by normalized name, keeping up to 6 URLs per channel."""
    merged = {}
    for ch in channels:
        k = ch.key()
        if not k:
            continue
        if k not in merged:
            merged[k] = ch
            continue
        existing = merged[k]
        if len(existing.urls) < 6 and ch.urls[0] not in existing.urls:
            existing.urls.append(ch.urls[0])
        if not existing.tvg_logo and ch.tvg_logo:
            existing.tvg_logo = ch.tvg_logo
        if existing.group == "其他" and ch.group != "其他":
            existing.group = ch.group
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
        hits = sorted(good.get(idx, []))
        if not hits:
            continue
        ch.urls = [u for _, u in hits[:4]]
        kept.append(ch)
    log("验活通过: %d/%d 个频道" % (len(kept), len(channels)))
    return kept


def extinf(ch, url):
    return ('#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s\n%s'
            % (ch.tvg_id, ch.name, ch.tvg_logo, ch.group, ch.name, url))


def write_m3u(path, channels):
    lines = ["#EXTM3U"]
    for ch in channels:
        for url in ch.urls:
            lines.append(extinf(ch, url))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    log("写入 %s (%d 个频道)" % (os.path.basename(path), len(channels)))


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
        "- CCTV: %d / 卫视: %d" % (stats["cctv"], stats["cntv"]),
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
    ap.add_argument("--max-urls", type=int, default=4)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--timeout", type=int, default=6)
    ap.add_argument("--max-checks", type=int, default=4000)
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
    merged = merged[:args.max_channels]

    if args.skip_validate:
        kept = merged
        log("已跳过验活 (--skip-validate)")
    else:
        kept = validate(merged, args.workers, args.timeout, args.max_checks)
        for ch in kept:
            ch.urls = ch.urls[:args.max_urls]

    cctv = [c for c in kept if CCTV_RE.search(c.name) or CCTV_RE.search(c.group)]
    cntv = [c for c in kept if CNTV_RE.search(c.name) or CNTV_RE.search(c.group)]
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
    }, source_stats)
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
