"""世界：持有地图、玩家、敌人列表，驱动每帧更新。"""

import math
from dataclasses import dataclass, field
from typing import Dict, List

from .. import constants as C
from ..ai.coordination import Role
from ..entity.stone import Stone, plan_landing
from .grid import GridMap, WALL


def _flood_floor(m: GridMap, sx: int, sy: int) -> set:
    """从 (sx, sy) 出发在可走格上做 BFS，返回可达格集合。"""
    seen = {(sx, sy)}
    stack = [(sx, sy)]
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in seen or m.is_wall(nx, ny):
                continue
            seen.add((nx, ny))
            stack.append((nx, ny))
    return seen


def _carve(m: GridMap, x0: int, y0: int, x1: int, y1: int) -> None:
    """把一块矩形从岩体里挖空（闭区间）。"""
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            m.set_floor(x, y)


def _add_cover(m: GridMap, blocks, spawn, protected=()) -> list:
    """往已经挖好的房间里放柱子 / 货箱，边放边做可达性校验。

    为什么必须校验：一块 2x3 的货箱如果正好卡在小房间的转折处，
    整个房间会被切成两半 —— 玩家过不去，敌人的 A* 也会失效，
    而这种"看起来没事"的关卡 bug 只能靠 flood fill 兜住。
    """
    placed = []
    prot = set(protected)
    for (bx, by, bw, bh) in blocks:
        cells = [(bx + dx, by + dy) for dx in range(bw) for dy in range(bh)]
        if any(m.is_wall(cx, cy) for cx, cy in cells):
            continue   # 已经有墙/柱子了，叠上去没意义
        if any((cx, cy) in prot for cx, cy in cells):
            continue   # 门口、目标点周围必须保持畅通
        for cx, cy in cells:
            m.set_wall(cx, cy)
        reach = _flood_floor(m, int(spawn[0]), int(spawn[1]))
        total = sum(1 for y in range(m.h) for x in range(m.w) if not m.is_wall(x, y))
        if len(reach) < total:
            for cx, cy in cells:
                m.set_floor(cx, cy)
            continue
        placed.append((bx, by, bw, bh))
    return placed


# 关卡的门（每道 2 格宽）。抽出来当模块常量，是因为它有三个消费者：
# 地图构建器用它挖洞、审计脚本用它算拓扑、难度体检用它统计"门窗口"。
# 一份数据三处用，才不会出现"审计说 20 道门、地图其实有 22 道"这种对不上号。
#
# 每条 = (x0, y0, x1, y1, 名字, 所属弧)
# 所属弧用来定价取舍：北弧近而密、南弧远而疏、X1 是捷径。
DOORS = (
    (11, 5, 11, 6, "S 东门", "北弧"),
    (21, 5, 21, 6, "G1 西门", "北弧"),
    (34, 5, 34, 6, "G1 东门", "北弧"),
    (41, 5, 41, 6, "G2 西门", "北弧"),
    (46, 17, 47, 17, "G2 南门", "北弧"),
    (46, 26, 47, 26, "G3 北门", "北弧"),
    (53, 30, 53, 31, "G3 东门", "北弧"),
    (63, 30, 63, 31, "G4 西门", "北弧"),
    (68, 36, 69, 36, "G4 南门", "北弧"),
    (68, 46, 69, 46, "T 北门", "北弧"),
    (5, 11, 6, 11, "S 南门", "南弧"),
    (5, 31, 6, 31, "G5 北门", "南弧"),
    (18, 38, 18, 39, "G5 东门", "南弧"),
    (31, 38, 31, 39, "G6 西门", "南弧"),
    (46, 40, 46, 41, "G6 东门", "南弧"),
    (56, 40, 56, 41, "G7 西门", "南弧"),
    (61, 49, 62, 49, "G7 南门", "南弧"),
    (67, 50, 67, 51, "T 西门", "南弧"),
    (24, 14, 25, 14, "X1 上口", "捷径"),
    (24, 37, 25, 37, "X1 下口", "捷径"),
)


