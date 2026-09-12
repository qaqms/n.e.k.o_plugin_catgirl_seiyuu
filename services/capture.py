"""窗口枚举与截屏（v0.1：Windows 桌面 OCR 通道）。

策略（DESIGN_BRIEF §4.1，零封号风险）：
- 只读查询 user32（EnumWindows/GetWindowRect/GetForegroundWindow），
  绝不注入、绝不 hook 游戏进程；
- 取图像用桌面区域截屏（PIL ImageGrab，窗口当前矩形）——DirectX 独占
  全屏会截到黑帧，检测后给出可读的错误提示（面板/状态里展示）；
- 非 Windows 平台返回结构化 Err，不影响插件其余功能加载。

所有阻塞调用（ctypes / ImageGrab）由调用方经 asyncio.to_thread 卸载。
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"

_DPI_AWARE_DONE = False


def ensure_dpi_aware() -> None:
    """把本进程钉为 DPI aware（per-monitor v2 优先，逐级降级）。

    未声明 awareness 的进程里 GetWindowRect 拿到的是系统虚拟化的**逻辑**
    坐标，而 Pillow 的 ImageGrab 会自行把进程切成 DPI aware 后按**物理**
    像素拓图——缩放慢率 >100% 时两套坐标系先后不一致，截到的区域和窗口
    错位。首次取图前显式统一，后续 rect/抓取恒为物理像素。best-effort。
    """

    global _DPI_AWARE_DONE
    if _DPI_AWARE_DONE or not IS_WINDOWS:
        return
    _DPI_AWARE_DONE = True
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:  # Win10 1703+：PROCESS_PER_MONITOR_DPI_AWARE_V2
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # type: ignore[attr-defined]
                return
        except Exception:  # noqa: BLE001
            pass
        try:  # Win8.1+
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            pass
        user32.SetProcessDPIAware()  # type: ignore[attr-defined]  # Vista+
    except Exception:  # noqa: BLE001 - 失败保持系统默认行为
        pass


def supported() -> bool:
    return IS_WINDOWS


def platform_reason() -> str:
    return "" if IS_WINDOWS else f"unsupported_platform:{sys.platform}"


# ---------------------------------------------------------------- ctypes 封装


def _user32() -> Any:
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]


def get_foreground_hwnd() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(_user32().GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0


def _window_text(hwnd: int) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(512)
    try:
        _user32().GetWindowTextW(hwnd, buf, 512)
    except Exception:  # noqa: BLE001
        return ""
    return buf.value or ""


def _window_pid(hwnd: int) -> int:
    import ctypes

    pid = ctypes.c_ulong(0)
    try:
        _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:  # noqa: BLE001
        return 0
    return int(pid.value or 0)


def window_title(hwnd: int) -> str:
    """窗口标题（公开封装，供插件层使用）。"""

    return _window_text(int(hwnd or 0))


def is_window_valid(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().IsWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """返回 (left, top, right, bottom)；窗口不存在/最小化/无尺寸 → None。"""

    if not IS_WINDOWS or not hwnd:
        return None
    ensure_dpi_aware()
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    try:
        user32 = _user32()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        # 最小化窗口矩形是 (-32000, -32000) 哨兵值
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right - left < 8 or bottom - top < 8 or left <= -32000:
            return None
        return (left, top, right, bottom)
    except Exception:  # noqa: BLE001
        return None


def _window_root(hwnd: int) -> int:
    """GetAncestor GA_ROOT=3：把子窗口归位到顶层窗口再比较前台。"""

    if not IS_WINDOWS:
        return 0
    try:
        return int(_user32().GetAncestor(hwnd, 3))
    except Exception:  # noqa: BLE001
        return hwnd


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    fg = get_foreground_hwnd()
    return bool(fg) and (_window_root(fg) == _window_root(hwnd) or fg == hwnd)


def list_windows() -> list[dict[str, Any]]:
    """可见 + 有标题 + 尺寸达标的顶层窗口（面板目标选择用）。"""

    if not IS_WINDOWS:
        return []
    ensure_dpi_aware()
    import ctypes

    results: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        hwnd = int(hwnd or 0)
        try:
            user32 = _user32()
            if not user32.IsWindowVisible(hwnd):
                return True
            rect = get_window_rect(hwnd)
            if rect is None:
                return True
            title = _window_text(hwnd)
            if not title:
                return True
            results.append({"hwnd": hwnd, "title": title, "pid": _window_pid(hwnd), "rect": list(rect)})
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        _user32().EnumWindows(_cb, 0)
    except Exception:  # noqa: BLE001
        return []
    return results


def foreground_window_info() -> dict[str, Any]:
    hwnd = get_foreground_hwnd()
    if not hwnd:
        return {"hwnd": 0, "title": "", "pid": 0}
    return {"hwnd": hwnd, "title": _window_text(hwnd), "pid": _window_pid(hwnd)}


# ---------------------------------------------------------------- 取图与编码


def grab_window(hwnd: int) -> Any:
    """按窗口当前矩形截屏，返回 PIL.Image（RGB）。失败抛 RuntimeError。"""

    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("window_rect_unavailable")
    return grab_rect(rect)


def grab_rect(rect: tuple[int, int, int, int]) -> Any:
    ensure_dpi_aware()
    try:
        from PIL import ImageGrab
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"pillow_unavailable:{exc!r}") from exc

    left, top, right, bottom = rect
    try:
        return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"grab_failed:{exc!r}") from exc


def crop_region(img: Any, region: dict[str, float]) -> Any:
    """按比例区域裁剪；坐标夹紧到图内，退化区域原样返回。"""

    w, h = img.size
    try:
        x = float(region.get("x", 0.0))
        y = float(region.get("y", 0.0))
        rw = float(region.get("w", 1.0))
        rh = float(region.get("h", 1.0))
    except Exception:  # noqa: BLE001
        return img
    x0 = max(0, min(w - 2, int(x * w)))
    y0 = max(0, min(h - 2, int(y * h)))
    x1 = max(x0 + 2, min(w, int((x + rw) * w)))
    y1 = max(y0 + 2, min(h, int((y + rh) * h)))
    if (x1 - x0) >= w - 1 and (y1 - y0) >= h - 1:
        return img
    return img.crop((x0, y0, x1, y1))


def looks_black(img: Any) -> bool:
    """黑帧检测：DirectX 独占全屏 / 受 DRM 保护表面会截出纯黑。"""

    try:
        small = img.convert("L").resize((32, 32))
        data = list(small.getdata())
        return (sum(data) / max(1, len(data))) < 6.0
    except Exception:  # noqa: BLE001
        return False


def to_jpeg_b64(img: Any, *, max_edge: int = 1280, quality: int = 72) -> dict[str, Any]:
    """缩到最长边 max_edge 的 JPEG base64（面板预览用，不进模型上下文）。"""

    w, h = img.size
    scale = min(1.0, float(max_edge) / max(w, h)) if max(w, h) else 1.0
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return {
        "image_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "width": img.size[0],
        "height": img.size[1],
    }
