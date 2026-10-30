"""W3 综合测试：地图连通性、协同分工、GOAP、行为树、性能。

运行：python scripts/test_w3.py
"""

import math
import sys
import time

sys.path.insert(0, ".")


def test_goap():
    from sneak_ai.ai.goap import Action, plan

    eat = Action("eat", 1.0, frozenset(["has_food"]), frozenset(["not_hungry"]))
    get_food = Action("get_food", 2.0, frozenset(), frozenset(["has_food"]))
    result = plan(frozenset(["hungry"]), frozenset(["not_hungry"]), [eat, get_food])
    names = [a.name for a in result] if result else None
    assert names == ["get_food", "eat"], f"GOAP 规划错误: {names}"
    print("[OK] GOAP 规划 get_food -> eat")


def test_behavior_tree():
    from sneak_ai.ai.behavior_tree import (
        Selector, Sequence, Condition, Action, Blackboard, Status,
    )

    bb = Blackboard()
    bb.set("see", True)
    tree = Selector([
        Sequence([Condition("s", lambda b: b.get("see")),
                  Action("chase", lambda b: (b.set("doing", "chase"), Status.SUCCESS)[1])]),
        Action("patrol", lambda b: (b.set("doing", "patrol"), Status.SUCCESS)[1]),
    ])
    tree.tick(bb)
    assert bb.get("doing") == "chase"
    bb.set("see", False)
    tree.tick(bb)
    assert bb.get("doing") == "patrol"
    print("[OK] 行为树 Selector 优先级切换")


def test_map_connectivity():
    from sneak_ai.core.world import build_demo_map
    from sneak_ai.pathing import astar

    grid = build_demo_map()
    # 关键点：四个角落 + 各区域中心。先确保是墙点则跳过（测试目标是连通性，
    # 不是验证某点是否恰好是墙）
    targets = [(5, 5), (70, 5), (5, 55), (70, 55), (30, 10), (50, 20), (30, 50), (70, 28)]
    for t in targets:
        if not grid.is_walkable(*t):
            continue
        p = astar(grid, (5, 5), t)
        assert p is not None, f"地图不可达: (5,5) -> {t}"
    # 更严格：全图无死区（所有可走格都从 (5,5) 可达）
    dead = sum(
        1 for y in range(grid.h) for x in range(grid.w)
        if grid.is_walkable(x, y) and astar(grid, (5, 5), (x, y)) is None
    )
    assert dead == 0, f"地图存在 {dead} 个死区格"
    print(f"[OK] 地图连通性：全图 {grid.w}x{grid.h} 无死区")


def test_coordination():
    from sneak_ai.ai.coordination import Coordinator, Role
    from sneak_ai.core.world import build_demo_map

    grid = build_demo_map()
    coord = Coordinator(grid)
    assert coord._chokepoints, "咽喉格预计算为空"
    print(f"[OK] 咽喉格预计算：{len(coord._chokepoints)} 个通道口")


def test_chase_performance():
    import pygame
    pygame.init()
    from sneak_ai.__main__ import Game

    g = Game()
    player = g.player
    player.x, player.y = 40.0, 28.0
    # 8 个敌人围住玩家，强制触发追击
    for i, e in enumerate(g.world.enemies[:8]):
        e.patrol = []
        e.x = 40.0 + (i % 4 - 1.5) * 3
        e.y = 28.0 + (i // 4 - 0.5) * 4
        e.facing = math.atan2(28.0 - e.y, 40.0 - e.x)
    # 4.8 秒：可疑度每帧只涨一次（早期版本 world.update 里敌人被更新了两遍，
    # 相当于双倍增速，这个测试原本按 2 秒写的，修复后就得给足时间）
    for _ in range(300):
        g.world.update(0.016)

    roles = {}
    for e in g.world.enemies:
        roles[e.role.value] = roles.get(e.role.value, 0) + 1
    assert "chase" in roles, f"没有追击敌人: {roles}"

    t0 = time.perf_counter()
    for _ in range(1000):
        g.world.update(0.016)
        # 追击会把敌人送到玩家脸上（触发捕获后世界冻结、update 直接 return），
        # 测性能时必须每帧复位，否则测到的是"空转"的假帧率
        if g.world.state != "infiltrate":
            g.world.state = "infiltrate"
            g.world.captured = False
    avg_ms = (time.perf_counter() - t0) / 1000 * 1000
    fps = 1000 / avg_ms
    print(f"[OK] 角色分布: {roles}")
    print(f"[OK] 性能: {avg_ms:.2f}ms/帧 -> {fps:.0f} FPS (目标 >= 60)")
    assert fps >= 60, f"帧率不达标: {fps:.0f} FPS"
    pygame.quit()


if __name__ == "__main__":
    test_goap()
    test_behavior_tree()
    test_map_connectivity()
    test_coordination()
    test_chase_performance()
    print("\nW3 综合测试全部通过 ✅")
