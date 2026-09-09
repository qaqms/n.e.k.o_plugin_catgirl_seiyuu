"""core.state 模式机单测。"""

from catgirl_seiyuu.core.state import MODE_OFF, MODE_PAUSED, MODE_RUNNING, ModeMachine


def test_full_cycle():
    m = ModeMachine()
    assert m.mode == MODE_OFF
    assert m.start() is True
    assert m.mode == MODE_RUNNING
    assert m.pause("user") is True
    assert m.mode == MODE_PAUSED
    assert m.paused_reason == "user"
    assert m.resume() is True
    assert m.mode == MODE_RUNNING
    assert m.paused_reason == ""
    assert m.stop() is True
    assert m.mode == MODE_OFF


def test_illegal_transitions():
    m = ModeMachine()
    assert m.pause() is False  # off 不能暂停
    assert m.resume() is False  # off 不能继续
    m.start()
    assert m.start() is True  # 重复 start 幂等
    assert m.mode == MODE_RUNNING
    m.stop()
    assert m.resume() is False


def test_running_from_paused_via_start():
    m = ModeMachine()
    m.start()
    m.pause("conflict")
    assert m.start() is True
    assert m.mode == MODE_RUNNING
    assert m.paused_reason == ""
