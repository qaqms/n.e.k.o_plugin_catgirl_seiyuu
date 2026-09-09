"""entries / 模式控制 / 消息联动行为测试（SDK 桩与工厂由 conftest fixture 提供）。"""

from catgirl_seiyuu.core.state import MODE_PAUSED, MODE_RUNNING
from catgirl_seiyuu.services import capture as cap


def _stub_windows(monkeypatch, hwnd=1234, title="Some Galgame"):
    monkeypatch.setattr(cap, "supported", lambda: True)
    monkeypatch.setattr(cap, "is_window_valid", lambda h: int(h) == hwnd)
    monkeypatch.setattr(cap, "window_title", lambda h: title if int(h) == hwnd else "")
    monkeypatch.setattr(cap, "foreground_window_info", lambda: {"hwnd": hwnd, "title": title, "pid": 42})
    monkeypatch.setattr(cap, "list_windows", lambda: [{"hwnd": hwnd, "title": title, "pid": 42}])
    monkeypatch.setattr(cap, "is_foreground", lambda h: int(h) == hwnd)


async def test_dub_start_and_stop(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    result = await plugin.dub_start()
    assert plugin._mode.mode == MODE_RUNNING
    assert result.value["target"] == "Some Galgame"
    stop = await plugin.dub_stop()
    assert stop.value["mode"] == "off"


async def test_pause_requires_running(cs_make_plugin, monkeypatch, cs_err):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    err = await plugin.dub_pause()
    assert isinstance(err, cs_err)
    await plugin.dub_start()
    await plugin.dub_pause()
    assert plugin._mode.mode == MODE_PAUSED
    await plugin.dub_resume()
    assert plugin._mode.mode == MODE_RUNNING


async def test_set_region_validation_and_persist(cs_make_plugin, cs_err, cs_ok):
    plugin = cs_make_plugin()
    bad = await plugin.set_region(x=0.5, y=0.5, w=0.9, h=0.9)
    assert isinstance(bad, cs_err)
    good = await plugin.set_region(x=0.1, y=0.6, w=0.8, h=0.3)
    assert isinstance(good, cs_ok)
    assert plugin._cfg["region"]["w"] == 0.8
    assert plugin.config.updates, "应写入运行时配置"


async def test_update_settings_whitelist_and_apply(cs_make_plugin, cs_err, cs_ok):
    plugin = cs_make_plugin()
    bad = await plugin.update_settings(patch={"nope": 1})
    assert isinstance(bad, cs_err)
    ok = await plugin.update_settings(patch={"stable_frames": "3", "dub_monologue": False})
    assert isinstance(ok, cs_ok)
    assert plugin._cfg["dub"]["stable_frames"] == 3
    assert plugin._gate.cfg.dub_monologue is False


async def test_feed_line_rejects_empty(cs_make_plugin, cs_err):
    plugin = cs_make_plugin()
    assert isinstance(await plugin.feed_line(line="  "), cs_err)


async def test_user_chat_pauses_dubbing(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    await plugin.dub_start()
    res = await plugin.on_user_chat(text="你好？", role="user")
    assert res.value["paused"] is True
    assert plugin._mode.mode == MODE_PAUSED


async def test_user_chat_ignores_mirrored_line(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    await plugin.dub_start()
    plugin._current_line = "第一句台词"
    res = await plugin.on_user_chat(text="第一句台词", role="user")
    assert res.value["paused"] is False
    assert plugin._mode.mode == MODE_RUNNING


async def test_llm_tool_start_returns_compact_dict(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    payload = await plugin.tool_start()
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["mode"] == "running"
    status = await plugin.tool_status()
    assert status["mode"] == "running"
