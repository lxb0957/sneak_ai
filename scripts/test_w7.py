"""W7 测试：关卡可玩性 / AI 诚实性 的回归守卫。

这组测试守的都是"看起来能跑、其实是坏的"那类问题：

1. 敌人必须真的会动（之前出生坐标落在格点角上，全部被钉死在原地，
   整个项目的 AI 演示其实是静止靶子 —— 而测试全绿）。
2. 巡逻点必须都在空格上（地图加了掩体后，配置里的点可能被盖住）。
3. 追击用的共享流场必须指向"最后已知位置"，不能用玩家真实坐标。
   否则敌人等于开透视，玩家一旦被发现就永远甩不掉。
4. 警觉必须是限时的（否则返程全程硬扛，撤离阶段无解）。
5. 关卡必须"有解"：巡逻周期内存在全程不被任何视锥覆盖的通路。
"""
import math
import sys

sys.path.insert(0, ".")

import pygame

pygame.init()

from collections import deque

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Role
from sneak_ai.core.world import (
    World,
    build_demo_map,
    build_spawn_and_patrols,
)
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception

DT = 1 / 60


def make_world():
    grid = build_demo_map()
    world = World(grid)
    world.player = Player(5.5, 5.5)
    spawn, cfg = build_spawn_and_patrols(grid)
    world.enemies = [Enemy(x, y, patrol=pts, perception=Perception())
                     for (x, y, pts) in cfg]
    return world


# ---------------------------------------------------------------- 关卡
def test_all_enemies_actually_move():
    """所有哨兵都必须能离开出生点 —— 这是"AI 演示其实是静止靶子"的防线。

    判据用**累计路程**而不是"离出生点的净位移"：
    新地图里有几只哨兵的第一个巡逻点只有 1 格远，走过去、站岗 1.6 秒、
    再折返回来 —— 2 秒时它的净位移接近 0，但它明明一直在动。
    用净位移当判据会得到"这些哨兵卡住了"的假警报（我第一版就撞了）。
    """
    world = make_world()
    prev = [(e.x, e.y) for e in world.enemies]
    travelled = [0.0] * len(world.enemies)
    for _ in range(int(2.0 / DT)):
        world.update(DT)
        for i, e in enumerate(world.enemies):
            travelled[i] += math.hypot(e.x - prev[i][0], e.y - prev[i][1])
            prev[i] = (e.x, e.y)
    stuck = [i for i, d in enumerate(travelled) if d < 0.5]
    assert not stuck, f"这些哨兵 2 秒内几乎没移动（疑似卡在墙里）：{stuck}"
    print(f"[OK] {len(world.enemies)} 个哨兵全部能正常巡逻"
          f"（2 秒累计路程 {min(travelled):.1f}~{max(travelled):.1f} 格）")


def test_no_enemy_stuck_at_wall():
    """哨兵不能卡墙：长时间跑下来，任何一只都不许出现"想走但原地不动"。

    上一个版本这里只会报"2 秒内没动过"，抓不住"走两步撞墙、然后永远顶在
    墙上"这种半卡死。现在按 0.5 秒窗口统计最长零位移时长 ——
    站岗环视会主动停 1.6 秒，所以阈值放到 2.5 秒。
    """
    world = make_world()
    # 把玩家摘出去，让哨兵只受巡逻驱动（否则追击会掩盖卡墙问题）。
    # 别用"把玩家挪到角落"的办法：新地图的角落是 G5 房间内部，
    # 哨兵会把玩家抓住 → state=DEFEAT → World.update 整体冻结 →
    # 21 只哨兵全部报"卡住"（这个假警报我踩过一次）。
    world.player = None

    tracks = [{"e": e, "prev": (e.x, e.y), "still": 0.0, "worst": 0.0}
              for e in world.enemies]
    for _ in range(int(45.0 / DT)):
        world.update(DT)
        for t in tracks:
            e = t["e"]
            moved = math.hypot(e.x - t["prev"][0], e.y - t["prev"][1])
            t["prev"] = (e.x, e.y)
            if moved < 0.004:
                t["still"] += DT
                t["worst"] = max(t["worst"], t["still"])
            else:
                t["still"] = 0.0

    stuck = [(i, round(t["worst"], 1), (round(t["e"].x, 1), round(t["e"].y, 1)))
             for i, t in enumerate(tracks) if t["worst"] > 2.5]
    assert not stuck, f"这些哨兵长时间原地不动（卡墙）：{stuck}"
    print(f"[OK] {len(tracks)} 个哨兵 45 秒内无卡墙（最长停留 "
          f"{max(t['worst'] for t in tracks):.1f}s = 站岗环视）")


