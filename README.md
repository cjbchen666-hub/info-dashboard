# 信息驾驶舱 · 云端自动更新版

把「每天 12:00 / 17:00 自动更新」从本机搬到 GitHub 云端：定时任务在 GitHub Actions 上执行数据采集，页面自动发布到 GitHub Pages，任何设备随时可访问，本机关机也不影响。

## 目录结构

```
InfoDashboardCloud/
├── update.py                 # 数据采集脚本(纯标准库, 无依赖)
├── dashboard.html            # 生成的驾驶舱页面(数据内嵌)
├── data.json                 # 原始数据(供调试)
└── .github/workflows/update.yml   # 云端定时任务定义
```

## 三步部署(约 5 分钟)

1. **建仓库**：在 GitHub 新建一个空仓库（例如 `info-dashboard`，Public 即可，不要勾选自动生成 README）。
2. **推代码**（任选其一）：
   - 在 WorkBuddy 里连接 GitHub 连接器，让我帮你推送；或
   - 本地命令行：
     ```bash
     cd C:/Users/27170/InfoDashboardCloud
     git init && git add . && git commit -m "init"
     git remote add origin https://github.com/<你的用户名>/<仓库名>.git
     git branch -M main && git push -u origin main
     ```
3. **开启 Pages**：仓库 Settings → Pages → Source 选 **GitHub Actions** → Save。

完成后，访问地址为 `https://<你的用户名>.github.io/<仓库名>/dashboard.html`。

## 工作原理

- `update.yml` 定义了每 2 小时自动运行（cron: `0 */2 * * *`，UTC，即北京时间整点）
- 运行时会：拉取仓库 → 用 Python 跑 `update.py` 抓取 A股/新番/俄乌/AI/硬件/小岛秀夫/TWICE/动画产业数据 → 自动把页面发布到 `gh-pages` 分支
- GitHub Pages 设置为「Deploy from a branch → gh-pages」，推送即自动生效
- 也可在 Actions 页面点 **Run workflow** 手动触发一次

## 常见问题

- **定时有延迟**：GitHub Actions 的定时任务高峰期最多可能延迟 10~20 分钟，属正常现象。
- **ISW 地图偶尔获取失败**：脚本自带重试 + 沿用上次成功数据的兜底机制，页面不会白屏。
- **页面不刷新**：页面打开后每 30 分钟自动重载，也能手动刷新。
- **数据源变更**：只需改 `update.py` 并推送，云端下次运行自动生效。

## 与本机版本的关系

- 本机 `C:\Users\27170\InfoDashboard\` 上的 12:00/17:00 定时任务可保留作为兜底，也可在 WorkBuddy 设置中暂停，避免重复生成（两者互不影响）。
