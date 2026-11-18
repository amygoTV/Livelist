# Livelist

中文 IPTV 直播源聚合仓库。由 GitHub Actions 每 6 小时自动抓取公开源、并发验活测速，生成可直接导入播放器的 m3u 列表。

当前状态见 [STATUS.md](STATUS.md)。

## 订阅地址

| 列表 | 说明 | 地址 |
|---|---|---|
| IPTV.m3u | CCTV + 卫视 + 指定香港频道 + 稳定台湾地区频道 + 移动/联通资源 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/IPTV.m3u` |
| CCTV.m3u | 央视频道 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/CCTV.m3u` |
| CNTV.m3u | 卫视频道 + 凤凰卫视/凤凰资讯/星空卫视 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/CNTV.m3u` |
| EPG.xml | 节目单 | `https://raw.githubusercontent.com/amygoTV/Livelist/main/EPG.xml` |

播放器（VLC / PotPlayer / MPV / Tivimate / TVBox 等）直接复制订阅地址即可。

## 工作原理

1. **抓取**：从以下公开源拉取 m3u
   - `iptv-org/iptv`：`streams/cn.m3u`、`hk.m3u`、`tw.m3u`、`mo.m3u`（分别对应中国内地、中国香港、中国台湾地区、中国澳门）
   - `fanmingming/live`：IPv6 列表（央视/卫视补充）
   - `Guovin/iptv-api`：其 GitHub Pages 发布的测速结果 `result.m3u`
2. **清洗**：仅保留 CCTV、卫视、凤凰卫视 / 凤凰资讯 / 星空卫视、台湾地区候选频道，以及移动 / 联通资源；剔除成人频道与更新时间戳等元数据行，按频道名归一化去重，每个频道最多保留 6 个候选接口。
3. **验活**：32 并发对接口发起带 Range 的请求，只保留 200/206 且有媒体数据的地址；同频道**优先 IPv6 接口**，再按响应延迟排序，每频道保留最快 4 条。若同一频道同时有移动和联通接口，会各保留至少一条再补齐其余。台湾地区频道需额外通过稳定性门槛：至少有 1 条可用接口，且最快延迟不超过 1500ms（可用 `--tw-min-urls` / `--tw-max-latency` 调整）。
4. **产出**：覆盖写入 `IPTV.m3u` / `CCTV.m3u` / `CNTV.m3u`，更新 `EPG.xml` 与 `STATUS.md`，由 Actions 自动提交。

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

- `IPTV.m3u`：自动生成，仅含 CCTV + 卫视 + 凤凰卫视/凤凰资讯/星空卫视 + 通过稳定性门槛的台湾地区频道 + 移动/联通可达资源，请勿手工编辑（下次更新会被覆盖）。
- `CCTV.m3u` / `CNTV.m3u`：自动生成，分别是央视频道、卫视（含指定香港频道）子集，请勿手工编辑。
- `EPG.xml`：节目单，抓取失败时保留上一版。
- `Livelist-QI.m3u`：历史遗留文件，新流水线不改动。
- `STATUS.md`：最近一次更新时间、各源条数、验活统计。
- `更新日志.md`：版本与变更记录。

## 说明与免责

- 本仓库只聚合 publicly available 的直播源链接，不托管任何视频内容。
- 直播源随时可能失效，建议搭配「定期验活 + 多源备份」使用。
- 移动/联通等运营商**内网**直播源通常只能在对应运营商网络内访问，公开验活节点无法验证，因此不会纳入自动列表；这里只对公网可验活且标记为移动/联通的资源做双网兼顾。
- 内容版权与地域授权归各版权方所有，请仅在个人合法范围内研究使用。
