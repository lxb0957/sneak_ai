"""W26：决策流 —— F1 的时间线面板 + `--trace` 的 JSONL 日志。

这个功能的全部价值建立在一句话上：**它只是看，不参与判断**。
所以第一条断言不是"日志里有什么"，而是"打开它和关掉它，游戏必须一模一样"——
把 `Trace.push` 换成空操作再跑一遍同一局，逐帧轨迹的哈希要一位不差。
没有这一条，后面所有"好用的日志"都可能是在观测一个被日志改变过的世界。

其余四条各钉一个真实会出问题的地方：

2. **因果顺序**：石子落地必须排在"他转调查"**之前**。第一版把事件抄在
   帧末，日志里因果是反的 —— 读的人会以为他是先转身、后听见声音。
3. **只在变化时记**：否则一秒 60 条，面板刷成瀑布、日志也没法 grep。
4. **面板不挡 HUD**：430 宽、抬到 H-122 是因为底栏在 H-62、"暴露！快找掩体"
   在 H-96。压上去正好是最该看见的时候看不见。
5. **JSONL 能读回来**：跨关是追加写，格式必须是"一行一条、可流式解析"。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai.ai.trace import Trace  # noqa: E402
from sneak_ai.debug.debug_renderer import PANEL_ROWS, DebugRenderer  # noqa: E402

import botrun  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402


def _traj(level: int, seed: int, silence: bool):
    """跑一局，返回 (逐帧轨迹哈希, 帧数)。

    silence=True 时把 `Trace.push` 换成空操作 —— 这是"观测不影响被观测物"
    的判据：两次哈希一致，说明记日志这件事对模拟没有任何贡献。
    """
    orig = Trace.push
    if silence:
        Trace.push = lambda self, *a, **k: None
    try:
        g = Game(start_level=level)
        g.menu.active = False
        frames = []

        def on_frame(t, game, mode):
            w, p = game.world, game.player
            frames.append("|".join(
                [f"{p.x:.4f},{p.y:.4f},{p.stamina:.2f}"]
                + [f"{e.x:.4f},{e.y:.4f},{e.suspicion:.4f},{e.state_name}"
                   for e in w.enemies]
            ))

        r = botrun.run(level=level, game=g, budget=45.0,
                       rng=__import__("random").Random(seed), loot=True,
                       on_frame=on_frame)
        return hashlib.sha1("\n".join(frames).encode()).hexdigest(), len(frames), r
    finally:
        Trace.push = orig


def _run_traced(level: int, seed: int, budget: float):
    """跑一局，返回 (world.trace, 结果)。"""
    g = Game(start_level=level)
    g.menu.active = False
    r = botrun.run(level=level, game=g, budget=budget,
                   rng=__import__("random").Random(seed), loot=True)
    return g.world.trace, r


def test_trace_is_observation_only():
    """开着记日志和关掉记日志，逐帧轨迹必须完全相同。"""
    a, na, ra = _traj(4, 31337, silence=False)
    b, nb, rb = _traj(4, 31337, silence=True)
    assert na == nb, f"两次跑出的帧数不同：{na} vs {nb}"
    assert a == b, ("决策流改变了模拟：开/关 Trace.push 的轨迹哈希不同\n"
                    f"  on ={a}\n  off={b}")
    assert ra["pos"] == rb["pos"] and ra["time"] == rb["time"], (
        f"结局不同：{ra['pos']}/{ra['time']} vs {rb['pos']}/{rb['time']}")
    print(f"[OK] 决策流只读：{na} 帧轨迹哈希一致（{a[:12]}…）")


def test_stone_causes_the_switch():
    """第 5 关：石子落地 → 同一个哨兵转调查，且顺序不能反。"""
    tr, r = _run_traced(4, 7, 60.0)
    rows = list(tr.rows)
    land = [i for i, x in enumerate(rows)
            if x.kind == "world" and x.data.get("event") == "stone_land"]
    assert land, "60 秒里一颗石子都没落地：这一局没在玩第 5 关"

    found = None
    for i in land:
        for j in range(i + 1, min(i + 12, len(rows))):
            x = rows[j]
            if x.kind == "state" and x.data.get("to") == "investigate":
                if x.t - rows[i].t <= 0.6:
                    found = (rows[i], x)
                    break
        if found:
            break
    assert found, "石子落地之后的 0.6 秒内没有任何哨兵转调查"
    land_row, state_row = found
    print(f"[OK] 因果顺序：t={land_row.t:.2f} 石子落地 → "
          f"t={state_row.t:.2f} #{state_row.who} 转调查")


def test_only_on_change():
    """只在真的改变时记：同一只哨兵不出现 frm == to 的行，且量级可控。"""
    tr, _ = _run_traced(4, 11, 90.0)
    rows = list(tr.rows)
    assert rows, "90 秒一条事件都没有"
    ts = [x.t for x in rows]
    assert ts == sorted(ts), "时间戳不是单调的：日志顺序和模拟时间对不上"

    for x in rows:
        if x.kind == "state":
            assert x.data["frm"] != x.data["to"], f"记了一条没变化的状态：{x.text}"

    states = [x for x in rows if x.kind == "state"]
    assert len(states) <= 200, (
        f"90 秒记了 {len(states)} 条状态变化，平均每 0.45 秒一条："
        f"这已经不是'变化'而是'每帧'了")
    print(f"[OK] 只在变化时记：90 秒 {len(rows)} 条事件 / {len(states)} 条状态变化")


def test_panel_does_not_cover_hud():
    """面板必须落在窗口内、底栏和处境文字之上。"""
    for size in ((1280, 960), (1024, 768), (800, 600)):
        sw, sh = size
        rect = DebugRenderer.panel_rect(size, PANEL_ROWS)
        assert rect.left >= 0 and rect.top >= 0, f"{size} 面板跑到窗口外：{rect}"
        assert rect.right <= sw and rect.bottom <= sh, f"{size} 面板超出窗口：{rect}"
        # 底栏从 H-62 起、处境文字中心在 H-96（字高约 30 → 上沿 H-111）
        assert rect.bottom <= sh - 111, (
            f"{size} 面板压到了底栏/处境文字：底边 {rect.bottom} > {sh - 111}")
        # 只有垂直方向躲开是不够的判据吗？这里确实是 —— 处境提示居中在
        # W/2，1024 窗口下它的左沿（约 W/2-100）比面板右沿更靠右，但两者
        # 的**纵向**区间不重叠（面板底 H-122、提示上沿 H-111）。所以只钉
        # 纵向：横向那一刀是多余的，写上去会误伤 1024 这一档。
        assert rect.bottom < sh - 111, "面板底边没有严格高于处境文字"
    print("[OK] 面板几何：三档窗口都在窗口内、且在底栏与处境文字之上")


def test_jsonl_roundtrip():
    """落盘 → 读回：一行一条、字段齐、能流式解析、跨关追加。"""
    tr, _ = _run_traced(4, 13, 30.0)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.jsonl"
        n1 = tr.dump(out, level=4)
        n2 = tr.dump(out, level=4)          # 模拟第二关：追加而不是覆盖
        assert n1 == n2 == len(tr.rows), f"落盘条数不对：{n1}/{n2}/{len(tr.rows)}"

        lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        assert sum(1 for x in lines if x["kind"] == "level") == 2, "跨关追加丢了关卡头"
        body = [x for x in lines if x["kind"] != "level"]
        assert len(body) == n1 * 2, f"追加后条数不对：{len(body)}"
        for x in body:
            for k in ("t", "who", "kind", "text", "data"):
                assert k in x, f"JSONL 缺字段 {k}：{x}"
            assert isinstance(x["data"], dict), f"data 不是对象：{x}"
        # 时间戳可排序（面板/日志都靠它对齐）
        assert [x["t"] for x in body[:n1]] == sorted(x["t"] for x in body[:n1])
    print(f"[OK] JSONL：{n1} 条落盘、跨关追加、字段齐全可流式解析")


def test_ring_buffer():
    """容量满了丢最旧的，但累计计数不丢 —— 面板显示"最近"靠的就是这个。"""
    t = Trace(keep=5)
    for i in range(12):
        t.push("world", f"e{i}")
    assert len(t.rows) == 5, f"环形缓冲没截断：{len(t.rows)}"
    assert t.rows[-1].text == "e11", "丢的不是最旧的"
    assert t.total == 12, f"累计条数不对：{t.total}"
    print("[OK] 环形缓冲：容量 5 存 12 条，留最新 5 条、累计 12")


def main():
    print("=== W26：AI 决策流（F1 时间线 + --trace）===")
    test_trace_is_observation_only()
    test_stone_causes_the_switch()
    test_only_on_change()
    test_panel_does_not_cover_hud()
    test_jsonl_roundtrip()
    test_ring_buffer()
    print("\nW26 全部通过：看得见决策，且看这件事本身不改变游戏。")


if __name__ == "__main__":
    main()
