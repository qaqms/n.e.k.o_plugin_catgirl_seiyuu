"""i18n / 错误码同源契约门（常驻 pytest，纯文件解析，不依赖宿主 SDK）。

族 A · 版本身份同源：
  A1 plugin.toml ↔ pyproject.toml ↔ CHANGELOG.md 最新版本。

族 C · 面板/入口 i18n 契约（与 forever_companion 主项 #10、your_memory 12 门同源）：
  C1 双语 bundle 键集一致（宿主按精确键查表，缺键即某语言直出码/英文）；
  C2 TSX `t("key")` 引用面 ⊆ bundle 键集；
  C3 zh bundle 值与 TSX `defaultValue` 逐字同源（防漂移：改文案必须两处同改，
     和 ym 一样把「面板所见中文 == bundle」钉死）；
  C4 后端 `tr(key, default=...)`（@plugin_entry name / @ui.action label）与
     zh bundle 逐字同源；注册文案不许裸中文字面量；
  C5 死键门：bundle 键必须被 TSX/后端引用，或属于 panel.errors.*（由 C6 双向
     同源门覆盖），不留「改了没人看」的僵尸键；
  C6 错误码契约：面板可达入口的 `Err(SdkError(...))` 字面实参只允许
     `^[a-z][a-z0-9_]*$` 稳定码且 ⊆ PANEL_ERROR_CODES；PANEL_ERROR_CODES 与
     bundle `panel.errors.<camelCase(码)>` 键集双向相等。非字面实参只允许
     `_stable_code(...)` 归一化调用（动态细节进日志，不进文案）；
  C7 暂停原因/模式字面量与 bundle `panel.pauseReason.*` / `panel.modeLabel.*`
     双向同源；
  C8 TSX 用户可见面不得残留中文字面量（剥注释、defaultValue 与豁免表后扫
     CJK；豁免：给宿主 TTS 的测试语料——那是数据不是文案）。

已知豁免面（有意为之，扩展要补注释并同步 README/CHANGELOG）：

- `@llm_tool` description / 返回值 note：给主 AI 的行为指令，中文合法（fc 同款）；
- `_push_hud` / `push_message` 运行时文案：HUD 走宿主按 session 路由，插件侧
  不知道目标 locale，且不经面板 bundle → 本轮保持中文（后续轮调研宿主 i18n）；
- `@plugin_entry` 的 `description`：宿主工具列表面向开发者/排障，与
  plugin.toml 的 description 同性质（卡片本地化另议）；
- bundle 的 `plugin.name` / `plugin.description` 卡片键：本插件未使用。
"""

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSX = (ROOT / "ui" / "panel.tsx").read_text(encoding="utf-8")
BACKEND = (ROOT / "__init__.py").read_text(encoding="utf-8")
STATE_PY = (ROOT / "core" / "state.py").read_text(encoding="utf-8")

# 宿主 runtime 插值语法（ui-kit interpolateI18n）：{name}
HOLDER = re.compile(r"\{\s*([A-Za-z_]\w*)\s*\}")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 数据面豁免：给宿主 TTS 的测试语料不是界面文案
TSX_CJK_EXEMPT = ["配音通道测试，一。配音通道测试，二。"]


def bundle(name: str) -> dict[str, str]:
    data = json.loads((ROOT / "i18n" / f"{name}.json").read_text(encoding="utf-8"))
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()), f"{name}: 必须扁平 键->字符串"
    return data


def camel(code: str) -> str:
    return re.sub(r"_+([a-z0-9])", lambda m: m.group(1).upper(), code)


# TSX 静态 t("key" 引用 → 出现位置列表
def tsx_t_calls(source: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1)) for m in re.finditer(r'\bt\(\s*"([^"]+)"', source)]


def tsx_default_values(source: str) -> list[tuple[int, str]]:
    return [
        (m.start(), m.group(1))
        for m in re.finditer(r'defaultValue:\s*"((?:[^"\\]|\\.)*)"', source)
    ]


# ---------------------------------------------------------------- 族 A


def test_a1_version_single_sourced():
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
    assert top, "CHANGELOG 找不到 [x.y.z] 版本节"
    v = manifest["plugin"]["version"]
    assert v == pyproject["project"]["version"] == top.group(1), (
        f"版本漂移: plugin.toml={v} pyproject={pyproject['project']['version']} changelog={top.group(1)}"
    )


# ---------------------------------------------------------------- 族 C


