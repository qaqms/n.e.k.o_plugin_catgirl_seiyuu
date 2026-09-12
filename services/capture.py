"""窗口枚举与截屏（v0.2：PrintWindow 窗口本体渲染 + 桌面截屏回退）。

策略（DESIGN_BRIEF §4.1，零封号风险不变）：
- 只读查询 user32/gdi32/dwmapi，绝不注入、绝不 hook 游戏进程；
- 取图优先 **PrintWindow(PW_RENDERFULLCONTENT)**：让系统把窗口内容渲染到
  内存位图——不受其它窗口遮挡、不受多屏负坐标影响，是「选一个窗口配音」
  的正确语义；
- PrintWindow 失败或返回黑帧（个别硬件加速交换链不走 GDI 重绘）时自动
  回退桌面区域截屏（PIL ImageGrab，DWM 可见边框坐标）；DirectX 独占全屏
  两条通道都可能截到黑帧，检测后给出可读错误；
- 几何统一：窗口矩形以 **DWMWA_EXTENDED_FRAME_BOUNDS**（真实可见边框）为
  准。Win10+ 的 GetWindowRect 含一圈透明 resize 边框，旧版直接拿它截屏
  会让预览/框选区域与窗口错位，PrintWindow 渲染出的图也按同样的差值裁掉；
- 列表纪律（v0.2）：列「所有玩家可能选的窗口」而不是猜——最小化窗口也
  列出并打标（截不了但能选，恢复后可用），被 DWM 伪装（cloaked，UWP/虚拟
  桌面藏起来的）与零尺寸垃圾窗口剔除；标题为空的窗口退回进程名做标签；
- 非 Windows 平台返回结构化失败，不影响插件其余功能加载。

所有阻塞调用（ctypes / ImageGrab）由调用方经 asyncio.to_thread 卸载。
本模块内的纯逻辑（window_accept / resolve_window_label / looks_black）不碰
ctypes，供单测直接调用。
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"

_DPI_AWARE_DONE = False

# 截屏方式（[dub] capture_mode）：
#   auto   = PrintWindow 优先，黑帧/失败回退桌面截屏（出厂默认）
#   window = 只用 PrintWindow（遮挡免疫，个别游戏会因此报错）
#   screen = 只用桌面区域截屏（v0.1 旧行为，排障对照用）
CAPTURE_MODES = ("auto", "window", "screen")
_capture_mode = "auto"

# PrintWindow 黑帧判定阈值：整窗平均亮度低于此值认为该窗口不走 GDI 重绘。
# 比桌面通道的 6.0 更宽（阈值 2.0 只拦「完全黑」），因为深色 VN 场景里
# 几行白字就能把均值抬到 5-15，误判会白白放弃可用的窗口渲染通道。
_PRINTWINDOW_BLACK_THRESHOLD = 2.0


def set_capture_mode(mode: str) -> str:
    """设置截屏方式；非法值保持当前设置。返回生效值。"""

    global _capture_mode
    if mode in CAPTURE_MODES:
        _capture_mode = mode
    return _capture_mode


def capture_mode() -> str:
    return _capture_mode


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


# ---------------------------------------------------------------- 纯逻辑（可单测）


def window_accept(
    *,
    title: str,
    process: str,
    visible: bool,
    rect_valid: bool,
    iconic: bool,
    cloaked: bool,
) -> str:
    """窗口列表准入裁决：返回拒绝原因（"" = 收录）。

    v0.2 的纪律是「列所有玩家可能选的窗口，让面板去过滤展示」，所以只剔除
    两类截不到/选错的：DWM 伪装窗（cloaked：UWP 挂起、其它虚拟桌面上的窗，
    IsWindowVisible 仍为真但看不见也截不到）与既不可见也非最小化的垃圾窗。
    最小化窗**收录并打标**——旧实现在这里静默丢掉最小化的游戏，玩家刷新
    半天看不到自己的窗口。标题为空的退回进程名，两者皆空才剔除。
    """

    if cloaked:
        return "cloaked"
    if not title and not process:
        return "no_label"
    if iconic:
        return ""
    if not visible:
        return "not_visible"
    if not rect_valid:
        return "no_size"
    return ""


def resolve_window_label(title: str, process: str) -> str:
    """列表标签：标题优先，退回进程名（`xxx.exe - 无标题窗` 的兜底）。"""

    title = (title or "").strip()
    if title:
        return title
    return (process or "").strip()


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


def _process_name(pid: int) -> str:
    """进程 exe 名（QUERY_LIMITED_INFORMATION，跨位数可用）。失败返回 ""。"""

    if not IS_WINDOWS or not pid:
        return ""
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            path = buf.value or ""
            return path.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def _current_pid() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetCurrentProcessId())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0


def window_title(hwnd: int) -> str:
    """窗口标题（公开封装，供插件层使用）。"""

    return _window_text(int(hwnd or 0))


def _is_cloaked(hwnd: int) -> bool:
    """DWMWA_CLOAKED（14）：UWP 挂起窗/其它虚拟桌面窗仍 visible 但不可见。"""

    if not IS_WINDOWS:
        return False
    import ctypes

    try:
        value = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
            int(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value)
        )
        return value.value != 0
    except Exception:  # noqa: BLE001
        return False


class _RECT:
    """ctypes RECT 定义（left/top/right/bottom）。"""

    def __init__(self) -> None:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        self.value = RECT()


def _rect_raw(hwnd: int) -> tuple[int, int, int, int] | None:
    """GetWindowRect 原始物理矩形（含 Win10+ 透明 resize 边框）。"""

    if not IS_WINDOWS or not hwnd:
        return None
    import ctypes

    rect = _RECT().value
    try:
        if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return _clean_rect(rect.left, rect.top, rect.right, rect.bottom)
    except Exception:  # noqa: BLE001
        return None


def _clean_rect(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int] | None:
    """夹紧垃圾矩形：最小化哨兵 (-32000) 与小于 8px 的退化窗一律 None。"""

    if right - left < 8 or bottom - top < 8 or left <= -32000 or top <= -32000:
        return None
    return (left, top, right, bottom)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """返回 (left, top, right, bottom) **可见**矩形；不可得/最小化 → None。

    优先 DWMWA_EXTENDED_FRAME_BOUNDS（9）——真实可见边框。GetWindowRect
    在其外圈还有一层透明 resize 边框（左右各 ~7px、底边更厚），旧版直接
    拿它算区域导致预览框选与窗口错位，这就是「抓取区域偏移」的主因之一。
    """

    if not IS_WINDOWS or not hwnd:
        return None
    ensure_dpi_aware()
    import ctypes

    rect = _RECT().value
    try:
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        if dwm.DwmGetWindowAttribute(
            int(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0:  # S_OK
            cleaned = _clean_rect(rect.left, rect.top, rect.right, rect.bottom)
            if cleaned is not None:
                return cleaned
    except Exception:  # noqa: BLE001
        pass
    return _rect_raw(hwnd)


def is_window_valid(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().IsWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


def is_minimized(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32().IsIconic(hwnd))
    except Exception:  # noqa: BLE001
        return False


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
    """按 **Z 序（前台优先）** 枚举候选目标窗口（面板选择用）。

    v0.2 变更：
    - 旧版从 GetForegroundWindow 开始用 GetWindow(GW_HWNDFIRST) 遍历会丢
      Z 序信息；改为 EnumWindows 本身的原生顺序（系统按 Z 序回调，最前
      的即最靠前的），并给前台窗打 focused 标；
    - 最小化窗收录（iconic 打标），cloaked 剔除；
    - 每行带 process exe 名 / pid / is_self（本进程窗口，如 N.E.K.O 主
      界面——列出但打标，配合 exclude_host_window 的运行时护栏）；
    - 标题为空的窗用进程名兜底标签，不再整窗消失。
    """

    if not IS_WINDOWS:
        return []
    ensure_dpi_aware()
    import ctypes

    results: list[dict[str, Any]] = []
    own_pid = _current_pid()
    fg = get_foreground_hwnd()
    fg_root = _window_root(fg) if fg else 0

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        hwnd = int(hwnd or 0)
        try:
            user32 = _user32()
            iconic = bool(user32.IsIconic(hwnd))
            visible = bool(user32.IsWindowVisible(hwnd))
            pid = _window_pid(hwnd)
            title = _window_text(hwnd)
            cloaked = _is_cloaked(hwnd)
            # 进程名有两个用途：空标题窗的兑底标签 + 面板列展示（同名 exe
            # 多窗口时玩家靠它区分）。OpenProcess 是百量级廉价调用，每窗查
            # 一次；被拒绝的窗多数在更早的条件就短路了。
            process = _process_name(pid)
            row_rect = None if iconic else get_window_rect(hwnd)
            reason = window_accept(
                title=title,
                process=process,
                visible=visible,
                rect_valid=row_rect is not None,
                iconic=iconic,
                cloaked=cloaked,
            )
            if reason:
                return True
            results.append({
                "hwnd": hwnd,
                "title": title,
                "label": resolve_window_label(title, process),
                "process": process,
                "pid": pid,
                "minimized": iconic,
                "focused": bool(fg) and (_window_root(hwnd) == fg_root),
                "is_self": bool(own_pid) and pid == own_pid,
            })
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
        return {"hwnd": 0, "title": "", "label": "", "process": "", "pid": 0}
    pid = _window_pid(hwnd)
    title = _window_text(hwnd)
    process = _process_name(pid)
    return {
        "hwnd": hwnd,
        "title": title,
        "label": resolve_window_label(title, process),
        "process": process,
        "pid": pid,
    }


def process_name(hwnd: int) -> str:
    """目标窗口的进程 exe 名（快照展示用）。"""

    return _process_name(_window_pid(int(hwnd or 0)))


# ---------------------------------------------------------------- 取图与编码


def _print_window(hwnd: int) -> tuple[Any, str]:
    """PrintWindow(PW_RENDERFULLCONTENT) 把窗口渲染进内存位图。

    返回 (Image | None, err_reason)。窗口本体渲染：不受遮挡、不受多屏
    负坐标影响。PW_RENDERFULLCONTENT(2) 让 Win10+ 把 DirectComposition/
    硬件加速内容一并重绘；仍拿不到内容的（个别独占交换链）出全黑图，
    由调用方按 _PRINTWINDOW_BLACK_THRESHOLD 判黑后走回退。
    """

    ensure_dpi_aware()
    import ctypes
    from ctypes import wintypes

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    size_rect = _rect_raw(hwnd)
    if size_rect is None:
        return None, "window_rect_unavailable"
    left, top, right, bottom = size_rect
    w, h = right - left, bottom - top

    user32 = _user32()
    gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.restype = ctypes.c_int

    hwnd_dc = mem_dc = None
    bmp = old = None
    try:
        hwnd_dc = user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None, "getwindowdc_failed"
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            return None, "createcompatibledc_failed"
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        if not bmp:
            return None, "createcompatiblebitmap_failed"
        old = gdi32.SelectObject(mem_dc, bmp)
        if not user32.PrintWindow(hwnd, mem_dc, 2):  # PW_RENDERFULLCONTENT
            return None, "printwindow_returned_false"
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 负值 = top-down 行序，和 PIL 一致
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        buf = (ctypes.c_char * (w * h * 4))()
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return None, "getdibits_failed"
        from PIL import Image

        img = Image.frombuffer("RGB", (w, h), bytes(buf), "raw", "BGRX", 0, 1)
        return img, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"printwindow_error:{exc!r}"
    finally:
        # GDI 对象是进程级稀缺资源：无论成败都按选入→位图→DC 的逆序归还。
        try:
            if mem_dc and old is not None:
                gdi32.SelectObject(mem_dc, old)
            if bmp:
                gdi32.DeleteObject(bmp)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if hwnd_dc:
                user32.ReleaseDC(hwnd, hwnd_dc)
        except Exception:  # noqa: BLE001
            pass


def _crop_to_visible_frame(hwnd: int, img: Any) -> Any:
    """把 PrintWindow 的全窗矩形图裁到 DWM 可见边框。

    PrintWindow 按 GetWindowRect（含透明 resize 边框）尺寸渲染，四角会带
    一圈黑边/透明边；裁掉后与桌面截屏通道共用同一坐标系，面板框选的
    比例区域在两条通道下含义一致。
    """

    raw = _rect_raw(hwnd)
    vis = get_window_rect(hwnd)
    if raw is None or vis is None or raw == vis:
        return img
    crop_l = max(0, vis[0] - raw[0])
    crop_t = max(0, vis[1] - raw[1])
    crop_r = max(0, raw[2] - vis[2])
    crop_b = max(0, raw[3] - vis[3])
    w, h = img.size
    if crop_l + crop_r >= w - 8 or crop_t + crop_b >= h - 8:
        return img  # 差值异常（多屏/DPI 边缘）：宁可不裁也别裁坏
    return img.crop((crop_l, crop_t, w - crop_r, h - crop_b))


def grab_window(hwnd: int) -> Any:
    """取窗口图像（阻塞）。失败抛 RuntimeError（消息为稳定错误码前缀）。

    auto 模式：PrintWindow → 黑帧/失败回退桌面区域截屏；被遮挡时仍拿到
    窗口本体。独占全屏等两条通道都给黑的场景由调用方 looks_black 兜底。
    """

    mode = _capture_mode if _capture_mode in CAPTURE_MODES else "auto"
    if mode in ("auto", "window"):
        img, err = _print_window(hwnd)
        if img is not None and not looks_black(img, threshold=_PRINTWINDOW_BLACK_THRESHOLD):
            return _crop_to_visible_frame(hwnd, img)
        if mode == "window":
            raise RuntimeError(err or "printwindow_black_frame")
    if is_minimized(hwnd):
        raise RuntimeError("target_minimized")
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


def looks_black(img: Any, threshold: float = 6.0) -> bool:
    """黑帧检测：DirectX 独占全屏 / 受 DRM 保护表面会截出纯黑。"""

    try:
        small = img.convert("L").resize((32, 32))
        data = list(small.getdata())
        return (sum(data) / max(1, len(data))) < float(threshold)
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
