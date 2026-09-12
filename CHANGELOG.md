# Changelog

本插件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## 路线图（v2+）

- **多角色声线**：speaker 判定已能拆出行首人名，下一步把 (speaker → 声线)
  路由到宿主不同角色/声线；受限于宿主 B 层 `/speak` 契约（只有
  `lanlan_name` 选角色，无独立音色参数），需先调研宿主多角色实例；
- **Textractor hook**：比 OCR 更准的文本源（带 speaker/换行语义），但
  需要 hook 游戏进程，与零注入原则冲突 → 作为显式开关的高级模式，
  风险文案写进面板；
- **游戏档案**：按窗口标题/进程名记忆区域与规则，切游戏自动切换。

## [0.2.0] - 2026-09-12

抓取子系统重构：从「拓屏幕区域」变成「拍窗口本体」，窗口列表改为全量候选 + 面板过滤。

### Changed

- **取图主链路改为 PrintWindow(PW_RENDERFULLCONTENT)**：系统把窗口内容
  渲染到内存位图——**不再受其它窗口遮挡影响**（旧版游戏被聊天窗/弹窗盖住
  时，OCR 读到的是遮挡物）；也不受多屏负坐标/任务栏自动隐藏影响。个别
  硬件加速窗体拿不到内容（全黑）时 auto 模式自动回退桌面区域截屏，
  两通道皆黑才报黑帧错。`[dub] capture_mode = auto/window/screen` 可显式
  指定（screen 即 v0.1 旧行为，排障对照用）。
- **几何基准换成 DWM 扩展帧边界**（DWMWA_EXTENDED_FRAME_BOUNDS）：Win10+
  的 GetWindowRect 含一圈透明 resize 边框（左右各 ~7px、底边更厚），旧版
  直接拿它截图导致预览/框选区域与窗口错位；PrintWindow 产出的全窗图也
  按同样差值裁边，两条通道与面板框选共用同一坐标系。
- **窗口列表改为「所有窗口都列出来让玩家选」**（用户反馈：不应只能靠
  标题猜游戏）：
  - 最小化窗口**收录并打 `[min]` 标**（旧版静默丢弃，玩家刷新永远看不
    到自己的游戏）；选中后 dub_start 给出「请先还原」的显式错而非默默
    失败，运行中最小化则自动暂停（`target_minimized`），还原后「继续
    配音」即接；
  - 每行附带进程 exe 名 / pid / 前台标记（★）/ 本程序窗口标记，标题为
    空的窗口退回进程名做标签不再整窗消失；
  - 仍剔除两类截不到的：DWM 伪装窗（cloaked：UWP 挂起/其它虚拟桌面，
    IsWindowVisible 仍为真）与零尺寸垃圾窗；上限 60 → 200，面板新增
    过滤输入框（标题/进程名/句柄，空格多词）。

### Added

- `[dub] capture_mode`（plugin.toml / config.example.toml / 面板「截屏方式」
  下拉）；非法值：面板保存拒绝，手工改 config 回落 auto 并记 warning。

### Tests

- 新增 `tests/test_capture.py` 12 例（列表裁决/最小化收录/标签兜底/
  黑帧阈值/模式校验与回退/dub_start 最小化拒绝/运行中最小化自动暂停/
  dub_windows 富化行/三层配置默认值同源）；37 → 49 例。

## [0.1.2] - 2026-09-12

纯聊天控制：不开面板、不碰鼠标，全程用嘴。

### Fixed

- **`@message(auto_start=True)` 签名漂移（致命）**：宿主 SDK 的 `message()` 已
  不接受 `auto_start` 参数，插件模块在真实 SDK 下 import 即 TypeError——
  v0.1.0/v0.1.1 的发布包在宿主元数据探测阶段就没有 `plugin.meta.json`，
  导入后插件根本无法加载。移除该参数；并把测试桦的 `message` 改为与宿主
  同签名的严格版，参数漂移从「打包才暴雷」提前到「测试即红」。

### Added

- **`catgirl_seiyuu_resume` llm 工具**：对猫娘说「继续配音 / 接着念」即可从
  暂停恢复，**复用暂停前的目标窗口**，不重新解析前台（此前没有 resume 工具，
  自动让位后只能去面板点「继续」）。
- **`dub_start` 暂停态降级**：无显式目标 + 处于暂停 + 原窗口存活时，start
  视同 resume。LLM 把「继续配音」翻成 start 也能成功（旧行为：重新解析前台
  → 撞上 v0.1.1 的宿主窗口护栏 → 报错）。显式传 hwnd/title（面板选窗）仍
  正常切换目标，不被劫持。

### Changed

