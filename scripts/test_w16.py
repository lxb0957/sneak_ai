"""W16 测试：听觉要过地图（声音代价场）。

这一组要回答的不是"能不能跑"，而是：
**耳朵和眼睛用的是不是同一张地图？**

旧的 `can_hear` 算纯欧氏距离 —— 隔着五格实体岩，只要直线距离够近照样听得见；
而视觉那边是用 `raycast` 做过遮挡的。同一套感知层里，眼睛要射线、耳朵能透视，
这不是"简化"，是漏子。这组测试把三条性质钉住：

1. 空旷走廊里，传播代价 ≈ 直线距离（不会因为改动把听觉莫名改强/改弱）；
2. 声音能穿墙，但穿一格要付 `SOUND_WALL_COST`（薄墙能听见、传不远）；
3. 隔着厚岩层、或要绕一大圈的，听不见 —— 这一条是旧实现最离谱的地方。

用法：python scripts/test_w16.py
"""

import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.ai.coordination import Coordinator  # noqa: E402
from sneak_ai.core.grid import GridMap  # noqa: E402
from sneak_ai.core.world import World, build_demo_map  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402
from sneak_ai.perception.perception import Perception  # noqa: E402
from sneak_ai.perception.sound import SoundField, hearing_range, path_cost  # noqa: E402

APPROX = 0.02


def approx(a, b, tol=APPROX, msg=""):
    assert abs(a - b) <= tol, f"{msg} 期望 {b}，实际 {a}"


# ---------------- 地图脚手架 ----------------

def two_rooms(gap: int, w: int = 24, h: int = 15) -> GridMap:
    """左房间 x1..4，右房间从 x(5+gap) 开始，中间 `gap` 格厚的岩层。

    gap=0 时两间房直接相连（对照组）；gap 越大墙越厚。
    """
    grid = GridMap.filled(w, h)
    for y in range(2, 13):
        for x in range(1, 5):
            grid.set_floor(x, y)
        for x in range(5 + gap, w - 1):
            grid.set_floor(x, y)
    return grid


def u_turn_grid() -> GridMap:
    """U 形走廊：(10,5) 和 (10,8) 直线只隔 3 格，但要绕 15 格才能过去。

    这正是"墙在中间"的最小复现：旧实现按 3 格算，哨兵隔着墙听得一清二楚。
    """
    grid = GridMap.filled(24, 14)
    for x in range(2, 17):
        grid.set_floor(x, 5)          # 上层走廊
    for y in range(5, 9):
        grid.set_floor(16, y)         # 右侧竖廊
    for x in range(10, 17):
        grid.set_floor(x, 8)          # 下层走廊
    return grid


def ring_grid() -> GridMap:
    """回字形走廊：外圈能走，中间是实体块。

    (8,4) 和 (8,10) 直线只隔 6 格，中间有 5 格岩 —— 直接穿墙要付 6+15=21，
    绕外圈只要 18。声音会选**便宜的那条**：绕一圈，而不是穿过去。
    """
    grid = GridMap.filled(18, 14)
    for x in range(2, 16):
        grid.set_floor(x, 4)
        grid.set_floor(x, 10)
    for y in range(4, 11):
        grid.set_floor(2, y)
        grid.set_floor(14, y)
    return grid


def open_room(w=20, h=12) -> GridMap:
    grid = GridMap.filled(w, h)
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            grid.set_floor(x, y)
    return grid


def make_world(grid, player=(3.5, 7.5), enemies=()):
    world = World(grid)
    world.coordinator = Coordinator(grid)
    world.player = Player(*player)
    world.enemies = list(enemies)
    return world


def loud_player(world):
    """让玩家处于"冲刺"档：噪音 1.85，听觉半径最大（7.5 格）。"""
    p = world.player
    p.moving = True
    p.sprinting = True
    return p


# ---------------- 1. 代价场的几何性质 ----------------

def test_open_corridor_cost_equals_distance():
    """空旷直走廊里，代价必须约等于直线距离 —— 否则整张图的听觉都会变。"""
    grid = open_room()
    c = path_cost(grid, (3.5, 6.5), (8.5, 6.5), limit=20)
    approx(c, 5.0, msg="横 5 格直线")
    c = path_cost(grid, (3.5, 6.5), (3.5, 2.5), limit=20)
    approx(c, 4.0, msg="竖 4 格直线")
    print("[OK] 空旷直线：代价 = 距离")


def test_diagonal_uses_euclidean_step():
    """对角一步是 1.414 而不是 1。

    如果按 1 算，声音会沿着 45° 方向比直线传得更远，听感上是一个八角星。
    """
    grid = open_room()
    c = path_cost(grid, (3.5, 3.5), (6.5, 6.5), limit=20)
    approx(c, 3 * math.sqrt(2), msg="纯对角 3 步")
    print("[OK] 对角步长 = √2，不会出现八角星")


