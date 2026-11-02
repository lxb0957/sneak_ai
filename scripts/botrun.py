"""机器人通关模拟：量化关卡难度，不启动窗口。

它模拟的不是"高手"，而是一个**看得懂视野锥、会等、会躲**的普通玩家：
  GO    按"危险度加权"的路径走，前方被视锥照着就蹲下等
  FLEE  真被盯上了（已被追击 / 贴身 / 确认发现）→ 跑向最近的掩体点
  HIDE  躲好后蹲着不动，直到周围可疑度降下来再继续

用法：
    SDL_VIDEODRIVER=dummy python scripts/botrun.py --aware
    SDL_VIDEODRIVER=dummy python scripts/botrun.py --aware --runs 5

判定标准：--dumb（只走最短路径）应该死得很快；
--aware 至少能赢一两局，说明关卡"难但有解"。
"""
import argparse
import heapq
import math
import random
import sys
from collections import deque

sys.path.insert(0, ".")

import pygame

pygame.init()

from sneak_ai import constants as C
from sneak_ai.__main__ import Game
from sneak_ai.ai.coordination import Role
from sneak_ai.pathing.astar import astar

_DIRS8 = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.4142135623730951), (1, -1, 1.4142135623730951),
    (-1, 1, 1.4142135623730951), (-1, -1, 1.4142135623730951),
]


def _keys_toward(p, tx, ty, sneak=True, sprint=False):
    keys = set()
    dx, dy = tx - p.x, ty - p.y
    if dx > 0.08:
        keys.add(pygame.K_d)
    elif dx < -0.08:
        keys.add(pygame.K_a)
    if dy > 0.08:
        keys.add(pygame.K_s)
    elif dy < -0.08:
        keys.add(pygame.K_w)
    if sprint:
        keys.add(pygame.K_SPACE)
    elif sneak:
        keys.add(pygame.K_LSHIFT)
    return keys


def _danger_map(world):
    """把每个敌人的视锥"照"到格子上，得到 0~1 的危险度。

    用固定条数射线扫描（14 × 45 × 6.5 ≈ 4000 次采样，约 3ms），
    所以可以每 0.4 秒重建一次喂给寻路，而不是每帧。
    """
    danger = {}
    for e in world.enemies:
        rng = e.view_range()
        if rng <= 0:
            continue
        # 贴身 1.6 格无条件高危：被抓是纯距离判定，从背后贴上去一样算被抓
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if math.hypot(dx, dy) <= 1.6:
                    danger[(int(e.x) + dx, int(e.y) + dy)] = 1.0

        fov = e.perception.fov_angle if e.perception else math.radians(110)
        rays = 44
        # 站岗的哨兵会左右环视（PATROL_SCAN_AMP）。只按"当前朝向"算危险，
        # 等于假设哨兵是一根不会转的探照灯 —— 机器人就会走进一个
        # 下一秒被扫到的格子。所以把 ±摆幅的锥体一起算进去，权重略低。
        sweep = C.PATROL_SCAN_AMP if e.dwelling else 0.0
        cones = [(0.0, 1.0)]
        if sweep > 0.0:
            cones += [(-sweep, 0.75), (sweep, 0.75)]
        for off, wmul in cones:
            for i in range(rays + 1):
                a = e.facing + off - fov / 2 + fov * i / rays
                ca, sa = math.cos(a), math.sin(a)
                for step in range(1, int(rng) + 1):
                    cx, cy = int(e.x + ca * step), int(e.y + sa * step)
                    if world.grid.is_wall(cx, cy):
                        break
                    w = (1.0 - step / (rng + 1)) * wmul
                    if w > danger.get((cx, cy), 0.0):
                        danger[(cx, cy)] = w
    return danger


