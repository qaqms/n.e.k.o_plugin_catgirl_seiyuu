"""v0.1.1 缺陷修复的回归测试（一轮一个 bug，反向对照：旧实现必红）。

覆盖：
1. worker 配置陈旧 —— 每句重读 self._cfg["dub"]（曾启动时快照一次，运行中
   update_settings 全不生效）；
2. 目标解析静默回退 —— 显式 hwnd/title 失败必须 Err（曾悄悄回退前台，从
   面板选中失焦/已关窗口时把宿主聊天窗口喂进 OCR）；
3. 自我朗读护栏 —— 默认前台命中宿主标题关键字 → Err(target_is_self)；
4. 配置消毒 —— 手工编辑成垃圾值的运行时 config 回落出厂默认而非炸；
5. worker 监督 —— 死亡 worker 在下一 tick 被重启。
"""

import asyncio

from catgirl_seiyuu.core.state import MODE_OFF, MODE_RUNNING
from catgirl_seiyuu.services import capture as cap


def _stub_windows(monkeypatch, hwnd=1234, title="Some Galgame"):
    monkeypatch.setattr(cap, "supported", lambda: True)
    monkeypatch.setattr(cap, "is_window_valid", lambda h: int(h) == hwnd)
    monkeypatch.setattr(cap, "window_title", lambda h: title if int(h) == hwnd else "")
    monkeypatch.setattr(cap, "foreground_window_info", lambda: {"hwnd": hwnd, "title": title, "pid": 42})
    monkeypatch.setattr(cap, "list_windows", lambda: [{"hwnd": hwnd, "title": title, "pid": 42}])
    monkeypatch.setattr(cap, "is_foreground", lambda h: int(h) == hwnd)


# ---------------------------------------------------------------- 1. worker 新鲜配置


async def test_speak_worker_reads_fresh_config_each_line(cs_make_plugin, cs_ok):
    plugin = cs_make_plugin()
    plugin._mode.start()
    calls: list[str] = []

    async def fake_speak(line, *, lanlan_name="", **_):
        calls.append(lanlan_name)
        return {"ok": True}

    plugin._host.speak = fake_speak
    plugin._ensure_worker()
    plugin._enqueue("第一句")
    await asyncio.sleep(0.05)
    # 走真实入口 update_settings：它把 self._cfg["dub"] 整个换成新 dict，
    # 旧 worker 若持启动时快照就读不到新值（反向对照：旧实现必红）
    ok = await plugin.update_settings(patch={"lanlan_name": "灵"})
    assert isinstance(ok, cs_ok), ok
    plugin._enqueue("第二句")
    await asyncio.sleep(0.05)
    assert calls == ["", "灵"], f"worker 拿陈旧配置快照: {calls}"
    plugin._speak_task.cancel()


# ---------------------------------------------------------------- 2/3. 目标解析


async def test_dub_start_rejects_stale_hwnd_without_fallback(cs_make_plugin, cs_err, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    err = await plugin.dub_start(hwnd=999)  # 已失效句柄：曾静默回退前台
    assert isinstance(err, cs_err)
    assert plugin._mode.mode == MODE_OFF


async def test_dub_start_title_match_and_miss(cs_make_plugin, cs_ok, cs_err, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch)
    ok = await plugin.dub_start(title="Some Galgame")
    assert isinstance(ok, cs_ok)
    await plugin.dub_stop()
    miss = await plugin.dub_start(title="No Such Window")
    assert isinstance(miss, cs_err)
    assert plugin._mode.mode == MODE_OFF


async def test_dub_start_foreground_host_window_rejected(cs_make_plugin, cs_err, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch, hwnd=1234, title="N.E.K.O")
    err = await plugin.dub_start()  # llm 渠道：前台是宿主 → 回声回路护栏
    assert isinstance(err, cs_err)
    assert "N.E.K.O" in str(err.error)
    assert plugin._mode.mode == MODE_OFF


async def test_dub_start_host_guard_can_be_disabled(cs_make_plugin, cs_ok, monkeypatch):
    plugin = cs_make_plugin()
    _stub_windows(monkeypatch, hwnd=1234, title="N.E.K.O")
    plugin._cfg["dub"]["exclude_host_window"] = False
    ok = await plugin.dub_start()
    assert isinstance(ok, cs_ok)
    assert plugin._mode.mode == MODE_RUNNING


# ---------------------------------------------------------------- 4. 配置消毒


async def test_load_config_sanitizes_garbage_values(cs_make_plugin):
    plugin = cs_make_plugin(dub={
        "poll_interval_ms": "abc",       # 转不了 int → 回落默认
        "stable_frames": 3.7,            # 可无损夹紧 → 3
        "ui_words": "notalist",          # 类型不对 → 回落默认
        "mirror_text": "yes",            # 字符串布尔 → True
        "lanlan_name": 123,              # 非 str → 回落默认
    })
    cfg = await plugin._load_config()
    dub = cfg["dub"]
    assert dub["poll_interval_ms"] == 900
    assert dub["stable_frames"] == 3
    assert dub["ui_words"] == []
    assert dub["mirror_text"] is True
    assert dub["lanlan_name"] == ""


# ---------------------------------------------------------------- 5. worker 监督


async def test_supervise_worker_restarts_dead_task(cs_make_plugin):
    plugin = cs_make_plugin()

    async def dead():
        raise RuntimeError("boom")

    stale = asyncio.create_task(dead())
    await asyncio.gather(stale, return_exceptions=True)  # 先把异常取出，避免告警
    plugin._speak_task = stale
    plugin._supervise_worker()
    assert plugin._speak_task is not stale
    assert not plugin._speak_task.done()
    plugin._speak_task.cancel()