def test_patrol_points_are_walkable():
    """巡逻点/出生点必须落在空格中心，否则碰撞判定会把敌人锁死。"""
    grid = build_demo_map()
    spawn, cfg = build_spawn_and_patrols(grid)
    assert not grid.is_wall(int(spawn[0]), int(spawn[1])), "玩家出生点在墙里"
    for i, (x, y, pts) in enumerate(cfg):
        assert not grid.is_wall(int(x), int(y)), f"第 {i} 个哨兵出生在墙里 ({x},{y})"
        # 坐标必须是格中心（x.5），不能是整数格点：整数格点是四格公共角点，
        # 圆形碰撞会判成"一直贴着墙"。
        assert abs(x - int(x) - 0.5) < 1e-6, f"第 {i} 个哨兵坐标未吸附到格中心: {x}"
        for px, py in pts:
            assert not grid.is_wall(int(px), int(py)), f"第 {i} 个哨兵的巡逻点在墙里 ({px},{py})"
    print(f"[OK] {len(cfg)} 组巡逻配置全部落在可走格中心")


def _watched_cells(world, rays=40):
    cells = set()
    for e in world.enemies:
        rng = e.view_range()
        if rng <= 0:
            continue
        fov = e.perception.fov_angle if e.perception else math.radians(110)
        for i in range(rays + 1):
            a = e.facing - fov / 2 + fov * i / rays
            ca, sa = math.cos(a), math.sin(a)
            for step in range(1, int(rng) + 1):
                cx, cy = int(e.x + ca * step), int(e.y + sa * step)
                if world.grid.is_wall(cx, cy):
                    break
                cells.add((cx, cy))
    return cells


def _reachable(grid, start, goal, blocked):
    sx, sy = int(start[0]), int(start[1])
    seen = {(sx, sy)}
    q = deque([(sx, sy)])
    while q:
        x, y = q.popleft()
        if (x, y) == (int(goal[0]), int(goal[1])):
            return True
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in seen or (nx, ny) in blocked or grid.is_wall(nx, ny):
                continue
            seen.add((nx, ny))
            q.append((nx, ny))
    return False


def test_level_has_stealth_solution():
    """关卡必须"有解"。

    这里换过判据，值得留一条记录：
    旧判据是"某个瞬间存在全程不被看见的通路"。它对**空地加墙**的地图成立，
    但新地图是实心开挖的 θ 形 —— 到终点必须逐门穿 6~8 道 2 格宽的门，
    要求"同一瞬间所有门都空着"等于要求 21 个哨兵同时闭眼，
    这个值必然是 0%，可玩家明明过得去。

    门控关卡的正确判据是**门窗口**：每道门都要有足够长的连续空档
    （人穿 2 格约 0.5s，留一倍余量取 1.0s）。只要每道门都有窗口，
    玩家就能一格一格地推进 —— 难，但有解。
    """
    world = make_world()
    world.player = None          # 只测巡逻，不让玩家影响哨兵
    from sneak_ai.core.world import DOORS

    STEP = 0.4
    samples = 120
    best = {d: 0.0 for d in DOORS}
    streak = {d: 0.0 for d in DOORS}

    for _ in range(samples):
        for _ in range(int(STEP * 60)):
            world.update(DT)
        watched = _watched_cells(world)
        for d in DOORS:
            cells = [(x, y) for y in range(d[1], d[3] + 1)
                     for x in range(d[0], d[2] + 1)]
            if any(c not in watched for c in cells):
                streak[d] += STEP
                best[d] = max(best[d], streak[d])
            else:
                streak[d] = 0.0

    bad = [(d[4], round(w, 1)) for d, w in best.items() if w < 1.0]
    assert not bad, f"这些门在 {samples * STEP:.0f}s 里从没有过 1 秒的通行窗口：{bad}"
    tight = min(best.items(), key=lambda kv: kv[1])
    print(f"[OK] 关卡有解：{len(DOORS)} 道门全部存在通行窗口"
          f"（最紧的「{tight[0][4]}」最长 {tight[1]:.1f}s）")