def _plan(world, start, goal, danger, weight=7.0):
    """危险度加权的 A*：绕开现在会被看到的格子。"""
    if not world.grid.is_walkable(*goal):
        return None
    sx, sy = int(start[0]), int(start[1])
    gx, gy = int(goal[0]), int(goal[1])
    openq = [(0.0, sx, sy)]
    gscore = {(sx, sy): 0.0}
    parent = {}
    seen = set()
    while openq:
        _, x, y = heapq.heappop(openq)
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if (x, y) == (gx, gy):
            path, cur = [], (x, y)
            while cur in parent:
                path.append(cur)
                cur = parent[cur]
            path.reverse()
            return path
        for dx, dy, cost in _DIRS8:
            nx, ny = x + dx, y + dy
            if not world.grid.is_walkable(nx, ny):
                continue
            if dx and dy and (
                world.grid.is_wall(x + dx, y) or world.grid.is_wall(x, y + dy)
            ):
                continue
            step = cost * (1.0 + weight * danger.get((nx, ny), 0.0))
            ng = gscore[(x, y)] + step
            if ng < gscore.get((nx, ny), 1e18):
                gscore[(nx, ny)] = ng
                parent[(nx, ny)] = (x, y)
                heapq.heappush(openq, (ng + math.hypot(gx - nx, gy - ny), nx, ny))
    return None


def _safe_path(world, danger, p, threat=None):
    """找最近的安全格，但要**离哨兵尽量远**，返回路径。

    逃跑不能"朝反方向跑"——那必然被逼进地图角落；也不能"只找眼前安全格"——
    那会躲到追兵鼻子底下。旧版是"取 BFS 里第一个离威胁 5 格的安全格"，
    在 θ 形的窄走廊里几乎总是失败：第一个安全格往往就在追兵身后。
    现在改成在 9 格范围内**枚举所有安全格**，按
    「离最近哨兵的距离 − 路径长度的一半」打分取最大。
    """
    sx, sy = int(p.x), int(p.y)
    guards = [(e.x, e.y) for e in world.enemies if e.alive]
    seen = {(sx, sy)}
    parent = {}
    depth = {(sx, sy): 0}
    q = deque([(sx, sy)])
    cands = []
    while q:
        x, y = q.popleft()
        d0 = depth[(x, y)]
        if d0 > 0 and d0 <= 9 and danger.get((x, y), 0.0) <= 0.02:
            near = min((math.hypot(x - gx, y - gy) for gx, gy in guards), default=99.0)
            cands.append((near - 0.5 * d0, (x, y)))
        if d0 >= 9:
            continue
        for dx, dy, _ in _DIRS8:
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not world.grid.is_walkable(nx, ny):
                continue
            if dx and dy and (
                world.grid.is_wall(x + dx, y) or world.grid.is_wall(x, y + dy)
            ):
                continue
            seen.add((nx, ny))
            parent[(nx, ny)] = (x, y)
            depth[(nx, ny)] = d0 + 1
            q.append((nx, ny))
    if not cands:
        return []
    cands.sort(reverse=True)
    best = cands[0][1]
    path = [best]
    while path[-1] in parent:
        path.append(parent[path[-1]])
    path.reverse()
    return path[1:]


def _threat(world, p):
    """(最危险敌人, 是否被看着, 是否已被协同追击, 最近距离, 附近最高可疑度)。"""
    worst, worst_score, watched, chased = None, 0.0, False, False
    nearest, max_susp = 1e9, 0.0
    for e in world.enemies:
        d = math.hypot(e.x - p.x, e.y - p.y)
        nearest = min(nearest, d)
        if e.role != Role.PATROL:
            chased = True
        if d > 12.0:
            continue
        max_susp = max(max_susp, e.suspicion)
        watched = watched or e.sees_player
        score = e.suspicion + (0.6 if e.sees_player else 0.0)
        if score > worst_score:
            worst_score, worst = score, e
    return worst, watched, chased, nearest, max_susp


def _los_clear(world, a, b) -> bool:
    """a 到 b 之间有没有墙（给"这一枪打得着吗"用）。"""
    steps = int(math.hypot(b[0] - a[0], b[1] - a[1]) * 4) + 1
    for i in range(1, steps):
        t = i / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        if world.grid.is_wall(int(x), int(y)):
            return False
    return True


def _body_would_be_seen(world, victim) -> bool:
    """打掉这个人之后，尸体会不会被第三个哨兵看见？

    这是"会用枪的玩家"和"看见人就扣扳机的玩家"的分界线：
    当着别人的面开枪，换来的是 5 秒空窗 + 一片搜查（尸体目击机制）；
    躲进空房间开枪，换来的才是干净的 5 秒。
    机器人必须会做这道判断题，否则它测出来的不是关卡难度，是它的愚蠢。
    """
    for o in world.enemies:
        if o is victim or not o.alive or o.grace > 0.0 or o.perception is None:
            continue
        d = math.hypot(o.x - victim.x, o.y - victim.y)
        if d > C.BODY_SPOT_DIST:
            continue
        if o.perception.can_see(
            o.pos, o.facing, (victim.x, victim.y), world.grid,
            view_range=o.view_range(),
        ) > 0.0:
            return True
    return False


