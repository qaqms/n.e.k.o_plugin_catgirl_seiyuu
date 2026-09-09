"""core.rules 判定层单测（纯 stdlib，无需 SDK / 宿主）。"""

from catgirl_seiyuu.core.rules import (
    GateConfig,
    LineGate,
    is_monologue,
    normalize_text,
    significant_count,
    similarity,
    split_speaker,
)


def _gate(**kw) -> LineGate:
    cfg = GateConfig(**{"stable_frames": 2, **kw})
    return LineGate(cfg)


def _settle(gate: LineGate, text: str) -> str | None:
    """喂两帧相同文本（默认 stable_frames=2），返回判定结果。"""

    assert gate.consider(text) is None  # 第一帧只建立 pending
    return gate.consider(text)


# ------------------------------------------------------------------ helpers


def test_normalize_and_counts():
    assert normalize_text(" 你好  世界 \n\n !! \t") == "你好 世界\n!!"
    assert significant_count("你好a!。") == 3
    assert similarity("你好世界", "你好世界") == 1.0
    assert similarity("你好世界", "你好   世界") == 1.0  # 空白被拍平
    assert similarity("你好世界", "你好世人") == 0.75  # 短句单字抖动不过阈值（真实台词更长）
    assert similarity("今天的晚霞真的很漂亮呢", "今天的晚霞琨的漂亮呢") > 0.8


def test_split_speaker():
    speaker, body = split_speaker("雪乃：今天我做了咖喱。")
    assert speaker == "雪乃"
    assert body == "今天我做了咖喱。"
    assert split_speaker("今天没有人名前缀")[0] == ""
    assert split_speaker("雪乃：第一行\n第二行")[1] == "第一行\n第二行"


def test_monologue_detection():
    assert is_monologue("（今天的阳光很好）")
    assert is_monologue("「远处传来钟声」")
    assert not is_monologue("（前半句 后半句没有闭合")


# ------------------------------------------------------------------ gate


def test_stabilization_filters_typewriter_halves():
    gate = _gate()
    # 打字机：逐帧变长且相似度不足 → 不播报
    assert gate.consider("今天") is None
    assert gate.consider("今天天气") is None
    assert gate.consider("今天天气很好") is None  # 新 pending，第 1 帧
    assert gate.consider("今天天气很好") == "今天天气很好"  # 第 2 帧稳定 → 播


def test_empty_frames_do_not_break_stability():
    gate = _gate()
    assert gate.consider("台词内容") is None
    assert gate.consider("") is None  # 转场空帧
    assert gate.consider("台词内容") == "台词内容"


def test_dedupe_prevents_replay():
    gate = _gate()
    assert _settle(gate, "第一句台词") == "第一句台词"
    # 画面不动：继续喂同样文本 → 不重播
    assert gate.consider("第一句台词") is None
    assert gate.consider("第一句台词") is None
    # 翻页回来也不重播
    assert _settle(gate, "第二句台词") == "第二句台词"
    assert _settle(gate, "第一句台词") is None


def test_ui_words_skipped():
    gate = _gate(ui_words=["自动", "SAVE"])
    assert _settle(gate, "自动") is None
    assert list(gate.skipped)[0]["reason"] == "ui_word"


def test_short_and_symbol_noise_skipped():
    gate = _gate()
    assert _settle(gate, "…") is None
    assert list(gate.skipped)[0]["reason"] in ("no_text", "too_short")


def test_monologue_switch():
    gate = _gate(dub_monologue=False)
    assert _settle(gate, "（内心独白）") is None
    assert list(gate.skipped)[0]["reason"] == "monologue"
    gate2 = _gate(dub_monologue=True)
    assert _settle(gate2, "（内心独白）") == "（内心独白）"


def test_protagonist_switch():
    gate = _gate(protagonist_names=["昴"], dub_protagonist=False)
    assert _settle(gate, "昴：我说什么来着") is None
    assert list(gate.skipped)[0]["reason"] == "protagonist"
    # 他人台词不受影响
    assert _settle(gate, "霞：你好呀") == "你好呀"


def test_speaker_plate_only_skipped_without_replay():
    gate = _gate()
    # 只显示名字的姓名板帧：等正文帧出现再配
    assert _settle(gate, "霞：") is None
    assert _settle(gate, "霞：你好") == "你好"


def test_hard_reset_clears_dedupe():
    gate = _gate()
    assert _settle(gate, "一句话") == "一句话"
    gate.hard_reset()
    assert _settle(gate, "一句话") == "一句话"
