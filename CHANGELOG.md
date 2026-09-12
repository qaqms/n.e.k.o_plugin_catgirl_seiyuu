# Changelog

本插件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
