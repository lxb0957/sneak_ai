"""AI 行为指标统计：量化"AI 是否真的变好了"。

这是简历里最有说服力的部分——不是"感觉更聪明了"，而是可复现的数字。

统计指标：
1. 平均发现时间：玩家暴露在敌人视野后，多久被确认发现（suspicion>=0.7）。
2. 捕获率：敌人逼近玩家到接触距离（<1.0 格）的比例与平均耗时。
3. 潜行绕后成功率：玩家潜行从敌人背后经过、不被发现的比例。
4. 协同分散度：追击时敌人的空间分布（标准差），越大说明越分散（没挤成一团）。

用法：python scripts/benchmark_ai_metrics.py
"""

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from sneak_ai.__main__ import Game  # noqa: E402


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _nearest_walkable(grid, x, y):
    """找离 (x,y) 最近的可走格（测试用，避免坐标恰好落在墙上）。"""
    if grid.is_walkable(int(x), int(y)):
        return (float(x), float(y))
    for r in range(1, 10):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                nx, ny = int(x) + dx, int(y) + dy
                if grid.is_walkable(nx, ny):
                    return (float(nx), float(ny))
    return (float(x), float(y))


def _open_row(grid, length):
    """找一条长度 length、完全无遮挡的水平可走走廊（返回起点格坐标）。

    为什么要找：追击指标必须给敌人一条直线通路。之前固定用 (8,8) 往右，
    而地图在那个位置有立柱 —— 敌人直线撞柱子卡住，捕获率算出来是 0%，
    看起来像"AI 退化了"，其实是测试场景本身不成立。
    """
    # 先找够长的；够长的找不到就退而求其次，返回**全场最长的那一条**。
    # 绝对不能返回 (2,2) 这种写死的兜底值：新地图是实心开挖的，
    # (2,2) 是岩体，把敌人和玩家都塞进墙里 → 捕获率算出 0%，
    # 看起来像"AI 退化了"，其实是测试场景根本没建立起来（这次就踩了）。
    best = (0, 2, 2)
    for y in range(2, grid.h - 2):
        run = 0
        for x in range(2, grid.w - 2):
            if grid.is_walkable(x, y):
                run += 1
                if run > best[0]:
                    best = (run, x, y)
                if run >= length:
                    return (x - length + 1, y)
            else:
                run = 0
    run, bx, by = best
    return (bx - run + 1, by)


def metric_detection_time(g, trials=30, max_frames=600):
    """玩家站在敌人正前方（无遮挡），测从暴露到被发现的帧数。"""
    times = []
    for _ in range(trials):
        g._restart()                       # 每局都用干净世界，避免互相污染
        grid = g.world.grid
        sx, sy = _open_row(grid, 8)
        enemy = g.world.enemies[0]
        enemy.patrol = []
        enemy.x, enemy.y = sx + 0.5, sy + 0.5
        enemy.facing = 0.0
        enemy.suspicion = 0.0
        # 玩家放正前方 4 格：这个距离上"要盯一会儿才确认"才看得出来
        g.player.x, g.player.y = sx + 4.5, sy + 0.5
        g.player.sneaking = False

        for frame in range(max_frames):
            g.world.update(0.016)
            if enemy.suspicion >= 0.7:
                times.append(frame)
                break
    if not times:
        return None
    return sum(times) / len(times)


def metric_capture_rate(g, trials=30, max_frames=900):
    """敌人追击玩家，统计逼近到接触距离(<1.0格)的比例和耗时。"""
    captured = 0
    times = []
    for _ in range(trials):
        g._restart()
        grid = g.world.grid
        sx, sy = _open_row(grid, 12)
        enemy = g.world.enemies[0]
        enemy.patrol = []
        enemy.x, enemy.y = sx + 0.5, sy + 0.5
        enemy.facing = 0.0
        enemy.suspicion = 1.0            # 直接进入追击（模拟"刚被确认发现"）
        enemy.role = enemy.role.CHASE
        g.player.x, g.player.y = sx + 10.5, sy + 0.5

        # 关键：追击靠共享流场，而流场目标是"最后已知位置"。
        # 不给它一个已知位置，敌人就没有流场可用，只能走直线逼近
        # （遇到拐角就废了）—— 这不是测 AI，是测它没数据时的降级路径。
        g.world.last_known = (g.player.x, g.player.y)

        for frame in range(max_frames):
            g.world.update(0.016)
            if _dist(enemy.pos, g.player.pos) < 1.0:
                captured += 1
                times.append(frame)
                break
    if captured == 0:
        return 0.0, None
    return captured / trials, sum(times) / len(times)


