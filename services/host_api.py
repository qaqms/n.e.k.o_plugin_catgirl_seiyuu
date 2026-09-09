"""宿主直连：POST /api/game/<type>/speak（B 层语音管线）。

沿用 forever_companion 成熟先例：stdlib urllib 同步请求 + asyncio.to_thread，
端口解析 `from config import MAIN_SERVER_PORT`（缺省 48911），
GET /health 的 instance_id 作 X-CSRF-Token（best-effort）。

`wait_for_audio_completion=true` 时宿主同步到播完才返回——插件侧的
"一句播完再播下一句"完全依赖这个语义，不需要自建播放队列。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

JsonObject = dict[str, Any]

DEFAULT_MAIN_PORT = 48911
DEFAULT_GAME_TYPE = "catgirl_seiyuu"


class HostSpeakClient:
    def __init__(self, logger: Any, *, port: int = 0, game_type: str = DEFAULT_GAME_TYPE) -> None:
        self.logger = logger
        self._port = int(port or 0)
        self.game_type = game_type or DEFAULT_GAME_TYPE

    def configure(self, *, port: int = 0, game_type: str = "") -> None:
        self._port = int(port or 0)
        if game_type:
            self.game_type = game_type

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._resolve_port()}"

    def _resolve_port(self) -> int:
        if self._port > 0:
            return self._port
        try:
            from config import MAIN_SERVER_PORT

            return int(MAIN_SERVER_PORT)
        except Exception:  # noqa: BLE001 - 安装态拿不到宿主 config 时用缺省
            return DEFAULT_MAIN_PORT

    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, body: JsonObject | None, timeout: float, headers: dict[str, str]) -> JsonObject:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {"ok": True, "data": payload}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "reason": f"http_{exc.code}", "detail": raw[:300]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "transport_error", "detail": repr(exc)[:200]}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self._resolve_port()}",
        }
        token = self._instance_id()
        if token:
            headers["X-CSRF-Token"] = token
        return headers

    def _instance_id(self) -> str:
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=2.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict):
                return str(payload.get("instance_id") or "")
        except Exception:  # noqa: BLE001 - health 不可达不阻塞播报尝试
            pass
        return ""

    # ------------------------------------------------------------------

    async def speak(
        self,
        line: str,
        *,
        lanlan_name: str = "",
        wait_for_completion: bool = True,
        mirror_text: bool = True,
        interrupt_audio: bool = False,
        timeout: float = 120.0,
    ) -> JsonObject:
        """播报一句台词。返回宿主响应；网络失败包装为 {ok:false, reason}。"""

        body: JsonObject = {
            "line": line,
            "game_type": self.game_type,
            "wait_for_audio_completion": bool(wait_for_completion),
            "mirror_text": bool(mirror_text),
        }
        if lanlan_name:
            body["lanlan_name"] = lanlan_name
        if interrupt_audio:
            body["interrupt_audio"] = True
        # 宿主对 line.strip() 为空返回 missing_line：纯打断（空 line + interrupt）
        # 在 v0.1 不可用，暂停语义 = 队列清空 + 当前句自然播完。
        path = f"/api/game/{self.game_type}/speak"
        return await asyncio.to_thread(self._request, "POST", path, body, timeout, self._headers())
