# Livelist 自动更新状态

- 最近更新: 2026-11-19 10:02:37 (北京时间)
- 源数量: 9
- 抓取频道: 2170 (去重后 800)
- 验活通过: 121；备用补齐源: 744 条（验活失败但保留，央视/卫视优先）
- CCTV: 57 / 卫视(含港澳台): 64
- 分组: 央视 46 / 卫视 48 / 港澳台 16 / 央视付费 11
- 剔除 GoodTV: 0 个；未匹配四类分组: 89 个
- 台湾地区（通过稳定性门槛）: 10 个，剔除 20 个
- 运营商: 移动 70 / 联通 32 / 双网 30 个频道
- 剔除“其他”类目: 313 个（台湾地区/运营商已归类保留）
- IPv6 接口: 124 条（同频道优先）
- TVLives.txt: 141 个频道 / 797 条线路（含国内源，仅去重不限流删源）
- 筛选范围: CCTV + 卫视 + 凤凰卫视/凤凰资讯/星空卫视 + 稳定台湾地区频道 + 移动/联通资源

## 数据源

- iptv-org CN: 509 条
- iptv-org HK(China): 26 条
- iptv-org TW(China): 26 条
- iptv-org MO(China): 10 条
- fanmingming IPv6: 82 条
- fanmingming ITV: 189 条
- fanmingming index: 94 条
- Guovin iptv-api: 833 条
- MyTV TVLives: 401 条

## 订阅地址

- `https://raw.githubusercontent.com/amygoTV/Livelist/main/IPTV.m3u`（M3U 合并多源）
- `https://raw.githubusercontent.com/amygoTV/Livelist/main/CCTV.m3u`
- `https://raw.githubusercontent.com/amygoTV/Livelist/main/CNTV.m3u`
- `https://raw.githubusercontent.com/amygoTV/Livelist/main/TVLives.txt`（TVBox 文本格式，每条源独立线路，含国内源）

> IPTV.m3u 为「单频道多源」合并写法；TVLives.txt 为 TVBox 常用的文本订阅格式（分组,#genre# + 频道名,URL），每条源都是独立线路，兼容性更好。TVLives.txt 保留全部候选源（验活通过的排前面作兜底，其后为国内运营商源——境外节点验活不了但你本地可能可播），不因境外验活失败而删源。本仓库每 6 小时自动更新。仅供个人合法研究使用。