def _pick_blocker(world, p, path, pi):
    """挑一个"挡在路线上、打得着"的哨兵。

    只打**路线上**的人：朝视野里随便一个人开枪，等于用一发子弹换
    三个哨兵过来调查 —— 亏。真正的用法是"门被堵住了，打掉门里的人"。
    """
    ahead = {tuple(c) for c in path[pi:pi + 8]}
    best, best_d = None, 1e9
    for e in world.enemies:
        if not e.alive:
            continue
        if (int(e.x), int(e.y)) not in ahead:
            continue
        d = math.hypot(e.x - p.x, e.y - p.y)
        if d > C.WEAPON_RANGE - 1.0 or d > best_d:
            continue
        if not _los_clear(world, (p.x, p.y), (e.x, e.y)):
            continue
        best, best_d = e, d
    return best


def _nearest_shootable(world, p):
    """最近的有视线的活哨兵（近距离反击用）。"""
    best, best_d = None, 1e9
    for e in world.enemies:
        if not e.alive or e.grace > 0.0:
            continue
        d = math.hypot(e.x - p.x, e.y - p.y)
        if d > C.WEAPON_RANGE - 0.5 or d > best_d:
            continue
        if not _los_clear(world, (p.x, p.y), (e.x, e.y)):
            continue
        best, best_d = e, d
    return best


def _wants_pickup(p, kind) -> bool:
    """这个补给对现在的机器人还有用吗（满了就不绕路去拿）。"""
    if kind == "ammo":
        return p.ammo < C.AMMO_CAP
    if kind == "stone":
        return p.stones < C.STONE_CAP
    if kind == "shield":
        return getattr(p, "shield", 0) < C.SHIELD_CAP
    return False


def _free_run(grid, x, y, dx, dy, cap=8):
    """从 (x,y) 沿 (dx,dy) 连续可走几格（不含自身）。"""
    n = 0
    cx, cy = x + dx, y + dy
    while n < cap and not grid.is_wall(cx, cy):
        n += 1
        cx += dx
        cy += dy
    return n


def _hug_wall(world, p, tx, ty):
    """窄廊里贴墙走：把目标点推向"离最近哨兵更远的那一侧"。

    为什么必须有这一步：走廊宽 3~4 格时，哨兵在中线、视锥 110°（半角 55°），
    玩家站在它的**正侧方**（90°）完全在锥外 —— 贴墙就能从它背后平行走过去。
    机器人原来严格走 A* 给的格中心，等于每次都迎面撞上，
    于是"窄段"在数据里永远是必死段。这不是关卡太狠，是尺子不会贴墙。

    只有在"纵向净宽 ≤4 格"时才生效：宽走廊里贴墙没有意义，
    反而会白白多走路。
    """
    # 只在"有总闸的关"启用。这条策略是为"长直廊 + 横跨中线的巡逻"写的，
    # 前三关没有这种结构（它们的哨兵站在门内侧和房间角落）。
    # 一开始我让它对所有关生效，第 1 关立刻从 11/12 掉到 0/3 ——
    # 后来收紧成"同一条廊里正对着的哨兵"，胜率回来了但慢了 9 秒：
    # 因为它仍然会把玩家从 A* 的格中心推开。**尺子的改动范围要最小化**，
    # 前三关的行为一个字节都不该被新关卡的策略影响。
    if not world.power:
        return tx, ty
    gx, gy = int(p.x), int(p.y)
    up = _free_run(world.grid, gx, gy, 0, 1)
    dn = _free_run(world.grid, gx, gy, 0, -1)
    height = up + dn + 1
    if height > 4:
        return tx, ty
    # 只躲"同一条廊里、几乎正对着"的那个人。
    #
    # 第一版只看"最近的哨兵"，结果是**第 1 关从 11/12 掉到 0/3** ——
    # 那张图的哨兵大多站在门内侧和房间角落（不在廊中线上），
    # 贴墙对它们毫无意义，只会把玩家从 A* 的格中心推开、贴到墙根擦碰撞。
    # 贴墙要躲的是"横跨走廊中线巡逻的人"，所以条件必须写成同一个空间语义：
    # 同一水平线（|Δy| ≤ 1.6）+ 前方 6 格内。
    near = None
    best = 1e9
    for e in world.enemies:
        if not e.alive:
            continue
        if abs(e.y - p.y) > 1.6 or abs(e.x - p.x) > 6.0:
            continue
        d = abs(e.x - p.x) + abs(e.y - p.y)
        if d < best:
            best, near = d, e
    if near is None:
        return tx, ty
    # 哨兵在上面 → 贴下墙；反之贴上墙。
    if near.y >= p.y:
        ty = (gy - dn) + 0.4
    else:
        ty = (gy + up) + 0.6
    return tx, ty


