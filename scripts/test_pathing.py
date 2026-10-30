"""寻路模块测试：A*/JPS 一致性、连通性、流场可到达性。

用法：python scripts/test_pathing.py
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import build_demo_map
from sneak_ai.pathing import astar, jps, FlowField, flowfield_path


def path_length(path):
    if not path:
        return None
    total = 0.0
    for i in range(1, len(path)):
        dx = abs(path[i][0] - path[i - 1][0])
        dy = abs(path[i][1] - path[i - 1][1])
        total += 1.414 if dx and dy else 1.0
    return total


def test_consistency():
    grid = build_demo_map()
    rng = random.Random(42)
    pairs = []
    # 随机采样可走点对
    walkable = [
        (x, y)
        for y in range(grid.h)
        for x in range(grid.w)
        if grid.is_walkable(x, y)
    ]
    for _ in range(200):
        s = rng.choice(walkable)
        g = rng.choice(walkable)
        pairs.append((s, g))

    mismatches = 0
    for s, g in pairs:
        # 同一个移动语义下才能比"是否等价"：
        # 游戏里的 A* 禁止斜穿墙角（圆形实体过不去），JPS 的剪枝规则
        # 建立在经典"允许切角"语义上，所以这里用 corner_cut=True 对齐口径。
        pa = astar(grid, s, g, corner_cut=True)
        pj = jps(grid, s, g)
        # 可达性一致
        if (pa is None) != (pj is None):
            mismatches += 1
            continue
        if pa is None:
            continue
        la, lj = path_length(pa), path_length(pj)
        if abs(la - lj) > 1e-6:
            mismatches += 1

    print(f"[{'OK' if mismatches == 0 else 'FAIL'}] A*/JPS 200 对路径一致 (不一致 {mismatches})")
    assert mismatches == 0


def test_flowfield_reach():
    grid = build_demo_map()
    start = (6, 5)       # 出生舱
    goal = (70, 52)      # 终端舱（贯穿整张新地图）
    pa = astar(grid, start, goal)
    pf = flowfield_path(grid, start, goal)
    assert pa is not None, "A* 应可达"
    assert pf is not None, "流场应可达"
    # 流场路径终点必须是 goal
    assert pf[-1] == goal, "流场应到达目标"
    print("[OK] 流场可达且终点正确")


def test_unreachable():
    grid = GridMap.empty(10, 10)
    for x in range(10):
        grid.set_wall(x, 5)  # 中间一整排墙，上下隔断
    assert astar(grid, (5, 2), (5, 8)) is None
    assert jps(grid, (5, 2), (5, 8)) is None
    assert FlowField(grid, (5, 8)).direction_at(5, 2) is None
    print("[OK] 不可达场景三算法一致返回空")


if __name__ == "__main__":
    test_consistency()
    test_flowfield_reach()
    test_unreachable()
    print("\n寻路测试全部通过 ✅")
