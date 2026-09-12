# Changelog

本插件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