def _path_len(grid, a, b):
    n = astar(grid, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])))
    return None if n is None else len(n)


def _plan_detour(w, p, path, pi, danger, max_off=2.0):
    """只捡**顺路**的补给：离接下来 25 步的路径不超过 4 格。

    为什么不按"绕路预算"来挑：算得出预算不代表走得出活路。
    实测按预算绕路会让机器人拐进 G7 然后被抓 —— 纯潜行胜率 3/3 直接掉到 0/3，
    三局死在同一个坐标。而"顺路才捡"既符合补给的设计语义（沿途补给），
    也不会把测量基线搞崩：机器人走的还是它本来就要走的那条路。

    这也顺带说明了一件事：**会绕路的机器人要单独做路径风险评估**，
    否则"贪心插入一个途经点"就足以让整条路线失效。
    """
    if not w.pickups:
        return None
    ahead = path[pi:pi + 25]
    if not ahead:
        return None
    best, best_score = None, 1e9
    for it in w.pickups:
        if it["taken"] or not _wants_pickup(p, it["kind"]):
            continue
        cell = (int(it["x"]), int(it["y"]))
        if danger.get(cell, 0.0) > 0.08:
            continue          # 补给点正被视锥照着：拿资源命没了是亏本买卖
        off = min(math.hypot(it["x"] - (c[0] + 0.5), it["y"] - (c[1] + 0.5))
                  for c in ahead)
        if off > max_off:
            continue
        # 同样顺路时优先拿最缺的：护盾 > 弹药 > 石子
        pri = {"shield": 0.0, "ammo": 0.6, "stone": 1.2}.get(it["kind"], 1.2)
        score = off + pri
        if score < best_score:
            best, best_score = it, score
    return best


def _place_enemy(world, e, frac):
    """把哨兵放到**它自己巡逻回路的 frac 处**（0..1，沿整条回路均分）。

    回路均分而不是只滑第 0 段：第 1 关那种 6 点巡逻，只滑第一段等于
    把四五个哨兵钉在原地，"16 个相位"实际只有几段路有变化。
    """
    if frac <= 1e-9:
        # frac=0 表示"不动"：保留关卡自己按坐标打散的初始相位。
        # 这里若顺手把 _patrol_idx 归零，相位扫描的第 0 个样本就不再是
        # 那条被记录下来的确定性基线，历史数字全部失去可比性。
        return
    n = len(e.patrol)
    pos = frac * n
    i = int(pos) % n
    f = pos - int(pos)
    ax, ay = e.patrol[i]
    bx, by = e.patrol[(i + 1) % n]
    cx, cy = _leg_point(world.grid, ax, ay, bx, by, f)
    if (cx, cy) != (ax, ay):
        e.x, e.y = cx, cy
        e.facing = math.atan2(by - ay, bx - ax)
        e._patrol_idx = (i + 1) % n   # 继续往前走，不要回头
    else:
        e._patrol_idx = i             # 这一段插值会穿墙：老老实实站在点上


