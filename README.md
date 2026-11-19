# Livelist

中文 IPTV 直播源聚合仓库。由 GitHub Actions 每 6 小时自动抓取公开源、并发验活测速，生成可直接导入播放器的 m3u 列表。

当前状态见 [STATUS.md](STATUS.md)。

## 订阅地址

| 列表 | 说明 | 地址 |
|---|---|---|
| IPTV.m3u | 央视 / 卫视 / 港澳台 / 央视付费，单频道最多 10 源（合并写法） | `https://raw.githubusercontent.com/amygoTV/Livelist/main/IPTV.m3u` |
| CCTV.m3u | 央视频道 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/CCTV.m3u` |
| CNTV.m3u | 卫视频道 + 凤凰卫视/凤凰资讯/星空卫视 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/CNTV.m3u` |
| TVLives.txt | TVBox 文本订阅格式，每条源独立线路，含国内运营商源 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/TVLives.txt` |
| EPG.xml | 节目单 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/EPG.xml` |

> **选哪个？** 如果你的播放器只认传统「一条线路一个地址」，用 `TVLives.txt`（TVBox / 影视仓等广泛支持，每条源都是独立线路，且包含国内 IPv6 源）。`IPTV.m3u` 是「单频道一条 EXTINF 挂多条源」的合并写法，只有支持多线路的播放器才能切换源。

播放器（VLC / PotPlayer / MPV / Tivimate / TVBox 等）直接复制订阅地址即可。

## 工作原理

1. **抓取**：从以下公开源拉取 m3u
   - `iptv-org/iptv`：`streams/cn.m3u`、`hk.m3u`、`tw.m3u`、`mo.m3u`（分别对应中国内地、中国香港、中国台湾地区、中国澳门）
   - `fanmingming/live`：`ipv6.m3u`（IPv6）、`itv.m3u`（电信）、`index.m3u`（联通）三份列表
   - `Guovin/iptv-api`：其 GitHub Pages 发布的测速结果 `result.m3u`
   - `amygoTV/MyTV`：`TVLives.txt`（TVBox 文本订阅格式，国内运营商 IPv6 源为主，作为 TXT 产物的主要来源）
2. **清洗**：仅保留 CCTV、卫视、凤凰卫视 / 凤凰资讯 / 星空卫视、台湾地区候选频道，以及移动 / 联通资源；剔除 GoodTV、成人频道、时间戳元数据及 `group-title="其他"` 的频道（台湾地区、港澳台、运营商频道会先归入对应类目再保留）。星空卫视兼容繁体「星空衛視」与 URL 中的 `STARTV` 标识，显示名统一为简体。错台源（如芒果TV `voc.com.cn` 冒充 CCTV-1）在解析阶段按域名黑名单剔除。频道名去掉 HEVC / 50FPS / 1080p / 中文频道后缀等画质修饰后归一化去重，同一电视台（如 CCTV 的多个画质变体）只显示一次，每个频道最多保留 24 个候选接口。
3. **验活 + 备用补齐**：32 并发对接口发起带 Range 的请求；同频道**优先 IPv6 接口**，再按响应延迟排序，移动/联通接口各先保留一条，每频道最多 10 条源（可用 `--max-urls` 调整）。验活通过后若不足 10 条，会用验活失败的候选源补齐作备用（多为电信/移动内网，境外 Actions 节点不可达，但用户本地网络可能可用），因此每个频道源数更充足。台湾地区频道仍需验活通过且最快延迟 ≤1500ms，不使用备用补齐。
4. **产出**：按 **央视 → 卫视 → 港澳台 → 央视付费** 排序，央视/央视付费内部按频道号排序（CCTV-1→17，5+ 紧跟 5，CGTN/CETV 置后）。生成两类列表：
   - `IPTV.m3u` / `CCTV.m3u` / `CNTV.m3u`：同一电视台一条 `#EXTINF` + 最多 10 条源（验活优先、备用补齐）；台湾地区频道需过稳定性门槛。
   - `TVLives.txt`：TVBox 文本格式（`分组,#genre#` + `频道名,URL`），每条源独立一行即独立线路。保留全部候选源（含国内运营商 IPv6 源），**不因境外验活失败而删源**；验活通过的源排在最前作兜底，其后为国内源。同时更新 `EPG.xml` 与 `STATUS.md`，由 Actions 自动提交。

所有逻辑在 [`scripts/update_livelist.py`](scripts/update_livelist.py)，仅使用 Python 标准库。

## 手动更新

到仓库 **Actions** 页签，选择 **Livelist Auto Update**，点击 **Run workflow** 即可立即刷新。

本地运行（需要 Python 3.8+）：

```bash
python scripts/update_livelist.py
```

参数示例：

```bash
# 跳过验活，用本地 m3u 快速演练
python scripts/update_livelist.py --source-file sample/cn.m3u --skip-validate --no-epg --output-dir out

# 调整台湾地区频道的稳定性门槛
python scripts/update_livelist.py --tw-min-urls 1 --tw-max-latency 1500
```

## 文件说明

- `IPTV.m3u`：自动生成，按央视 / 卫视 / 港澳台 / 央视付费四类分组，央视内部按频道号排序；单频道一条 `#EXTINF` + 最多 10 条源（验活优先、备用补齐）；不含 GoodTV 与“其他”类目，请勿手工编辑（下次更新会被覆盖）。
- `CCTV.m3u` / `CNTV.m3u`：自动生成，分别是央视（含央视付费）、卫视（含港澳台）子集，格式同上，请勿手工编辑。
- `TVLives.txt`：自动生成，TVBox 文本订阅格式；每条源独立一行（独立线路），保留国内运营商源，错台源（如冒充 CCTV1 的芒果TV域名）按黑名单剔除；请勿手工编辑（下次更新会被覆盖）。
- `EPG.xml`：节目单，抓取失败时保留上一版。
- `Livelist-QI.m3u`：历史遗留文件，新流水线不改动。
- `STATUS.md`：最近一次更新时间、各源条数、验活统计。
- `更新日志.md`：版本与变更记录。

## 说明与免责

- 本仓库只聚合 publicly available 的直播源链接，不托管任何视频内容。
- “一条 `#EXTINF` 下接多条源”是部分播放器（如 TVBox / TiviMate 等）支持的多线路写法；若你的播放器只认传统「一条 `#EXTINF` 对一条 URL」，请把多出的 URL 行单独补上 `#EXTINF` 后再导入。
- 直播源随时可能失效，建议搭配「定期验活 + 多源备份」使用。
- 电信/移动/联通等运营商**内网**直播源通常只能在对应运营商网络内访问，境外公开验活节点无法验证。在 `IPTV.m3u` 中这类源作为**备用源**排在已验活源之后；在 `TVLives.txt` 中这类源作为**独立线路完整保留**（不因境外验活失败删除）。若你所在网络（如对应运营商内网）可访问即可播放，不可用时有前面的已验活源兜底。
- 内容版权与地域授权归各版权方所有，请仅在个人合法范围内研究使用。
