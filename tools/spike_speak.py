# -*- coding: utf-8 -*-
"""Spike A: 验证宿主游戏 SDK B 层语音接口对插件的可用性。

验证目标（对应 DESIGN_BRIEF_catgirl_seiyuu.md §4.3）：
  1. 预路由（无 /route/start）直接 POST /api/game/<game_type>/speak 是否被接受；
  2. line 是否被猫娘声线**逐字**播报（镜射文本气泡）；
  3. wait_for_audio_completion=true 时请求是否同步到播完才返回（串行排程依据）；
  4. 连续两句的排程是否互不重叠；
  5. 响应里的 voice_source / method 字段形状（插件侧解析用）。

用法：在 N.E.K.O 宿主运行时执行
    python tools/spike_speak.py [--port 48911] [--lanlan 灵] [--text "..."]

这是一次性验证脚本，不进插件运行时。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

GAME_TYPE = "catgirl_seiyuu"


def _post(base: str, path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return {"_http_error": e.code, "_body": raw[:500]}
    except Exception as e:  # noqa: BLE001
        return {"_error": repr(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=48911)
    ap.add_argument("--lanlan", default="", help="角色名；留空=宿主当前角色")
    ap.add_argument("--text", default="测试配音通道，一。测试配音通道，二。")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    failures = 0

    # 0) health：拿 instance_id（CSRF 兜底备用）
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as r:
            health = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 宿主 main server 不可达 {base}: {e}")
        return 1
    print(f"[ok] health: {health}")

    # 1) 单句、非阻塞（queued 即返回）
    body = {"line": args.text.split("。")[0] + "。", "mirror_text": True}
    if args.lanlan:
        body["lanlan_name"] = args.lanlan
    r1 = _post(base, f"/api/game/{GAME_TYPE}/speak", body, 10.0)
    print(f"[1] pre-route speak(queued): {json.dumps(r1, ensure_ascii=False)}")
    if r1.get("ok") is not True:
        failures += 1
        print("    -> 预路由播报被拒，检查 reason")
    else:
        print("    -> PASS 预路由可用")

    # 2) 单句、阻塞等播完
    body2 = dict(body)
    body2["line"] = args.text.split("。")[1] + "。" if "。" in args.text else args.text
    body2["wait_for_audio_completion"] = True
    t0 = time.monotonic()
    r2 = _post(base, f"/api/game/{GAME_TYPE}/speak", body2, 60.0)
    dur = time.monotonic() - t0
    print(f"[2] blocking speak: {dur:.2f}s -> {json.dumps(r2, ensure_ascii=False)[:400]}")
    if r2.get("ok") is True and dur > 0.5:
        print("    -> PASS 完成回调可用（同步等待时长即排程依据）")
    else:
        failures += 1

    print()
    print("SPEAK SPIKE:", "ALL PASS" if failures == 0 else f"{failures} FAIL(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