- `dub_pause` 幂等化：聊天里说「暂停」与 `pause_on_user_message` 自动让位
  存在竞态（谁先到都能停），现在已暂停时重复暂停返回成功，不再回
  「当前不在配音中」误导主人。
- `catgirl_seiyuu_start` / `catgirl_seiyuu_pause` 工具描述改写，引导 LLM
  区分「继续（resume）」与「重新开始（start）」。

### Tests

- 新增 `tests/test_chat_control.py` 6 例（resume 复用目标 / start 暂停态降级
  / 显式换目标不受降级影响 / 竞态幂等 / off 时报错 / 目标已死拒绝恢复）；
  31 → 37 例。反向对照：除「显式换目标」等价例外，5 例在 v0.1.1 上全部钉红。

### 打包与文案澄清（实测反馈驱动）

- **宿主元数据 schema 不匹配排查**：9/3 打包版宿主只认 schema 3，本仓 CLI
  产 schema 4 → 老宿主丢弃打包元数据回落 manifest → 面板所有按钮报
  「UI action ... is not a plugin entry」。解法：用老宿主同 commit 的 CLI 重打
  （README「打包元数据与宿主版本」节已固化流程）；已验证源码对新/老宿主
  双向兼容。另：老宿主 `@message` 的 auto_start 默认值就是 True，签名修复
  不影响其让位行为。
- 面板「下载 OCR 模型」按钮更名「下载其它语言模型」，并加提示：默认中文
  PP-OCRv4 模型已随包内置（vendor 体积大头其实是 OpenCV/onnxruntime/numpy），
  仅切换 [ocr] 语言/版本时才需要下载。

## [0.1.1] - 2026-09-12

缺陷修复轮（无新入口、无契约变化）。

### Fixed

- **播报 worker 配置陈旧**：`_speak_worker` 此前在启动时快照一次 `[dub]`
  配置，运行期间 `update_settings`（面板保存设置）改的 `lanlan_name` /
  `mirror_text` / `speak_timeout_s` 对正在跑的播报全部无效，必须
  stop/start 才生效。现在每句开工前重读当前配置。
- **目标窗口静默回退**：`dub_start` / `capture_preview` / `test_capture`
  显式传入的 `hwnd`（面板选择的窗口）失效时，旧实现会不声不响地回退到
  「当前前台窗口」——从面板启动时前台往往是 N.E.K.O 自己的窗口，等于把
  宿主聊天界面喂进 OCR。现在显式 `hwnd` / `title` 解析失败直接报错
  （`invalid_hwnd` / `title_not_found`），仅在不指定目标时才取前台。
- **自我朗读回声回路护栏**：默认跟随前台开启配音时，前台窗口标题命中
  `host_window_keywords`（默认 `["N.E.K.O"]`，子串、忽略大小写）→ 拒绝并
  给出可执行的提示。新增 `[dub] exclude_host_window`（默认 `true`）可关。
- **运行时配置消毒**：手工编辑过的 `config/plugin.toml` 中的非法类型
  （字符串数字、标量代替列表等）此前可把 startup 或轮询循环打死。现在
  `_load_config` 末尾按出厂默认的类型逐项夹紧/回落，并记 warning。
- **播报 worker 死亡监管**：worker 协程一旦意外终止，队列只进不出、面板
  仍显示 running，配音静默卡死。轮询循环现在每 tick 监督：发现死亡即记
  日志并重启。
- **Windows DPI 缩放截屏错位**：缩放 ≠100% 时 `GetWindowRect`（逻辑坐标）
  与 Pillow `ImageGrab`（内部会把进程切成 DPI aware 后按物理像素抓）两套
  坐标系先后不一致，截到的区域与窗口错位。首次取图前显式把进程钉为
  per-monitor v2 DPI aware（逐级降级，best-effort），坐标系统一为物理像素。

### Added

- `[dub] exclude_host_window`、`host_window_keywords` 两键（plugin.toml /
  config.example.toml / 面板可经 `update_settings` 修改）。

### Tests

- 新增 `tests/test_bugfixes.py` 7 例（worker 新鲜配置 / 失效句柄不回退 /
  标题匹配与落空 / 宿主窗口护栏及其开关 / 配置消毒 / worker 监督重启）；
  24 → 31 例。反向对照：除「护栏可关」正例（旧实现本就放行，绿属预期）
  外，其余 6 例在旧实现上全部钉红。

## [0.1.0] - 2026-09-12

首个版本：外部截屏 + OCR 配音管线（稳定窗口 / 去重 / UI 词 / speaker
规则判定），宿主游戏 SDK B 层 `POST /api/game/catgirl_seiyuu/speak`
逐句播报（`wait_for_audio_completion` 串行），llm_tool 语音开关，
Hosted TSX 面板（窗口选择 / 区域框选 / 规则微调 / 跳过列表）。