def test_c1_locale_keysets_identical():
    zh, en = bundle("zh-CN"), bundle("en")
    assert set(zh) == set(en), f"键集漂移 zh-only={set(zh) - set(en)} en-only={set(en) - set(zh)}"


def test_c2_tsx_references_covered_by_bundle():
    keys = {k for _, k in tsx_t_calls(TSX)}
    missing = {k for k in keys if k not in bundle("zh-CN")}
    assert not missing, f"TSX 引用了 bundle 没有的键：{missing}"


def test_c3_zh_bundle_matches_tsx_default_values():
    zh = bundle("zh-CN")
    pairs: dict[str, list[str]] = {}
    t_calls = tsx_t_calls(TSX)
    for pos, dv in tsx_default_values(TSX):
        precede = [tp for tp, tk in t_calls if tp < pos]
        assert precede, f"defaultValue 出现在任何 t() 之前（pos={pos}）"
        key = dict(t_calls)[max(precede)]
        pairs.setdefault(key, []).append(dv)
    for key, defaults in pairs.items():
        assert set(defaults) == {zh[key]}, (
            f"{key}: TSX defaultValue {sorted(set(defaults))} != zh bundle {zh[key]!r}"
        )


def test_c4_backend_tr_defaults_match_zh_bundle():
    """后端 tr() 注册文案：键必须进 bundle；若 default 写了中文则必须与 zh 同源。

    ui.action 的 label 约定用英文兜底（与 fc 一致，宿主按 locale 查 bundle，
    只有查不到时才回落 default），故英文 default 不强制相等；但 entries.*
    （入口名）default 写的是中文，必须与 bundle 逐字同源，防止改一处漏一处。
    """

    zh = bundle("zh-CN")
    trs = re.findall(r'\btr\(\s*"([^"]+)"\s*,\s*default="((?:[^"\\]|\\.)*)"', BACKEND)
    assert trs, "后端找不到 tr() 注册文案——装饰器退化？"
    for key, default in trs:
        assert key in zh, f"tr 引用缺键 {key}"
        if CJK.search(default):
            assert zh[key] == default, f"{key}: tr 中文 default {default!r} != zh bundle {zh[key]!r}"
    # 入口名（面板/工具列表可见）必须逐条有中文兜底，不能像 label 那样留英文
    for key, default in trs:
        if key.startswith("entries."):
            assert CJK.search(default), f"{key}: 入口名 default 应为中文，实际 {default!r}"


def test_c5_no_dead_bundle_keys():
    zh = bundle("zh-CN")
    referenced = {k for _, k in tsx_t_calls(TSX)}
    referenced |= set(re.findall(r'\btr\(\s*"([^"]+)"', BACKEND))
    errors = {k for k in zh if k.startswith("panel.errors.")}
    dead = set(zh) - referenced - errors
    assert not dead, f"bundle 死键（TSX/后端均无静态引用）：{dead}"


def _sdk_error_args() -> list[ast.expr]:
    tree = ast.parse(BACKEND)
    args: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SdkError":
            if node.args:
                args.append(node.args[0])
    return args


def test_c6a_backend_error_args_are_codes_or_normalized():
    from catgirl_seiyuu import PANEL_ERROR_CODES  # conftest 已注入桩 SDK 并加载包

    for arg in _sdk_error_args():
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            assert CODE_RE.match(arg.value), f"SdkError 实参必须是稳定 ASCII 码：{arg.value!r}"
            assert arg.value in PANEL_ERROR_CODES, f"码不在 PANEL_ERROR_CODES：{arg.value!r}"
        elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "_stable_code":
            continue  # 归一化调用：值域由映射表 + fallback 保证 ⊆ PANEL_ERROR_CODES
        else:
            raise AssertionError(f"SdkError 实参形态非法（裸码或 _stable_code 之外一律拒绝）：{ast.dump(arg)[:120]}")


def test_c6b_error_codes_and_bundle_bidirectional():
    from catgirl_seiyuu import PANEL_ERROR_CODES

    zh, en = bundle("zh-CN"), bundle("en")
    for name, data in (("zh-CN", zh), ("en", en)):
        keys = {k[len("panel.errors."):] for k in data if k.startswith("panel.errors.")}
        codes_camel = {camel(c) for c in PANEL_ERROR_CODES}
        assert keys == codes_camel, f"{name}: panel.errors 键集 != PANEL_ERROR_CODES，差集 {keys ^ codes_camel}"


