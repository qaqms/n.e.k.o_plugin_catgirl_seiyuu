"""反向对照：逐门定向破坏 → 目标门必红 → 还原 → 终验全绿。

级联判据（ym 台账纪律）：目标门出现在红名单即算可红，不要求孤红。
"""

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# 与 release_gate 同源：Windows GBK 控制台下 summary 的 emoji 会炸 traceback，
# 统一码面 UTF-8 + 容忍不可编码字符（v0.2.2 反向对照实测踩中）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 已被重定向/替换过的流
        pass

ROOT = Path(__file__).resolve().parents[1]
CUR_VERSION = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["plugin"]["version"]
FILES = ["ui/panel.tsx", "__init__.py", "i18n/zh-CN.json", "i18n/en.json", "pyproject.toml"]
BACKUP_DIR = Path(".reverse-backup")

# (标签, 文件, 旧串, 新串, 期望红的测试名)
MUTATIONS = [
    ("C1 双语键集", "i18n/en.json", '"panel.windowFilter"', '"panel.enonly": "x",\n  "panel.windowFilter"', "test_c1_locale_keysets_identical"),
    ("C2 TSX 引用面", "ui/panel.tsx", 't("panel.mode", {', 't("panel.nope", {', "test_c2_tsx_references_covered_by_bundle"),
    ("C3 defaultValue 同源", "ui/panel.tsx", 'defaultValue: "区域已保存"', 'defaultValue: "区域已保存啦"', "test_c3_zh_bundle_matches_tsx_default_values"),
    ("C4 tr() 中文同源", "__init__.py", 'default="窗口列表"', 'default="窗口列表们"', "test_c4_backend_tr_defaults_match_zh_bundle"),
    ("C5 死键", "i18n/zh-CN.json", '"panel.windowFilter": "窗口过滤",', '"panel.windowFilter": "窗口过滤",\n  "panel.dead.zombie": "僵尸",', "test_c5_no_dead_bundle_keys"),
    ("C6a 中文码回潮", "__init__.py", 'return Err(SdkError("not_running"))', 'return Err(SdkError("当前不在配音中"))', "test_c6a_backend_error_args_are_codes_or_normalized"),
    ("C6b errors 双向同步", "i18n/zh-CN.json", '  "panel.errors.unknownSetting": "未知设置项"\n', "", "test_c6b_error_codes_and_bundle_bidirectional"),
    ("C6d 归一化映射", "__init__.py", '"ocr_extract_failed": "ocr_failed",', '"ocr_extract_failed": "ocr_broke",', "test_c6d_stable_code_normalizes_runtime_errors"),
    ("C7 暂停原因同源", "__init__.py", 'self._mode.pause("manual")', 'self._mode.pause("weird")', "test_c7_pause_reasons_and_modes_single_sourced"),
    ("C8 TSX 硬编码中文", "ui/panel.tsx", 'const mode = state.mode || "off"', 'const mode = state.mode || "未开启态"', "test_c8_tsx_no_hardcoded_cjk_outside_contract"),
    ("C9 插值参数缺位", "ui/panel.tsx", "defaultValue: \"区域 x={x} y={y} w={w} h={h}（相对窗口比例）\", x: shown.x.toFixed(2),", "defaultValue: \"区域 x={x} y={y} w={w} h={h}（相对窗口比例）\",", "test_c9_interpolation_params_present"),
    ("A1 版本同源", "pyproject.toml", f'version = "{CUR_VERSION}"', 'version = "9.9.9"', "test_a1_version_single_sourced"),
]


def run_pytest() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_i18n_contract.py", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    BACKUP_DIR.mkdir(exist_ok=True)
    for f in FILES:
        src = ROOT / f
        dst = BACKUP_DIR / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    results = []
    try:
        for label, fname, old, new, want_test in MUTATIONS:
            path = ROOT / fname
            text = path.read_text(encoding="utf-8")
            assert old in text, f"[{label}] 破坏锚点未命中：{fname}"
            assert text.count(old) == 1 or label.startswith("C1"), f"[{label}] 锚点不唯一：{fname}"
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            code, out = run_pytest()
            failed = set(re.findall(r"FAILED tests/test_i18n_contract\.py::(\w+)", out))
            red = code != 0
            hit = want_test in failed
            results.append((label, want_test, red, hit, sorted(failed)))
            # 还原本门
            shutil.copy2(BACKUP_DIR / fname, path)
            if not red or not hit:
                # 立即单独重验一次还原是否成功
                code2, _ = run_pytest()
                print(f"  [{label}] 还原后复跑 rc={code2}")
    finally:
        for f in FILES:
            shutil.copy2(BACKUP_DIR / f, ROOT / f)
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)

    print("\n================ 反向对照报告 ================")
    all_ok = True
    for label, want, red, hit, failed in results:
        mark = "OK " if (red and hit) else "BAD"
        if not (red and hit):
            all_ok = False
        print(f"[{mark}] {label}: 破坏后红={red} 目标门命中={hit} 红名单={failed}")
    print("\n" + ("全部可红 ✅" if all_ok else "存在哑门，勿发布！"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
