# Changelog

本插件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## 路线图（v2+）

- **多角色声线**：speaker 判定已能拆出行首人名，下一步把 (speaker → 声线)
  路由到宿主不同角色/声线；受限于宿主 B 层 `/speak` 契约（只有
  `lanlan_name` 选角色，无独立音色参数），需先调研宿主多角色实例；
- **Textractor hook**：比 OCR 更准的文本源（带 speaker/换行语义），但
  需要 hook 游戏进程，与零注入原则冲突 → 作为显式开关的高级模式，
  风险文案写进面板；
- **游戏档案**：按窗口标题/进程名记忆区域与规则，切游戏自动切换；
- **HUD/LLM 面向文案的本地化**：本轮豁免面（`_push_hud` 运行时文案、
  `@plugin_entry` description）继续中文直出，后续轮调研宿主 i18n 解析面。

## [0.2.2] - 2026-09-12

v1 体验清账轮（台账主项 4）：句末标点优先，堵死打字机增长帧半句开口。

### Added

- **句末标点优先（`punctuation_priority`，出厂默认开）**：`LineGate` 定型判定
  重写为「冻结前提 + 句读快车道」三层：
  1. **防半句（真正的 bug）**：长句逐帧变长时相邻帧相似度本就 ≥0.9（每帧
     只多一两字），v0.2.1 的相似链会把增长帧误计入稳定计数、在半句处
     开口；现在定型必须以**末两帧完全一致（冻结）**为前提，增长中不开口；
  2. **不变迟钝**：末位命中句读（`sentence_end_chars`，默认
     `。！？!?…」』`）的候选冻结 2 帧即播，`stable_frames` 调高也吃不到 N；
     未命中句读的普通台词维持旧阈值语义（满 N 且冻结，need=2 时与旧版对
     静止文本行为一致），只多等真停打的瞬时；
  3. **兜底防漏播**：OCR 系统性抖字导致永不冻结时，相似链攒到 N+4 帧强制
     定型（`STUCK_EXTRA_FRAMES`）；
  4. 开关关闭 = v0.2.1 纯 N 帧窗口行为逐字回归（`sentence_end_chars=""` = 只
     保留冻结铁律不提前定型），真机验证不满意可一键退回。
- 新配置键 `dub.punctuation_priority`（bool）/ `dub.sentence_end_chars`（str）
  全链路接通：plugin.toml / config.example.toml / DEFAULTS 消毒 / 面板开关 /
  `update_settings` 白名单 / 快照 `settings`；`test_rules` +7 例（增长不开口 /
  句读提前 / 无句读满窗 / 关闭回归 / 抖动兜底 / 自定义句读集 / 普通台词不被
  钉死），`test_entries` 白名单断言同步。

### Fixed

- **面板「下载语言模型」前端 30s 掉镖（台账小毛病①实测清算）**：实测超时链
  发现卡点不在宿主网关而在宿主桥默认 deadline——`surfaceApi.call` 不传
  `timeoutMs` 时前端 30s（runtime.js 默认 / axios 全局 `API_TIMEOUT`）就中断，
  而 `ocr_download` 入口允许 300s（宿主 `host.trigger` 按入口 meta.timeout
  等待，非 `NEKO_PLUGIN_TRIGGER_TIMEOUT` 的 10s 默认）。现在面板 `call()`
  wrapper 透传 options，`ocr_download` 显式 `{ timeoutMs: 315_000 }`：
  240s（HTTP 下载）< 300s（入口）< 315s（面板），服务端错误先于前端 abort
  到达；链路同源事实进 README 坑位存档。

### Tools

- `tools/spike_speak.py` 从 v0.1 历史形态更新到现行 B 层契约（台账小毛病②）：
  补齐 X-CSRF-Token（/health → instance_id）/ Origin / body `game_type` 与
  `HostSpeakClient` 逐字段同源（旧脚本对启用 CSRF 的宿主直接 403，会误判
  「B 层不可用」）；新增契约字段形状打印与「时长过短可能未真播」提醒；
  定位为人工排障自检，不进运行时、不进 release 门。

