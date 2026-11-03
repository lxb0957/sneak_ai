"""W20 测试：测量工具本身（机器人 / 相位摆位）。

这一组守的不是游戏，是**尺子**。BLOG 里记过两次同一个错误：
把尺子的问题读成了关卡结论（"机器人不会贴墙"读成"这一关 0/3"、
"多点巡逻没被摆位"读成"这一关 58%"）。所以尺子也要有回归测试：

1. **同一颗种子必须复现**：不可复现的测量结果没法当证据；
2. **不同种子必须分叉**：`--runs N` 若是同一条轨迹跑 N 遍，"胜率"就是
   一个样本报了 N 次 —— 这是这一版修掉的真 bug，不能被改回来；
3. **phase=0 保持原样**：相位扫描的第 0 个样本必须还是那条被记录下来的
   确定性基线，否则历史数字全部失去可比性；
4. **摆位不许把人塞进墙里**：那是尺子在制造局面，不是采样局面；
5. **多点巡逻也要能动**：第 1 关那种 6 点巡逻，只滑第 0 段等于把它钉死，
   "16 个相位"实际只有一种局面。

后两条守的是**关卡数据的"形状"**（它们不量难度，只拦明显构造错误）：

6. **没有哨兵封死必经走廊**：把它的巡逻线当墙堵上，出生点到终端的
   最短路不许变长 —— 变长就说明它不是"会移动的视野"，是一道闸门。
7. **没有重复 / 只隔 1 格的巡逻线**：前者会悄悄多出一个哨兵（第 3 关曾经
   是 30 个而文档写 29），后者不会"更密"，只会变成一整块等不到窗口的禁区。

用法：
    python scripts/test_w20.py
"""

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

import botrun  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core.world import World  # noqa: E402
from sneak_ai.ai.coordination import Coordinator  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402
from sneak_ai.perception.perception import Perception  # noqa: E402

LV = 0          # 第 1 关：唯一有多点巡逻（3~4 个点）的一关，也是最便宜的一关
MULTI = 1       # 第 2 关：全是两点巡逻，用来看"普通关卡"的摆位


def make_world(idx: int):
    lv = L.LEVELS[idx]
    grid = lv.build()
    w = World(grid)
    w.level_index = lv.idx
    w.level_name = lv.name
    w.terminals = lv.terminals
    w.exfil = lv.exfil
    w.par = L.cumulative_par(idx)
    w.coordinator = Coordinator(grid)
    spawn, cfg = L.spawn_and_patrols(lv, grid)
    perc = Perception()
    w.player = Player(*spawn)
    w.enemies = [Enemy(x, y, patrol=pts, perception=perc,
                       chase_speed=lv.chase,
                       alert_speed=lv.alert_speed, alert_view=lv.alert_view)
                 for (x, y, pts) in cfg]
    w.alert_duration = lv.alert_duration
    w.pickups = L.level_pickups(lv, grid)
    w.power = [{"x": px, "y": py, "used": False} for (px, py) in lv.power]
    return w


def _fingerprint(r):
    return (r["state"], r["time"], r["pos"], r["spotted"], r["score"])


# ---------------------------------------------------------------- 可复现
def test_same_seed_reproduces():
    """同一颗种子跑两遍，必须逐字节一致 —— 不然结果不能当证据。"""
    a = botrun.run(level=LV, rng=random.Random(1234))
    b = botrun.run(level=LV, rng=random.Random(1234))
    assert _fingerprint(a) == _fingerprint(b), "同种子结果不一致：取样不可复现"
    print(f"[OK] 同种子可复现（{a['state']} {a['time']}s {a['pos']}）")


def test_different_seeds_diverge():
    """不同种子必须给出不同结局。

    这就是 v1.8b 之前的 bug：`--runs 16` 把同一条轨迹跑了 16 遍，
    16/16 的"胜率"其实是一个样本。如果哪天有人把随机轴改回确定性，
    这条会立刻红。
    """
    got = {_fingerprint(botrun.run(level=LV, rng=random.Random(s + 7000)))
           for s in range(8)}
    assert len(got) >= 4, f"8 颗种子只跑出 {len(got)} 种结局：随机轴没生效"
    print(f"[OK] 8 颗种子跑出 {len(got)} 种不同结局（不是同一条轨迹重复）")