def build_demo_map() -> GridMap:
    """生成 80x60 的"哨站"关卡：实心岩体里挖出来的 θ 形设施。

    ⚠️ 这是一次**结构反转**的改动，值得说清楚为什么：

    旧版是"一大块空地 + 往里摆墙/货箱"。问题在于玩家永远可以在空地里贴边走，
    视锥再密也只是"绕远一点"，路线选择实际上是几十条，难度全靠堆敌人。
    这次改成先填满岩体，再**挖**出房间和走廊 —— 路线数量由挖洞决定：
    两个舱室之间只留三条通路（上弧 / 下弧 / 中间一条竖井捷径），
    每条通路上都是 2 格宽的门。想通过，只能穿门；穿门，就只能等视锥扫过去。

    于是"极少路径选择"变成一个可以量化的东西（见 scripts/audit_routes.py）：
    互不重叠的路线数 = 4，咽喉门 = 20 道。
    """
    W, H = 80, 60
    m = GridMap.filled(W, H)

    # ---- 1. 挖房间（闭区间，x0,y0,x1,y1）----
    # S=出生舱  G1..G7=七个哨站房间  T=终端舱
    for (x0, y0, x1, y1) in (
        (3, 3, 10, 10),      # S  出生舱（撤离点也在这里，形成折返闭环）
        (22, 2, 33, 13),     # G1 北哨塔
        (42, 2, 57, 16),     # G2 机库
        (39, 27, 52, 35),    # G3 中继站
        (64, 22, 75, 35),    # G4 东厅
        (3, 32, 17, 44),     # G5 西仓库
        (32, 36, 45, 48),    # G6 南机房
        (57, 36, 66, 48),    # G7 泵站
        (68, 47, 77, 56),    # T  终端舱
    ):
        _carve(m, x0, y0, x1, y1)

    # ---- 2. 挖走廊（3 格宽：够玩家侧身贴墙，不够绕过哨兵）----
    for (x0, y0, x1, y1) in (
        (12, 5, 20, 7),      # A1 出生舱 → G1
        (35, 5, 40, 7),      # A2 G1 → G2
        (46, 18, 50, 25),    # A3 机库竖向井（G2 → G3）
        (54, 30, 62, 32),    # A4 G3 → G4
        (68, 37, 70, 45),    # A5 东厅 → 终端舱
        (5, 12, 6, 30),      # B1 出生舱 → 西仓库（2 格宽长竖廊）
        (19, 38, 30, 40),    # B2 西仓库 → 南机房
        (47, 40, 55, 42),    # B3 南机房 → 泵站
        (61, 50, 66, 52),    # B4 泵站 → 终端舱
        (24, 15, 25, 36),    # X1 竖井：G1 直通 B2 的高风险捷径
    ):
        _carve(m, x0, y0, x1, y1)

    # ---- 3. 打门（每条通路都收窄到 2 格）----
    # 这一步是"路径很少但依然有解"的关键：门宽 2 格 = 一次只够一个人过，
    # 而哨兵的身位 + 视野锥在门内侧横扫时，玩家必须等它走开。
    for (x0, y0, x1, y1, _name, _arc) in DOORS:
        _carve(m, x0, y0, x1, y1)

    # ---- 4. 房间里的柱子 / 货箱：制造视线死角，但不新增通路 ----
    # 门和目标点周围 2 格内保持空，否则会出现"进门就被柱子卡住"的手感事故。
    protected = set()
    for (x0, y0, x1, y1, _name, _arc) in DOORS:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                protected.add((x0 + dx, y0 + dy))
                protected.add((x1 + dx, y1 + dy))
    for cx, cy in (C.DATA_TERMINAL, C.EXFIL_POINT):
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                protected.add((int(cx) + dx, int(cy) + dy))

    cover_blocks = [
        # G1 北哨塔
        (26, 6, 2, 2), (29, 9, 2, 2), (25, 11, 2, 2),
        # G2 机库（大开间：柱子更密，否则 8 格视野一眼看穿整个房间）
        (46, 5, 2, 2), (52, 6, 2, 2), (45, 10, 3, 2), (51, 12, 2, 2),
        (48, 13, 2, 2),
        # G3 中继站
        (43, 29, 2, 2), (48, 32, 2, 2),
        # G4 东厅
        (68, 25, 2, 2), (72, 29, 2, 2), (66, 32, 2, 2),
        # G5 西仓库
        (6, 35, 2, 2), (12, 38, 3, 2), (7, 41, 2, 2),
        # G6 南机房
        (35, 38, 2, 2), (40, 41, 2, 3), (33, 45, 2, 2),
        # G7 泵站
        (59, 39, 2, 2), (63, 42, 2, 2), (58, 45, 2, 2),
        # T 终端舱（别挡住终端，终端在 74.5,54.5）
        (70, 49, 2, 2),
    ]
    _add_cover(m, cover_blocks, (5.5, 5.5), protected)

    # ---- 5. 连通性自检：任何一块该到的区域到不了，立刻报错 ----
    # 关卡是手写坐标，写错一个数字就会挖出一条死路 —— 让它在构建期就炸掉，
    # 而不是等玩家走到墙前面才发现。
    #
    # 这里用"可达格数 == 总可走格数"做全图连通校验，而不是只抽查几个坐标：
    # 抽查点很容易刚好落在柱子上（我第一次写就撞了），校验本身反而变成噪音。
    reach = _flood_floor(m, 5, 5)
    floors = [(x, y) for y in range(H) for x in range(W) if not m.is_wall(x, y)]
    if len(reach) != len(floors):
        orphans = sorted(set(floors) - reach)[:6]
        raise RuntimeError(
            f"关卡不连通：{len(floors) - len(reach)} 个可走格从出生点不可达，"
            f"例如 {orphans}"
        )
    return m


def _snap_to_floor(grid, x: float, y: float) -> tuple:
    """把坐标吸附到最近的空格中心（原地是墙时按半径扩散找）。"""
    cx, cy = int(x), int(y)
    if not grid.is_wall(cx, cy):
        return (x, y)
    for r in range(1, 8):
        best = None
        best_d = 1e9
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                nx, ny = cx + dx, cy + dy
                if grid.is_wall(nx, ny):
                    continue
                d = dx * dx + dy * dy
                if d < best_d:
                    best_d, best = d, (nx, ny)
        if best is not None:
            return (best[0] + 0.5, best[1] + 0.5)
    return (x, y)


def build_spawn_and_patrols(grid=None):
    """返回 (玩家出生点, 敌人生成配置列表)。

    每个敌人配置 = (x, y, 巡逻点列表)。

    布置原则（比"每块区域放几个"重要得多）：
    1. **每个门后面都有人在看**。θ 形地图只有 20 个咽喉门，
       21 个哨兵刚好做到"门均一个 + 大房间两个"，密度用覆盖的门数衡量。
    2. **出生舱 S 里一个都不放**。玩家一睁眼就在别人视锥里，
       那不叫难，那叫劝退。
    3. 巡逻点沿走廊轴向摆，别横穿房间对角 —— 横穿会让哨兵长时间背对门口，
       玩家可以无脑跟在它后面走，等于没有关卡。
    """
    player_spawn = (5.5, 5.5)

    enemy_cfg = [
        # ---- 上弧 A（近路，守卫最密：走这条路就是硬碰硬）----
        # 硬规则：**任何巡逻点离任何门至少 3 格**。
        # 这条规则是用血换来的：最初我把"每个门后都有人看"写成了
        # "有人贴着门来回走"，结果 2 格宽的门被永久封死 ——
        # 机器人 8 秒撞死在 S 东门，改完巡逻点才通。
        # 门后的人该做的是"扫过去"，不是"站在门里"。
        (15, 6, [(15, 6), (18, 6)]),                       # A1 走廊中段
        (27, 4, [(27, 4), (31, 7), (27, 11)]),             # G1 内圈
        (24, 8, [(24, 8), (27, 8)]),                       # G1 西侧
        (31, 8, [(31, 8), (31, 12)]),                      # G1 东侧（守 X1 上口的外围）
        (38, 6, [(38, 5), (38, 7)]),                       # A2 哨位：横着扫，背对两端门
        (45, 4, [(45, 4), (54, 4), (54, 14), (46, 13)]),   # G2 内圈
        (50, 8, [(50, 8), (50, 12)]),                      # G2 中部
        (47, 21, [(47, 21), (49, 21)]),                    # A3 竖井：横着扫
        (41, 29, [(41, 29), (50, 31)]),                    # G3 对角线
        (57, 31, [(57, 31), (60, 31)]),                    # A4 走廊中段
        (67, 27, [(67, 27), (74, 27), (74, 33), (69, 33)]),  # G4 内圈
        (70, 30, [(70, 27), (70, 33)]),                    # G4 中部
        (69, 40, [(69, 40), (69, 43)]),                    # A5 咽喉中段
        (72, 50, [(72, 50), (76, 50)]),                    # T 舱：在终端前方横巡
        # ---- 下弧 B（远路，守卫略少：绕远换一条安静的路）----
        # B1（19 格长、2 格宽）和 X1（22 格长、2 格宽）里**一个都不放**：
        # 2 格宽没有绕身空间，走廊里站人 = 玩家必死。长窄廊的正确用法是
        # "承诺跑"：廊里没人，但两端房间的哨兵能看进来，
        # 玩家得先等门口的人转身，再一口气冲过去。
        (5, 36, [(5, 36), (15, 36), (15, 43), (5, 43)]),   # G5 内圈
        (10, 41, [(10, 40), (10, 43)]),                    # G5 中部
        (37, 38, [(37, 38), (43, 38), (43, 46), (37, 46)]),  # G6 内圈
        (37, 40, [(37, 40), (44, 44)]),                    # G6 斜巡
        (51, 41, [(51, 41), (53, 41)]),                    # B3 走廊中段
        (59, 38, [(59, 38), (65, 38), (65, 46), (60, 46)]),  # G7 内圈
        (62, 43, [(62, 42), (62, 45)]),                    # G7 中部（守南门的外围）
    ]

    # 坐标统一转成"格中心"（x+0.5）：配置里写的是整数格点，
    # 而整数坐标正好落在 4 个格子的公共**角点**上。圆形碰撞用
    # "圆心到墙格最近点距离"，角点接触距离恰好为 0，会被判定为一直在
    # 墙里 —— 结果就是所有敌人开局被钉死在原地。
    cfg_centered = [
        (x + 0.5, y + 0.5, [(px + 0.5, py + 0.5) for px, py in pts])
        for (x, y, pts) in enemy_cfg
    ]

    # 柱子/货箱可能正好盖住手工写的巡逻点。这里做一次吸附：
    # 落在墙里的点挪到最近的空格，避免"哨兵卡在墙里不动"这种查半天
    # 查不出来的关卡 bug（也是我踩过的坑）。
    if grid is not None:
        cfg_centered = [
            (*_snap_to_floor(grid, x, y),
             [_snap_to_floor(grid, px, py) for px, py in pts])
            for (x, y, pts) in cfg_centered
        ]
    return player_spawn, cfg_centered