## [0.2.1] - 2026-09-12

工程纪律收编轮（fc/ym 同源）：五门发布闸门落地，面板 i18n/错误码契约整改。

### Fixed

- **面板 Hosted TSX 类型错误（v0.1 就带着入库，市场 CI 不跑这道门所以一真没发现）**：
  `Grid columns=` → `cols`（6 处，旧属性被运行时丢弃，四列布局一直在默默降级）；
  `NumberInput`/`Select` 不支持 `label` 属性 → 改包 `Field`（轮询间隔/稳定帧数
  两个输入框此前没有标题）；`StatusBadge text=` → `label`（状态徽章此前渲染为空）；
  `tone="neutral"` 不在 Tone 类型内 → `"default"`；解构名 `api` 与宿主注入的
  全局对象同名被检查器拒收 → `api: surfaceApi`（ym 同款）；`State` 类型里
  `target` 字段重复声明→合并。验证方式：`tools/release_gate.py` 的
  hosted-tsx 门在修复前红（逐条命中）、修复后绿。

### Changed

- **错误码契约（fc 主项 #10 同源）**：面板可达入口的 `Err(SdkError(…))` 全部
  改发稳定 ASCII 码（`^[a-z][a-z0-9_]*$`，契约码集 `PANEL_ERROR_CODES` 23 码），
  动态细节（异常 repr、宿主 reason、非法键名）一律进日志；前端 `errorText()`
  按 `panel.errors.<camelCase>` 翻译，未知码回落码本身可排查。`last_error` /
  `last_mode_hint` 快照字段同步改发码；底层错误（PrintWindow 家族、OCR 后端）
  经 `_stable_code()` 映射归一，未识别形态落 `capture_failed`。
- **面板 i18n 收编**：所有用户可见文案（模式/暂停原因/统计标签/目标行/toast/
  占位符/跳过表头/OCR 状态/区域行）走 `t(key, { defaultValue })`，zh bundle 与
  defaultValue 逐字同源；双语 bundle 40 → **105 键**；注册文案（入口名）
  `@plugin_entry(name=…)` 全部改走 `tr()`（ui.action label 英文兜底，fc 同款）。
- 入口 `test_speak`/`feed_line` 的空文本错误码统一为 `empty_line`。

### Added

- `tools/release_gate.py` 五门（pytest / ruff / check / **release（挂载+按裸 id
  跑 `check -r`，复刻市场 CI）** / hosted-tsx），携带 ym 台账全部纪律：.vscode
  随副本、缓存排除表、UTF-8 管道钩、副本用后即删、ruff 钉 CI 原样参数
  `uvx ruff==0.12.4 --ignore-noqa`。
- `tests/test_i18n_contract.py` 常驻契约门 13 例（A1 版本同源；C1-C9 双语键集/
  TSX 引用面/defaultValue 同源/tr 同源/死键/码形/双向同步/映射不越界/归一化
  行为/暂停原因与模式同源/插值参数；C8 TSX 硬编码中文扫描带豁免表）。
- `tools/reverse_control.py` 反向对照脚本：12/12 逐门实测可红（C5/C6b 的跨门
  级联属 JSON 结构性连坐，判据按目标门命中），终验全绿。
- `.gitignore` 补 `uv.lock`（与 fc 对齐：锁文件不入库，vendor/ 本就是产物）。

### Tests

- 49 → 62 例；两处断言随契约迁移（回声护栏改钉 `target_is_self` 码、
  最小化拒绝改钉 `target_minimized` 码）。

## [0.2.0] - 2026-09-12

抓取子系统重构：从「截屏幕那块区域」变成「拍窗口本体」，窗口列表改为全量候选 + 面板过滤。

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
