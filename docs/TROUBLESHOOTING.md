# 排错与恢复

| 现象 | 判断和处理 |
|---|---|
| 进程存在但没有帧进展 | 看 `scratch/<key>/progress.json` 中帧号、时间与 updated_unix。至少比较两次真实进度，不能用进程存活判定正常。超过5分钟不变核对真实 Colab 日志。 |
| CLI Connection lost | 单独连接丢失，云端可能仍工作。用 CLI ls/download 读取小型 status、每天 JSON 或进度文件；不要生产中重新挂载、重启内核或再 exec。 |
| Google Drive authorization needed | 本人打开 CLI 的实时链接授权，返回终端按 Enter。旧链接或运行时已回收时重新创建取回会话；不要把授权链接/代码提交到 Git。 |
| 授权完成但挂载卡住 | 若 WebSocket 回传丢失，只在尚未开始生产的取回运行时里中止挂载、重新连接；已授权凭据通常无需重复同意。生产中禁止这样操作。 |
| TLS EOF / refresh failed | 优先检查网络或当前代理；一次刷新请求失败不代表凭据撤销。只使用自己的 HTTP_PROXY/HTTPS_PROXY 设置，不把某台电脑的代理地址写入项目。 |
| 本地进度写入失败 | 云端每日分段为恢复依据，不因此停止云端生产。没有正在生产时，检查本机磁盘/权限，再重新读取云端状态。 |
| 下载中断 / 云端已完成 | 保留 `.mp4.part`，运行 retrieve。8MiB块避开整片超大传输，逐块与全片校验；不要因整片下载失败重新制作。 |
| No space left | 不自动删除用户文件；更改工作盘/下载盘，移走本任务临时文件后恢复。空间估算是规划值，下载使用实际长度检查。 |
| 新会话看到旧 status=rendering | 新运行时确认前一个运行时已消失后可重新处理未完成当天；已有活跃会话则拒绝第二条 production exec。会话名应专用于本项目。 |
| 无影像对在20分钟内 | 该时间段不能按当前规则完整制作。调整日期或修复数据入口；程序不会造帧或隐藏观测时间。 |

**状态拆分：** `rendering` → `concatenating` → 云端 `complete` → 本机 `DOWNLOAD` → `VERIFIED` → runtime stopped。云端 complete 不代表成片已取回。授权阻塞需要人工动作，不应无限循环生产巡检或推算完成时间。
