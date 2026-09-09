"""OCR 服务：复用宿主 `plugin.plugins._shared.rapidocr`（与内置 galgame 通道同一栈）。

- 默认 PP-OCRv4 + ch 模型随 rapidocr-onnxruntime 包内自带，无需下载；
  切换语言/版本时经 download_rapidocr_models 拉取（面板按钮触发）。
- 推理是阻塞调用：调用方负责 asyncio.to_thread 卸载；
  _shared 内部自带跨插件推理锁，这里不再加锁。
- 一切导入失败/缺模型 → 结构化状态（available=False + reason），
  插件其余功能不受影响（fail-soft）。
"""

from __future__ import annotations

from typing import Any

DEFAULT_ENGINE = "onnxruntime"
DEFAULT_LANG = "ch"
DEFAULT_MODEL = "mobile"
DEFAULT_VERSION = "PP-OCRv4"


class OcrService:
    def __init__(self, logger: Any, *, plugin_id: str) -> None:
        self.logger = logger
        self._plugin_id = plugin_id
        self._backend: Any = None
        self._engine = DEFAULT_ENGINE
        self._lang = DEFAULT_LANG
        self._model = DEFAULT_MODEL
        self._version = DEFAULT_VERSION
        self._init_error = ""

    def params(self) -> dict[str, str]:
        return {
            "engine_type": self._engine,
            "lang_type": self._lang,
            "model_type": self._model,
            "ocr_version": self._version,
        }

    def configure(self, *, engine_type: str = "", lang_type: str = "", model_type: str = "", ocr_version: str = "") -> None:
        changed = False
        pairs = {
            "_engine": engine_type or DEFAULT_ENGINE,
            "_lang": lang_type or DEFAULT_LANG,
            "_model": model_type or DEFAULT_MODEL,
            "_version": ocr_version or DEFAULT_VERSION,
        }
        for attr, value in pairs.items():
            if getattr(self, attr) != value:
                setattr(self, attr, value)
                changed = True
        if changed:
            self.close()

    # ------------------------------------------------------------------

    def _install_target(self) -> str:
        try:
            from plugin.plugins._shared.rapidocr._paths import (
                default_rapidocr_install_target_raw,
            )

            return default_rapidocr_install_target_raw(self._plugin_id)
        except Exception:  # noqa: BLE001 - 安装态没有 _shared 时走默认布局解析
            return ""

    def ensure(self) -> bool:
        """惰性初始化后端。返回是否可用；失败原因留在 status()。"""

        if self._backend is not None:
            return True
        self._init_error = ""
        try:
            from plugin.plugins._shared.rapidocr import RapidOcrBackend
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"shared_rapidocr_unavailable:{exc!r}"
            return False
        try:
            self._backend = RapidOcrBackend(
                install_target_dir_raw=self._install_target(),
                engine_type=self._engine,
                lang_type=self._lang,
                model_type=self._model,
                ocr_version=self._version,
                plugin_id=self._plugin_id,
            )
            self._backend.warmup_async(self.logger)
        except Exception as exc:  # noqa: BLE001
            self._backend = None
            self._init_error = f"backend_init_failed:{exc!r}"
            return False
        return True

    def extract(self, image: Any) -> str:
        """阻塞 OCR：PIL.Image → 纯文本（多行 \n 连接）。失败抛 RuntimeError。"""

        if self._backend is None and not self.ensure():
            raise RuntimeError(self._init_error or "ocr_unavailable")
        try:
            text = self._backend.extract_text(image)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"ocr_extract_failed:{exc!r}") from exc
        return str(text or "")

    def status(self) -> dict[str, Any]:
        return {
            "available": self._backend is not None or (not self._init_error),
            "initialized": self._backend is not None,
            "error": self._init_error,
            **self.params(),
        }

    def close(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.close()
        except Exception:  # noqa: BLE001
            pass
        self._backend = None

    # ------------------------------------------------------------------

    async def download_models(self, *, timeout_seconds: float = 180.0) -> dict[str, Any]:
        """按当前 (ocr_version, lang_type) 拉取缺失模型（bundled PP-OCRv4+ch 为 no-op）。"""

        from plugin.plugins._shared.rapidocr import download_rapidocr_models

        result = await download_rapidocr_models(
            logger=self.logger,
            install_target_dir_raw=self._install_target(),
            ocr_version=self._version,
            lang_type=self._lang,
            model_type=self._model,
            timeout_seconds=timeout_seconds,
            plugin_id=self._plugin_id,
        )
        # 模型集变化后重建后端
        self.close()
        return result if isinstance(result, dict) else {"ok": bool(result)}