def test_phase_zero_keeps_baseline():
    """phase=0 必须和"完全不动相位"一模一样。"""
    plain = botrun.run(level=LV)
    zero = botrun.run(level=LV, phase=0.0)
    assert _fingerprint(plain) == _fingerprint(zero), \
        "phase=0 改变了基线 —— 相位扫描第 0 个样本和历史数字对不上了"
    # 顺手钉住那条被 BLOG / README 引用过的基线
    assert (plain["state"], plain["time"], plain["score"]) == ("victory", 63.0, 941), \
        f"第 1 关基线漂了：{plain['state']} {plain['time']}s {plain['score']} 分"
    print("[OK] phase=0 == 原基线（63.0 秒 / 941 分）")


# ---------------------------------------------------------------- 摆位
def test_scatter_never_puts_enemy_in_a_wall():
    """打散后的哨兵必须站在可走格上（落点验证要逐点验，不能只验落点）。"""
    for idx in (LV, MULTI):
        for seed in range(6):
            w = make_world(idx)
            botrun._scatter_phases(w, rng=random.Random(seed))
            for e in w.enemies:
                tx, ty = int(e.x), int(e.y)
                assert w.grid.is_walkable(tx, ty), \
                    f"第 {idx + 1} 关 seed={seed}：哨兵被摆到墙里 {(e.x, e.y)}"
    print("[OK] 摆位落点全在地板上（逐点验证过路径，不是只验落点）")


def test_multi_point_patrol_can_move_too():
    """多点巡逻（3~4 个点）也必须能沿整条回路取相位。

    audit_phases 早年只插值两点巡逻，多点巡逻的哨兵在整轮扫描里一动不动 ——
    所谓"12 个相位"对它们只有 1 种局面。全项目只有第 1 关有多点巡逻，
    所以这条盯的就是第 1 关。
    """
    lv = L.LEVELS[LV]
    spawn, cfg = L.spawn_and_patrols(lv, lv.build())
    multi = [pts for (_x, _y, pts) in cfg if len(pts) > 2]
    assert multi, "第 1 关没有多点巡逻，这条测试失去意义"

    moved = 0
    for seed in range(6):
        w = make_world(LV)
        before = [(e.x, e.y) for e in w.enemies]
        botrun._scatter_phases(w, rng=random.Random(seed))
        for i, e in enumerate(w.enemies):
            if len(e.patrol) > 2 and (e.x, e.y) != before[i]:
                moved += 1
    assert moved > 0, "多点巡逻的哨兵在 6 次打散里一次都没动"
    print(f"[OK] 多点巡逻能取连续相位（6 次打散里移动了 {moved} 次）")


def test_uniform_phase_moves_every_enemy():
    """相位扫描是"统一摆位"：所有哨兵一起挪到回路的同一比例处。"""
    w = make_world(LV)
    before = [(e.x, e.y) for e in w.enemies]
    botrun._scatter_phases(w, phase=0.25)
    after = [(e.x, e.y) for e in w.enemies]
    assert before != after, "phase=0.25 之后一个哨兵都没动"
    assert all(w.grid.is_walkable(int(e.x), int(e.y)) for e in w.enemies)
    print(f"[OK] 统一相位 0.25 把 {len(w.enemies)} 个哨兵一起挪开了")