def _scatter_phases(world, rng=None, phase=None):
    """把哨兵的巡逻相位挪开（**取样用，不改变关卡本身**）。

    在这之前 `--runs N` 是把同一条确定性轨迹跑 N 遍：16 局的输出字节级相同，
    "胜率 16/16"其实是一个样本被报了 16 次。这和"机器人不会贴墙"那次是同一类
    错误 —— 把尺子的问题读成了关卡结论。

    两种用法，共用同一个落点函数（`_place_enemy`），所以两条轴量的是同一件事：
      phase=k/n   相位扫描：所有哨兵统一挪到回路的 k/n 处，得到 n 个**固定局面**
      rng         随机采样：每个哨兵各自随机，外加随机驻留相位与开局反应时间
    """
    for e in world.enemies:
        if not e.patrol:
            continue
        if phase is not None:
            _place_enemy(world, e, phase % 1.0)
            continue
        _place_enemy(world, e, rng.random())
        # 驻留相位也打散：一半样本"站在点上环视"，一半"走在路上"。
        if rng.random() < 0.5:
            e._dwell = rng.uniform(0.0, C.PATROL_PAUSE)
            e._dwell_face = e.facing
            e._dwell_t = 0.0


def _leg_point(grid, ax, ay, bx, by, f):
    """巡逻段 a→b 上 frac 处；若这一路上会穿墙就退回起点。

    逐点验证而不是只看落点：只看落点的话，落点恰好在墙那边时哨兵会被
    传送到墙的背面 —— 那是尺子在制造局面，不是在采样局面。
    """
    cx, cy = ax + (bx - ax) * f, ay + (by - ay) * f
    for k in range(1, 7):
        u = k / 6
        px, py = ax + (cx - ax) * u, ay + (cy - ay) * u
        if not grid.is_walkable(int(px), int(py)):
            return ax, ay
    return cx, cy


