"""台词判定层：稳定窗口 + 去重 + 规则过滤 + speaker/独白仲裁。

OCR 每帧给出的是一整块区域文本；本模块的职责是把它变成
「一条值得播报的新台词」或者「什么都没有」：

1. normalize：逐行清洗、去空行；
2. 稳定窗口：连续 N 帧文本（相似度 ≥ threshold）一致才候选 —— 对抗打字机半句；
   句末标点优先（punctuation_priority，默认开）在此之上加两条铁律：
   定型必须以「末两帧完全一致」（冻结）为前提 —— 打字机逐帧变长时
   相邻帧相似度也可能 ≥ threshold（长句每帧只多一两字），旧版会把增长帧
   误计入稳定计数、在半句处开口；末位命中句读的候选冻结 2 帧即播
   （stable_frames 调高也不迟钝）；OCR 抖动永不冻结时以 N+4 帧兜底强制定型，
   防整句漏播。开关关闭 = v0.2.1 纯 N 帧窗口行为。
3. 去重窗口：最近 N 条已消费文本 hash（翻页/回看/循环待机不重播）；
4. 规则过滤：有效字符下限、总长上限、纯符号、UI 词整行命中；
5. speaker/独白仲裁：行首「名字：」命中主角名 → 按 dub_protagonist；
   整句（）/「」包裹视为独白 → 按 dub_monologue；其余为普通他人台词，必配。

被跳过的非空行记入 skipped 环形列表，供面板展示"跳过了：xxx"纠错。
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# 行首说话人：短前缀 + 冒号（半/全角），前缀里不含句读
_SPEAKER_LINE_RE = re.compile(r"^([^\s：:。，,.!?！？『「（(){<>【]{1,12})[：:]")

# 独白/引用外层括号对（整句包裹才算）
_WRAP_PAIRS: tuple[tuple[str, str], ...] = (
    ("（", "）"),
    ("(", ")"),
    ("「", "」"),
    ("『", "』"),
    ("\u201c", "\u201d"),  # 中文双引号
    ("<", ">"),
)

_WS_RE = re.compile(r"[ \t\f\v\u3000]+")
_SIGNIFICANT_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# 句末标点默认集：文本末位命中其一 → 视为「说完的整句」，允许提前定型。
DEFAULT_SENTENCE_END_CHARS = "。！？!?…」』"

# 冻结失败兜底：连续 N+STUCK_EXTRA_FRAMES 帧仍等不来完全一致（OCR 系统性
# 抖字）→ 强制定型，宁晚半拍不漏整句。
STUCK_EXTRA_FRAMES = 4


def ends_with_sentence_end(text: str, chars: str) -> bool:
    """拍平空白后末位是否命中句读集；chars 为空 → 永不命中（纯 N 帧窗口）。"""

    if not chars:
        return False
    flat = flat_text(text)
    return bool(flat) and flat[-1] in chars


def normalize_text(raw: str) -> str:
    """逐行清洗：压缩空白、去空行；返回 \n 连接的规范文本。"""

    lines = []
    for line in (raw or "").splitlines():
        line = _WS_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def significant_count(text: str) -> int:
    """文字/数字字符数（中日韩 + 字母数字）：过滤纯符号、页码噪声。"""

    return len(_SIGNIFICANT_RE.findall(text or ""))


def flat_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def similarity(a: str, b: str) -> float:
    """去空白后的字符相似度（OCR 单字误读不应打断稳定计数）。"""

    fa, fb = flat_text(a), flat_text(b)
    if not fa and not fb:
        return 1.0
    if not fa or not fb:
        return 0.0
    return SequenceMatcher(None, fa, fb).ratio()


def text_hash(text: str) -> str:
    return hashlib.sha1(flat_text(text).encode("utf-8")).hexdigest()


def split_speaker(text: str) -> tuple[str, str]:
    """尝试拆出行首说话人：返回 (speaker, 剩余文本)；无说话人时 speaker=""。"""

    first, _, rest = text.partition("\n")
    m = _SPEAKER_LINE_RE.match(first)
    if not m:
        return "", text
    speaker = m.group(1).strip()
    remainder = (first[m.end():].strip() + ("\n" + rest if rest else "")).strip()
    return speaker, remainder


def is_monologue(text: str) -> bool:
    """整句被同一对括号/引号包裹 → 内心独白（或引用，交给用户规则裁决）。"""

    flat = flat_text(text)
    if len(flat) < 2:
        return False
    for open_ch, close_ch in _WRAP_PAIRS:
        if flat.startswith(open_ch) and flat.endswith(close_ch):
            return True
    return False


@dataclass
class GateConfig:
    stable_frames: int = 2
    similarity_threshold: float = 0.9
    punctuation_priority: bool = True
    sentence_end_chars: str = DEFAULT_SENTENCE_END_CHARS
    dedupe_window: int = 64
    min_significant_chars: int = 2
    max_line_chars: int = 220
    ui_words: list[str] = field(default_factory=list)
    dub_protagonist: bool = False
    dub_monologue: bool = True
    protagonist_names: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls, dub: dict[str, Any]) -> "GateConfig":
        return cls(
            stable_frames=max(1, int(dub.get("stable_frames", 2))),
            similarity_threshold=float(dub.get("similarity_threshold", 0.9)),
            punctuation_priority=bool(dub.get("punctuation_priority", True)),
            sentence_end_chars=str(dub.get("sentence_end_chars", DEFAULT_SENTENCE_END_CHARS)),
            dedupe_window=max(1, int(dub.get("dedupe_window", 64))),
            min_significant_chars=max(0, int(dub.get("min_significant_chars", 2))),
            max_line_chars=max(10, int(dub.get("max_line_chars", 220))),
            ui_words=[str(w) for w in dub.get("ui_words", []) or []],
            dub_protagonist=bool(dub.get("dub_protagonist", False)),
            dub_monologue=bool(dub.get("dub_monologue", True)),
            protagonist_names=[str(n) for n in dub.get("protagonist_names", []) or []],
        )


class LineGate:
    """逐帧喂入 OCR 文本，输出"可播报的新台词"。非线程安全：只在事件循环里用。"""

    SKIP_LOG_LIMIT = 20

    def __init__(self, cfg: GateConfig | None = None) -> None:
        self.cfg = cfg or GateConfig()
        self._pending_text = ""
        self._pending_count = 0
        self._last_frame = ""
        self._frozen_streak = 0
        self._seen: deque[str] = deque(maxlen=max(1, self.cfg.dedupe_window))
        self._seen_set: set[str] = set()
        self.skipped: deque[dict[str, str]] = deque(maxlen=self.SKIP_LOG_LIMIT)

    def configure(self, cfg: GateConfig) -> None:
        self.cfg = cfg
        # 去重窗口大小变化时保留旧记忆（缩窗只影响后续淘汰）
        if self._seen.maxlen != max(1, cfg.dedupe_window):
            self._seen = deque(self._seen, maxlen=max(1, cfg.dedupe_window))
            self._seen_set = set(self._seen)

    def reset(self) -> None:
        """清空当前稳定计数（start/stop/pause 时调用；去重记忆保留）。"""

        self._pending_text = ""
        self._pending_count = 0
        self._last_frame = ""
        self._frozen_streak = 0

    def hard_reset(self) -> None:
        self.reset()
        self._seen.clear()
        self._seen_set.clear()
        self.skipped.clear()

    # ------------------------------------------------------------------

    def consider(self, raw_text: str) -> str | None:
        """喂入一帧 OCR 文本；返回应播报的台词（无则 None）。"""

        text = normalize_text(raw_text)
        if not text:
            # 空帧（转场/立绘切换）不打断稳定计数：瞬时误读不应吃掉窗口
            return None

        frozen = text == self._last_frame
        self._last_frame = text
        self._frozen_streak = self._frozen_streak + 1 if frozen else 1

        if self._pending_text and (
            text == self._pending_text
            or similarity(text, self._pending_text) >= self.cfg.similarity_threshold
        ):
            self._pending_count += 1
            # 打字机逐帧变长：始终采用最新（更完整）的一版
            self._pending_text = text
        else:
            self._pending_text = text
            self._pending_count = 1

        if not self._is_settled(text):
            return None

        candidate = self._pending_text
        h = text_hash(candidate)
        if h in self._seen_set:
            # 已消费过：留在原地等待画面切换，不重播
            return None

        line, reason = self._adjudicate(candidate)
        # 无论播不播，这一帧的内容都算"已看过"，避免稳定计数反复触发
        self._consume(h)
        self._pending_text = ""
        self._pending_count = 0
        self._last_frame = ""
        self._frozen_streak = 0

        if reason:
            self._record_skip(candidate, reason)
            return None
        return line

    # ------------------------------------------------------------------

    def _is_settled(self, text: str) -> bool:
        """候选是否可判定为「定型」（模块 docstring 第 2 条的打字机防线）。"""

        cfg = self.cfg
        need = cfg.stable_frames
        if not cfg.punctuation_priority:
            return self._pending_count >= need  # v0.2.1 行为：纯 N 帧相似链窗口

        frozen_need = 1 if need <= 1 else 2
        # 兜底：OCR 系统性抖字导致永不「完全一致」时，相似链够长也得放行，
        # 否则整句永远读不出来。
        chain_rescue = self._pending_count >= need + STUCK_EXTRA_FRAMES
        if ends_with_sentence_end(text, cfg.sentence_end_chars):
            # 句末标点优先：冻结 2 帧即播，不吃 stable_frames 的 N
            return self._frozen_streak >= frozen_need or chain_rescue
        # 未命中句读：增长链可以攒，但开口前文本必须真的停止变化（冻结）；
        # 否则只是把「半句卡帧提前播」的机会还给旧 bug。need<=2 时与旧版
        # 对静止文本行为一致（冻结 2 帧 = 相似链 2 帧），差别只在增长中。
        return (
            self._frozen_streak >= frozen_need
            and self._pending_count >= need
            or chain_rescue
        )

    # ------------------------------------------------------------------

    def _adjudicate(self, text: str) -> tuple[str, str]:
        """返回 (最终台词, 跳过原因)。跳过原因非空时台词无效。"""

        cfg = self.cfg
        if significant_count(text) < cfg.min_significant_chars:
            return "", "too_short"
        if len(flat_text(text)) > cfg.max_line_chars:
            return "", "too_long"

        speaker, body = split_speaker(text)
        line = body if speaker else text
        if speaker and not line:
            # 只有名字没有正文（姓名板单独帧）：等正文出现，不算跳过
            return "", "speaker_only"

        flat_line = flat_text(line)
        if not flat_line:
            return "", "empty"
        if significant_count(line) < cfg.min_significant_chars:
            return "", "no_text"

        if self._is_ui_line(line):
            return "", "ui_word"

        if speaker and speaker in cfg.protagonist_names and not cfg.dub_protagonist:
            return "", "protagonist"
        if not speaker and is_monologue(line) and not cfg.dub_monologue:
            return "", "monologue"
        if speaker and is_monologue(line):
            # 有主名的独白行：同样按独白开关处理
            if not cfg.dub_monologue:
                return "", "monologue"

        return line, ""

    def _is_ui_line(self, line: str) -> bool:
        flat = flat_text(line)
        for word in self.cfg.ui_words:
            fw = flat_text(word)
            if fw and flat == fw:
                return True
        return False

    def _consume(self, h: str) -> None:
        if len(self._seen) == self._seen.maxlen and self._seen:
            old = self._seen[0]
            # popleft 后重放 set，保持与 deque 环形淘汰一致
            self._seen_set.discard(old)
        self._seen.append(h)
        self._seen_set.add(h)

    def _record_skip(self, text: str, reason: str) -> None:
        self.skipped.appendleft({"text": text[:80], "reason": reason, "at": str(_now())})


def _now() -> float:
    import time

    return time.time()
