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


def main():
    print("=== W20：测量工具本身（机器人 / 相位摆位） ===")
    test_same_seed_reproduces()
    test_different_seeds_diverge()
    test_phase_zero_keeps_baseline()
    test_scatter_never_puts_enemy_in_a_wall()
    test_multi_point_patrol_can_move_too()
    test_uniform_phase_moves_every_enemy()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