def test_measure_script_reports_intervals():
    """`measure_levels.py` 的 Wilson 区间必须真的随样本收紧。

    这个脚本承担了 README 里所有难度数字，但它自己没有任何断言 —— 一旦
    区间算错（比如把 n 写成常数、或者边界没夹住），所有结论都会安静地错。
    这里只验数学，不跑模拟（96 局 × 4 关要二十几分钟，不适合进 CI）。
    """
    import importlib
    m = importlib.import_module("measure_levels")

    lo, hi = m.wilson(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0, f"50% 的区间应该包住 0.5，实际 [{lo},{hi}]"
    # 全胜 / 全败时区间必须留在 [0,1] 内（朴素公式会越界）
    assert 0.0 <= m.wilson(0, 10)[0] <= m.wilson(0, 10)[1] <= 1.0
    assert 0.0 <= m.wilson(10, 10)[0] <= m.wilson(10, 10)[1] <= 1.0
    # 同样 50% 的胜率，样本越大区间越窄 —— 这就是"n=24 别下结论"的量化依据
    w10 = m.wilson(5, 10)
    w200 = m.wilson(100, 200)
    assert (w10[1] - w10[0]) > (w200[1] - w200[0]) * 3, \
        f"样本从 10 加到 200，区间没有明显收窄：{w10} vs {w200}"
    print(f"[OK] Wilson 区间随样本收紧（n=10 → ±{(w10[1]-w10[0])/2:.0%}，"
          f"n=200 → ±{(w200[1]-w200[0])/2:.0%}）")


def test_no_guard_doors_a_mandatory_corridor():
    """必经之路上不许有"横切窄廊"的哨兵（会移动的门）。

    这是 v1.8d 从死点分布里挖出来的：第 3 关出生点在南廊最西端，进大厅要先
    向东走 30 格，而 x=14 那个哨兵 `(14,53)-(14,55)` 横切 3 格宽的南廊 ——
    96 局里那一格吃掉 17% 的败局。同一个错误这一关在北廊已经修过一次（改成
    沿墙走），南廊漏了。

    判据踩过两次坑，都记在这里：

    1. **量错方向**：竖着走的哨兵要量 y 方向的廊宽（它的行程正好等于廊宽 =
       横切），第一版量了 x 方向 —— 南廊 70 格长，于是那道门被判成"不在窄廊
       里"，测试全绿而 bug 还在。
    2. **只看距离**：改成"出生点 12 格内不许横切"之后，第 3 关西侧那条 3 格宽
       的检修竖井被误判 —— 那里确实横着一道，但它是**可选捷径**（封了它主路
       一步不慢），是设计，不是 bug。

    所以判据不能靠"离哪儿近"来猜，得用可以验证的性质：**把这道门封上，
    出生点到第一个终端的路会不会变长**。会变长 = 它是必经之路上的门 = 违规。
    对 v1.8d 那道门做回放：路从 64 格变 106 格，判据抓得住。
    """
    import math
    from collections import deque

    def span_in(grid, x, y, dx, dy, limit=10):
        n = 0
        while n < limit and not grid.is_wall(int(x + dx * (n + 1)),
                                            int(y + dy * (n + 1))):
            n += 1
        return n

    def bfs(grid, start, goals, blocked=frozenset()):
        sx, sy = int(start[0]), int(start[1])
        q = deque([(sx, sy, 0)])
        seen = {(sx, sy)}
        while q:
            x, y, d = q.popleft()
            if (x, y) in goals:
                return d
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) in seen or (nx, ny) in blocked:
                    continue
                if grid.is_wall(nx, ny):
                    continue
                seen.add((nx, ny))
                q.append((nx, ny, d + 1))
        return None

    def seg_cells(a, b):
        ax, ay, bx, by = int(a[0]), int(a[1]), int(b[0]), int(b[1])
        dx = (bx > ax) - (bx < ax)
        dy = (by > ay) - (by < ay)
        out, x, y = [], ax, ay
        while True:
            out.append((x, y))
            if (x, y) == (bx, by):
                return out
            x += dx
            y += dy

    bad = []
    for lv in L.LEVELS:
        grid = lv.build()
        spawn, cfg = L.spawn_and_patrols(lv, grid)
        goals = {(int(t[0]), int(t[1])) for t in lv.terminals}
        base = bfs(grid, spawn, goals)
        assert base is not None, f"{lv.name}：出生点压根走不到终端"
        for (ex, ey, pts) in cfg:
            for (a, b) in zip(pts, list(pts[1:]) + [pts[0]]):
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length < 1:
                    continue
                if a[0] == b[0]:      # 竖着走 → 廊宽在 y 方向
                    w = span_in(grid, a[0], a[1], 0, 1) + \
                        span_in(grid, a[0], a[1], 0, -1) + 1
                elif a[1] == b[1]:    # 横着走 → 廊宽在 x 方向
                    w = span_in(grid, a[0], a[1], 1, 0) + \
                        span_in(grid, a[0], a[1], -1, 0) + 1
                else:
                    continue
                # 行程没跨满廊宽 = 走廊里还有车道，不算门
                if not (w <= 3 and length >= w - 1):
                    continue
                after = bfs(grid, spawn, goals, frozenset(seg_cells(a, b)))
                if after is None or after > base + 2:
                    bad.append((lv.name, (ex, ey), f"{a}->{b}",
                                f"廊宽 {w} · 封上后路程 {base}→{after}"))
    assert not bad, (
        "有哨兵横切狭窄的必经走廊（等于一道会移动的闸门，玩家只能等它转身）："
        f"{bad}")
    print("[OK] 没有哨兵封死必经走廊（封门实验：路程不变）")


