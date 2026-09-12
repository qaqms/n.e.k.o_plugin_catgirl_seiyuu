"""v0.1.2 纯聊天控制的回归测试。

目标：不碰面板，全程用嘴——
- 「继续配音」→ catgirl_seiyuu_resume：复用暂停前的目标窗口，不重新解析前台；
- 「打开配音」在暂停态 + 无显式目标 → dub_start 视同继续（LLM 翻成
  start 也不会撞前台护栏）；
- 「暂停」与 pause_on_user_message 的竞态 → dub_pause 幂等成功。
"""

from catgirl_seiyuu.core.state import MODE_PAUSED, MODE_RUNNING
from catgirl_seiyuu.services import capture as cap


def _stub_windows(monkeypatch, hwnd=1234, title="Some Galgame"):
    monkeypatch.setattr(cap, "supported", lambda: True)
    monkeypatch.setattr(cap, "is_window_valid", lambda h: int(h) == hwnd)
    monkeypatch.setattr(cap, "window_title", lambda h: title if int(h) == hwnd else "")
    monkeypatch.setattr(cap, "foreground_window_info", lambda: {"hwnd": hwnd, "title": title, "pid": 42})
    monkeypatch.setattr(cap, "list_windows", lambda: [{"hwnd": hwnd, "title": title, "pid": 42}])
    monkeypatch.setattr(cap, "is_foreground", lambda h: int(h) == hwnd)


async def _start_then_user_pause(plugin, monkeypatch):
    _stub_windows(monkeypatch)
    await plugin.dub_start()
    res = await plugin.on_user_chat(text="先停一下，我问你个事", role="user")
    assert res.value["paused"] is True
    assert plugin._mode.mode == MODE_PAUSED


async def test_resume_tool_reuses_saved_target(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    await _start_then_user_pause(plugin, monkeypatch)
    # 主人打完字，前台切回宿主自己的窗口：resume 绝不能重新解析前台
    _stub_windows(monkeypatch, hwnd=1234, title="N.E.K.O")
    payload = await plugin.tool_resume()
    assert payload["ok"] is True
    assert plugin._mode.mode == MODE_RUNNING
    assert payload["target"] == "Some Galgame"
    assert plugin._target_hwnd == 1234


async def test_start_tool_while_paused_degrades_to_resume(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    await _start_then_user_pause(plugin, monkeypatch)
    # LLM 把「继续配音」翻成 start 的兜底：暂停态 + 无显式目标 + 原窗口存活
    # → 视同继续；旧行为会解析前台（这里是宿主窗）→ target_is_self 报错。
    _stub_windows(monkeypatch, hwnd=1234, title="N.E.K.O")
    payload = await plugin.tool_start()
    assert payload["ok"] is True, payload
    assert plugin._mode.mode == MODE_RUNNING
    assert payload["target"] == "Some Galgame"


async def test_start_with_explicit_hwnd_still_switches_target(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    await _start_then_user_pause(plugin, monkeypatch)
    # 显式换目标（面板选窗后点开始）不被"视同继续"劫持
    _stub_windows(monkeypatch, hwnd=5678, title="Second Game")
    result = await plugin.dub_start(hwnd=5678)
    assert plugin._target_hwnd == 5678
    assert result.value["target"] == "Second Game"


async def test_pause_after_auto_pause_is_idempotent(cs_make_plugin, cs_err, monkeypatch):
    plugin = cs_make_plugin()
    await _start_then_user_pause(plugin, monkeypatch)
    # 自动让位已抢先把配音暂停：聊天里再说「暂停」应幂等成功而非 Err
    again = await plugin.dub_pause()
    assert not isinstance(again, cs_err)
    assert again.value["mode"] == MODE_PAUSED


async def test_resume_when_off_reports_error(cs_make_plugin):
    plugin = cs_make_plugin()
    payload = await plugin.tool_resume()
    assert payload["ok"] is False


async def test_resume_rejects_dead_target_window(cs_make_plugin, monkeypatch):
    plugin = cs_make_plugin()
    await _start_then_user_pause(plugin, monkeypatch)
    monkeypatch.setattr(cap, "is_window_valid", lambda h: False)
    payload = await plugin.tool_resume()
    assert payload["ok"] is False
    assert plugin._mode.mode == MODE_PAUSED
