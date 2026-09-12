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


# ------------------------------------------------ 句末标点优先（v0.2.2 主线 A）


def test_growth_chain_no_longer_settles_mid_sentence():
    """打字机逐帧变长、相邻帧相似度 ≥0.9（长句每帧只多一字）：增长中不得开口。"""

    gate = _gate()  # stable_frames=2：v0.2.1 会在第 2 帧就把半句读出去
    assert gate.consider("今天的晚霞真的很漂") is None
    assert gate.consider("今天的晚霞真的很漂亮") is None  # 相似链计数满 2，但未冻结
    assert gate.consider("今天的晚霞真的很漂亮呢") is None  # 仍在增长


def test_punctuation_settles_at_two_frozen_frames_with_high_stable():
    """句读结尾 + 冻结 2 帧：stable_frames 调到 5 也提前定型。"""

    gate = _gate(stable_frames=5)
    line = "今天的晚霞真的很漂亮呢。"
    assert gate.consider(line) is None
    assert gate.consider(line) == line


def test_no_punctuation_needs_full_window_then_freeze():
    """未命中句读：增长链可攒，但开口前必须冻结；stable=4 时第 2/3 帧不开口。"""

    gate = _gate(stable_frames=4)
    line = "今天的晚霞真的很漂亮呢"
    assert gate.consider(line) is None  # 1
    assert gate.consider(line) is None  # 2 冻结但未攒满 N 且无句读
    assert gate.consider(line) is None  # 3
    assert gate.consider(line) == line  # 4 满窗且冻结


def test_punctuation_priority_off_is_legacy_behavior():
    """开关关闭 = v0.2.1 纯 N 帧相似链：增长中也可定型（回归铁到旧版）。"""

    gate = _gate(punctuation_priority=False)
    assert gate.consider("今天的晚霞真的很漂") is None
    assert gate.consider("今天的晚霞真的很漂亮") == "今天的晚霞真的很漂亮"


def test_stuck_ocr_jitter_falls_back_to_chain_rescue():
    """两帧交替抖动（永不冻结）：N+4 帧兜底强制定型，不漏整句。"""

    gate = _gate(stable_frames=2)
    a, b = "今天的晚霞真的很漂", "今天的晚霞真的很漂亮"  # ratio≈0.95 ≥0.9
    outs = [gate.consider(t) for t in (a, b, a, b, a, b)]
    assert outs[:5] == [None] * 5
    assert outs[5] == b  # 第 6 帧 = need+4 兜底，采用最新一版


def test_custom_and_empty_sentence_end_chars():
    gate = _gate(stable_frames=5, sentence_end_chars="♪")
    line = "ending♪"
    assert gate.consider(line) is None
    assert gate.consider(line) == line

    gate2 = _gate(stable_frames=3, sentence_end_chars="")  # 空集：无提前定型
    line2 = "好的。"
    assert gate2.consider(line2) is None
    assert gate2.consider(line2) is None  # streak 2 但句读集为空、未满 N
    assert gate2.consider(line2) == line2


def test_mid_pause_without_punctuation_still_settles_when_frozen():
    """真正的停顿（非增长）即使无句读也要能播：不能把普通台词钉死。"""

    gate = _gate(stable_frames=3)
    half = "没有句号的台词"
    assert gate.consider(half) is None
    assert gate.consider(half) is None
    assert gate.consider(half) == half