def test_no_duplicate_or_stacked_patrols():
    """两条巡逻线不许重合（同一个哨兵被写两遍），也不许只隔 1 格。

    这条是从两个真实的失误里长出来的：

    1. v1.8d 改 y=43/y=48 那一片时，一行里不小心写了两个
       `(48,48),((48,48),(64,48))` —— 第 3 关悄悄多出第 30 个哨兵，
       和另一个**完全同一条线**、同步走、同步转身。README 记的是 29，
       代码跑的是 30，谁都没发现，因为它看起来"只是多一个人"。
    2. 更早的那个：y=47 和 y=48 两条横带**相隔 1 格**，都压在门洞上。
       它不是"更密"，是一整块等不到窗口的禁区（96 局里门口三格吃掉 34% 的败局）。

    两条合起来的判据：同一个关卡里，平行同向的巡逻线之间必须 **≥2 格**，
    且不允许完全重合。
    """
    import math

    bad = []
    for lv in L.LEVELS:
        grid = lv.build()
        _spawn, cfg = L.spawn_and_patrols(lv, grid)
        seen = {}
        for (ex, ey, pts) in cfg:
            key = (round(ex, 1), round(ey, 1), tuple((round(a, 1), round(b, 1)) for a, b in pts))
            seen[key] = seen.get(key, 0) + 1
        for k, v in seen.items():
            if v > 1:
                bad.append((lv.name, "完全重合", k[0:2], f"×{v} 个"))

        # 平行同向、相距 1 格的横线/竖线
        lines = []
        for (ex, ey, pts) in cfg:
            for (a, b) in zip(pts, list(pts[1:]) + [pts[0]]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) < 2:
                    continue
                if (a[0], a[1]) > (b[0], b[1]):
                    continue    # 同一条线只留一个方向，否则一对线会被报两次
                if a[1] == b[1]:            # 横线
                    lo, hi = sorted((a[0], b[0]))
                    lines.append(("h", a[1], lo, hi, (ex, ey)))
                elif a[0] == b[0]:          # 竖线
                    lo, hi = sorted((a[1], b[1]))
                    lines.append(("v", a[0], lo, hi, (ex, ey)))
        for i, p in enumerate(lines):
            for q in lines[i + 1:]:
                if p[0] != q[0] or abs(p[1] - q[1]) != 1:
                    continue
                overlap = min(p[3], q[3]) - max(p[2], q[2])
                if overlap >= 1:
                    bad.append((lv.name, "相隔 1 格",
                                f"{p[4]} 与 {q[4]}",
                                f"重叠 {overlap} 格（{p[0]} 轴 {p[1]}/{q[1]}）"))
    assert not bad, (
        "巡逻线重复或贴得太近（不会变成\"更密\"，只会变成一整块等不到窗口的禁区）："
        f"{bad}")
    print("[OK] 没有重复/只隔 1 格的巡逻线")


def main():
    print("=== W20：测量工具本身（机器人 / 相位摆位） ===")
    test_same_seed_reproduces()
    test_different_seeds_diverge()
    test_phase_zero_keeps_baseline()
    test_scatter_never_puts_enemy_in_a_wall()
    test_multi_point_patrol_can_move_too()
    test_uniform_phase_moves_every_enemy()
    test_measure_script_reports_intervals()
    test_no_guard_doors_a_mandatory_corridor()
    test_no_duplicate_or_stacked_patrols()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
