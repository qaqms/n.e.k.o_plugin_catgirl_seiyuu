"""猫娘声优（catgirl_seiyuu）— 让猫娘用本人的声音逐字朗读游戏台词。

v0.1（DESIGN_BRIEF_catgirl_seiyuu.md §6）：
- 文本源：外部截屏 + `_shared/rapidocr` OCR（不触碰游戏进程，零封号风险）；
- 判定：稳定窗口 + 去重 + UI 词/speaker/独白规则（core/rules.py）；
- 播报：宿主游戏 SDK B 层官方出口 POST /api/game/<type>/speak，
  `wait_for_audio_completion` 同步回执实现"一句播完再播下一句"；
- 控制面：llm_tool（主人一句话开关）+ Hosted TSX 面板（区域框选/规则/手动投喂）。

fail-closed：安装后默认不启动、总模式默认 off；不自动恢复上次运行态。
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    message,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
    unwrap_or,
)

from .core.rules import GateConfig, LineGate
from .core.state import MODE_PAUSED, ModeMachine
from .services import capture
from .services.host_api import HostSpeakClient
from .services.ocr import OcrService

JsonObject = dict[str, Any]

PLUGIN_ID = "catgirl_seiyuu"

# 出厂默认：三层合并的最底层（< plugin.toml 各段 < 运行时 config）
DEFAULTS: dict[str, dict[str, Any]] = {
    "dub": {
        "poll_interval_ms": 900,
        "idle_poll_interval_ms": 2500,
        "stable_frames": 2,
        "similarity_threshold": 0.9,
        # 句末标点优先：末位命中句读的候选冻结 2 帧即播；未命中的额外要求
        # 末两帧完全一致（防打字机增长帧被相似链误计入稳定、半句开口）。
        "punctuation_priority": True,
        "sentence_end_chars": "。！？!?…」』",
        "dedupe_window": 64,
        "min_significant_chars": 2,
        "max_line_chars": 220,
        "ui_words": [],
        "dub_protagonist": False,
        "dub_monologue": True,
        "protagonist_names": [],
        "pause_on_user_message": True,
        "slow_poll_when_unfocused": True,
        "mirror_text": True,
        "lanlan_name": "",
        "speak_timeout_s": 120,
        "main_server_port": 0,
        "speak_game_type": PLUGIN_ID,
        # 默认目标（前台窗口）命中宿主标题关键字时直接拒绝：防止「朗读
        # 自己聊天记录」的回声回路（OCR 读聊天气泡 → TTS 播报 → 新气泡…）。
        "exclude_host_window": True,
        "host_window_keywords": ["N.E.K.O"],
        # 截屏方式：auto = PrintWindow 优先、黑帧/失败回退桌面截屏；
        # window = 仅窗口本体渲染（遮挡免疫）；screen = 仅桌面截屏（v0.1 旧行为）。
        "capture_mode": "auto",
    },
    "ocr": {
        "engine_type": "onnxruntime",
        "lang_type": "ch",
        "model_type": "mobile",
        "ocr_version": "PP-OCRv4",
    },
    "region": {"x": 0.05, "y": 0.68, "w": 0.9, "h": 0.24},
}

_DUB_PATCH_TYPES: dict[str, type] = {
    "poll_interval_ms": int,
    "idle_poll_interval_ms": int,
    "stable_frames": int,
    "similarity_threshold": float,
    "punctuation_priority": bool,
    "sentence_end_chars": str,
    "dedupe_window": int,
    "min_significant_chars": int,
    "max_line_chars": int,
    "ui_words": list,
    "dub_protagonist": bool,
    "dub_monologue": bool,
    "protagonist_names": list,
    "pause_on_user_message": bool,
    "slow_poll_when_unfocused": bool,
    "mirror_text": bool,
    "lanlan_name": str,
    "speak_timeout_s": float,
    "main_server_port": int,
    "speak_game_type": str,
    "exclude_host_window": bool,
    "host_window_keywords": list,
    "capture_mode": str,
}


# ---------------------------------------------------------------- 错误码契约层
# 面板可达入口的 Err(SdkError(...)) 只允许携带稳定 ASCII 码（^[a-z][a-z0-9_]*$），
# 前端 errorText() 按 panel.errors.<camelCase(码)> 翻译（与 forever_companion
# 主项 #10 同源）。动态细节（异常 repr、宿主 reason）一律进日志，不进文案。
# tests/test_i18n_contract.py 有常驻门：字面码 ⊆ PANEL_ERROR_CODES == bundle
# panel.errors.* 键集（双向）。

_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

PANEL_ERROR_CODES: frozenset[str] = frozenset({
    "capture_unsupported",
    "capture_failed",
    "black_frame",
    "target_window_gone",
    "target_minimized",
    "invalid_hwnd",
    "title_not_found",
    "target_is_self",
    "no_window",
    "not_running",
    "not_started",
    "ocr_unavailable",
    "ocr_failed",
    "region_not_numeric",
    "region_out_of_range",
    "empty_line",
    "patch_invalid",
    "unknown_setting",
    "setting_type_invalid",
    "capture_mode_invalid",
    "speak_failed",
    "speak_rejected",
    "ocr_download_failed",
})

# ModeMachine.pause(reason) 的合法值（面板 panel.pauseReason.* 翻译）。
PAUSE_REASONS: frozenset[str] = frozenset({"user", "manual", "conflict", "error"})

# 底层模块（capture / ocr）抛出的错误码前缀 → 契约码归一化；
# 未列出且不合码形的一律落到调用点 fallback。
_ERROR_CODE_MAP: dict[str, str] = {
    "unsupported_platform": "capture_unsupported",
    "printwindow_black_frame": "black_frame",
    "window_rect_unavailable": "capture_failed",
    "grab_failed": "capture_failed",
    "pillow_unavailable": "capture_failed",
    "getwindowdc_failed": "capture_failed",
    "createcompatibledc_failed": "capture_failed",
    "createcompatiblebitmap_failed": "capture_failed",
    "printwindow_returned_false": "capture_failed",
    "getdibits_failed": "capture_failed",
    "printwindow_error": "capture_failed",
    "shared_rapidocr_unavailable": "ocr_unavailable",
    "backend_init_failed": "ocr_unavailable",
    "ocr_unavailable": "ocr_unavailable",
    "ocr_extract_failed": "ocr_failed",
}


def _stable_code(raw: object, fallback: str = "capture_failed") -> str:
    """把任意错误串（含 `code: 细节` 形态）归一为契约码；不可识别落 fallback。"""

    base = str(raw or "").split(":", 1)[0].strip()
    code = _ERROR_CODE_MAP.get(base, base)
    return code if code in PANEL_ERROR_CODES else fallback


# 给主 AI / HUD 的中文提示（模型面向渠道，i18n 契约豁免面；面板不发裸码）。
_LLM_HINTS: dict[str, str] = {
    "capture_unsupported": "当前平台不支持截屏配音（仅 Windows）。",
    "invalid_hwnd": "目标窗口已失效（可能已关闭），请在面板重新选择窗口",
    "title_not_found": "找不到标题匹配的目标窗口",
    "target_minimized": "目标窗口已最小化：请先还原游戏窗口再开始",
    "target_is_self": (
        "前台是 N.E.K.O 自己的窗口：请先切到游戏窗口再试，"
        "或在面板里指定目标窗口（可在 [dub] exclude_host_window 关闭此保护）"
    ),
    "no_window": "没有可截取的窗口",
    "black_frame": "截取到黑帧：请把游戏切到窗口化/无边框窗口模式",
    "not_started": "未开启配音，请先开始",
    "not_running": "当前不在配音中",
    "speak_failed": "播报失败：宿主语音通道异常",
    "speak_rejected": "宿主拒绝了播报请求（语音通道可能被占用）",
    "ocr_download_failed": "OCR 模型下载失败",
}

# HUD 暂停通知的中文原因（HUD 走宿主渲染，不经面板 bundle → 同豁免面）。
_HUD_PAUSE_HINTS: dict[str, str] = {
    "target_window_gone": "目标窗口已关闭",
    "target_minimized": "目标窗口被最小化，还原后点继续即可",
    "black_frame": "截取到黑帧（请把游戏切到窗口化/无边框窗口）",
    "capture_unsupported": "当前平台不支持截屏",
    "capture_failed": "窗口截取失败",
    "ocr_unavailable": "OCR 引擎不可用",
    "ocr_failed": "OCR 识别失败",
    "speak_failed": "播报通道异常",
    "speak_rejected": "宿主拒绝了播报请求",
}


def _llm_note(code: object) -> str:
    c = str(code or "")
    return _LLM_HINTS.get(c, f"操作失败（{c}）")


def _coerce_value(default: Any, value: Any) -> Any:
    """按出厂默认值的类型夹紧运行时配置；转不动就回落默认。

    运行时 config 可被手工编辑成任意形状（错类型/嵌套/None），不能让
    垃圾值穿透到 int()/float() 现场把 startup 或轮询循环打死。
    """

    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(value, (int, float)):
            return bool(value)
        return default
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        if not isinstance(value, (list, tuple)):
            return default
        return [str(item) for item in value]
    return value


@neko_plugin
class CatgirlSeiyuuPlugin(NekoPluginBase):
    """配音模式主控：模式机 + 轮询循环 + 判定门 + 播报排程。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cfg: dict[str, dict[str, Any]] = copy.deepcopy(DEFAULTS)
        self._mode = ModeMachine()
        self._gate = LineGate(GateConfig())
        self._ocr = OcrService(ctx.logger, plugin_id=PLUGIN_ID)
        self._host = HostSpeakClient(ctx.logger, game_type=PLUGIN_ID)

        self._loop_task: asyncio.Task | None = None
        self._speak_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=3)

        self._target_hwnd = 0
        self._target_title = ""
        self._current_line = ""
        self._last_error = ""
        self._spoken = 0
        self._last_mode_hint = ""

    # ==================================================================
    # 配置
    # ==================================================================

    async def _load_config(self) -> dict[str, dict[str, Any]]:
        """三层合并：DEFAULTS < plugin.toml 各段(metadata) < 运行时 config。"""

        cfg = copy.deepcopy(DEFAULTS)
        meta = self.metadata or {}
        if isinstance(meta, dict):
            for section, values in cfg.items():
                manifest = meta.get(section)
                if isinstance(manifest, dict):
                    for key in values:
                        if key in manifest:
                            values[key] = manifest[key]
        getter = getattr(getattr(self, "config", None), "get", None)
        if getter is not None:
            for section, values in cfg.items():
                for key in values:
                    try:
                        value = await getter(f"{section}.{key}")
                    except Exception:  # noqa: BLE001 - 运行时配置缺位按默认
                        continue
                    if value is not None:
                        values[key] = value
        # 消毒层：任何来源（手工编辑的 toml / 坏缓存）的非法类型回落默认
        for section, values in cfg.items():
            for key, default in DEFAULTS.get(section, {}).items():
                raw = values.get(key, default)
                fixed = _coerce_value(default, raw)
                if fixed != raw:
                    self.logger.warning(
                        "config {}.{} 值非法（{!r}），回退出厂默认", section, key, raw
                    )
                values[key] = fixed
        return cfg

    def _apply_config(self) -> None:
        dub = self._cfg["dub"]
        self._gate.configure(GateConfig.from_settings(dub))
        ocr = self._cfg["ocr"]
        self._ocr.configure(
            engine_type=str(ocr.get("engine_type", "")),
            lang_type=str(ocr.get("lang_type", "")),
            model_type=str(ocr.get("model_type", "")),
            ocr_version=str(ocr.get("ocr_version", "")),
        )
        self._host.configure(
            port=int(dub.get("main_server_port", 0) or 0),
            game_type=str(dub.get("speak_game_type", PLUGIN_ID)),
        )
        mode = str(dub.get("capture_mode", "auto")).strip().lower()
        if mode not in capture.CAPTURE_MODES:
            self.logger.warning("capture_mode 非法（{}），按 auto 执行", mode)
            mode = "auto"
        capture.set_capture_mode(mode)

    # ==================================================================
    # 生命周期
    # ==================================================================

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._cfg = await self._load_config()
        self._apply_config()
        # fail-closed：上次运行态只提示、不自动重开（隐私/打扰考虑）
        try:
            saved = unwrap_or(await self.store.get("last_mode"), None)
            if isinstance(saved, dict) and saved.get("mode") == "running":
                # 快照里只发码，面板按 panel.hint.lastMode 翻译展示
                self._last_mode_hint = "last_mode_not_resumed"
        except Exception:  # noqa: BLE001 - store 不可用不影响启动
            pass
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._dub_loop())
        self.logger.info("猫娘声优就绪（模式={}）", self._mode.mode)
        return Ok({"status": "ready", "mode": self._mode.mode})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
        self._drain_queue()
        self._ocr.close()
        try:
            await self.store.set("last_mode", {"mode": self._mode.mode})
        except Exception:  # noqa: BLE001
            pass
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        self._cfg = await self._load_config()
        self._apply_config()
        return Ok({"status": "reconfigured"})

    # ==================================================================
    # 轮询循环：截屏 → OCR → 判定 → 入队；播报 worker 串行消费
    # ==================================================================

    def _tick_interval_s(self) -> float:
        dub = self._cfg["dub"]
        base = float(dub.get("poll_interval_ms", 900)) / 1000.0
        if bool(dub.get("slow_poll_when_unfocused", True)) and self._target_hwnd:
            if not capture.is_foreground(self._target_hwnd):
                base = max(base, float(dub.get("idle_poll_interval_ms", 2500)) / 1000.0)
        return max(0.2, base)

    async def _dub_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._tick_interval_s())
                if not self._mode.is_running():
                    continue
                self._supervise_worker()
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 循环永不因单帧异常死亡
                self.logger.warning("dub tick error: {}", exc)
                await asyncio.sleep(1.0)

    async def _tick_once(self) -> None:
        if not capture.supported():
            self._pause_with_error(capture.platform_reason())
            return
        if not capture.is_window_valid(self._target_hwnd):
            self._pause_with_error("target_window_gone")
            return
        if await asyncio.to_thread(capture.is_minimized, self._target_hwnd):
            # 最小化窗没有可截的内容（PrintWindow 出旧帧/垃圾，桌面矩形是
            # 哨兵坐标）：暂停而不是静默吃帧，还原后面板/语音「继续配音」即接。
            self._pause_with_error("target_minimized")
            return
        try:
            frame = await asyncio.to_thread(capture.grab_window, self._target_hwnd)
            frame = await asyncio.to_thread(capture.crop_region, frame, self._cfg["region"])
            if await asyncio.to_thread(capture.looks_black, frame):
                self._pause_with_error("black_frame: 请把游戏切到窗口化/无边框窗口")
                return
            text = await asyncio.to_thread(self._ocr.extract, frame)
        except RuntimeError as exc:
            self._pause_with_error(str(exc))
            return
        self._last_error = ""
        line = self._gate.consider(text)
        if line:
            self._enqueue(line)

    def _pause_with_error(self, reason: object) -> None:
        """暂停并记录：状态快照只存契约码，中文细节进日志与 HUD（豁免面）。"""

        code = _stable_code(reason)
        if self._last_error == code:
            return
        self._last_error = code
        self._mode.pause("error")
        self._drain_queue()
        self.logger.warning("配音暂停：{}（detail: {}）", code, reason)
        self._push_hud("配音模式已暂停：" + _HUD_PAUSE_HINTS.get(code, code))

    def _enqueue(self, line: str) -> None:
        try:
            self._queue.put_nowait(line)
        except asyncio.QueueFull:
            # 画面已翻页：丢最旧未播的，播最新的（宁播新句不播过期句）
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _speak_worker(self) -> None:
        """串行播报协程：一句播完（宿主同步回执）才取下一句。

        每句开工前重读 self._cfg["dub"]：update_settings 立即生效。
        曾因在循环外快照一次配置，运行中改 lanlan_name/mirror_text/超时
        全部无效，直到下次 stop/start。
        """

        while True:
            line = await self._queue.get()
            if not self._mode.is_running():
                continue
            dub = self._cfg["dub"]
            self._current_line = line
            try:
                result = await self._host.speak(
                    line,
                    lanlan_name=str(dub.get("lanlan_name", "") or ""),
                    wait_for_completion=True,
                    mirror_text=bool(dub.get("mirror_text", True)),
                    timeout=float(dub.get("speak_timeout_s", 120)),
                )
            except Exception as exc:  # noqa: BLE001
                self._current_line = ""
                self._pause_with_error(f"speak_failed:{exc!r}")
                continue
            self._current_line = ""
            if result.get("ok") is True:
                self._spoken += 1
                self._last_error = ""
                continue
            reason = str(result.get("reason") or "unknown")
            if reason in ("busy", "route_owned_by_other_game", "game_route_inactive"):
                # 与内置小游戏等争抢语音槽：让路（DESIGN_BRIEF §7）
                self._mode.pause("conflict")
                self._drain_queue()
                self._push_hud("有别的活动占用了语音通道，配音已暂停；结束后可在面板继续")
            else:
                self._pause_with_error(f"speak_rejected:{reason}")

    async def _speak_now(self, line: str) -> None:
        """手动投喂/测试：不依赖轮询模式的即时播报。"""

        dub = self._cfg["dub"]
        result = await self._host.speak(
            line,
            lanlan_name=str(dub.get("lanlan_name", "") or ""),
            wait_for_completion=True,
            mirror_text=bool(dub.get("mirror_text", True)),
            timeout=float(dub.get("speak_timeout_s", 120)),
        )
        if result.get("ok") is not True:
            raise RuntimeError(f"speak_rejected:{result.get('reason')}")
        self._spoken += 1

    # ==================================================================
    # 消息联动：主人开口 → 配音让位
    # ==================================================================

    @message(id="seiyuu_user_chat", source="chat")
    async def on_user_chat(self, text: str = "", **kw):
        if not self._mode.is_running():
            return Ok({"paused": False})
        if not bool(self._cfg["dub"].get("pause_on_user_message", True)):
            return Ok({"paused": False})
        stripped = (text or "").strip()
        if not stripped or stripped == self._current_line.strip():
            # 空消息或被镜射回流的台词本身：不让位
            return Ok({"paused": False})
        # 只认真正来自主人的输入；角色自己的消息（镜射/播报回流）不触发
        role = str(kw.get("role") or kw.get("sender") or "").lower()
        if role and role not in ("user", "master", "玩家", "主人"):
            return Ok({"paused": False})
        self._mode.pause("user")
        self._drain_queue()
        self._push_hud("主人说话了，配音已自动暂停")
        return Ok({"paused": True})

    # ==================================================================
    # UI 上下文与动作
    # ==================================================================

    @ui.context(id="dubbing")
    async def dubbing_context(self):
        return self._snapshot()

    def _snapshot(self) -> JsonObject:
        dub = self._cfg["dub"]
        return {
            "mode": self._mode.mode,
            "paused_reason": self._mode.paused_reason,
            "target": {
                "hwnd": self._target_hwnd,
                "title": self._target_title,
                "process": capture.process_name(self._target_hwnd) if self._target_hwnd else "",
                "minimized": bool(self._target_hwnd) and capture.is_minimized(self._target_hwnd),
            },
            "focus": bool(self._target_hwnd) and capture.is_foreground(self._target_hwnd),
            "capture_supported": capture.supported(),
            "current_line": self._current_line,
            "queue_size": self._queue.qsize(),
            "spoken": self._spoken,
            "last_error": self._last_error,
            "last_mode_hint": self._last_mode_hint,
            "region": dict(self._cfg["region"]),
            "settings": {
                "poll_interval_ms": dub.get("poll_interval_ms"),
                "stable_frames": dub.get("stable_frames"),
                "punctuation_priority": dub.get("punctuation_priority"),
                "dub_protagonist": dub.get("dub_protagonist"),
                "dub_monologue": dub.get("dub_monologue"),
                "pause_on_user_message": dub.get("pause_on_user_message"),
                "slow_poll_when_unfocused": dub.get("slow_poll_when_unfocused"),
                "protagonist_names": dub.get("protagonist_names"),
                "ui_words": dub.get("ui_words"),
                "capture_mode": dub.get("capture_mode", "auto"),
            },
            "skipped": list(self._gate.skipped),
            "ocr": self._ocr.status(),
        }

    def _push_hud(self, text: str) -> None:
        try:
            self.push_message(
                visibility=["hud"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": text}],
                source=PLUGIN_ID,
            )
        except Exception:  # noqa: BLE001 - HUD 失败不影响主流程
            pass

    # ==================================================================
    # 运行时入口（面板动作 / 跨插件调用）
    # ==================================================================

    # 错误文案契约：入口只发 PANEL_ERROR_CODES 码；面板侧由 errorText() 按
    # panel.errors.* 翻译，LLM 侧用 _LLM_HINTS（模型面向，中文豁免）。

    def _resolve_target_sync(self, hwnd: int, title: str) -> tuple[int, str, str]:
        """解析配音目标，返回 (hwnd, title, err_reason)；err_reason 非空即失败。

        纪律：显式指定的 hwnd/title 解析失败时**必须报错**，不再静默回退
        前台——旧行为在面板选中失探窗口后把宿主自己的聊天窗口喂进 OCR，
        形成「朗读自己」的回声回路。仅当两者都未指定（llm 渠道默认开启）
        才取前台窗口，且默认排除命中宿主标题关键字的窗口。
        """

        if hwnd:
            if capture.is_window_valid(int(hwnd)):
                return int(hwnd), capture.window_title(int(hwnd)), ""
            return 0, "", "invalid_hwnd"
        if title:
            for win in capture.list_windows():
                if win.get("title") == title:
                    return int(win["hwnd"]), str(win["title"]), ""
            return 0, "", "title_not_found"
        info = capture.foreground_window_info()
        fg_hwnd = int(info.get("hwnd") or 0)
        fg_title = str(info.get("title") or "")
        dub = self._cfg["dub"]
        if fg_hwnd and bool(dub.get("exclude_host_window", True)):
            keywords = [str(k) for k in dub.get("host_window_keywords", []) or [] if str(k)]
            lowered = fg_title.casefold()
            for keyword in keywords:
                if keyword.casefold() in lowered:
                    return 0, "", "target_is_self"
        return fg_hwnd, fg_title, ""

    @ui.action(label=tr("actions.start", default="Start dubbing"), tone="primary")
    @plugin_entry(
        id="dub_start",
        name=tr("entries.start.name", default="开始配音"),
        description="开始配音模式：朗读目标窗口（默认当前前台窗口）对话框区域的台词。",
        input_schema={
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "default": 0, "description": "目标窗口句柄（0=当前前台）"},
                "title": {"type": "string", "default": "", "description": "按标题匹配目标窗口"},
            },
        },
        llm_result_fields=["mode", "target"],
    )
    async def dub_start(self, hwnd: int = 0, title: str = "", **_):
        if not capture.supported():
            return Err(SdkError("capture_unsupported"))
        # 纯聊天控制兼容：无显式目标 + 处于暂停 + 原目标窗口仍存活 → 视同
        # 「继续」。否则 LLM 把「继续配音」翻成 start 时会重新解析前台——
        # 前台是主人正在打字的聊天窗，必撞 target_is_self 护栏而报错。
        # 要换目标请显式传 hwnd/title（面板选窗口后就会传）。
        if not hwnd and not (title or "") and self._mode.mode == MODE_PAUSED:
            if capture.is_window_valid(self._target_hwnd):
                return await self.dub_resume()
        resolved_hwnd, resolved_title, err_reason = await asyncio.to_thread(
            self._resolve_target_sync, int(hwnd or 0), str(title or "")
        )
        if not resolved_hwnd:
            return Err(SdkError(_stable_code(err_reason, "no_window")))
        if await asyncio.to_thread(capture.is_minimized, resolved_hwnd):
            return Err(SdkError("target_minimized"))
        self._target_hwnd = resolved_hwnd
        self._target_title = resolved_title
        self._gate.reset()
        self._last_error = ""
        self._last_mode_hint = ""
        self._drain_queue()
        self._mode.start()
        self._ensure_worker()
        try:
            await self.store.set("last_mode", {"mode": "running"})
        except Exception:  # noqa: BLE001
            pass
        self._push_hud(f"配音模式已开启：朗读「{resolved_title}」")
        return Ok({"mode": "running", "target": resolved_title})

    def _ensure_worker(self) -> None:
        if self._speak_task is None or self._speak_task.done():
            self._speak_task = asyncio.create_task(self._speak_worker())

    def _supervise_worker(self) -> None:
        """配音运行中监管播报 worker：意外死亡 → 记日志并重启。

        没有这一层时 worker 崩溃 = 队列只进不出，配音静默卡死且面板
        看不出异常（状态仍显示 running）。
        """

        task = self._speak_task
        if task is not None and task.done() and not task.cancelled():
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc is not None:
                self.logger.warning("speak worker 意外终止，已重启：{}", exc)
        self._ensure_worker()

    @ui.action(label=tr("actions.stop", default="Stop dubbing"))
    @plugin_entry(id="dub_stop", name=tr("entries.stop.name", default="停止配音"), description="停止配音模式并清空待播队列。")
    async def dub_stop(self, **_):
        self._mode.stop()
        self._gate.reset()
        self._drain_queue()
        return Ok({"mode": "off"})

    @ui.action(label=tr("actions.pause", default="Pause dubbing"))
    @plugin_entry(id="dub_pause", name=tr("entries.pause.name", default="暂停配音"), description="暂停配音模式（保留区域与去重记忆）。")
    async def dub_pause(self, **_):
        if self._mode.mode == MODE_PAUSED:
            # pause_on_user_message 可能已抢先暂停（聊天说「暂停」与自动让位的
            # 竞态）：幂等成功，不再回「当前不在配音中」误导主人。
            return Ok({"mode": MODE_PAUSED, "paused_reason": self._mode.paused_reason})
        if not self._mode.pause("manual"):
            return Err(SdkError("not_running"))
        self._drain_queue()
        return Ok({"mode": self._mode.mode, "paused_reason": self._mode.paused_reason})

    @ui.action(label=tr("actions.resume", default="Resume dubbing"))
    @plugin_entry(id="dub_resume", name=tr("entries.resume.name", default="继续配音"), description="从暂停恢复配音模式。")
    async def dub_resume(self, **_):
        if self._mode.mode == "off":
            return Err(SdkError("not_started"))
        if not capture.is_window_valid(self._target_hwnd):
            return Err(SdkError("invalid_hwnd"))
        self._mode.resume()
        self._last_error = ""
        self._ensure_worker()
        return Ok({"mode": "running", "target": self._target_title})

    @plugin_entry(
        id="dub_status",
        name=tr("entries.status.name", default="配音状态"),
        description="查询配音模式当前状态。",
        metadata={"result_kind": "event"},
        llm_result_fields=["mode"],
    )
    async def dub_status(self, **_):
        snap = await asyncio.to_thread(self._snapshot)
        return Ok({k: snap[k] for k in ("mode", "paused_reason", "last_error", "spoken", "current_line")})

    @ui.action(label=tr("actions.windows", default="List windows"), refresh_context=False)
    @plugin_entry(
        id="dub_windows",
        name=tr("entries.windows.name", default="窗口列表"),
        description="列出可选择的桌面窗口（面板框选目标用）。",
    )
    async def dub_windows(self, **_):
        if not capture.supported():
            return Err(SdkError("capture_unsupported"))
        wins = await asyncio.to_thread(capture.list_windows)
        slim = [
            {k: w.get(k) for k in ("hwnd", "title", "label", "process", "pid", "minimized", "focused", "is_self")}
            for w in wins[:200]
        ]
        return Ok({
            "windows": slim,
            "total": len(wins),
            "summary": f"共 {len(wins)} 个候选窗口（前台最前，含最小化/本程序标记）",
        })

    @ui.action(label=tr("actions.preview", default="Capture preview"), refresh_context=False)
    @plugin_entry(
        id="capture_preview",
        name=tr("entries.preview.name", default="截取预览"),
        description="截取目标窗口当前画面（缩略 JPEG，供面板框选对话框区域）。",
        input_schema={"type": "object", "properties": {"hwnd": {"type": "integer", "default": 0}}},
    )
    async def capture_preview(self, hwnd: int = 0, **_):
        if not capture.supported():
            return Err(SdkError("capture_unsupported"))
        target, title, err_reason = await asyncio.to_thread(self._resolve_target_sync, int(hwnd or 0), "")
        if not target:
            return Err(SdkError(_stable_code(err_reason, "no_window")))
        try:
            img = await asyncio.to_thread(capture.grab_window, target)
        except RuntimeError as exc:
            self.logger.warning("capture_preview failed: {}", exc)
            return Err(SdkError(_stable_code(exc, "capture_failed")))
        if await asyncio.to_thread(capture.looks_black, img):
            return Err(SdkError("black_frame"))
        meta = await asyncio.to_thread(capture.to_jpeg_b64, img)
        return Ok({**meta, "title": title, "summary": "已截取预览画面"})

    @ui.action(label=tr("actions.region", default="Save region"), refresh_context=False)
    @plugin_entry(
        id="set_region",
        name=tr("entries.region.name", default="保存区域"),
        description="保存对话框区域（相对目标窗口的比例坐标 0-1）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "w": {"type": "number"},
                "h": {"type": "number"},
            },
            "required": ["x", "y", "w", "h"],
        },
    )
    async def set_region(self, x: float = 0.0, y: float = 0.0, w: float = 0.0, h: float = 0.0, **_):
        try:
            x, y, w, h = float(x), float(y), float(w), float(h)
        except Exception:  # noqa: BLE001
            return Err(SdkError("region_not_numeric"))
        if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.02 <= w <= 1.0 - x and 0.02 <= h <= 1.0 - y):
            return Err(SdkError("region_out_of_range"))
        region = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
        self._cfg["region"] = region
        try:
            await self.config.update({"region": region})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("region persist failed: {}", exc)
        return Ok({"region": region})

    @ui.action(label=tr("actions.feed", default="Feed one line"), refresh_context=False)
    @plugin_entry(
        id="feed_line",
        name=tr("entries.feed.name", default="手动投喂"),
        description="把一行台词直接交给猫娘朗读（任何模式可用，绕过 OCR 判定）。",
        input_schema={"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"]},
    )
    async def feed_line(self, line: str = "", **_):
        line = (line or "").strip()
        if not line:
            return Err(SdkError("empty_line"))
        if self._mode.is_running():
            self._ensure_worker()
            self._enqueue(line)
            return Ok({"queued": True})
        try:
            await self._speak_now(line)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("feed_line speak failed: {}", exc)
            return Err(SdkError("speak_failed"))
        return Ok({"spoken": True})

    @ui.action(label=tr("actions.settings", default="Save settings"), refresh_context=True)
    @plugin_entry(
        id="update_settings",
        name=tr("entries.settings.name", default="更新设置"),
        description="更新 [dub] 段设置（只接受已知键，写入运行时配置并即时生效）。",
        input_schema={"type": "object", "properties": {"patch": {"type": "object"}}, "required": ["patch"]},
    )
    async def update_settings(self, patch: JsonObject | None = None, **_):
        if not isinstance(patch, dict) or not patch:
            return Err(SdkError("patch_invalid"))
        clean: JsonObject = {}
        for key, value in patch.items():
            expected = _DUB_PATCH_TYPES.get(str(key))
            if expected is None:
                self.logger.warning("update_settings unknown key: {}", key)
                return Err(SdkError("unknown_setting"))
            if expected is bool:
                clean[key] = bool(value)
            elif expected in (int, float):
                try:
                    clean[key] = expected(value)
                except Exception:  # noqa: BLE001
                    self.logger.warning("update_settings bad type for {}: {!r}", key, value)
                    return Err(SdkError("setting_type_invalid"))
            elif expected is list:
                if not isinstance(value, list):
                    self.logger.warning("update_settings bad list type for {}: {!r}", key, value)
                    return Err(SdkError("setting_type_invalid"))
                clean[key] = [str(v) for v in value]
            elif key == "capture_mode":
                value = str(value).strip().lower()
                if value not in capture.CAPTURE_MODES:
                    return Err(SdkError("capture_mode_invalid"))
                clean[key] = value
            else:
                clean[key] = str(value)
        merged = {**self._cfg["dub"], **clean}
        try:
            await self.config.update({"dub": clean})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("settings persist failed: {}", exc)
        self._cfg["dub"] = merged
        self._apply_config()
        return Ok({"updated": list(clean.keys())})

    @ui.action(label=tr("actions.testSpeak", default="Test speak"), refresh_context=False)
    @plugin_entry(
        id="test_speak",
        name=tr("entries.testSpeak.name", default="试听播报"),
        description="立即用宿主 TTS 播报一句测试文本（验证配音通道）。",
        input_schema={"type": "object", "properties": {"line": {"type": "string"}}},
        metadata={"result_kind": "event"},
    )
    async def test_speak(self, line: str = "配音通道正常。", **_):
        line = (line or "").strip()
        if not line:
            return Err(SdkError("empty_line"))
        try:
            await self._speak_now(line)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("test_speak failed: {}", exc)
            return Err(SdkError("speak_failed"))
        return Ok({"spoken": True, "summary": "播报通道正常"})

    @ui.action(label=tr("actions.testOcr", default="Test OCR"), refresh_context=False)
    @plugin_entry(
        id="test_capture",
        name=tr("entries.testOcr.name", default="测试识别"),
        description="对当前目标窗口的选定区域截屏并 OCR，返回识别文本（调试框选）。",
        input_schema={"type": "object", "properties": {"hwnd": {"type": "integer", "default": 0}}},
    )
    async def test_capture(self, hwnd: int = 0, **_):
        if not capture.supported():
            return Err(SdkError("capture_unsupported"))
        target, _title, err_reason = await asyncio.to_thread(self._resolve_target_sync, int(hwnd or 0), "")
        if not target:
            return Err(SdkError(_stable_code(err_reason, "no_window")))
        try:
            img = await asyncio.to_thread(capture.grab_window, target)
            img = await asyncio.to_thread(capture.crop_region, img, self._cfg["region"])
            text = await asyncio.to_thread(self._ocr.extract, img)
        except RuntimeError as exc:
            self.logger.warning("test_capture failed: {}", exc)
            return Err(SdkError(_stable_code(exc, "capture_failed")))
        return Ok({"text": text, "summary": f"识别到 {len(text)} 字符"})

    @ui.action(label=tr("actions.ocrModels", default="Download OCR models"), refresh_context=True)
    @plugin_entry(
        id="ocr_download",
        name=tr("entries.ocrModels.name", default="OCR 模型下载"),
        description="按当前语言/版本下载 RapidOCR 模型（默认中文 v4 为内置，通常无需点击下载）。",
        timeout=300.0,
    )
    async def ocr_download(self, **_):
        try:
            result = await self._ocr.download_models(timeout_seconds=240.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("ocr model download failed: {}", exc)
            return Err(SdkError("ocr_download_failed"))
        return Ok({"downloaded": True, "summary": str(result.get("status") or "done"), "detail": {k: v for k, v in result.items() if isinstance(v, (str, int, float, bool))}})

    # ==================================================================
    # LLM 工具（主 AI 听懂"打开/结束配音模式"）
    # ==================================================================

    @llm_tool(
        name="catgirl_seiyuu_start",
        description=(
            "打开配音模式：开始逐字朗读目标窗口的游戏台词。主人说「打开配音模式/"
            "帮我配音/朗读游戏台词」时调用。若只是从暂停恢复，优先用 catgirl_seiyuu_resume。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def tool_start(self, **_):
        result = await self.dub_start()
        if isinstance(result, Err):
            return {"ok": False, "note": _llm_note(result.error)}
        return {"ok": True, "mode": "running", "target": result.value.get("target", ""),
                "note": "配音已开始，最多确认一句，不要解说不要报幕"}

    @llm_tool(
        name="catgirl_seiyuu_stop",
        description="结束配音模式：停止朗读并清空队列。主人说「结束配音/不用念了」时调用。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_stop(self, **_):
        await self.dub_stop()
        return {"ok": True, "mode": "off", "note": "配音已停止"}

    @llm_tool(
        name="catgirl_seiyuu_pause",
        description="暂停配音模式（恢复请用 catgirl_seiyuu_resume，彻底退出用 catgirl_seiyuu_stop）。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_pause(self, **_):
        result = await self.dub_pause()
        if isinstance(result, Err):
            return {"ok": False, "note": _llm_note(result.error)}
        return {"ok": True, "mode": "paused", "note": "配音已暂停"}

    @llm_tool(
        name="catgirl_seiyuu_resume",
        description=(
            "继续配音模式：接着暂停前的目标窗口恢复朗读（不重新选窗）。"
            "主人说「继续配音/恢复配音/接着念/继续读吧/你接着念」时调用。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def tool_resume(self, **_):
        result = await self.dub_resume()
        if isinstance(result, Err):
            return {"ok": False, "note": _llm_note(result.error)}
        return {"ok": True, "mode": "running", "target": result.value.get("target", ""),
                "note": "配音已继续，最多确认一句，不要解说不要报幕"}

    @llm_tool(
        name="catgirl_seiyuu_status",
        description="查询配音模式状态（off/running/paused、已播句数、异常）。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_status(self, **_):
        result = await self.dub_status()
        value = unwrap_or(result, {}) or {}
        return {"mode": value.get("mode", "off"), "spoken": value.get("spoken", 0),
                "error": value.get("last_error", "")}
