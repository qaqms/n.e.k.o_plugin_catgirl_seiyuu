"""配音模式状态机。

纯 stdlib 实现（不依赖 SDK），方便单测；插件层负责在迁移成功后落 Store。
语义：
  off     —— 循环空转，不截屏不 OCR 不播报（安装后的出厂态，fail-closed）
  running —— 轮询截屏 → OCR → 判定 → 排队播报
  paused  —— 保留目标窗口/区域/去重记忆，但停止采集与播报；
             reason 记录"为什么暂停"（user=主人说话 / conflict=语音通道争抢 /
             manual=手动暂停 / focus=目标失焦超时）
"""

from __future__ import annotations

MODE_OFF = "off"
MODE_RUNNING = "running"
MODE_PAUSED = "paused"

VALID_MODES = (MODE_OFF, MODE_RUNNING, MODE_PAUSED)


class ModeMachine:
    """极简三态机。所有迁移都返回 bool（False = 非法迁移，状态不变）。"""

    def __init__(self) -> None:
        self._mode = MODE_OFF
        self._reason = ""

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def paused_reason(self) -> str:
        return self._reason

    def is_running(self) -> bool:
        return self._mode == MODE_RUNNING

    def start(self) -> bool:
        # off/paused → running（running 时重复 start 视为幂等成功）
        if self._mode == MODE_RUNNING:
            return True
        self._mode = MODE_RUNNING
        self._reason = ""
        return True

    def pause(self, reason: str = "manual") -> bool:
        if self._mode != MODE_RUNNING:
            return False
        self._mode = MODE_PAUSED
        self._reason = reason
        return True

    def resume(self) -> bool:
        if self._mode != MODE_PAUSED:
            return False
        self._mode = MODE_RUNNING
        self._reason = ""
        return True

    def stop(self) -> bool:
        self._mode = MODE_OFF
        self._reason = ""
        return True