def run(aware=True, immune=False, budget=200.0, weight=7.0, verbose=False,
        flee_mode="goal", game=None, on_frame=None, armed=False, loot=False,
        level=0, rng=None, start_delay=0.0, phase=None):
    """loot=False 时机器人完全无视补给 —— 这是**测量基线**。

    为什么默认关：机器人是量化关卡难度的尺子，尺子本身不能随着被测物一起变。
    补给是给玩家的系统，要用它做对照实验时再显式 --loot 打开。
    """
    """跑一局。

    game / on_frame 是给 recorder 用的：传进来一局已有对局 + 每帧回调，
    录像就能**复用同一份策略代码**，而不是另抄一份 —— 演示与测量走同一条路径，
    才谈得上"这段录像不是摆拍"。
    """
    g = game or Game()
    if game is None and level:
        # 只测单关：直接切到第 level 关重建世界，跨关统计清空。
        # 不这么做的话，机器人赢了第 1 关会触发关卡推进，测量对象就跑偏了。
        g.level_index = level
        g._carry = {}
        g._new_world()
    w, p = g.world, g.player
    # 相位打散必须发生在任何一帧 update 之前：晚一帧，哨兵就已经按原本的
    # 同步相位走过一步，"随机"就成了"确定性 + 一点噪声"，样本还是同一批。
    if rng is not None or phase is not None:
        _scatter_phases(w, rng=rng, phase=phase)

    def goal():
        # 总闸：这关只要有没拉过的闸，就先去拉。
        # 这是机器人对"新机制"的全部理解 —— 它不知道黑灯能撑多久，
        # 只知道"这张图上有个开关，先按了再往前走"。
        # 刻意不给它"什么时候拉最划算"的判断：尺子越聪明，
        # 量出来的就不是关卡难度，而是尺子的水平。
        for node in w.power:
            if node["used"]:
                continue
            if math.hypot(node["x"] - p.x, node["y"] - p.y) < 60.0:
                return (int(node["x"] - 0.5), int(node["y"] - 0.5))
        # 目标点读关卡实例（world.terminals / world.exfil），
        # 不是 constants 的全局常量 —— 后者永远是第 1 关的坐标。
        # 多个数据节点时取**最近的**那个：机器人不做全局排序，它只看眼前
        # 这一步 —— "先拿哪一份"因此是留给玩家的决定，不是被算法替她做掉的。
        if w.state == C.STATE_INFILTRATE:
            left = w.remaining() or list(enumerate(w.terminals))
            _i, (tx, ty) = min(
                left, key=lambda it: (it[1][0] - p.x) ** 2 + (it[1][1] - p.y) ** 2)
            return (int(tx), int(ty))
        return (int(w.exfil[0]), int(w.exfil[1]))

    target = goal()
    danger = _danger_map(w)
    path = (_plan(w, (p.x, p.y), target, danger, weight) if aware
            else astar(g.world.grid, (int(p.x), int(p.y)), target)) or []
    pi = 0   # A* 返回值不含起点，所以第一条就是"下一步"
    mode = "GO"
    mode_t = 0.0
    unseen_t = 0.0
    replan = 0.0
    t = 0.0
    stuck, last = 0, (p.x, p.y)
    legs, legs_done = [], 0   # 每段数据节点的到达时刻（见下面的分段打点）
    detour = None      # 当前正在绕路去拿的补给点（被追时会立刻丢弃）
    detour_t = 0.0

    while t < budget and w.state in (C.STATE_INFILTRATE, C.STATE_EXFILTRATE):
        # 开局反应时间：人不会在读到关卡的第一帧就迈步。这段时间玩家原地不动，
        # 世界照常走 —— 相当于换个时刻进场，也是相位轴的一部分。
        if t < start_delay:
            p._keys = set()
            w.update(1 / 60)
            t += 1 / 60
            continue
        # 走到闸跟前就按：靠近判定和玩家那条路径完全一致（world.nearby_power），
        # 机器人不许有"特殊权限"，否则量出来的通关率不可信。
        if w.power and w.nearby_power() is not None:
            w.activate_power()
        if goal() != target:
            target = goal()
            mode, mode_t, replan = "GO", 0.0, 0.0

        if not aware:
            if pi < len(path):
                tx, ty = path[pi][0] + 0.5, path[pi][1] + 0.5
                if math.hypot(tx - p.x, ty - p.y) < 0.4:
                    pi += 1
                else:
                    p._keys = _keys_toward(p, tx, ty, sneak=True)
            else:
                path = astar(g.world.grid, (int(p.x), int(p.y)), target) or []
                pi = 0
                p._keys = {pygame.K_LSHIFT}
        else:
            worst, watched, chased, nearest, max_susp = _threat(w, p)
            mode_t += 1 / 60
            replan -= 1 / 60

            # ---- 顺路补给：只在"绕一点点就够"时才绕，被追时一律不绕 ----
            if detour is not None and mode != "GO":
                detour = None      # 被追了就别惦记补给，活着到终点优先
                replan = 0.0
            if detour is not None:
                detour_t += 1 / 60
                if (detour["taken"]
                        or math.hypot(detour["x"] - p.x, detour["y"] - p.y)
                        < C.PICKUP_RADIUS
                        or detour_t > 8.0):
                    detour = None
                    replan = 0.0
            elif loot and mode == "GO" and w.pickups:
                detour = _plan_detour(w, p, path, pi, danger)
                if detour is not None:
                    detour_t = 0.0
                    replan = 0.0

            if replan <= 0.0:
                danger = _danger_map(w)
                if mode == "FLEE" and flee_mode == "cover":
                    path = _safe_path(w, danger, p, worst)
                    pi = 0
                else:
                    # 逃命不等于往回跑：目标是"活着到终点"。
                    # 追兵比你慢（走路 4.4 > 追击 3.0），保持目标方向用走的甩开，
                    # 往往比满地图找掩体更快也更稳。
                    if detour is not None:
                        detour_target = (int(detour["x"]), int(detour["y"]))
                        path = _plan(w, (p.x, p.y), detour_target,
                                     danger, weight) or []
                    else:
                        path = _plan(w, (p.x, p.y), target, danger, weight) or []
                    pi = 0
                replan = 0.5 if mode == "FLEE" else 0.4

            # ---- 前瞻：接下来 6 格有多危险 / 我现在被照着没有 ----
            # 这两个量必须分开看：
            # · 只是"门口被照着"→ 站在原地等（站的地方是安全的，等它扫过去就行）
            # · "我自己正被照着"→ 必须退出视线（蹲在原地也是活靶子）
            ahead = max((danger.get(tuple(c), 0.0) for c in path[pi:pi + 6]),
                        default=0.0)
            danger_here = danger.get((int(p.x), int(p.y)), 0.0)
            goal_near = False
            if pi < len(path):
                goal_near = math.hypot(path[pi][0] - target[0],
                                       path[pi][1] - target[1]) < 3.0

            if mode == "GO":
                if watched and (chased or nearest < 5.0):
                    mode, mode_t, replan = "FLEE", 0.0, 0.0
                elif ahead > 0.25 and not goal_near:
                    # 前方被照着：先决定是"等"还是"退"
                    if danger_here > 0.05:
                        mode, mode_t, replan = "FLEE", 0.0, 0.0
                    else:
                        mode, mode_t = "HOLD", 0.0
            elif mode == "HOLD":
                # 停在视线外，等门前的人转身 —— 这就是真人过门前的动作
                if watched and nearest < 5.0:
                    mode, mode_t, replan = "FLEE", 0.0, 0.0
                elif ahead <= 0.20:
                    mode, mode_t, replan = "GO", 0.0, 0.0
                elif armed and mode_t > 1.2 and p.ammo > 0:
                    # 等了 1.2 秒门还是不开：说明门口那个人不会自己走开。
                    # 这才是武器的正确用法 —— 不是"清场"，是"把锁撬开一次"。
                    blocker = _pick_blocker(w, p, path, pi)
                    if blocker is not None and not _body_would_be_seen(w, blocker):
                        # 只有在"没人能看见这个位置"时才开：这一枪换来的才是
                        # 干净的 5 秒空窗。有人看着的话，尸体警报会把这片区域
                        # 变成警戒区，等于用一发子弹换来一整片危险 —— 不划算。
                        w.shoot((blocker.x, blocker.y))
                        mode, mode_t, replan = "GO", 0.0, 0.0
                elif mode_t > 8.0:
                    # 没枪 / 打不着 / 一直有人看着：要么换个门，要么硬着头皮走
                    mode, mode_t, replan = "GO", 0.0, 0.0
            elif mode == "FLEE":
                # 被逼到墙角才开枪：这是最后手段，不是常规解法。
                # 早期版本在 4.5 格就还击，结果是"边跑边制造尸体"：
                # 尸体又被目击 → 越跑人越多 → 带枪玩法 3 局全灭。
                # 枪用来"打通一条路"，不是用来打追兵的 ——
                # 打掉一个追兵，他 5 秒后补位，而你的位置已经暴露了。
                if armed and p.ammo > 0 and nearest < 2.0:
                    close = _nearest_shootable(w, p)
                    if close is not None:
                        w.shoot((close.x, close.y))
                        unseen_t = 0.0
                # 连续 0.8 秒没被看见就算脱身。用"瞬时 watched"当条件会卡死：
                # 追兵在拐角处时断时续地看到你，FLEE 永远退不出去。
                unseen_t = 0.0 if watched else unseen_t + 1 / 60
                # 光"看不见"不够：追兵就贴在 2 格外，站着不动等于等他撞上来。
                # 必须同时拉开距离，才敢停下来蹲着。
                if (unseen_t > 0.8 and nearest > 4.5) or mode_t > 10.0:
                    mode, mode_t, unseen_t = "HIDE", 0.0, 0.0
            elif mode == "HIDE":
                # 躲够了、周围安静了才继续走；有人摸过来就先跑
                if nearest < 2.5:
                    mode, mode_t, replan = "FLEE", 0.0, 0.0
                elif max_susp < 0.12 or mode_t > 6.0:
                    mode, mode_t, replan = "GO", 0.0, 0.0

            if mode in ("HIDE", "HOLD"):
                p._keys = {pygame.K_LSHIFT}
            elif pi < len(path):
                tx, ty = path[pi][0] + 0.5, path[pi][1] + 0.5
                if math.hypot(tx - p.x, ty - p.y) < 0.4:
                    pi += 1
                else:
                    tx, ty = _hug_wall(w, p, tx, ty)
                    p._keys = _keys_toward(
                        p, tx, ty,
                        sneak=(mode == "GO" and danger_here > 0.25),
                        sprint=(mode == "FLEE" and p.stamina > 35),
                    )
            else:
                p._keys = {pygame.K_LSHIFT}

        w.update(1 / 60)
        t += 1 / 60
        # 分段打点：每拿到一个数据节点记一次（时刻 / 被发现次数）。
        # 为什么必须记：第 3 关有两个节点，而机器人的死法经常集中在**出口那道
        # 门**上 —— 只看"胜/负"的话，前半段改得再狠都看不出来，
        # 因为胜负早被出口那一下决定了。分段数据才看得见"哪一段在压人"。
        while legs_done < len(w.taken):
            legs.append({"idx": w.taken[legs_done], "t": round(w.elapsed, 1),
                         "spotted": w.spotted})
            legs_done += 1
        if on_frame is not None:
            on_frame(t, g, mode)

        if immune and w.state == C.STATE_DEFEAT:
            w.captured = False
            w.state = C.STATE_EXFILTRATE if w.has_data else C.STATE_INFILTRATE

        if math.hypot(p.x - last[0], p.y - last[1]) < 0.004:
            stuck += 1
            if stuck > 50:
                replan, stuck = 0.0, 0
        else:
            stuck = 0
        last = (p.x, p.y)

        if verbose and int(t * 60) % 180 == 0:
            print(f"  t={t:5.1f} [{mode:4s}] {w.state:10s} ({p.x:.1f},{p.y:.1f}) "
                  f"threat={w.threat:.2f} seen={w.seeing} spotted={w.spotted}")

    return {
        "state": w.state,
        "time": round(w.elapsed, 1),
        "spotted": w.spotted,
        "kills": w.kills,
        "bodies": w.bodies_found,
        "looted": sum(1 for it in w.pickups if it["taken"]),
        "shield_left": getattr(p, "shield", 0),
        "score": w.score(),
        "rank": w.rank() if w.state == C.STATE_VICTORY else "-",
        "style": w.style_bonus(),
        "peak": round(w.peak_threat, 2),
        "decoy": w.decoy_hits,
        # 基础分单列出来，是为了回答"这一局赢在效率还是赢在方式"。
        # 只给总分的话，两种完全不同的玩法可能拿到同一个数字，
        # 调权重时根本不知道是哪一半在起作用。
        "base": int(round(w.base_score())),
        "pos": (round(p.x), round(p.y)),
        # 分段：第几个节点、什么时刻到的、到的时候已经被看见几次
        "legs": legs,
        "nodes": len(w.taken),
        "nodes_total": len(w.terminals),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumb", action="store_true")
    ap.add_argument("--aware", action="store_true")
    ap.add_argument("--immune", action="store_true")
    ap.add_argument("--armed", action="store_true", help="允许机器人开枪")
    ap.add_argument("--loot", action="store_true",
                    help="让机器人顺路捡补给（默认关闭：基线测量不受补给影响）")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20261102,
                    help="取样基种子；第 i 局用 seed+i，失败可复现")
    ap.add_argument("--jitter", type=float, default=0.0,
                    help="随机幅度（秒）：打散哨兵巡逻相位 + 随机开局反应时间。"
                         ">0 时 --runs 才是真采样；0 时是那条固定的确定性基线")
    ap.add_argument("--level", type=int, default=0,
                    help="关卡序号（0=哨站 1=环形枢纽），默认第 1 关")
    ap.add_argument("--weight", type=float, default=7.0)
    ap.add_argument("--flee-mode", default="goal", choices=["goal", "cover"])
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    results = []
    for i in range(a.runs):
        rng = random.Random(a.seed + i) if a.jitter > 0 else None
        # 反应时间也进样本：0 秒是"读图就冲"，jitter 秒是"先观察一会儿"。
        delay = rng.uniform(0.0, a.jitter) if rng else 0.0
        r = run(aware=not a.dumb, immune=a.immune, weight=a.weight, verbose=a.verbose,
                flee_mode=a.flee_mode, armed=a.armed, loot=a.loot, level=a.level,
                rng=rng, start_delay=delay)
        r["seed"] = a.seed + i
        results.append(r)
        print(r)
    if a.runs > 1:
        wins = sum(1 for r in results if r["state"] == C.STATE_VICTORY)
        print(f"胜率 {wins}/{len(results)}  "
              f"平均用时 {sum(r['time'] for r in results) / len(results):.1f}s  "
              f"平均被发现 {sum(r['spotted'] for r in results) / len(results):.1f} 次  "
              f"平均击倒 {sum(r['kills'] for r in results) / len(results):.1f} 人  "
              f"平均得分 {sum(r['score'] for r in results) / len(results):.0f}")
        # 同一条轨迹重复 N 次不是胜率。让它自己喊出来，别再靠人眼发现。
        distinct = len({(r["state"], r["time"], r["pos"], r["spotted"]) for r in results})
        if distinct == 1:
            print(f"⚠ {len(results)} 局是同一条轨迹（--jitter 0）："
                  f"这不是胜率，是 1 个样本报了 {len(results)} 次；"
                  f"要真采样请加 --jitter 1")
        else:
            print(f"不同结局 {distinct}/{len(results)} 种（seed {a.seed}..{a.seed + len(results) - 1}）")

