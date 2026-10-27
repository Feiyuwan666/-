# 量化小助手（安卓 App + 云端盯盘）

持仓盯盘、达标/止损提醒，微信推送。两部分：

| 部分 | 干什么 | 依赖手机吗 |
|---|---|---|
| **App**（`www/index.html`，Capacitor 打包成 APK） | 看盘、加/改持仓、手动刷新、前台实时盯盘、一键把持仓同步到云端 | 需要，但只在你打开它的时候 |
| **云端盯盘**（`.github/workflows/monitor.yml`） | 交易日每 5 分钟拉一次行情，达标/止损直接推微信 | **不需要**。手机关机、App 没开都能收到 |

> 为什么要云端那套：安卓 WebView App 一旦切后台/锁屏，JS 定时器会被系统挂起，靠"后台保活"根本不稳。把盯盘挪到 GitHub Actions 上跑，才是真·后台。

## 下载安装

👉 **[Releases · apk-latest](../../releases/tag/apk-latest)** → 点 `lianghua.apk`

华为 / HarmonyOS 安装前：

1. **设置 → 系统和更新 → 纯净模式 → 退出**
2. **设置 → 安全 → 更多安全设置 → 安装外部来源应用 → 文件管理 → 打开**

> ⚠️ 别用微信 / QQ 传 APK（会变成 `.apk.1` 装不上）。

## 一次性配置（3 步）

### 1. 放好 Server酱 SendKey

仓库 → **Settings → Secrets and variables → Actions → New repository secret**

- Name：`SERVERCHAN_KEY`
- Secret：你的 `SCT…` 开头的 SendKey

云端盯盘靠它推微信。没配也能跑，只是不推送（日志里会提示）。

### 2. App 里填两个东西（只存手机本地，不进仓库）

打开 App → 下方「⚙️ 监控设置」：

- **Server酱 SendKey** → 点「📨 测试微信推送」，微信收到即通
- **GitHub Token** → 细粒度 token，只要**这一个仓库**的 `Contents: Read and write`

### 3. 加持仓 → 点「☁️ 同步持仓到云端」

App 会把持仓和阈值写到仓库的 `data/positions.json`，云端盯盘下一次运行（≤5 分钟）就读到了。

改完持仓记得再点一次同步。

## 云端怎么跑

```yaml
schedule: '*/5 1-7 * * 1-5'   # UTC，即北京时间 9:00-15:59 工作日每 5 分钟
```

`scripts/monitor.py` 每次做的事：

1. 读 `data/positions.json`（App 同步过来的持仓 + 达标/止损阈值）
2. 判断是否交易时段（9:30–11:30、13:00–15:00；周末和全零成交量自动跳过）
3. 拉腾讯财经行情 `qt.gtimg.cn`
4. 逐个判断：`(现价-买入价)×数量 ≥ 达标金额` → 推🎯；`跌幅 ≥ 止损线` → 推🛑
5. 同一只票同一天同一信号只推一次（去重记录在 `data/state.json`）

想手动试一次：**Actions → 云端盯盘 → Run workflow**（勾了 force 就忽略交易时段）。

## 行情与数据

- 行情：腾讯财经 `qt.gtimg.cn`（A 股实时快照，现价 / 涨跌幅）
- 推送：Server酱 Turbo `sctapi.ftqq.com`
- App 内请求走 **CapacitorHttp 原生通道**，绕开 WebView 跨域限制；脚本加载失败会自动切到原生 HTTP 做备用通道
- 持仓和设置存在手机 localStorage；同步后 `data/positions.json` 里也有一份（仓库公开的话，别把持仓金额当隐私看）

### 已知限制

| 限制 | 说明 |
|---|---|
| App 锁屏 / 切后台不再刷新 | 安卓 WebView 后台挂起 JS。想要 24 小时盯盘请依赖「云端盯盘」 |
| 云端 5 分钟一次可能有延迟 | GitHub Actions 定时任务属尽力而为，高峰期可能晚几分钟 |
| 公开仓库 60 天无提交，定时任务会被自动停 | 点一下 Actions 里的 Run workflow 即可恢复 |
| 中文股票名偶尔显示成代码 | 备用 HTTP 通道按 UTF-8 解码 GBK 行情，名称乱码时回退成代码，价格不受影响 |
| 覆盖安装要求签名一致 | 配置了 `KEYSTORE_B64` + `KEYSTORE_PASS` 两个 Secret 才会用固定证书重签；否则每次构建签名不同，升级要先卸载（会丢本地数据，建议先同步云端） |

## 工程结构

```
├── .github/workflows/build-apk.yml   # 打 APK（Capacitor + Gradle + 图标 + 固定签名）
├── .github/workflows/monitor.yml     # 云端盯盘（交易日每 5 分钟）
├── www/index.html                    # App 全部界面与逻辑
├── scripts/monitor.py                # 盯盘判断 + 微信推送
├── data/positions.json               # App 同步过来的持仓（云端读这份）
├── data/state.json                   # 最近检查结果与去重记录
├── resources/mipmap-*/               # 应用图标
├── capacitor.config.json             # 包名 com.feiyuwan.lianghua + CapacitorHttp
└── package.json
```

改 App：改 `www/index.html` → 提交 `main` → Actions 自动打包（3~5 分钟）→ Releases 更新。
