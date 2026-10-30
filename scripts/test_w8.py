"""W8 测试：投石诱敌 + 噪音可见化。

这组测试守的不是"功能存在"，而是三条**设计约束**：
1. 石子只能让哨兵"起疑"，绝不能直接进入追击（否则等于免费的隐身）；
2. 落点必须停在墙前（否则会出现"对着墙扔、哨兵去调查一堵墙"）；
3. 已经在追人的哨兵不受石子影响（视觉信息优先于听觉）。

用法：
    python scripts/test_w8.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Role
from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import World
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.entity.stone import plan_landing
from sneak_ai.perception.perception import Perception


def make_arena(size=30):
    """一块空场地：投石相关的行为要可复现，就别把地图复杂度混进测试。"""
    grid = GridMap.empty(size, size)
    for x in range(size):
        grid.set_wall(x, 0)
        grid.set_wall(x, size - 1)
    for y in range(size):
        grid.set_wall(0, y)
        grid.set_wall(size - 1, y)
    w = World(grid)
    w.player = Player(10.5, 12.5)
    return w


def add_guard(w, x, y, facing=0.0, patrol=None):
    e = Enemy(x, y, patrol=patrol or [], perception=Perception())
    e.facing = facing
    w.enemies.append(e)
    return e


def test_landing_stops_before_wall():
    """落点必须落在可走格上：否则哨兵会跑去"调查一堵墙"。"""
    grid = GridMap.empty(30, 30)
    for y in range(30):
        grid.set_wall(16, y)     # 竖墙
    landing = plan_landing(grid, (10.5, 10.5), 0.0)   # 朝东扔

    assert not grid.is_wall(int(landing[0]), int(landing[1])), "落点不能落在墙里"
    assert landing[0] < 16.0, f"落点应停在墙前，实际 {landing}"
    # 逐点检查：起点到落点之间不能穿墙
    for i in range(41):
        t = i / 40
        x = 10.5 + (landing[0] - 10.5) * t
        assert not grid.is_wall(int(x), 10), "飞行路径不能穿墙"
    print(f"[OK] 石子停在墙前：落点 x={landing[0]:.2f}（墙在 x=16）")


def test_stone_pulls_guard_to_noise():
    """扔石子后，附近的哨兵应当"起疑 → 走过去查看"。

    几何要点：玩家在哨兵**背后**（东侧），石子扔到哨兵**前方**（西侧）。
    这样哨兵转身走向石子时必然背对玩家 —— 否则它调查到一半就会看见
    站在空地上的玩家，测试测到的就不是"诱敌"而是"自投罗网"。
    """
    w = make_arena()
    w.player.x, w.player.y = 16.5, 12.5
    guard = add_guard(w, 14.5, 12.5, facing=-math.pi / 2)  # 朝北，玩家在它东侧视野外

    assert w.throw_stone((9.5, 12.5)) is True
    assert w.player.stones == C.STONE_COUNT - 1

    landing = (w.stones[0].tx, w.stones[0].ty)
    before = math.hypot(guard.x - landing[0], guard.y - landing[1])

    # 1.3 秒：够它转过身走 3 格多，但还没走到落点开始环视
    for _ in range(int(1.3 * 60)):
        w.update(1 / 60)

    during = math.hypot(guard.x - landing[0], guard.y - landing[1])
    assert guard.state_name == "investigate", f"应进入调查，实际 {guard.state_name}"
    assert during < before - 1.5, f"应向落点靠近：{before:.2f} → {during:.2f}"
    assert guard.role == Role.PATROL, "调查阶段不应升级成协同追击"
    print(f"[OK] 哨兵被石子引走：距落点 {before:.2f} → {during:.2f} 格")


def test_stone_never_confirms():
    """石子只能制造"起疑"，不能直接把人卖了 —— 这是机制的核心平衡点。"""
    w = make_arena()
    w.player.x, w.player.y = 16.5, 12.5
    guard = add_guard(w, 14.5, 12.5, facing=-math.pi / 2)
    w.throw_stone((9.5, 12.5))
    for _ in range(int(0.6 * 60)):
        w.update(1 / 60)
    assert guard.suspicion >= C.SUSPICION_CURIOUS, "应当起疑"
    assert guard.suspicion < C.SUSPICION_CONFIRM, (
        f"听到响动不能直接确认发现：{guard.suspicion:.2f}"
    )
    assert guard.role == Role.PATROL, "石子不应让哨兵进入协同追击"
    print(f"[OK] 石子只起疑不确认：可疑度 {guard.suspicion:.2f} < {C.SUSPICION_CONFIRM}")


def test_guard_far_away_is_not_alerted():
    """听觉有半径：隔着大半张图的哨兵不该被一颗石子惊动。"""
    w = make_arena(40)
    guard = add_guard(w, 34.5, 34.5, facing=0.0)
    w.player.x, w.player.y = 10.5, 10.5
    w.throw_stone((10.5, 6.5))
    for _ in range(60):
        w.update(1 / 60)
    assert guard.suspicion == 0.0, "半径外的哨兵不应有可疑度"
    print("[OK] 听觉半径生效：远处哨兵未被惊动")


def test_stone_does_not_distract_chaser():
    """已经在追你的哨兵不会因为一颗石子回头 —— 否则可以无限甩锅。"""
    w = make_arena()
    guard = add_guard(w, 15.5, 12.5, facing=0.0)
    guard.role = Role.CHASE
    guard._last_seen = (w.player.x, w.player.y)
    guard.suspicion = 0.9

    w._emit_noise(10.5, 8.5, C.STONE_NOISE_RADIUS, C.STONE_SUSPICION, "stone")

    assert guard.role == Role.CHASE, "追击中的哨兵不应被石子带偏"
    assert guard._last_seen == (w.player.x, w.player.y), "追击目标不应被改写"
    print("[OK] 追击中的哨兵不吃诱饵")


def test_stone_ammo_and_cooldown():
    """石子是有限资源：3 颗、有冷却，用完不能继续扔。"""
    w = make_arena()
    assert w.player.stones == C.STONE_COUNT
    assert w.throw_stone((10.5, 8.5)) is True
    assert w.throw_stone((10.5, 8.5)) is False, "冷却中不能再扔"

    for _ in range(int(C.STONE_COOLDOWN * 60) + 2):
        w.update(1 / 60)

    for _ in range(C.STONE_COUNT - 1):
        assert w.throw_stone((10.5, 8.5)) is True
        for _ in range(int(C.STONE_COOLDOWN * 60) + 2):
            w.update(1 / 60)

    assert w.player.stones == 0
    assert w.throw_stone((10.5, 8.5)) is False, "没石子了不能再扔"
    assert "stone_empty" in w.events
    print(f"[OK] 石子限量 {C.STONE_COUNT} 颗 + {C.STONE_COOLDOWN}s 冷却")


def test_ping_and_stone_lifecycle():
    """落点声波要能被看见，也要自己消失（不然会在地图上留一堆残影）。"""
    w = make_arena()
    w.throw_stone((10.5, 8.5))
    # 飞行约 0.25s；只跑 0.4s 是为了在声波消失前抓住它（声波只活 0.75s）
    for _ in range(int(0.4 * 60)):
        w.update(1 / 60)

    assert not w.stones, "落地后石子应从列表移除"
    assert any(p["kind"] == "stone" for p in w.pings), "落地应产生声波"
    for _ in range(int(C.STONE_PING_LIFE * 60) + 10):
        w.update(1 / 60)
    assert not w.pings, "声波应按时消失"
    print("[OK] 石子与声波的生命周期正确")


def test_noise_radius_scales_with_movement():
    """噪音圈半径必须严格对应三档移动 —— 这是玩家唯一的"听觉尺子"。"""
    p = Player(1.5, 1.5)
    p.moving = False
    still = p.noise_radius()
    p.moving, p.sneaking = True, True
    sneak = p.noise_radius()
    p.sneaking = False
    walk = p.noise_radius()
    p.sprinting = True
    sprint = p.noise_radius()

    assert still == 0.0, "站着不动不应该有噪音圈"
    assert sneak < walk < sprint, f"半径应随噪音递增：{sneak}/{walk}/{sprint}"
    assert abs(sneak - C.HEAR_RANGE * 0.73) < 1e-6
    assert abs(sprint - C.HEAR_RANGE * 1.5) < 1e-6
    print(f"[OK] 噪音半径：静止 0 / 潜行 {sneak:.1f} / 走路 {walk:.1f} / 冲刺 {sprint:.1f} 格")


if __name__ == "__main__":
    test_landing_stops_before_wall()
    test_stone_pulls_guard_to_noise()
    test_stone_never_confirms()
    test_guard_far_away_is_not_alerted()
    test_stone_does_not_distract_chaser()
    test_stone_ammo_and_cooldown()
    test_ping_and_stone_lifecycle()
    test_noise_radius_scales_with_movement()
    print("\n全部投石 / 噪音测试通过 ✅")
