"""v0.2 抓取重构的回归测试（反向对照：v0.1 实现必红的点都留了注释）。

覆盖：
1. 窗口列表裁决 window_accept —— cloaked/无标签/不可见/零尺寸剔除，
   **最小化窗收录**（v0.1 在这里把最小化游戏静默丢掉）；
2. resolve_window_label —— 空标题退回进程名，不再整窗消失；
3. looks_black 阈值参数化 —— PrintWindow 通道用更宽的黑帧判据；
4. capture_mode 设置面 —— 非法值拒绝、合法值生效并透传到 capture 模块；
5. dub_start 最小化目标显式报错（而不是开走后截到哨兵坐标/旧帧）；
6. _tick_once 运行中最小化 → 自动暂停 target_minimized；
7. dub_windows 返回富化行（label/process/minimized/focused/is_self）。
"""

import tomllib
from pathlib import Path

from catgirl_seiyuu.core.state import MODE_OFF, MODE_PAUSED
from catgirl_seiyuu.services import capture as cap


def _stub_windows(monkeypatch, hwnd=1234, title="Some Galgame", minimized=False):
    monkeypatch.setattr(cap, "supported", lambda: True)
    monkeypatch.setattr(cap, "is_window_valid", lambda h: int(h) == hwnd)
    monkeypatch.setattr(cap, "window_title", lambda h: title if int(h) == hwnd else "")
    monkeypatch.setattr(cap, "process_name", lambda h: "game.exe" if int(h) == hwnd else "")
    monkeypatch.setattr(
        cap, "foreground_window_info",
        lambda: {"hwnd": hwnd, "title": title, "label": title, "process": "game.exe", "pid": 42},
    )
    monkeypatch.setattr(cap, "list_windows", lambda: [{
        "hwnd": hwnd, "title": title, "label": title, "process": "game.exe",
        "pid": 42, "minimized": minimized, "focused": True, "is_self": False,
    }])
    monkeypatch.setattr(cap, "is_foreground", lambda h: int(h) == hwnd)
    monkeypatch.setattr(cap, "is_minimized", lambda h: minimized and int(h) == hwnd)


# ---------------------------------------------------------------- 1. 列表裁决（纯逻辑）


def test_window_accept_rejects_unpickable():
    kw = dict(title="Game", process="game.exe", visible=True, rect_valid=True, iconic=False, cloaked=False)
    assert cap.window_accept(**kw) == ""
    # DWM 伪装窗：IsWindowVisible 仍为真但看不见（UWP 挂起/别的虚拟桌面）→ 必须剔除
    assert cap.window_accept(**{**kw, "cloaked": True}) == "cloaked"
    assert cap.window_accept(**{**kw, "title": "", "process": ""}) == "no_label"
    assert cap.window_accept(**{**kw, "visible": False}) == "not_visible"
    assert cap.window_accept(**{**kw, "rect_valid": False}) == "no_size"


def test_window_accept_lists_minimized():
    # 反向对照：v0.1 用 get_window_rect=None 把最小化游戏整个过滤掉，
    # 玩家刷新永远看不到自己的窗口。v0.2 收录并打标。
    assert cap.window_accept(
        title="Game", process="game.exe", visible=False,
        rect_valid=False, iconic=True, cloaked=False,
    ) == ""


def test_resolve_window_label_falls_back_to_process():
    assert cap.resolve_window_label("Some Galgame", "game.exe") == "Some Galgame"
    # 反向对照：v0.1 空标题窗口直接消失；现在退回进程名可选
    assert cap.resolve_window_label("  ", "SomeGame.exe") == "SomeGame.exe"
    assert cap.resolve_window_label("", "") == ""


# ---------------------------------------------------------------- 2/3. 黑帧与模式（纯逻辑）


class _FakeGray:
    def __init__(self, values):
        self._v = values

    def getdata(self):
        return self._v


class _FakeImg:
    def __init__(self, luminance):
        self._v = [luminance] * (32 * 32)

    def convert(self, _mode):
        return self

    def resize(self, _size):
        return _FakeGray(self._v)


