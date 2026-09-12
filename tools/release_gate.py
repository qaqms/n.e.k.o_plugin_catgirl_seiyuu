#!/usr/bin/env python3
"""发版前全链校验：把本插件的门一次跑完，任一红即非零退出（ym/fc 同款移植）。

为什么需要它：本仓是宿主仓的**同级**独立目录，宿主工具链默认插件住在
`<host>/plugin/plugins/<id>/` 下，于是两类命令在独立仓里直接失效——

1. `neko-plugin check catgirl_seiyuu`（按 id）找不到目录，只能用路径形式；
2. `frontend/plugin-manager` 的 `check-hosted-tsx` 会 `realpathSync` 目标并断言
   它仍在宿主仓内（`assertPathInsideRepo`），所以 **junction/符号链接也过不了**，
   必须是宿主仓内的真实副本。

本脚本把 ② 需要的副本生命周期收进自己手里：复制到宿主仓的临时目录 → 跑门 →
无论成败都清理，绝不在宿主仓留残留物（宿主 .gitignore 没有本插件的条目，留下来
就是脏工作树）。副本用点前缀目录名，与内置插件列表一眼区分。

五门（ym 版式）：pytest / ruff / check / release（CI 复刻：挂载 + 按裸 id 跑
`check -r`）/ hosted-tsx。release 门钉住 ym 台账纪律：「凡按目录名/路径形态断言
的门必须区分独立仓态与挂载态」——本地路径形式的绿 ≠ 市场 CI 的绿。

用法（在插件目录内）：
    uv run python tools/release_gate.py            # 全跑
    uv run python tools/release_gate.py --only release,hosted-tsx
    uv run python tools/release_gate.py --keep     # 跑完保留宿主仓副本，便于反复迭代

宿主仓位置解析顺序：--host-root > $NEKO_HOST_ROOT > 本插件的 ../../N.E.K.O。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows 控制台默认码面是 GBK：summary 里一个 emoji 就能让全绿的运行以 traceback 收场，
# 而 CI 会把这看成门禁失败。统一把输出码面换成 UTF-8 并容忍不可编码字符。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 已被重定向/替换过的流
        pass

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR_NAME = ".catgirl_seiyuu-gate-probe"  # 点前缀：与内置插件区分，且天然不匹配 id 扫描
MOUNT_DIR_NAME = "catgirl_seiyuu"             # CI 复刻态：宿主 plugin/plugins/ 下的裸 id 目录
GATES = ("pytest", "ruff", "check", "release", "hosted-tsx")


def _resolve_host_root(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("NEKO_HOST_ROOT") or str(PLUGIN_ROOT.parent / "N.E.K.O")
    root = Path(raw).expanduser().resolve()
    if not (root / "plugin" / "sdk").is_dir():
        raise SystemExit(
            f"宿主仓看起来不对：{root}\n"
            "  期望它是 N.E.K.O 宿主仓根（含 plugin/sdk）。用 --host-root 或环境变量 NEKO_HOST_ROOT 指定。"
        )
    return root


def _exe(name: str) -> str:
    """解析可执行文件绝对路径。

    Windows 上 npm/npx 是 npm.cmd，而 subprocess 在 shell=False 下不走 PATHEXT，
    直接传 "npm" 会 WinError 2；uv/node 同理（.exe）。解析不到就原样交回，
    让 _run 抛出可读错误。
    """
    return shutil.which(name) or name


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    argv = [_exe(cmd[0]), *cmd[1:]]
    # 码面钉 UTF-8：text=True 默认按本地码页（Windows=GBK）解码子进程管道，
    # 子进程输出 UTF-8 中日韩字节即崩 reader 线程（fc 实测 UnicodeDecodeError），
    # 判定靠 returncode 不受连累，但门日志会丢一大截——与脚本自身的 stdout 同口径
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=False)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def _tail(text: str, limit: int = 12) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(f"    {ln}" for ln in lines[-limit:])


def _uv() -> list[str]:
    return ["uv", "run", "--project", str(PLUGIN_ROOT.parent / "N.E.K.O")]


def gate_pytest(host_root: Path, probe: Path | None) -> tuple[bool, str]:
    ok, out = _run([*_uv(), "python", "-m", "pytest", "tests", "-q"], PLUGIN_ROOT)
    return ok, _tail(out)


def gate_ruff(host_root: Path, probe: Path | None) -> tuple[bool, str]:
    # CI 原样参数：市场 CI 跑 --ignore-noqa（钉 ruff==0.12.4 + 本仓 ruff.toml），
    # 本地 noqa 抑制在远端不作数（fc/ym 两次踩坑）。
    ok, out = _run(["uvx", "ruff==0.12.4", "check", "--ignore-noqa",
                    "--config", "ruff.toml", "."], PLUGIN_ROOT)
    return ok, _tail(out)


def gate_check(host_root: Path, probe: Path | None) -> tuple[bool, str]:
    # 路径形式：独立仓不在宿主的 plugin/plugins 下，按 id 必然找不到
    ok, out = _run([*_uv(), "neko-plugin", "check", str(PLUGIN_ROOT)], host_root)
    return ok, _tail(out, 8)


def gate_release(host_root: Path, probe: Path | None) -> tuple[bool, str]:
    # CI 复刻：市场 verify 是把仓库挂进 <host>/plugin/plugins/<id> 后按**裸 id**
    # 跑 check -r 的。用独立仓路径形式跑量不到挂载态才有的行为（目录名变裸
    # id 等），本地全绿 CI 必红——故此处真实复制 + 按 id。
    mount = host_root / "plugin" / "plugins" / MOUNT_DIR_NAME
    if mount.exists():
        return False, f"    宿主仓已存在 {mount}，拒覆盖现有副本"
    _copy_to(mount)
    ok, out = _run([*_uv(), "neko-plugin", "check", "-r", MOUNT_DIR_NAME], host_root)
    return ok, _tail(out, 12)


def gate_hosted_tsx(host_root: Path, probe: Path | None) -> tuple[bool, str]:
    if probe is None or not probe.exists():
        return False, "    宿主仓副本不存在（需要先跑本脚本的复制步骤）"
    rel = (Path("plugin") / "plugins" / probe.name).as_posix()
    ok, out = _run(["npm", "run", "check-hosted-tsx", "--", rel],
                   host_root / "frontend" / "plugin-manager")
    return ok, _tail(out, 10)


# 注意：.vscode 必须在副本内——它是仓库存档文件（check -r 要求 settings.json/
# tasks.json 两件仓库支持文件，缺位即拒），CI 的 cp -R 也是全量拷贝。
# 只排除本地跑出来的缓存与虚环境。
_EXCLUDES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _copy_to(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _EXCLUDES or n.endswith(".neko-plugin")}

    src = PLUGIN_ROOT
    for child in sorted(src.iterdir()):
        if child.name in _EXCLUDES:
            continue
        dst = target / child.name
        if child.is_dir():
            shutil.copytree(child, dst, ignore=_ignore)
        else:
            shutil.copy2(child, dst)


def _make_probe_copy(host_root: Path) -> Path:
    target = host_root / "plugin" / "plugins" / PROBE_DIR_NAME
    _copy_to(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="catgirl_seiyuu 发版前全链校验")
    parser.add_argument("--host-root", help="N.E.K.O 宿主仓根目录（默认 ../../N.E.K.O 或 $NEKO_HOST_ROOT）")
    parser.add_argument("--only", help=f"逗号分隔的门子集，可选 {','.join(GATES)}")
    parser.add_argument("--keep", action="store_true", help="跑完保留宿主仓副本（便于反复迭代 hosted-tsx）")
    args = parser.parse_args()

    wanted = [g.strip() for g in args.only.split(",")] if args.only else list(GATES)
    unknown = [g for g in wanted if g not in GATES]
    if unknown:
        raise SystemExit(f"未知门：{unknown}；可选：{list(GATES)}")

    host_root = _resolve_host_root(args.host_root)
    runners = {
        "pytest": gate_pytest,
        "ruff": gate_ruff,
        "check": gate_check,
        "release": gate_release,
        "hosted-tsx": gate_hosted_tsx,
    }

    print(f"plugin: {PLUGIN_ROOT}\nhost:   {host_root}\n")

    probe: Path | None = None
    mount: Path | None = None
    results: list[tuple[str, bool]] = []
    try:
        if "hosted-tsx" in wanted:
            probe = _make_probe_copy(host_root)
            print(f"probe copy: {probe}\n")
        for name in wanted:
            ok, detail = runners[name](host_root, probe or Path(tempfile.gettempdir()))
            results.append((name, ok))
            print(f"[{'OK ' if ok else 'FAIL'}] {name}")
            if detail:
                print(detail)
    finally:
        # 无论前面哪道门抛异常，两份宿主仓副本都必须消失：宿主 .gitignore 没有
        # 本插件条目，留残就是给人家仓库刷脏（release 门的挂载副本也在内）
        mount = host_root / "plugin" / "plugins" / MOUNT_DIR_NAME
        leftovers = [p for p in (probe, mount) if p and p.exists()]
        for p in leftovers:
            if args.keep:
                print(f"\n保留宿主仓副本（记得自己删）：{p}")
            else:
                shutil.rmtree(p, ignore_errors=True)
        if leftovers and not args.keep:
            print("\n已清理宿主仓副本")

    failed = [n for n, ok in results if not ok]
    print("\n" + ("全链通过 ✅" if not failed else f"失败的门：{failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