# 沿途补给点（整数格坐标，构建时统一转成格中心）。
# 和 DOORS 一样抽成模块常量：地图构建器、审计脚本、测试三处共用一份数据，
# 不会出现"文档说 14 个点、图上实际只有 12 个"这种对不上号。
#
# 摆放原则（这是设计的重点，不是随便撒）：
# 1. **三条弧都有，且都不在主路正中间** —— 走哪条路决定你能拿到什么，
#    "选路"才真的有代价（W11 测试守着这条：三条弧都得出货）。
# 2. **离任何巡逻点至少 3 格**。这条是用一次实测换来的：
#    第一版我把"风险—收益"理解成"把补给摆在哨兵来回走的线上，逼玩家卡时机"，
#    结果机器人绕过去就撞进怀里，纯潜行胜率 3/3 → 0/3，三局死在同一个坐标。
#    3 格是"看得见、拿得到、但不用贴着人站"的距离。
# 3. **离门至少 2.5 格**，且要么四周开阔（可走邻域 ≥6 有退路），
#    要么整片区域根本没有巡逻（长窄廊的"承诺跑"点位）。
#    补给不该逼玩家走进死角 —— 那是送死，不是难度。
# 4. **护盾只给三处**（A3 竖井南端 / G6 南角 / X1 捷径中段）。
#    护盾能挡一次必死，是最强的资源，给多了潜行会退化成硬闯。
PICKUPS = (
    (23, 4, "ammo"),     # G1 西北角：出第一道门后的第一笔账
    (24, 11, "stone"),   # G1 西南角：要绕开主路才拿得到
    (44, 7, "ammo"),     # G2 机库西侧
    (48, 24, "shield"),  # A3 竖井南端：北弧最窄的咽喉
    (43, 33, "stone"),   # G3 南沿
    (71, 30, "stone"),   # G4 中部
    (65, 43, "ammo"),    # G7 南角
    (75, 55, "ammo"),    # T 舱内：拿到数据、准备折返时的补给
    (6, 26, "stone"),    # B1 长竖廊中段（承诺跑的路上，全程无人）
    (14, 40, "ammo"),    # G5 仓库东侧
    (40, 45, "shield"),  # G6 南机房南角
    (63, 51, "stone"),   # B4 走廊：进终端舱前的最后一段
    (34, 38, "ammo"),    # G6 北部
    (24, 26, "shield"),  # X1 捷径中段：走最凶的路，给最好的奖励
)


def build_pickups(grid=None):
    """返回补给点列表：每项 = {"x", "y", "kind", "taken"}。

    坐标同样 +0.5 转成格中心（整数坐标落在四格公共角点上，
    会被圆形碰撞判成"一直在墙里"），并且落在柱子/货箱里的点会被吸附出来，
    避免出现"看得见却永远捡不到"的补给。
    """
    out = []
    for (x, y, kind) in PICKUPS:
        px, py = x + 0.5, y + 0.5
        if grid is not None:
            px, py = _snap_to_floor(grid, px, py)
        out.append({"x": px, "y": py, "kind": kind, "taken": False})
    return out