def test_looks_black_threshold_parameter():
    assert cap.looks_black(_FakeImg(0), threshold=6.0) is True
    assert cap.looks_black(_FakeImg(10), threshold=6.0) is False
    # PrintWindow 通道阈值更宽（2.0）：深色 VN 场景 + 少量白字（均值 3）
    # 不该被判黑而放弃可用的窗口渲染通道
    assert cap.looks_black(_FakeImg(3), threshold=2.0) is False
    assert cap.looks_black(_FakeImg(3), threshold=6.0) is True


def test_capture_mode_setter_validates():
    original = cap.capture_mode()
    try:
        assert cap.set_capture_mode("window") == "window"
        assert cap.capture_mode() == "window"
        cap.set_capture_mode("nonsense")  # 非法值：保持当前，不越改越坏
        assert cap.capture_mode() == "window"
    finally:
        cap.set_capture_mode(original)


# ---------------------------------------------------------------- 4. 设置面接线


async def test_update_settings_accepts_and_applies_capture_mode(cs_make_plugin, cs_ok):
    plugin = cs_make_plugin()
    ok = await plugin.update_settings(patch={"capture_mode": "Screen"})  # 大小写归一
    assert isinstance(ok, cs_ok)
    assert plugin._cfg["dub"]["capture_mode"] == "screen"
    assert cap.capture_mode() == "screen"
    cap.set_capture_mode("auto")


async def test_update_settings_rejects_bad_capture_mode(cs_make_plugin, cs_err):
    plugin = cs_make_plugin()
    err = await plugin.update_settings(patch={"capture_mode": "teleport"})
    assert isinstance(err, cs_err)
    assert "capture_mode" in str(err.error)


async def test_apply_config_falls_back_invalid_capture_mode(cs_make_plugin, monkeypatch):
    # 手工编辑 runtime config 写了垃圾值：warning + 按 auto 执行，而不是把
    # capture 模块设成未知模式
    seen: list[str] = []
    monkeypatch.setattr(cap, "set_capture_mode", lambda m: seen.append(m) or m)
    warnings: list[str] = []
    plugin = cs_make_plugin()
    plugin.logger = type("L", (), {
        "warning": lambda self, *a: warnings.append(str(a)),
        "info": lambda self, *a: None,
    })()
    plugin._cfg["dub"]["capture_mode"] = "teleport"
    plugin._apply_config()
    assert seen == ["auto"]
    assert warnings


# ---------------------------------------------------------------- 5/6. 最小化目标


async def test_dub_start_rejects_minimized_target(cs_make_plugin, cs_err, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch, minimized=True)
    err = await plugin.dub_start(hwnd=1234)
    # 反向对照：v0.1 无此检查——最小化窗照样 start，轮询吃哨兵坐标报错才停
    assert isinstance(err, cs_err)
    assert "最小化" in str(err.error)
    assert plugin._mode.mode == MODE_OFF


async def test_tick_pauses_when_target_minimized(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch, minimized=True)
    plugin._mode.start()
    plugin._target_hwnd = 1234
    await plugin._tick_once()
    assert plugin._mode.mode == MODE_PAUSED
    assert "target_minimized" in plugin._last_error


# ---------------------------------------------------------------- 7. dub_windows 形状


async def test_dub_windows_returns_rich_rows(cs_make_plugin, cs_ok, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    ok = await plugin.dub_windows()
    assert isinstance(ok, cs_ok)
    row = ok.value["windows"][0]
    assert row["label"] == "Some Galgame"
    assert row["process"] == "game.exe"
    assert row["focused"] is True
    assert row["minimized"] is False
    assert row["is_self"] is False
    assert ok.value["total"] == 1


# ---------------------------------------------------------------- 8. 模块级默认与文档同源


def test_capture_mode_defaults_in_all_layers():
    # plugin.toml / config.example.toml / DEFAULTS 三处必须都有 capture_mode，
    # 且默认一致（打包检查与面板读的是不同来源，漂移过一次 v0.1.1 的坑）
    root = Path(__file__).resolve().parents[1]
    import catgirl_seiyuu as pkg

    assert pkg.DEFAULTS["dub"]["capture_mode"] == "auto"
    manifest = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["dub"]["capture_mode"] == "auto"
    example = tomllib.loads((root / "config.example.toml").read_text(encoding="utf-8"))
    assert example["dub"]["capture_mode"] == "auto"