def test_wall_costs_and_thickness():
    """一格薄墙：多付一份穿墙费；五格厚岩：传不过去。"""
    thin = path_cost(two_rooms(1), (3.5, 7.5), (6.5, 7.5), limit=20)
    approx(thin, 3.0 + C.SOUND_WALL_COST, msg="隔一格墙的 3 格距离")

    thick = path_cost(two_rooms(5), (3.5, 7.5), (10.5, 7.5), limit=7.5)
    assert thick == math.inf, f"五格厚岩不该传得过去，实际代价 {thick}"
    print(f"[OK] 一格墙 +{C.SOUND_WALL_COST:g} 格代价；五格厚岩 = 传不过去")


def test_path_not_straight_line():
    """回字形走廊：直线 6 格、穿墙 21、绕外圈 18 —— 声音走 18 那条。

    这一条把"代价场"和"数穿墙格子"区分开了：纯数墙会选穿墙（21），
    代价场会选绕行（18）。只有真的在图上搜最短路才做得出这个结果。
    """
    grid = ring_grid()
    c = path_cost(grid, (8.5, 4.5), (8.5, 10.5), limit=30)
    # 横 10 + 竖 4 + 两个斜角 = 14 + 2√2（斜着切角比直角走短一点）
    approx(c, 14 + 2 * math.sqrt(2), tol=0.05, msg="回字形绕行")
    straight = 6.0
    assert c > 2.5 * straight, f"代价 {c} 应远大于直线 {straight:.1f}"
    # U 形那组：直线 3 格、只有 2 格墙，穿墙 9 < 绕行 15 —— 这时它会穿过去，
    # 也就是"薄墙能传、只是传不远"，这是有意的（见 sound.py 的说明）。
    u = path_cost(u_turn_grid(), (10.5, 5.5), (10.5, 8.5), limit=30)
    approx(u, 3.0 + 2 * C.SOUND_WALL_COST, msg="两格薄墙穿过去")
    print(f"[OK] 绕行 {c:.1f} 格 > 直线 6 格；薄墙按 {u:.1f} 格穿过去")


def test_field_is_bounded_by_limit():
    """场只展开到 limit：超出范围一律 inf，不会偷偷算出一个"很远但有限"的值。"""
    grid = open_room()
    f = SoundField(grid, (3, 6), 4.0)
    assert f.cost_at((5, 6)) == 2.0
    assert f.cost_at((10, 6)) == math.inf
    assert len(f.cost) < 60, "limit=4 的场不该铺满整张图"
    print("[OK] 代价场按半径截断")


# ---------------- 2. 感知层怎么用它 ----------------

def test_can_hear_prefers_cost_over_distance():
    """传了 cost 就必须用 cost；没传才退回直线（只给无地图的纯函数用）。"""
    p = Perception(hear_range=5.0)
    near = (2.0, 0.0)
    assert p.can_hear((0.0, 0.0), near, C.NOISE_SPRINT) > 0.0
    assert p.can_hear((0.0, 0.0), near, C.NOISE_SPRINT, cost=99.0) == 0.0
    print("[OK] cost 优先于直线距离")


def test_hearing_range_is_shared():
    """`Player.noise_radius()` 画的圈必须就是 AI 用的那个圈。

    这两处原来各抄了一份公式 —— 哪天调了系数，屏幕上的圈就不再是
    AI 真正的听觉范围，玩家看到的指南是假的。
    """
    player = Player(0.0, 0.0)
    p = Perception()
    for noise in (C.NOISE_SNEAK, C.NOISE_WALK, C.NOISE_SPRINT):
        player.moving = True
        player.sprinting = noise == C.NOISE_SPRINT
        player.sneaking = noise == C.NOISE_SNEAK
        r = player.noise_radius()
        approx(r, hearing_range(C.HEAR_RANGE, noise), msg="玩家画的圈")
        just_in = p.can_hear((0.0, 0.0), (r * 0.98, 0.0), noise, cost=r * 0.98)
        just_out = p.can_hear((0.0, 0.0), (r * 1.02, 0.0), noise, cost=r * 1.02)
        assert just_in > 0.0 and just_out == 0.0, f"半径 {r:.2f} 的边界不对"
    print("[OK] 屏幕上的噪音圈 = AI 的听觉范围（同一个函数）")


def test_enemy_behind_thick_wall_does_not_hear():
    """集成：玩家和哨兵隔五格岩，冲刺也听不见；墙没了就听得见（对照组）。"""
    for gap, expect in ((5, False), (0, True)):
        grid = two_rooms(gap)
        enemy = Enemy(10.5, 7.5, perception=Perception())
        world = make_world(grid, player=(3.5, 7.5), enemies=[enemy])
        loud_player(world)
        enemy._update_senses(1 / 60.0, world, world.player)
        assert enemy.hears_player is expect, (
            f"墙厚 {gap}：期望 hears_player={expect}，实际 {enemy.hears_player}"
        )
    print("[OK] 隔厚岩听不见、无墙听得见（都在 7 格直线内）")


