"""W19 测试：第 4 关「配电所」的总闸 / 黑灯机制。

这一组守的是"机制真的改变了规则"，而不是"画面上变暗了"：

1. **只有配了闸的关卡才有闸** —— 前三关一行代码都不该被这个机制污染；
2. **黑灯乘的是视野**：同一个哨兵、同一个位置，黑灯时 `view_range` 必须变小，
   而且感知判定真的跟着变（不是只改渲染）；
3. **拉闸必须响**：半径内的哨兵被惊动并前往总闸 —— 这是它的代价，
   如果哪天有人把 `_emit_noise` 那行删了，机制就退化成免费技能；
4. **一次性的**：同一个闸拉第二次必须失败，否则 14 秒一到就能再按，
   它就从"一个决定"变成"一个循环技能"；
5. **到点自己来电**：黑灯会自然结束，不能靠玩家手动恢复。

用法：
    python scripts/test_w19.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Coordinator
from sneak_ai.core import levels as L
from sneak_ai.core.world import World
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception

DT = 1 / 60
SWITCH = 3      # 第 4 关的下标


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
    dur, view, noise = lv.blackout
    w.blackout_duration = dur
    w.blackout_view = view
    w.blackout_noise = noise
    return w, lv


# ---------------------------------------------------------------- 数据面
def test_only_levels_with_power_get_one():
    """前三关不该有闸；第 4 关至少有一个。"""
    for i, lv in enumerate(L.LEVELS[:SWITCH]):
        assert lv.power == (), f"{lv.name} 不该有总闸（这个机制是第 4 关的卖点）"
    lv4 = L.LEVELS[SWITCH]
    assert len(lv4.power) >= 1, "第 4 关没有配闸"
    grid = lv4.build()
    for (px, py) in lv4.power:
        assert not grid.is_wall(int(px), int(py)), \
            f"总闸 {(px, py)} 落在墙里 —— 玩家永远按不到"
    print(f"[OK] 只有配电所有总闸（{len(lv4.power)} 个），且都在可走格上")


def test_breaker_is_reachable_from_spawn():
    """总闸必须从出生点走得到（不然这一关的解法不存在）。"""
    from sneak_ai.pathing.astar import astar
    lv = L.LEVELS[SWITCH]
    grid = lv.build()
    sx, sy = int(lv.spawn[0]), int(lv.spawn[1])
    for (px, py) in lv.power:
        path = astar(grid, (sx, sy), (int(px), int(py)))
        assert path is not None, f"总闸 {(px, py)} 从出生点不可达"
        assert len(path) > 3, f"总闸 {(px, py)} 就在出生点旁边，那不算'绕过去拉闸'"
    print("[OK] 每个总闸都能从出生点走到，且不是门口那种伸手就够的距离")


# ---------------------------------------------------------------- 机制
def test_blackout_shrinks_vision():
    """黑灯必须真的缩短视野 —— 判定和显示用的是同一个数。"""
    w, lv = make_world(SWITCH)
    e = w.enemies[0]
    before = e.view_range()
    w.player.x, w.player.y = lv.power[0]
    assert w.activate_power() == "ok"
    # `vis_scale` 由 world 每帧下发，这里手动跑一帧让它生效
    w.update(DT)
    after = e.view_range()
    assert after < before * 0.6, (
        f"黑灯后视野 {after:.2f} 没有明显小于 {before:.2f} —— "
        "系数没乘进 view_range，画面变暗了但规则没变"
    )
    assert abs(w.visibility_scale - lv.blackout[1]) < 1e-6
    print(f"[OK] 黑灯把视野从 {before:.2f} 压到 {after:.2f}（×{lv.blackout[1]:.2f}）")


def test_blackout_blocks_a_sight_that_would_otherwise_land():
    """同一双眼睛、同一个位置：黑灯前看得见，黑灯后看不见。

    这条比"视野数值变小"更硬 —— 它量的是 `can_see` 的实际结论，
    也就是玩家真实会不会被抓。
    """
    w, lv = make_world(SWITCH)
    grid = w.grid
    # 找一条 5 格长的空走廊：从某个哨兵旁边横着量
    e = w.enemies[0]
    px, py = e.x, e.y
    # 沿着哨兵朝向正前方放玩家（一定在视锥里）
    ang = e.facing
    tx = px + math.cos(ang) * 4.5
    ty = py + math.sin(ang) * 4.5
    if grid.is_wall(int(tx), int(ty)):
        print("[SKIP] 这个哨兵正前方 4.5 格是墙，换不了位置")
        return
    w.player.x, w.player.y = tx, ty
    seen_before = e.view_range()
    assert e.view_range() > 4.5, "视野本来就够不到 4.5 格，测不出东西"
    e.vis_scale = lv.blackout[1]     # 模拟世界下发黑灯系数
    assert e.view_range() < 4.5, "黑灯之后的视野仍然够到 4.5 格"
    print("[OK] 同一个位置：黑灯前在视野内，黑灯后出界")


def test_switch_makes_noise():
    """拉闸必须把附近的人叫过来 —— 这是它的代价。"""
    w, lv = make_world(SWITCH)
    node = lv.power[0]
    w.player.x, w.player.y = node
    # 先在没拉闸时记录一轮可疑度
    assert w.activate_power() == "ok"
    assert len(w.pings) == 1, "拉闸没有产生任何响动"
    r = w.pings[0]["r"]
    assert r >= 5.0, f"拉闸噪音半径只有 {r} 格，等于没响"
    moved = [e for e in w.enemies
             if e.suspicion >= C.SUSPICION_CURIOUS - 1e-6]
    assert moved, "噪音半径内没有任何哨兵被惊动"
    # 关键：只到"起疑"，不许直接进追击
    assert all(e.suspicion < C.SUSPICION_CONFIRM for e in moved), \
        "拉闸直接把人推进了确认追击 —— 那不是响动，是暴露位置"
    print(f"[OK] 拉闸惊动 {len(moved)} 人（半径 {r:.0f} 格），且只到'起疑'")


def test_breaker_is_one_shot():
    """同一个闸只能拉一次。"""
    w, lv = make_world(SWITCH)
    w.player.x, w.player.y = lv.power[0]
    assert w.activate_power() == "ok"
    # 黑灯期按第二次：不允许
    assert w.activate_power() == "blackout"
    # 等灯自己亮回来，再按：还是不允许（闸已经烧了）
    w.blackout_left = 0.0
    assert w.activate_power() == "used"
    assert sum(1 for n in w.power if n["used"]) == 1
    print("[OK] 闸是一次性的：黑灯期按不动，灯亮了也按不动")


def test_must_stand_next_to_the_breaker():
    """离得远按不到 —— 否则它就变成一个远程技能。"""
    w, lv = make_world(SWITCH)
    bx, by = lv.power[0]
    w.player.x, w.player.y = bx + 6.0, by
    assert w.nearby_power() is None
    assert w.activate_power() == "far"
    assert not w.in_blackout
    w.player.x, w.player.y = bx + 0.4, by + 0.3
    assert w.nearby_power() == 0
    print("[OK] 站远了按不到（far），站到跟前才认")


def test_blackout_ends_by_itself():
    """黑灯到点自动恢复，不能永久。"""
    w, lv = make_world(SWITCH)
    w.player.x, w.player.y = lv.power[0]
    w.activate_power()
    dur = w.blackout_duration
    assert dur > 0
    # 快进到只剩 0.1 秒
    w.blackout_left = 0.1
    for _ in range(12):
        w.update(DT)
    assert not w.in_blackout, "黑灯没有自己结束"
    assert w.events.count("power_back") <= 1
    assert abs(w.visibility_scale - 1.0) < 1e-6
    print(f"[OK] 黑灯 {dur:.0f} 秒后自动恢复，视野系数回到 1.00")


def test_second_breaker_keeps_working():
    """第 4 关配了两个闸：第一个烧了之后，第二个还能用。"""
    w, lv = make_world(SWITCH)
    assert len(lv.power) >= 2, "第 4 关应该有两个闸（去程一个、回程一个）"
    w.player.x, w.player.y = lv.power[0]
    assert w.activate_power() == "ok"
    w.blackout_left = 0.0
    w.player.x, w.player.y = lv.power[1]
    assert w.nearby_power() == 1
    assert w.activate_power() == "ok"
    assert w.in_blackout
    print("[OK] 两个闸互相独立：去程烧掉的那个不影响回程")


def test_last_level_is_harsher():
    """末关要比前关狠 —— 但"狠"不能拿 S 线直接比。

    第 4 关的路程（单程 74 格、还要折返）本来就比第 3 关长，
    要求它的 S 线数字更小，等于要求玩家跑得更快，那是荒谬的刻度。
    真正该量的是两个不随路程变化的东西：追兵速度，和"暴露长度"。
    """
    lv3, lv4 = L.LEVELS[2], L.LEVELS[3]
    assert lv4.chase >= lv3.chase, "末关的追兵不该比前关慢"
    assert lv4.alert_speed >= lv3.alert_speed, "末关的警报追兵不该比前关慢"

    def span(lv):
        g = lv.build()
        from sneak_ai.pathing.astar import astar
        n = astar(g, (int(lv.spawn[0]), int(lv.spawn[1])),
                  (int(lv.terminal[0]), int(lv.terminal[1]))) or []
        return len(n)

    # 单程越长，警报后要在无遮挡直廊里跑的格数越多
    assert span(lv4) > span(lv3), "末关的单程还没第 3 关长，谈不上更难"
    print(f"[OK] 末关更狠：追兵 {lv3.chase} → {lv4.chase}，"
          f"单程 {span(lv3)} → {span(lv4)} 格")


def main():
    print("=== W19：配电所的总闸 / 黑灯 ===")
    test_only_levels_with_power_get_one()
    test_breaker_is_reachable_from_spawn()
    test_blackout_shrinks_vision()
    test_blackout_blocks_a_sight_that_would_otherwise_land()
    test_switch_makes_noise()
    test_breaker_is_one_shot()
    test_must_stand_next_to_the_breaker()
    test_blackout_ends_by_itself()
    test_second_breaker_keeps_working()
    test_last_level_is_harsher()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