def metric_sneak_bypass(g, trials=30, max_frames=300):
    """玩家潜行从敌人背后绕过，全程不进入正面视野锥，测是否被听觉发现。

    绕后成功 = 从敌人背后（180° 背面）经过且不被发现。
    """
    success = 0
    grid = g.world.grid
    for _ in range(trials):
        enemy = g.world.enemies[0]
        enemy.patrol = []
        ex, ey = _nearest_walkable(grid, 8.0, 8.0)
        enemy.x, enemy.y = ex, ey
        enemy.facing = 0.0  # 朝右，背后是左侧(x < ex)
        enemy.suspicion = 0.0
        # 玩家从敌人背后垂直方向绕（保持 x 在敌人左侧，即始终在背面）
        g.player.x, g.player.y = ex - 3.0, ey - 3.0
        g.player.sneaking = True

        detected = False
        for frame in range(max_frames):
            # 沿背后垂直移动（从上到下），始终在背面半平面内
            g.player.y += 0.08
            g.world.update(0.016)
            if enemy.suspicion >= 0.7:
                detected = True
                break
            if g.player.y > ey + 3.0:
                break
        if not detected:
            success += 1
    return success / trials


def metric_dispersion(g, trials=20):
    """追击时敌人空间分布的标准差（越大越分散，证明协同没挤成一团）。"""
    dispersions = []
    grid = g.world.grid
    for _ in range(trials):
        # 让多个敌人追击
        player = g.player
        cx, cy = _nearest_walkable(grid, 40.0, 28.0)
        player.x, player.y = cx, cy
        player.sneaking = False
        for i, e in enumerate(g.world.enemies[:8]):
            e.patrol = []
            e.x = cx + (i % 4 - 1.5) * 3
            e.y = cy + (i // 4 - 0.5) * 4
            e.facing = math.atan2(cy - e.y, cx - e.x)
            e.suspicion = 1.0
        for _ in range(120):
            g.world.update(0.016)

        # 统计追击中敌人的位置散布
        chasing = [e for e in g.world.enemies[:8] if e.role.value != "patrol"]
        if len(chasing) < 2:
            continue
        xs = [e.x for e in chasing]
        ys = [e.y for e in chasing]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        spread = math.sqrt(
            sum((x - mx) ** 2 + (y - my) ** 2 for x, y in zip(xs, ys)) / len(chasing)
        )
        dispersions.append(spread)
    if not dispersions:
        return 0.0
    return sum(dispersions) / len(dispersions)


def main():
    pygame.init()
    g = Game()
    print("AI 行为指标统计（30 局取平均）\n" + "=" * 50)

    t = metric_detection_time(g)
    if t is not None:
        print(f"平均发现时间: {t:.0f} 帧 ≈ {t*0.016:.2f} 秒")
    else:
        print("平均发现时间: 未发现（需检查视野配置）")

    rate, avg_t = metric_capture_rate(g)
    print(f"追击捕获率: {rate*100:.0f}%", end="")
    if avg_t is not None:
        print(f" | 平均捕获耗时 {avg_t*0.016:.2f} 秒")
    else:
        print(" | 未捕获")

    bypass = metric_sneak_bypass(g)
    print(f"潜行绕后成功率: {bypass*100:.0f}%（越高=感知越拟真，不会被秒发现）")

    disp = metric_dispersion(g)
    print(f"追击协同分散度: {disp:.2f} 格（越大=敌人越分散，没挤成一团）")

    pygame.quit()
    print("=" * 50)


if __name__ == "__main__":
    main()