# ---------------------------------------------------------------- AI 诚实性
def test_flow_field_targets_last_known_not_true_position():
    """敌人看不见玩家时，流场目标不能是玩家真实坐标（那就是透视）。"""
    world = make_world()
    # 摆在 A1 走廊里（y=6 一线是通的）：换个坐标就得重新确认这条视线没有遮挡，
    # 否则"敌人没看见玩家"会被误判成"AI 不记录最后已知位置"。
    e = Enemy(13.5, 6.5, perception=Perception())
    e.facing = 0.0
    world.enemies = [e]

    # 先让敌人看到玩家，建立"最后已知位置"
    world.player.x, world.player.y = 18.5, 6.5
    for _ in range(int(2.0 / DT)):
        world.update(DT)
        if e.role != Role.PATROL:
            break
    assert world.last_known is not None, "看到玩家后应记录最后已知位置"

    # 玩家绕远（脱离视线）：G7 房间内部，和 A1 完全不在一处
    world.player.x, world.player.y = 61.0, 41.0
    world.update(DT)
    assert not e.sees_player, "该位置不应被看见"
    lk = world.last_known
    assert lk is not None and math.hypot(lk[0] - 60.0, lk[1] - 40.0) > 10, \
        f"最后已知位置被玩家的真实坐标污染了：{lk}"
    print("[OK] 流场目标是最后已知位置，敌人在视野外不会上帝视角追踪")


def test_alert_is_time_limited():
    """拿数据后的全图警觉必须限时解除，否则撤离阶段无解。"""
    world = make_world()
    world.player.x, world.player.y = C.DATA_TERMINAL
    world.update(DT)
    assert world.alert and world.alert_left > 0, "接触终端后应触发限时警觉"
    assert all(e.alerted for e in world.enemies), "警觉期内所有哨兵应进入高警戒"

    world.player.x, world.player.y = 40.0, 20.0   # 挪开，避免被抓冻结世界
    for _ in range(int((C.ALERT_DURATION + 1.0) / DT)):
        world.update(DT)
    assert not world.alert, "警觉到点应自动解除"
    assert not any(e.alerted for e in world.enemies), "解除后哨兵应恢复常态视野"
    print(f"[OK] 警觉限时 {C.ALERT_DURATION:.0f} 秒，到点自动解除")


# ---------------------------------------------------------------- 性能
def test_frame_budget_with_14_enemies():
    """21 个敌人 + 门控地图，单帧逻辑耗时必须远低于 16.6ms。"""
    import time

    world = make_world()
    for _ in range(30):
        world.update(DT)
    t0 = time.perf_counter()
    n = 300
    for _ in range(n):
        world.update(DT)
    ms = (time.perf_counter() - t0) / n * 1000
    assert ms < 8.0, f"单帧逻辑 {ms:.2f}ms 太慢"
    print(f"[OK] 单帧逻辑 {ms:.2f}ms -> {1000 / ms:.0f} FPS（仅逻辑，不含渲染）")


if __name__ == "__main__":
    test_all_enemies_actually_move()
    test_no_enemy_stuck_at_wall()
    test_patrol_points_are_walkable()
    test_level_has_stealth_solution()
    test_flow_field_targets_last_known_not_true_position()
    test_alert_is_time_limited()
    test_frame_budget_with_14_enemies()
    print("\nW7 关卡与 AI 诚实性测试全部通过 ✅")