def test_u_turn_hearing_is_blocked():
    """集成：U 形走廊，直线 3 格、隔两格墙也和聋子一样。

    旧实现按 3 格算 → 冲刺半径 7.5，听得一清二楚。新实现要付 3+2×墙费，
    再远一点就超出半径。这是玩家最能感知到的一类差别：贴墙站着不会被听走。
    """
    enemy = Enemy(10.5, 8.5, perception=Perception())
    world = make_world(u_turn_grid(), player=(10.5, 5.5), enemies=[enemy])
    loud_player(world)
    enemy._update_senses(1 / 60.0, world, world.player)
    assert not enemy.hears_player, "隔着两格墙，直线 3 格也不该听见"
    d = math.hypot(enemy.x - world.player.x, enemy.y - world.player.y)
    assert d < hearing_range(C.HEAR_RANGE, C.NOISE_SPRINT), "对照组前提：直线距离在半径内"
    cost = world.sound_cost(enemy.pos)
    assert cost > hearing_range(C.HEAR_RANGE, C.NOISE_SPRINT), cost
    print(f"[OK] 直线 {d:.1f} 格、实际代价 {cost:.1f} 格 → 听不见")


# ---------------- 3. 缓存与性能 ----------------

def test_world_caches_field_per_tile():
    """场按"玩家所在格"缓存：站着不动 30 帧只建一次，跨一格才重建。"""
    grid = two_rooms(5)
    enemy = Enemy(10.5, 7.5, perception=Perception())
    world = make_world(grid, player=(3.5, 7.5), enemies=[enemy])

    for _ in range(30):
        world.update(1 / 60.0)
    assert world.sound_builds == 1, f"站着不动应只建 1 次，实际 {world.sound_builds}"

    world.player.x += 1.0
    world.update(1 / 60.0)
    assert world.sound_builds == 2, f"跨一格应重建，实际 {world.sound_builds}"
    print("[OK] 声音场按格缓存（30 帧 1 次，跨格才重建）")


def test_sound_limit_follows_configured_hearing():
    """场要展开到**配置的**听觉半径，而不是写死的 7.5。

    测试/关卡里经常有大耳朵哨兵（hear_range=12）。场上限要是按常量写死，
    它们会以为自己聋了 —— 这种错不报错，只让断言莫名其妙地红。
    """
    world = make_world(open_room(), enemies=[Enemy(5.5, 5.5, perception=Perception(hear_range=12.0))])
    assert world._sound_limit() >= 18.0, world._sound_limit()
    print(f"[OK] 场上限跟着配置走：{world._sound_limit():.1f} 格")


def test_field_build_is_cheap():
    """真实关卡上建一次场要够快 —— 玩家每秒可能跨好几格。"""
    grid = build_demo_map()
    world = World(grid)
    world.player = Player(5.5, 5.5)
    t0 = time.perf_counter()
    n = 20
    for _ in range(n):
        SoundField(grid, (5, 5), world._sound_limit())
    per = (time.perf_counter() - t0) / n
    assert per < 0.020, f"单次建场 {per * 1000:.1f} ms，太慢"
    print(f"[OK] 真实关卡建场 {per * 1000:.2f} ms/次")


# ---------------- 4. 响动也要过地图 ----------------

def test_emit_noise_does_not_cross_thick_rock():
    """石子落地的响动同样穿不过厚岩；没有墙时照常惊动人（对照组）。"""
    for gap, expect in ((5, 0), (0, 1)):
        grid = two_rooms(gap)
        enemy = Enemy(10.5, 7.5, perception=Perception())
        world = make_world(grid, player=(3.5, 7.5), enemies=[enemy])
        n = world._emit_noise(3.5, 7.5, C.STONE_NOISE_RADIUS, C.STONE_SUSPICION, "stone")
        assert n == expect, f"墙厚 {gap}：期望惊动 {expect} 人，实际 {n}"
        if expect == 0:
            assert enemy.suspicion == 0.0, "听不见就不该抬可疑度"
    print("[OK] 响动按代价场扩散，不穿厚岩")


def main() -> int:
    tests = [
        test_open_corridor_cost_equals_distance,
        test_diagonal_uses_euclidean_step,
        test_wall_costs_and_thickness,
        test_path_not_straight_line,
        test_field_is_bounded_by_limit,
        test_can_hear_prefers_cost_over_distance,
        test_hearing_range_is_shared,
        test_enemy_behind_thick_wall_does_not_hear,
        test_u_turn_hearing_is_blocked,
        test_world_caches_field_per_tile,
        test_sound_limit_follows_configured_hearing,
        test_field_build_is_cheap,
        test_emit_noise_does_not_cross_thick_rock,
    ]
    for t in tests:
        t()
    print(f"\nW16 全部通过：{len(tests)} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
