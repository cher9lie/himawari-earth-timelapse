# Himawari Earth Timelapse · 向日葵地球昼夜视频

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/cher9lie/himawari-earth-timelapse/blob/main/notebooks/Himawari_Timelapse.ipynb)

输入日期、采样间隔、帧率、输出尺寸和保存位置，制作真实卫星观测的地球延时视频。白天保留 NICT 可见光配色，夜侧融合**同一观测时间**的 B13 云图。原始瓦片只在内存中存在；云端持久化每天的视频分段，电脑仅取回最终视频和校验/缺测记录。

## 最快开始

**浏览器用户：点击上方 Colab 按钮，运行准备单元格，填写表单，再运行预估、基准和制作单元格。** CPU 即可。首次挂载 Google Drive 需要本人 Google 授权；不能无人值守绕过。

**命令行用户：**先安装 Python 3.12+（固定版本的 Colab CLI 要求），下载本仓库后运行：

```bash
python quickstart.py
```

Windows 也可双击 `start.cmd`。启动器在仓库内创建 `.venv`、安装依赖并询问配置；默认只保存配置和显示预估，不会擅自开始整月任务。Colab CLI 官方支持 Linux/macOS；本项目 Windows 批处理适配覆盖了本次实际使用的流程，交互 console 请用 WSL。依赖安装和 Google 登录仍需网络。

## 手动安装及命令

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[colab]"
earth-video configure
earth-video estimate
earth-video doctor
earth-video benchmark --backend colab
earth-video run --backend colab
```

无需激活环境也可直接运行 `.venv/Scripts/python.exe -m himawari_timelapse.cli ...`（Linux 为 `.venv/bin/python`）。配置文件默认 `config.json`；每条命令支持 `--config 自定义.json`。

`benchmark` 渲染前 12 帧并测量下载、融合、编码总用时，保留运行时供制作使用；不再使用时运行 `earth-video release`。`run` 完成后按 8 MiB 分块下载、逐块校验、校验完整 SHA-256，复制缺测记录，成功后自动释放**指定会话**的运行时。失败保留已完成分段和 `.mp4.part`，不会假装成功，也不会停止仍在渲染的云端工作。

## 配置

| 字段 | 意义 / 默认 |
|---|---|
| `start`, `end` | 开始日期 / 不包含的结束日期；`2026-07-01` 到 `2026-08-01` |
| `interval_minutes` | 每多少分钟取一帧；10 的整数倍，10–1440 |
| `fps` | 视频播放帧率；15，范围 1–60 |
| `width`, `height` | 输出尺寸；1920×1080，偶数 |
| `timezone_offset` | 固定 UTC 时差；8 表示北京时间，无夏令时自动换算 |
| `workspace` | 本机脚本、基准记录和下载中转目录，可设 D 盘等位置 |
| `download_dir`, `output_name` | 电脑成片位置及文件名 |
| `save_root` | Colab Drive 分段持久化目录，或本地后端的持久化目录 |
| `scratch` | 编码临时目录；Colab 默认 `/content/earth_video_work` |
| `session` | 专用 Colab 会话名，避免占用其他任务 |
| `crf`, `preset`, `workers` | 质量 / 编码速度 / 并发；18 / medium / 8 |

源地球圆盘只有 1100×1100 像素。更大的输出尺寸是重采样和画布调整，**不会增加观测细节**。不支持 5 分钟原生观测；该源的时间栅格为 10 分钟。北京时间 2026 年 7 月的示例是 **4464 帧 / 15 fps = 297.6 秒**。

## 空间和耗时估算

`estimate` 显示帧数、时长、最低瓦片请求数、成片区间、Drive 保留分段与成片的峰值、临时编码与电脑下载峰值。Drive 按三份成片预留，覆盖重新拼接时分段、旧成片和待发布成片同时存在的情况。默认按 1080p 6–14 Mbps 的容量假设估算，随画布像素数缩放；CRF 编码是可变码率，实际结果可能超出区间。七月示例成片假设约 213–497 MiB，实际原始任务成片约 276 MiB。这是容量规划，不是磁盘配额保证。

没有基准数据时耗时显示 `null / 未测量`。基准后按每帧实测时间外推 ×0.8–1.8 的规划范围，明确排除授权、排队、长缺测、网络重试和成片取回；不是完成承诺。更换参数后旧基准失效。下载开始前检查实际文件长度和可用空间；大任务建议使用至少数 GiB 空闲的下载盘。

## 恢复与本地运行

运行时消失后，用相同渲染参数再次 `run`：每日分段 SHA-256 与配置匹配才跳过，未完成的一天重做。参数不变但会话名/路径可以改。已有云端任务仍标记运行中时，不提交第二条 exec；先在 Colab 核实真实任务是否结束。云端已经完成、仅电脑下载中断时运行：

```bash
earth-video retrieve --backend colab
```

新的运行时仍需 Google 授权。前缀与每个分块都重校验后续传；遇到损坏会保留文件并明确报错。命令行保持终端运行，Colab 连接中断不代表云端任务停止。

本地模式需自行安装 FFmpeg（同时提供 `ffprobe`）并加入 PATH，把配置中的 `save_root`、`scratch` 改为本地目录：

```bash
earth-video benchmark --backend local
earth-video run --backend local
```

Colab 笔记本中的电脑下载位置由浏览器设置控制；需要精确指定本机目录时使用命令行入口。不要把 Drive 的分段目录设为电脑自动同步，否则分段也会被同步到电脑。

## 方法、缺测和数据使用

参见 [处理方法](docs/METHOD.md)、[排错与恢复](docs/TROUBLESHOOTING.md)。完整同步 RGB/B13 对缺测时，在 ±20 分钟内按 -10、+10、-20、+20 顺序选择完整影像对，并显示**实际观测时间**和 `HELD FRAME: DATA GAP`，保留 `data_gaps.jsonl`；更长缺测停止。无 AI 插帧、城市灯光、静态陆地贴图或昼间自动提亮。

代码采用 MIT 许可证；卫星数据不包含在代码许可证中。请遵守 [NICT 影像使用条款](https://himawari8.nict.go.jp/ja/himawari8-about.htm)，保留 NICT/JMA 署名，研究对外发表、媒体使用等按其说明办理。瓦片接口可能变化，历史覆盖不保证；不要对服务器进行大规模并发抓取。

本项目基于一次完整七月制作实践整理。通用版有离线编码/续传/缺测回归测试；不同月份的可用性、所有分辨率及所有 Colab 账户均未逐一验证。官方工具：[google-colab-cli](https://github.com/googlecolab/google-colab-cli)，[Colab 资源说明](https://research.google.com/colaboratory/faq.html)。

## 开发测试

```bash
python -m unittest discover -s tests -v
```

测试用合成图验证多日编码、拼接、参数校验、恢复跳过、真实时间标注、坏 PNG 重试、前缀损坏与分块续传；不下载卫星原图。CI 在 Linux 安装 FFmpeg 后执行。
