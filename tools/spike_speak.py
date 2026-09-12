# -*- coding: utf-8 -*-
"""S: 配音通道自检（对齐 v0.2+ B 层 /speak 契约，复刻 services/host_api.py 请求面）。

v0.1 首发版 spike 的历史坑（本次收编修正）：
  - 缺 X-CSRF-Token（GET /health → instance_id）与 Origin 头 → 对启用 CSRF
    校验的宿主直接 403，误判成"B 层不可用"；
  - body 缺 game_type 字段（宿主按 body 优先解析）；
  - 未覆盖错误码路径，排障时给不出可定位的输出。

验证目标（宿主运行、插件不必启动，纯 HTTP）：
  1. /health 可达并拿到 instance_id；
  2. 预路由非阻塞 speak（wait=false）被接受且返回 ok:true；
  3. 阻塞 speak（wait=true）同步到播完才返回（时长 >0 即"一句播完再播
     下一句"排程依据成立）；
  4. 响应里 voice_source / method 等字段形状打印出来供人眼核对。

用法：python tools/spike_speak.py [--port 48911] [--lanlan 灵] [--text "..."]
退出码：0 全过；1 任一失败（输出里有 FAIL 定位）。
本脚本只用于人工排障，不进插件运行时、不进 release 门。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

GAME_TYPE = "catgirl_seiyuu"


def _request(method: str, url: str, body: dict | None, timeout: float, headers: dict[str, str]) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {"ok": True, "data": payload}
    except urllib.error.HTTPError as e:  # 与 HostSpeakClient._request 同形：先试解析 body
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {**parsed, "_http_error": e.code}
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": f"http_{e.code}", "_body": raw[:500]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "transport_error", "detail": repr(e)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=48911)
    ap.add_argument("--lanlan", default="", help="角色名；留空=宿主当前角色")
    ap.add_argument("--text", default="测试配音通道，一。测试配音通道，二。")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    failures = 0

    # 0) health：instance_id 作 X-CSRF-Token（与 HostSpeakClient._instance_id 同源）
    probe = _request("GET", f"{base}/health", None, 3.0, {})
    instance_id = str(probe.get("instance_id") or "")
    if not probe.get("ok", True) or not instance_id:
        print(f"[FAIL] 宿主 main server 不可达或无 instance_id: {json.dumps(probe, ensure_ascii=False)[:200]}")
        return 1
    print(f"[ok] health: instance_id={instance_id[:8]}…")

    headers = {
        "Content-Type": "application/json",
        "Origin": base,
    }
    if instance_id:
        headers["X-CSRF-Token"] = instance_id

    sentences = [s.strip() + "。" for s in args.text.split("。") if s.strip()] or [args.text]
    path = f"/api/game/{GAME_TYPE}/speak"

    def _body(line: str, wait: bool) -> dict:
        b: dict = {"line": line, "game_type": GAME_TYPE, "wait_for_audio_completion": wait, "mirror_text": True}
        if args.lanlan:
            b["lanlan_name"] = args.lanlan
        return b

    # 1) 非阻塞（queued 即返回）：预路由可用性
    r1 = _request("POST", base + path, _body(sentences[0], False), 10.0, headers)
    print(f"[1] speak(wait=false): {json.dumps(r1, ensure_ascii=False)[:300]}")
    if r1.get("ok") is not True:
        failures += 1
        print(f"    -> FAIL 预路由播报被拒 reason={r1.get('reason')!r}")
    else:
        print("    -> PASS 预路由可用")

    # 2) 阻塞等播完：完成回调 + 串行排程依据
    line2 = sentences[1] if len(sentences) > 1 else sentences[0]
    t0 = time.monotonic()
    r2 = _request("POST", base + path, _body(line2, True), 120.0, headers)
    dur = time.monotonic() - t0
    print(f"[2] speak(wait=true) {dur:.2f}s: {json.dumps(r2, ensure_ascii=False)[:300]}")
    if r2.get("ok") is True:
        print(f"    -> PASS 同步回执可用（{'含真实播放时长' if dur > 0.5 else '⚠ 时长过短，可能未真正播完，检查 TTS 后端'}）")
    else:
        failures += 1
        print(f"    -> FAIL reason={r2.get('reason')!r}")
    for field in ("voice_source", "method", "audio_duration"):
        if field in r2:
            print(f"    · 契约字段 {field} = {r2[field]!r}")

    print()
    print("SPEAK SPIKE:", "ALL PASS" if failures == 0 else f"{failures} FAIL(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