@dataclass
class World:
    grid: GridMap
    player: object = None
    enemies: List[object] = field(default_factory=list)
    coordinator: object = None  # 协同分配器
    flow_field: object = None  # 当前共享的流场（追击玩家时构建）
    _flow_repel: float = 0.0  # 流场重建节流

    stones: List[object] = field(default_factory=list)  # 飞行中的石子
    pings: List[dict] = field(default_factory=list)     # 噪音可视化（落点声波）
    shots: List[dict] = field(default_factory=list)     # 弹道曳光（短命特效）
    hit_marks: List[dict] = field(default_factory=list)  # 命中记号（打中才有的瞬时反馈）
    pickups: List[dict] = field(default_factory=list)   # 沿途补给（弹药 / 石子 / 护盾）

    # ---- 游戏状态 ----
    state: str = C.STATE_INFILTRATE
    alert: bool = False        # 警觉状态（拿数据后为 True，敌人更强）
    alert_left: float = 0.0    # 警觉封锁剩余时间（秒）
    last_known: tuple = None   # 全队最后确知玩家位置（流场目标，不用真实坐标）
    last_known_age: float = 0.0  # 距上次确知过了多久（秒）
    has_data: bool = False     # 是否已取得数据
    elapsed: float = 0.0       # 本局用时（秒）
    captured: bool = False     # 是否被抓住
    # 抓住你的那个哨兵（结算页用它画“谁抓的 + 从哪来”）：
    # 没有它，失败画面只是一句“任务失败”，玩家学不到任何东西。
    captured_by: object = None
    _alert_timer: float = 0.0  # 警觉触发后的短暂提示计时

    # ---- 反馈 / 统计 ----
    threat: float = 0.0        # 全场最高可疑度（HUD 暴露条）
    seeing: int = 0            # 当前正看着玩家的敌人数
    engaged: int = 0           # 已参与追击/包抄/封路的敌人数
    spotted: int = 0           # 本局被发现次数（每次"进入追击"计一次）
    events: List[str] = field(default_factory=list)  # 本帧事件（给音效用）
    kills: int = 0             # 击倒次数（含重复击倒同一人）
    bodies_found: int = 0      # 尸体被目击的次数（直接影响"开枪划不划算"）

    # ---- 风格统计（只影响风格分，不影响玩法判定）----
    # 为什么这些要单独计数而不是从别的字段推：
    # "扔了 3 次石子"和"真的引开了 3 个人"是两件事 —— 往空地扔石子没人听见，
    # 前者能刷分、后者不能。所以必须记 _emit_noise 真正惊动的人次。
    decoy_hits: int = 0        # 被石子真的引去调查的人次
    shield_kills: int = 0      # 靠护盾反杀的次数
    peak_threat: float = 0.0   # 本局暴露度峰值（判"全身而退"）
    spots_by_enemy: Dict[str, int] = field(default_factory=dict)  # 每个哨兵的发现次数

    _body_scan_cd: float = 0.0  # 尸体扫描节流
    _chasing_flag: bool = False
    _alarm_cd: float = 0.0     # 报警广播节流
    _engaged_sig: tuple = ()   # 参与者集合签名（变化时强制重算角色）

    def update(self, dt: float) -> None:
        # 结算状态：冻结世界更新，等待玩家操作
        if self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return

        self.elapsed += dt
        self._alarm_cd = max(0.0, self._alarm_cd - dt)
        self._update_alert(dt)
        self.events.clear()  # 每帧清空，主循环消费后即丢弃

        if self.player is not None:
            self.player.update(dt, self)
            self._update_stones(dt)
            self._update_pickups()
            self._update_pings(dt)
        self._update_shots(dt)
        self._update_hit_marks(dt)

        # 玩法判定：接触数据终端 → 进入逃脱；接触撤离点 → 胜利
        if self.player is not None:
            self._check_objectives()
        # 警觉提示计时递减
        if self._alert_timer > 0:
            self._alert_timer -= dt

        # 协同角色分配：只给"已参与"的敌人分配（不是全图 18 个一起追）
        self._assign_roles()
        # 目击者向附近队友报警（局部广播，而非全图开图）
        self._maybe_broadcast()
        # 尸体目击：谁看见同僚倒下，谁就去拉警报
        self._check_bodies(dt)

        # 更新流场：有敌人在追击时，每 0.5s 重建一次（玩家 4格/s，0.5s 仅动 2 格，
        # 方向场无需更频繁重建，这是性能关键）
        #
        # 关键修正：流场的目标必须是「全队最后已知位置」，而不是玩家的真实坐标。
        # 之前直接拿 player.pos 建场，等于给所有追兵开了透视 ——
        # 只要你被看见过一次，即使已经绕到墙后，追兵依然精确知道你在哪，
        # 追击永远甩不掉（实测机器人被一路追着跑 60 秒不脱身）。
        self._flow_repel -= dt
        if self._flow_repel <= 0:
            self._flow_repel = 0.5
            if self.player is not None and self.engaged > 0 and self.last_known is not None:
                from ..pathing.flowfield import FlowField
                self.flow_field = FlowField(
                    self.grid, (int(self.last_known[0]), int(self.last_known[1]))
                )
            else:
                self.flow_field = None

        for e in self.enemies:
            e.update(dt, self)

        # 「最后已知位置」必须在敌人刷新完感知之后再更新：
        # 放在敌人更新之前，读到的是上一帧的 sees_player，
        # 玩家瞬移/绕墙后仍会被记成"刚被看见"，等于延迟一帧的透视。
        #
        # 注意：这里曾经留着第二遍 `e.update(dt, self)`，等于敌人每帧跑两步 ——
        # 哨兵实际移速是配置的 2 倍（2.2 格/s 变成 4.4，比玩家走路还快），
        # 可疑度也是双倍速上涨。肉眼看就是"哨兵像开了加速外挂"，
        # 而这种 bug 不会让任何测试变红，只会让手感和数值表对不上。
        if self.player is not None:
            if any(e.sees_player for e in self.enemies):
                self.last_known = (self.player.x, self.player.y)
                self.last_known_age = 0.0
            else:
                self.last_known_age += dt

        # 捕获判定必须放在敌人刷新完感知**之后**：
        # 它要读"这个哨兵知不知道你在附近"（sees_player / suspicion），
        # 放在前面读到的是上一帧的感知 —— 玩家贴到哨兵身上时，第一帧永远抓不到。
        if self.player is not None and not self.captured:
            self._check_capture()

        self._aggregate_threat()

    # ---- 协同 ----
    def _assign_roles(self) -> None:
        """给"已经参与"的敌人分配协同角色。

        "参与" = 已确认发现（suspicion >= 阈值）或还挂着上一轮角色。
        没参与的哨兵继续保持巡逻 —— 这是关键：一个敌人看到你，
        不应该让全图 18 个敌人同时精确知道你在哪。
        """
        if self.player is None:
            self.engaged = 0
            return

        engaged = [
            e for e in self.enemies
            if e.alive and (e.role != Role.PATROL
                            or e.suspicion >= C.SUSPICION_CONFIRM)
        ]
        self.engaged = len(engaged)
        if not engaged:
            self._engaged_sig = ()
            return

        px, py = int(self.player.x), int(self.player.y)
        if self.coordinator is None:
            for e in engaged:
                e.role = Role.CHASE
                e.target_tile = (px, py)
            return

        # 参与人数变了就立刻重算，否则「第 19 个敌人」要等 0.3s 节流才拿到角色
        sig = tuple(sorted(id(e) for e in engaged))
        if sig != self._engaged_sig:
            self._engaged_sig = sig
            self.coordinator.invalidate()

        self.coordinator.assign(engaged, self.player.pos, self.grid)
        for e in engaged:
            # 缓存里没有这个敌人（刚参与、还没轮到重算）就先保持原角色
            item = self.coordinator.assignments.get(id(e))
            if item is not None:
                e.role, e.target_tile = item

    def _maybe_broadcast(self) -> None:
        """目击者向 RADIO_RANGE 内的队友喊一声：附近哨兵会"过去看看"。

        注意只把可疑度抬到"起疑"级别（<确认阈值），
        所以队友是**调查**而不是瞬间精确追击 —— 拟真且难以被玩家察觉规律。
        """
        if self._alarm_cd > 0.0 or self.player is None:
            return
        witnesses = [
            e for e in self.enemies
            if e.alive and e.sees_player and e.suspicion >= C.SUSPICION_CONFIRM
        ]
        if not witnesses:
            return
        self._alarm_cd = 1.5
        fired = False
        for e in self.enemies:
            if not e.alive:
                continue
            if e.suspicion >= C.SUSPICION_CURIOUS:
                continue
            for w in witnesses:
                if (e.x - w.x) ** 2 + (e.y - w.y) ** 2 <= C.RADIO_RANGE ** 2:
                    e.suspicion = C.SUSPICION_CURIOUS + 0.05
                    e._last_seen = (self.player.x, self.player.y)
                    fired = True
                    break
        if fired:
            self.events.append("radio")

    # ---- 尸体目击：让"开枪"的代价可以被延迟结算 ----
    def _check_bodies(self, dt: float) -> None:
        """看见同僚的尸体 → 拉警报。

        这是开枪的第二种代价。枪声只说明"有人开了枪"（还会被定位误差糊掉），
        而尸体是**物证**：谁看见谁就明白"自己人被干掉了"，这块区域立刻被搜一遍。

        时机上故意卡得很紧 —— 尸体只存在 ENEMY_RESPAWN（5 秒），
        所以这条规则只在"当着别人的面开枪"时才触发。
        躲进空房间开枪依旧安全：这不是随机惩罚，是给玩家的判断题。
        """
        self._body_scan_cd -= dt
        if self._body_scan_cd > 0.0:
            return
        self._body_scan_cd = C.BODY_SCAN_INTERVAL
        if self.player is None:
            return

        corpses = [e for e in self.enemies if not e.alive and not e.body_found]
        if not corpses:
            return

        for corpse in corpses:
            finder = None
            for e in self.enemies:
                if not e.alive or e.grace > 0.0 or e.perception is None:
                    continue
                if math.hypot(e.x - corpse.x, e.y - corpse.y) > C.BODY_SPOT_DIST:
                    continue
                if e.perception.can_see(
                    e.pos, e.facing, (corpse.x, corpse.y), self.grid,
                    view_range=e.view_range(),
                ) > 0.0:
                    finder = e
                    break
            if finder is None:
                continue

            corpse.body_found = True
            self.bodies_found += 1
            self.events.append("body_found")
            # 红色扩散环：玩家必须能看见"我这一枪被记账了"，否则只会觉得莫名其妙被围
            self.pings.append({
                "x": corpse.x, "y": corpse.y, "r": C.BODY_SPOT_DIST,
                "age": 0.0, "life": C.BODY_PING_LIFE, "kind": "body",
            })
            self._alert_body(finder, corpse)

    def _alert_body(self, finder, corpse) -> None:
        """目击者拉警报：他和附近的队友都进入一段"警戒期"。

        刻意**不让他们跑向尸体**（这是踩过坑之后改的）：
        玩家杀人的位置几乎总是一道门，让哨兵走过去等于自动堵死唯一的路，
        带枪玩法直接变成必输。改成"原地警戒 + 可疑度抬到起疑"之后，
        惩罚从"堵你的路"变成"这片区域短时间内很敏感" ——
        玩家能读出来（暴露条变红、头顶 ? 亮起），也能绕开，但不能再赖在这里。

        同样刻意**不给确认级可疑度**：发现尸体不等于发现入侵者。
        """
        rr = C.BODY_RADIO_RANGE ** 2
        for e in self.enemies:
            if not e.alive:
                continue
            if e is not finder:
                near_finder = (e.x - finder.x) ** 2 + (e.y - finder.y) ** 2 <= rr
                near_body = (e.x - corpse.x) ** 2 + (e.y - corpse.y) ** 2 <= rr
                if not (near_finder or near_body):
                    continue
            # 已经在追真人的，不该被一具尸体打断
            if e.suspicion >= C.SUSPICION_CONFIRM:
                continue
            if e is finder:
                e.suspicion = max(e.suspicion, C.BODY_SPOT_SUSPICION)
                e.body_alert = C.BODY_ALERT_TIME
            else:
                e.suspicion = max(e.suspicion, C.SUSPICION_CURIOUS + 0.04)
                e.body_alert = max(e.body_alert, C.BODY_ALLY_ALERT)
            # 警戒时会给一个短促的"转身张望"：不是走来走去，就是原地转头
            e._look_timer = 0.0
            e._scan_dir = 1.0 if e.x >= corpse.x else -1.0

    def _update_hit_marks(self, dt: float) -> None:
        for h in self.hit_marks:
            h["age"] += dt
        self.hit_marks = [h for h in self.hit_marks if h["age"] < h["life"]]

    # ---- 沿途补给 ----
    def _update_pickups(self) -> None:
        """玩家走到补给点上就自动拾取（走过去就够，不要求按键）。

        不给"按住 E 拾取"这类操作：潜行游戏里玩家的注意力全在视锥上，
        多一个按键只会让它变成"忘了按所以没拿到"。
        """
        p = self.player
        r2 = C.PICKUP_RADIUS ** 2
        for it in self.pickups:
            if it["taken"]:
                continue
            if (it["x"] - p.x) ** 2 + (it["y"] - p.y) ** 2 > r2:
                continue
            kind = it["kind"]
            if kind == "ammo":
                if p.ammo >= C.AMMO_CAP:
                    continue          # 满了就不浪费：留着路上可能需要
                p.ammo = min(C.AMMO_CAP, p.ammo + C.PICKUP_AMMO)
                self.events.append("pickup_ammo")
            elif kind == "stone":
                if p.stones >= C.STONE_CAP:
                    continue
                p.stones = min(C.STONE_CAP, p.stones + C.PICKUP_STONE)
                self.events.append("pickup_stone")
            elif kind == "shield":
                if p.shield >= C.SHIELD_CAP:
                    continue
                p.shield += 1
                self.events.append("pickup_shield")
            else:
                continue
            it["taken"] = True
            self.pings.append({
                "x": it["x"], "y": it["y"], "r": 2.2, "age": 0.0,
                "life": C.PICKUP_PING_LIFE, "kind": "pickup_" + kind,
            })

    def _apply_shield_break(self, e) -> None:
        """护盾挡下一次抓捕：贴上来的人当场倒下，玩家活着但暴露了。

        为什么是"反杀"而不是"弹开"：弹开的话玩家会站在原地和哨兵贴脸，
        下一帧再被抓一次，护盾等于没有。而反杀把那个人从场上拿掉 5 秒
        （走正常重生流程），玩家才有"立刻撤离"的机会窗口 ——
        护盾买的是时间，不是无敌。

        代价和开枪完全一致：记一次击倒（扣分）、留下一具可能被目击的尸体、
        动静会惊动附近的人。想拿 S 档（零击倒）就不能靠它。
        """
        p = self.player
        p.shield = max(0, p.shield - 1)
        e.kill()
        self.kills += 1
        self.shield_kills += 1
        # 动静：比枪声小，但绝不是无声（否则护盾就成了零成本的免死金牌）
        self._emit_noise(p.x, p.y, C.SHIELD_NOISE_RADIUS, C.SHIELD_SUSPICION,
                         jitter=C.WEAPON_NOISE_JITTER)
        self.events.append("shield_break")

    # ---- 手枪：hitscan 射击 ----
    def aim_target(self, target):
        """把"鼠标点在世界里的位置"翻译成"玩家其实想打的那个人"。

        为什么需要这一步（这是玩家反馈"瞄着人打却一直打不死"的根因）：
        角色的命中判定点是**脚底** (e.x, e.y)，但精灵是脚底对齐往上画的
        （48px 高 ≈ 1.5 格，身体中心在脚底上方 0.7 格）。
        于是玩家瞄准眼睛看到的那个人 → 实际射线从它头顶飞过去 → 打在背后的墙上。
        判定和画面不一致，玩家只会得到一个结论："这枪是坏的"。

        所以点击先做一次"身份判定"：落在身体轮廓里就吸附到本体中心，
        然后照常走射线。**只吸附身份，不豁免遮挡** —— 隔着墙点人依然打不中，
        因为 _los 会否掉。
        """
        if self.player is None:
            return None
        px, py = self.player.pos
        best, best_d = None, 1e9
        for e in self.enemies:
            if not e.alive or e.grace > 0.0:
                continue
            if math.hypot(e.x - px, e.y - py) > C.WEAPON_RANGE:
                continue
            bx, by = e.x, e.y - C.WEAPON_AIM_OFFSET
            d = math.hypot(target[0] - bx, target[1] - by)
            if d > C.WEAPON_AIM_SNAP or d >= best_d:
                continue
            if not self._los(px, py, e.x, e.y):
                continue
            best, best_d = e, d
        return best

    def _los(self, x0, y0, x1, y1) -> bool:
        """两点之间有没有墙挡着（步长与 _trace_shot 一致，结论不会打架）。"""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return True
        dx, dy = dx / length, dy / length
        t = 0.0
        while t < length:
            t += 0.08
            if self.grid.is_wall(int(x0 + dx * t), int(y0 + dy * t)):
                return False
        return True

    def shoot(self, target) -> str:
        """朝 target（世界坐标）开一枪。返回结果字符串，供音效/测试使用：

        "wall" / "enemy" / "max"（没打中任何东西） / "empty" / "idle"

        为什么用 hitscan 而不是子弹实体：
        这是"消音手枪"，子弹速度远快于实体移动，做实体只会引入
        "子弹追不上贴墙的哨兵"这种莫名其妙的判定；射线求交既准又便宜。
        """
        if self.player is None or self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return "idle"
        if not self.player.can_fire():
            self.events.append("gun_empty")
            return "empty"

        # 先认人再打枪：点中的是"那个人"，不是"那一格地面"
        snapped = self.aim_target(target)
        if snapped is not None:
            target = (snapped.x, snapped.y)

        ox, oy = self.player.pos
        dx, dy = target[0] - ox, target[1] - oy
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            dx, dy, dist = 1.0, 0.0, 1.0
        dx, dy = dx / dist, dy / dist

        # 朝向跟着枪口走：开枪时人是朝着那个方向的
        self.player.facing = math.atan2(dy, dx)
        self.player.consume_ammo()

        kind, hx, hy, hit_enemy = self._trace_shot(ox, oy, dx, dy, C.WEAPON_RANGE)

        self.shots.append({
            "x0": ox, "y0": oy, "x1": hx, "y1": hy,
            "age": 0.0, "life": C.WEAPON_TRACER_LIFE, "kind": kind,
        })
        self.events.append("shot")

        if hit_enemy is not None:
            # 命中记号记在**身体中心**而不是脚底：玩家盯着的是"人"，
            # 反馈画在脚下会让人以为打中的是地板。
            self.hit_marks.append({
                "x": hit_enemy.x,
                "y": hit_enemy.y - C.WEAPON_AIM_OFFSET,
                "age": 0.0,
                "life": C.HITMARK_LIFE,
            })
            hit_enemy.kill()
            self.kills += 1
            self.events.append("hit")

        # 枪声比石子响一个数量级，而且方向明确（误差 0.35 格）：
        # 这是玩家为"清掉一个门卫"付的价，不是免费的。
        # 注意仍然只抬到"起疑"级别 —— 听到不等于看到，
        # 否则一枪就等于给自己画了个全图可见的红点。
        self._emit_noise(
            ox, oy, C.WEAPON_NOISE_RADIUS, C.WEAPON_SUSPICION,
            "shot", jitter=C.WEAPON_NOISE_JITTER,
        )
        return kind

    def _trace_shot(self, ox, oy, dx, dy, max_len):
        """沿射线步进：先撞墙就停，先碰到活着的敌人就命中。

        步长 0.08 格 ≈ 2.5 像素，比最薄的墙（1 格）细得多，
        不会出现"穿墙打人"；命中半径用 WEAPON_SPREAD 略放宽，
        避免玩家在第 30 帧才看到哨兵拐角、鼠标还差两像素就判空。
        """
        step = 0.08
        t = 0.0
        spread2 = C.WEAPON_SPREAD ** 2
        while t < max_len:
            t = min(max_len, t + step)
            x, y = ox + dx * t, oy + dy * t
            if self.grid.is_wall(int(x), int(y)):
                return ("wall", x - dx * step, y - dy * step, None)
            for e in self.enemies:
                if not e.alive or e.grace > 0.0:
                    continue
                if (e.x - x) ** 2 + (e.y - y) ** 2 <= spread2:
                    return ("enemy", x, y, e)
        x, y = ox + dx * max_len, oy + dy * max_len
        return ("max", x, y, None)

    def _update_shots(self, dt: float) -> None:
        for s in self.shots:
            s["age"] += dt
        self.shots = [s for s in self.shots if s["age"] < s["life"]]

    # ---- 敌人重生 ----
    def pick_respawn_point(self, enemy) -> tuple:
        """给即将复活的哨兵挑一个落点：**自己的巡逻网里离玩家最远的那一个**。

        为什么不原地复活：原地重生等于在玩家刚走过的地方刷人，
        玩家没有任何手段预期，只能被随机背刺。
        为什么不是全图最远点：哨兵应该回到"自己的岗位"，
        被击倒的是 G4 的人，就不该从 G7 冒出来。
        """
        cands = [enemy.home] + [p for p in enemy.patrol if p != enemy.home]
        cands = [c for c in cands if not self.grid.is_wall(int(c[0]), int(c[1]))]
        if not cands:
            return enemy.home
        px, py = (self.player.x, self.player.y) if self.player else (-1e9, -1e9)
        return max(cands, key=lambda c: (c[0] - px) ** 2 + (c[1] - py) ** 2)

    # ---- 投石诱敌 ----
    def throw_stone(self, target=None) -> bool:
        """扔一颗石子。target 为空时朝角色朝向扔（Q），否则朝目标点扔（鼠标右键）。

        返回是否真的扔出去了（没石子 / 冷却中 / 已经结算 → False）。
        """
        if self.player is None or self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return False
        if not self.player.can_throw():
            self.events.append("stone_empty")
            return False

        landing = plan_landing(
            self.grid, self.player.pos, self.player.facing, target
        )
        # 朝投掷方向转身：扔出去的东西从背后飞出来会很怪
        dx, dy = landing[0] - self.player.x, landing[1] - self.player.y
        if abs(dx) + abs(dy) > 1e-6:
            self.player.facing = math.atan2(dy, dx)

        self.player.consume_stone()
        self.stones.append(Stone(self.player.x, self.player.y, *landing))
        self.events.append("stone_throw")
        return True

    def _update_stones(self, dt: float) -> None:
        for st in list(self.stones):
            if st.update(dt):
                self.stones.remove(st)
                n = self._emit_noise(
                    st.tx, st.ty, C.STONE_NOISE_RADIUS, C.STONE_SUSPICION, "stone"
                )
                # 只有"真的把人引过去"才记风格分：往空地扔石子不算本事
                self.decoy_hits += n
                self.events.append("stone_land")

    def _update_pings(self, dt: float) -> None:
        for p in self.pings:
            p["age"] += dt
        self.pings = [p for p in self.pings if p["age"] < p["life"]]

    def _emit_noise(self, x: float, y: float, radius: float,
                    level: float, kind: str = "noise",
                    jitter: float = None) -> int:
        """在某处制造一次响动，让半径内的哨兵过去查看。返回被惊动的人数。

        三个设计约束（缺一个这机制就会变成"群体嘲讽"或"精确制导"）：
        1. 只抬到"起疑"级别，绝不越过确认阈值 —— 听到不等于看到；
        2. 落点带定位误差 —— 声音只能给出"大概在那片"，哨兵得自己搜；
        3. 已经在追你的、或正看着你的哨兵不受影响 —— 视觉信息优先于听觉。
        """
        self.pings.append({
            "x": x, "y": y, "r": radius,
            "age": 0.0, "life": C.STONE_PING_LIFE, "kind": kind,
        })
        jit = C.STONE_INVESTIGATE_JITTER if jitter is None else jitter
        alerted = 0
        for e in self.enemies:
            if not e.alive or e.role != Role.PATROL or e.sees_player:
                continue
            d = math.hypot(e.x - x, e.y - y)
            if d > radius:
                continue
            fall = 1.0 - d / radius

            # 确定性"伪随机"偏移：用坐标当种子，同一局同一个哨兵每次猜得一样偏，
            # 测试可复现，也不会出现"每次落点都被精确知道"的机器感。
            jx = math.sin(e.x * 12.9898 + e.y * 78.233) * jit
            jy = math.cos(e.x * 51.137 + e.y * 27.619) * jit
            tx, ty = x + jx, y + jy
            if self.grid.is_wall(int(tx), int(ty)):
                tx, ty = x, y

            e.suspicion = min(
                C.SUSPICION_CONFIRM - 0.08,
                max(e.suspicion, level + 0.10 * fall),
            )
            e._last_seen = (tx, ty)
            e._noise_lock = C.STONE_MEMORY_LOCK
            e._investigate_time = 0.0
            e._look_timer = 0.0
            e._path = None
            e._path_target = None
            e._path_failed_for = None
            alerted += 1
        return alerted

    # ---- 反馈聚合 ----
    def _aggregate_threat(self) -> None:
        """把每个敌人的感知结果汇总为 HUD 需要的两个数字。"""
        self.threat = 0.0
        self.seeing = 0
        chasing = False
        for e in self.enemies:
            if e.suspicion > self.threat:
                self.threat = e.suspicion
            if e.sees_player:
                self.seeing += 1
            if e.role is not None and e.role.value != "patrol":
                chasing = True

        # 暴露度峰值：判"全身而退"用。不能用当前 threat，因为结算那一刻
        # 可能已经降下来了 —— 玩家中途被惊到过一次就算破功。
        if self.threat > self.peak_threat:
            self.peak_threat = self.threat

        # 被发现次数：每次"从安全进入追击"计一次，避免同一轮追击反复计数
        if chasing and not self._chasing_flag:
            self.spotted += 1
            self.events.append("spotted")
            # 记下"是谁发现的"：用来识别"在一个哨兵面前反复晃"这种刷分行为
            for e in self.enemies:
                if e.alive and e.role is not None and e.role.value != "patrol":
                    key = f"{e.x:.1f},{e.y:.1f}"
                    self.spots_by_enemy[key] = self.spots_by_enemy.get(key, 0) + 1
        self._chasing_flag = chasing

    # ---- 计分 ----
    def style_bonus(self) -> List[tuple]:
        """风格分：返回 [(名称, 分值)]，只列真的做到的项。

        每一项都是**可验证的行为**而不是"尝试"：
        - "全身而退"看 peak_threat —— 一次都没让谁确认看到你；
        - 引开看 decoy_hits（真的有人被引过去），不看扔了几颗；
        - 干净开枪要求 kills>0 且 bodies_found==0 —— 没开枪的人不该拿这一项。
        """
        out = []
        if self.peak_threat < C.SUSPICION_CONFIRM:
            out.append(("全身而退", C.SCORE_STYLE_UNSEEN))
        if self.kills == 0:
            out.append(("零击倒", C.SCORE_STYLE_NO_KILL))
        if self.decoy_hits >= 3:
            out.append((f"引开 {self.decoy_hits} 人次", C.SCORE_STYLE_DECOY))
        if self.shield_kills > 0:
            out.append(("护盾反杀", C.SCORE_STYLE_SHIELD_KILL))
        if self.kills > 0 and self.bodies_found == 0:
            out.append(("开枪未被目击", C.SCORE_STYLE_CLEAN_SHOT))
        return out

    def style_total(self) -> int:
        total = sum(v for _, v in self.style_bonus())
        # 反向补丁：在同一个哨兵面前反复被发现，说明不是"潜行"而是"刷"。
        # 没有这一条，玩家可以靠反复进出视野吃满"全身而退"以外的所有分项。
        if any(n >= C.REPEAT_SPOT_LIMIT for n in self.spots_by_enemy.values()):
            total -= C.SCORE_REPEAT_PENALTY
        return total

    def base_score(self) -> int:
        """基础分：只衡量"完成得多快、多干净、多克制"。

        时间项**封顶**：超过 SCORE_TIME_FREE 的部分不再扣分。
        不封顶的代价是这条曲线决定了全部玩法 —— 慢就是差、快就是好，
        风格分再怎么调也只是在"快"里面挑赢家（score_table 里"为快冒险"
        比"静默流"高 140 分，就是这么来的）。封顶之后时间变成
        "够快就行"的门槛，超出部分归风格分去裁决。
        """
        t = min(self.elapsed, C.SCORE_TIME_FREE) + \
            max(0.0, self.elapsed - C.SCORE_TIME_FREE) * C.SCORE_TIME_OVER_RATE
        return (
            C.SCORE_BASE
            - t * C.SCORE_TIME_PENALTY
            - self.spotted * C.SCORE_SPOTTED_PENALTY
            - self.kills * C.SCORE_KILL_PENALTY
        )

    def score(self) -> int:
        """基础分 + 风格分。

        基础分 = 效率（快 + 少被发现 + 少击倒）；
        风格分 = 方式（全身而退 / 零击倒 / 引开 / 反弹杀 / 干净开枪）。

        只留基础分时最优解唯一（直线冲），风格分让"慢一点但漂亮"也值得打。
        另一个意义：明细可以在结算页逐条列出 —— 玩家能看到
        "这次差 60 分就能到 A"，这比一个孤零零的总分有说服力得多。

        没通关就是 0 分。这一条必须写在 `score()` 里，不能只在结算页
        "不显示分数"：测试、脚本、最佳纪录判定全都会调这个函数，
        结算页藏着不改，存档里照样会存进一个 827 分的失败局。
        之前 score_table 就抓到了这个 bug —— 失败局拿了全场最高分。
        """
        if self.state != C.STATE_VICTORY:
            return 0
        return max(0, int(round(self.base_score() + self.style_total())))

    def record(self) -> dict:
        return {
            "score": self.score(),
            "time": round(self.elapsed, 1),
            "spotted": self.spotted,
            "kills": self.kills,
            "rank": self.rank(),
            # 风格分明细也存下来：结算页和"最佳纪录"都能说清分数从哪来
            "style": [{"name": n, "value": v} for n, v in self.style_bonus()],
            "style_total": self.style_total(),
        }

    def rank(self) -> str:
        """按用时评级（S/A/B/C）。

        S 档额外要求"一枪没开、一次没被发现"：S 是"干净潜行"，
        不是"跑得快的屠夫"。把这条写进评级而不是只写进分数，
        是因为评级比分数更容易被玩家当成目标。
        """
        t = self.elapsed
        if t <= C.RANK_S and self.kills == 0 and self.spotted == 0:
            return "S"
        if t <= C.RANK_A:
            return "A"
        if t <= C.RANK_B:
            return "B"
        return "C"

    # ---- 玩法判定 ----
    def _check_objectives(self) -> None:
        """检测玩家是否接触目标点，推进游戏状态。"""
        px, py = self.player.x, self.player.y
        # 潜入阶段：接触数据终端 → 取得数据，进入逃脱，触发全图警觉
        if self.state == C.STATE_INFILTRATE:
            tx, ty = C.DATA_TERMINAL
            if (px - tx) ** 2 + (py - ty) ** 2 < 0.8 ** 2:
                self.has_data = True
                self.state = C.STATE_EXFILTRATE
                self.alert = True
                self._alert_timer = 3.0  # 屏幕顶部短暂提示
                self._apply_alert()
                self.events.append("pickup")
        # 逃脱阶段：接触撤离点 → 胜利
        elif self.state == C.STATE_EXFILTRATE:
            tx, ty = C.EXFIL_POINT
            if (px - tx) ** 2 + (py - ty) ** 2 < 0.8 ** 2:
                self.state = C.STATE_VICTORY
                self.events.append("victory")

    def _check_capture(self) -> None:
        """哨兵**知道你在附近**且贴到 0.6 格内 → 被抓住。

        这里必须带一个"知情"条件（看得见 / 已经起疑 / 已在追）。
        旧版只看距离，于是出现了一个极不公平的场景：一个压根没注意到你的
        哨兵从走廊穿过去，只是刚好从你身上踩过去，就把你抓住了 ——
        玩家结算页上"被发现 0 次"和"任务失败"同时出现，完全无法理解。
        尸体警报会把哨兵往"尸体附近"引，这个漏洞会被高频踩到，必须堵。

        阵亡的和刚复活（grace）的哨兵不参与：尸体抓人不算，
        而"在玩家脸上刷出来的哨兵立刻抓人"更是纯粹的随机惩罚。
        """
        px, py = self.player.x, self.player.y
        for e in self.enemies:
            if not e.alive or e.grace > 0.0:
                continue
            aware = (e.sees_player or e.suspicion >= C.SUSPICION_CURIOUS
                     or e.role != Role.PATROL)
            if not aware:
                continue
            if (e.x - px) ** 2 + (e.y - py) ** 2 < 0.6 ** 2:
                # 护盾优先于失败判定：贴上来的人被当场击倒，玩家活着但暴露了
                if getattr(self.player, "shield", 0) > 0:
                    self._apply_shield_break(e)
                    return
                self.captured = True
                self.captured_by = e
                self.state = C.STATE_DEFEAT
                # 结算页的"被发现"必须和"被抓住"对得上：
                # 一个只是起疑、还没进入追击的人抓住你，也要记一次发现，
                # 否则玩家会看到"被发现 0 次 · 任务失败"。
                if e.role == Role.PATROL and e.suspicion < C.SUSPICION_CONFIRM:
                    self.spotted += 1
                self.events.append("defeat")
                break

    def _apply_alert(self) -> None:
        """警觉状态：全图敌人视野更远、追击更快。"""
        for e in self.enemies:
            e.alerted = True
        self.alert_left = C.ALERT_DURATION

    def _update_alert(self, dt: float) -> None:
        """警觉是**限时封锁**，不是永久 debuff。

        原本拿完数据后全图永久 +2 格视野 +0.6 追击速度，
        等于返程必须全程硬扛 —— 而返程要横穿整张地图，
        结果就是"潜入做得再好，撤离必死"。真实设施也是这样：
        警报拉响 → 搜索一阵 → 没找到人 → 恢复常态。
        这样撤离阶段有了两种解法：抢在封锁期内冲出去，或者等封锁解除再走。
        """
        if self.alert_left <= 0.0:
            return
        self.alert_left = max(0.0, self.alert_left - dt)
        # 最后 3 秒闪烁提示（HUD 用 alert_left 画倒计时）
        if self.alert_left == 0.0:
            self.alert = False
            for e in self.enemies:
                e.alerted = False
            self.events.append("alert_end")

