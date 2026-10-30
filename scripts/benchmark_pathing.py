"""寻路性能 benchmark：对比 A* / JPS / 流场在多敌人场景下的耗时。

核心结论（简历引用）：
- 单敌人：JPS 比 A* 快（扩展节点少）。
- 多敌人（共享同一目标）：流场 O(1) 摊薄，远超"每个敌人跑一次 A*"。

用法：python scripts/benchmark_pathing.py
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import build_demo_map
from sneak_ai.pathing import astar, jps, FlowField


def make_agents(grid, n, rng):
    """随机采样 n 个可走起点。"""
    walkable = [
        (x, y) for y in range(grid.h) for x in range(grid.w) if grid.is_walkable(x, y)
    ]
    return [rng.choice(walkable) for _ in range(n)]


def bench_astar(grid, starts, goal):
    t0 = time.perf_counter()
    for s in starts:
        astar(grid, s, goal)
    return time.perf_counter() - t0


def bench_jps(grid, starts, goal):
    t0 = time.perf_counter()
    for s in starts:
        jps(grid, s, goal)
    return time.perf_counter() - t0


def bench_flowfield(grid, starts, goal):
    t0 = time.perf_counter()
    ff = FlowField(grid, goal)  # 只算一次
    for s in starts:
        ff.direction_at(*s)
    return time.perf_counter() - t0


def main():
    grid = build_demo_map()
    rng = random.Random(7)
    goal = (70, 52)   # 终端舱内部（新地图里这个点必须真的是可走格，否则 A* 立刻返回 None，整张表都是 0）

    agent_counts = [1, 10, 50, 100, 200]
    # 每个规模跑 3 轮取最小值：Python 的 GC / 系统调度会让单次测量抖动 ±20%，
    # 取最小值等于"剔除被操作系统打断的那些轮"，比平均值更能反映算法本身的成本。
    REPS = 3
    print(f"{'敌人数量':<8}{'A* (ms)':<12}{'JPS (ms)':<12}{'流场 (ms)':<12}")
    print("-" * 44)

    results = {}
    for n in agent_counts:
        starts = make_agents(grid, n, rng)
        ta = min(bench_astar(grid, starts, goal) for _ in range(REPS)) * 1000
        tj = min(bench_jps(grid, starts, goal) for _ in range(REPS)) * 1000
        tf = min(bench_flowfield(grid, starts, goal) for _ in range(REPS)) * 1000
        results[n] = (ta, tj, tf)
        print(f"{n:<8}{ta:<12.2f}{tj:<12.2f}{tf:<12.2f}")

    # 关键结论：200 敌人时 A* vs 流场
    ta200 = results[200][0]
    tf200 = results[200][2]
    speedup = ta200 / tf200
    print("-" * 44)
    print(f"200 敌人：A* 耗时 {ta200:.2f}ms → 流场 {tf200:.2f}ms，"
          f"提速 {speedup:.1f}x（-{(1 - tf200/ta200)*100:.1f}%）")
    print(f"（每个规模 {REPS} 轮取最小值；地图为当前关卡 {grid.w}x{grid.h}）")


if __name__ == "__main__":
    main()