def test_c6c_fallback_and_maps_stay_in_contract():
    from catgirl_seiyuu import _ERROR_CODE_MAP, PANEL_ERROR_CODES

    for name, data in (("zh-CN", bundle("zh-CN")), ("en", bundle("en"))):
        # fallback 码自身必须有文案，否则归一化成功≠用户看得见
        assert "panel.errors.captureFailed" in data, "fallback 码 capture_failed 缺文案"
    for src, target in _ERROR_CODE_MAP.items():
        assert CODE_RE.match(src), f"映射键本身必须码形：{src!r}"
        assert target in PANEL_ERROR_CODES, f"映射值越界：{src} -> {target}"


def test_c6d_stable_code_normalizes_runtime_errors():
    from catgirl_seiyuu import PANEL_ERROR_CODES, _stable_code

    cases = {
        "unsupported_platform:linux": "capture_unsupported",
        "printwindow_returned_false": "capture_failed",
        "ocr_extract_failed:RuntimeError('x')": "ocr_failed",
        "shared_rapidocr_unavailable:ImportError(...)": "ocr_unavailable",
        # 带中文细节的旧形态：base 在 ':' 前切开，细节不进状态快照
        "target_minimized: 请还原游戏窗口后继续": "target_minimized",
        "完全不可读的中文错误": "capture_failed",
        "": "capture_failed",
        # 码形合法但非契约码（如未来新增的内部码）：落 fallback 而非直接喷给用户
        "brand_new_internal_code:细节": "capture_failed",
    }
    for raw, want in cases.items():
        got = _stable_code(raw)
        assert got == want, f"_stable_code({raw!r}) = {got!r}，期望 {want!r}"
        assert got in PANEL_ERROR_CODES


def test_c7_pause_reasons_and_modes_single_sourced():
    from catgirl_seiyuu import PAUSE_REASONS

    zh = bundle("zh-CN")
    reasons = set(re.findall(r'_mode\.pause\("([^"]+)"\)', BACKEND))
    assert reasons <= PAUSE_REASONS, f"_mode.pause 用了契约外原因：{reasons - PAUSE_REASONS}"
    bundle_reasons = {k[len("panel.pauseReason."):] for k in zh if k.startswith("panel.pauseReason.")}
    assert bundle_reasons == set(PAUSE_REASONS), f"pauseReason 键集 != PAUSE_REASONS：{bundle_reasons ^ set(PAUSE_REASONS)}"

    modes = set(re.findall(r'^MODE_(?:OFF|RUNNING|PAUSED) = "([^"]+)"', STATE_PY, re.M))
    bundle_modes = {k[len("panel.modeLabel."):] for k in zh if k.startswith("panel.modeLabel.")}
    assert modes == bundle_modes, f"状态机模式值 != panel.modeLabel 键集：{modes ^ bundle_modes}"


def test_c8_tsx_no_hardcoded_cjk_outside_contract():
    src = TSX
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)  # 块注释
    src = re.sub(r"(?<!:)//[^\n]*", "", src)  # 行注释（避开 URL 的 ://）
    src = re.sub(r'defaultValue:\s*"(?:[^"\\]|\\.)*"', "", src)  # t() 兜底文案（C3 已管）
    for lit in TSX_CJK_EXEMPT:
        src = src.replace(f'"{lit}"', '""')
    hits = [(m.start(), src[max(0, m.start() - 40):m.start() + 10]) for m in CJK.finditer(src)]
    assert not hits, f"TSX 残留硬编码中文（应收编进 t()+bundle）：{hits[:5]}"


def test_c9_interpolation_params_present():
    """带 {placeholder} 的键：调用点必须以顶层参数传入；双语占位集合一致。"""
    zh, en = bundle("zh-CN"), bundle("en")
    for key, value in zh.items():
        holders = {h.group(1) for h in HOLDER.finditer(value)}
        holders |= {h.group(1) for h in HOLDER.finditer(en[key])}
        if not holders:
            continue
        call_sites = [pos for pos, k in tsx_t_calls(TSX) if k == key]
        assert call_sites, f"键 {key} 的占位符要求 TSX 调用点传参，但没找到 t(\"{key}\")"
        window_start = call_sites[0]
        window = TSX[window_start:window_start + 600]
        for holder in holders:
            assert re.search(rf"\b{re.escape(holder)}\s*:", window), (
                f"{key}: 调用点缺插值参数 {holder!r}"
            )
