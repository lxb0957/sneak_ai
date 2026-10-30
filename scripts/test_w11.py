"""W11 测试：沿途补给（弹药 / 石子 / 护盾）。

这组测试守的是**"资源是路线换来的"**这个建模目标：

补给
1. 每个点位都必须落在可走格上 —— 被柱子/货箱吃掉的点等于"看得见捡不到"；
2. 走过去自动拾取，且同一个点不能重复捡（否则绕一圈就能无限刷）；
3. 上限必须生效：弹药满了就不该白吃掉一个补给点（要留给回程）；
4. 三条弧都得有补给 —— 否则"选路"这个决策就消失了。

护盾
5. 护盾挡掉一次抓捕：玩家不死，**贴上来那个人当场倒地**，护盾被消耗
   （不是"弹开"：弹开的话下一帧又被抓，护盾等于没用）；
6. 没护盾时照样会被抓 —— 它是资源，不是免死金牌；
7. 破盾有代价：记一次击倒（扣分）、动静会让附近的人起疑但不确认。

用法：
    python scripts/test_w11.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Role
from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import (
    DOORS,
    PICKUPS,
    World,
    build_demo_map,
    build_pickups,
)
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception

DT = 1 / 60


# ---------------------------------------------------------------- 工具
def make_arena(size=32):
    """一个四面围墙的空房间，用来隔离测试拾取/护盾机制。"""
    grid = GridMap.empty(size, size)
    for i in range(size):
        grid.set_wall(i, 0)
        grid.set_wall(i, size - 1)
        grid.set_wall(0, i)
        grid.set_wall(size - 1, i)
    w = World(grid)
    w.player = Player(8.5, 10.5)
    return w


def add_guard(w, x, y, facing=0.0, patrol=None):
    e = Enemy(x, y, patrol=patrol or [], perception=Perception())
    e.facing = facing
    w.enemies.append(e)
    return e


def tick(w, seconds):
    for _ in range(int(seconds * 60)):
        w.update(DT)


def drop(w, x, y, kind):
    it = {"x": x, "y": y, "kind": kind, "taken": False}
    w.pickups.append(it)
    return it


# ---------------------------------------------------------------- 点位合法性
def test_every_pickup_is_reachable():
    """补给点不能落在墙里（房间里的柱子会把手工坐标吃掉）。"""
    grid = build_demo_map()
    items = build_pickups(grid)
    assert len(items) == len(PICKUPS), "补给点数量与 PICKUPS 常量对不上"

    bad = [it for it in items if grid.is_wall(int(it["x"]), int(it["y"]))]
    assert not bad, f"有补给点落在墙里（永远捡不到）：{bad[:3]}"

    # 也不能直接压在门上：门是唯一通路，站在门中间白拿等于没有代价
    door_cells = [(x + 0.5, y + 0.5)
                  for d in DOORS
                  for y in range(d[1], d[3] + 1)
                  for x in range(d[0], d[2] + 1)]
    on_door = [it for it in items
               if any(math.hypot(it["x"] - cx, it["y"] - cy) < 1.0
                      for cx, cy in door_cells)]
    assert not on_door, f"有补给点压在门上：{[(i['x'], i['y']) for i in on_door]}"
    print(f"[OK] {len(items)} 个补给点全部可走、且不压门")


def test_pickups_spread_over_all_three_arcs():
    """三条弧都得有补给，否则"走哪条路"这个决策就不成立了。

    判定方式：每个补给点归到离它最近的门所属的弧（够远的一律算"房间内"），
    然后要求上弧 / 下弧 / 捷径各有货。
    """
    items = build_pickups(build_demo_map())
    arcs = {}
    for it in items:
        best, bd = None, 1e9
        for (x0, y0, x1, y1, _n, arc) in DOORS:
            cx, cy = (x0 + x1) / 2 + 0.5, (y0 + y1) / 2 + 0.5
            d = math.hypot(it["x"] - cx, it["y"] - cy)
            if d < bd:
                best, bd = arc, d
        arcs.setdefault(best, []).append(it["kind"])

    assert len(arcs) >= 3, f"补给只集中在 {list(arcs)} 这几条弧上，选路失去意义"
    for arc in ("北弧", "南弧"):
        assert arcs.get(arc), f"{arc} 上没有任何补给"
    print("[OK] 补给分散在 " + " / ".join(
        f"{k}×{len(v)}" for k, v in sorted(arcs.items())))


# ---------------------------------------------------------------- 拾取
def test_walking_over_pickup_grants_resource():
    """走过去自动拾取：不需要按键，且同一点不能捡两次。"""
    w = make_arena()
    p = w.player
    p.ammo = 1
    it = drop(w, 12.5, 10.5, "ammo")

    p.x, p.y = 12.5, 10.5
    w.update(DT)
    assert it["taken"], "走上去就该自动拾取"
    assert p.ammo == 1 + C.PICKUP_AMMO, f"弹药应 +{C.PICKUP_AMMO}，实际 {p.ammo}"

    # 再走一遍：不能重复拿
    before = p.ammo
    for _ in range(10):
        w.update(DT)
    assert it["taken"] and p.ammo == before, "同一个补给点被重复拾取了"
    print(f"[OK] 走过即拾取（弹药 1 → {p.ammo}），且不可重复")


def test_pickup_respects_cap():
    """资源满了就不消耗补给点：要留给回程，不是路过必捡。"""
    w = make_arena()
    p = w.player
    p.ammo = C.AMMO_CAP
    it = drop(w, 12.5, 10.5, "ammo")
    p.x, p.y = 12.5, 10.5
    w.update(DT)
    assert not it["taken"], "弹药已满，不该白白吃掉这个补给点"
    assert p.ammo == C.AMMO_CAP
    print(f"[OK] 已满（{C.AMMO_CAP} 发）时不消耗补给点，留给回程")


def test_stone_and_shield_pickups():
    """石子堆与护盾各自生效，且护盾最多一层。"""
    w = make_arena()
    p = w.player
    p.stones = 0
    p.x, p.y = 12.5, 10.5
    drop(w, 12.5, 10.5, "stone")
    w.update(DT)
    assert p.stones == C.PICKUP_STONE, f"石子应 +{C.PICKUP_STONE}，实际 {p.stones}"

    drop(w, 12.5, 10.5, "shield")
    w.update(DT)
    assert p.shield == 1, f"护盾应为 1，实际 {p.shield}"
    drop(w, 12.5, 10.5, "shield")
    w.update(DT)
    assert p.shield == 1, f"护盾上限 {C.SHIELD_CAP}，实际 {p.shield}"
    print(f"[OK] 石子 +{C.PICKUP_STONE}、护盾上限 {C.SHIELD_CAP} 层")


# ---------------------------------------------------------------- 护盾
def test_shield_absorbs_one_capture_and_kills_the_grabber():
    """护盾挡一次抓捕：玩家活着，贴上来的人当场倒地。

    这是"碰到护盾的敌人会死亡"的落点 —— 但注意它不是被动光环：
    只有在哨兵**已经知情**并贴到身上、准备抓人的那一刻才触发。
    """
    w = make_arena()
    p = w.player
    p.shield = 1
    # 哨兵正对玩家、已经起疑（满足"知情"条件），距离在抓捕范围内
    g = add_guard(w, 8.9, 10.5, facing=math.pi)
    g.suspicion = C.SUSPICION_CURIOUS + 0.05

    w.update(DT)
    assert w.state != C.STATE_DEFEAT, "有护盾时不该直接失败"
    assert not w.captured, "护盾应该挡掉这次抓捕"
    assert p.shield == 0, "护盾应被消耗"
    assert not g.alive, "贴上来抓人的哨兵必须当场倒地"
    assert w.kills == 1, "破盾击倒要记一次击倒（和开枪同代价）"
    print("[OK] 护盾挡下一次抓捕：玩家存活，抓人者当场倒地，护盾耗尽")


def test_without_shield_capture_still_kills():
    """没护盾时照样被抓 —— 它是资源，不是免死金牌。"""
    w = make_arena()
    w.player.shield = 0
    g = add_guard(w, 8.9, 10.5, facing=math.pi)
    g.suspicion = C.SUSPICION_CURIOUS + 0.05
    w.update(DT)
    assert w.state == C.STATE_DEFEAT, "没有护盾时必须照常失败"
    assert w.kills == 0, "没破盾就不该有击倒"
    print("[OK] 无护盾时抓捕照常生效（护盾是资源，不是无敌）")


def test_shield_break_makes_noise_but_not_confirmed():
    """破盾有代价：附近的人会起疑，但不等于看见你。"""
    w = make_arena(40)
    p = w.player
    p.x, p.y = 20.5, 20.5
    p.shield = 1
    grabber = add_guard(w, 20.9, 20.5, facing=math.pi)
    grabber.suspicion = C.SUSPICION_CURIOUS + 0.05
    # 4 格外另一个哨兵：应该被动静惊动，但不能被"确认"
    near = add_guard(w, 24.5, 20.5, facing=0.0)

    w.update(DT)
    assert near.suspicion > 0, "破盾应该有动静"
    assert near.suspicion < C.SUSPICION_CONFIRM, (
        f"破盾不能等于暴露位置，实际 {near.suspicion:.2f}")
    assert near.role == Role.PATROL, "破盾不该让人直接进入协同追击"
    print(f"[OK] 破盾惊动附近哨兵到 {near.suspicion:.2f}（< 确认阈值）")


def test_frame_budget_with_pickups():
    """补给不应该拖慢帧：14 个点每帧只做 14 次距离比较。"""
    import time

    w = World(build_demo_map())
    w.player = Player(5.5, 5.5)
    w.pickups = build_pickups(w.grid)
    spawn, cfg = __import__(
        "sneak_ai.core.world", fromlist=["build_spawn_and_patrols"]
    ).build_spawn_and_patrols(w.grid)
    w.enemies = [Enemy(x, y, patrol=pts, perception=Perception())
                 for (x, y, pts) in cfg]
    for _ in range(60):
        w.update(DT)
    n = 300
    t0 = time.perf_counter()
    for _ in range(n):
        w.update(DT)
    dt = (time.perf_counter() - t0) / n * 1000
    assert dt < 4.0, f"单帧逻辑 {dt:.2f}ms 太慢"
    print(f"[OK] {len(w.enemies)} 哨兵 + {len(w.pickups)} 补给点："
          f"单帧逻辑 {dt:.2f}ms（{1000/dt:.0f} FPS）")


# ---------------------------------------------------------------- 入口
def main():
    print("=== W11 沿途补给 / 护盾 测试 ===")
    test_every_pickup_is_reachable()
    test_pickups_spread_over_all_three_arcs()
    test_walking_over_pickup_grants_resource()
    test_pickup_respects_cap()
    test_stone_and_shield_pickups()
    test_shield_absorbs_one_capture_and_kills_the_grabber()
    test_without_shield_capture_still_kills()
    test_shield_break_makes_noise_but_not_confirmed()
    test_frame_budget_with_pickups()
    print("\nW11 沿途补给 / 护盾 测试全部通过 ✅")


if __name__ == "__main__":
    main()
