"""GOAP 落地：跟丢之后"去查哪几处、按什么顺序查"。

## 为什么这件事该交给规划器，而不是再写一条 if/else

跟丢的位置附近，往往有若干个"从最后已知位置看不见"的藏身点（箱子背面、
墙角、柱子后面）。玩家真正会做的事就是往那里一躲。哨兵该查哪些、先查哪个，
取决于现场几何 —— 写死成"永远先查最近的箱子"，玩家两次之后就摸清了规律。

这里把候选点变成**动作集**，把"每一处都查过"写成**目标**，
让规划器在状态空间里自己排出总脚程最短的顺序。也就是说：
被规划出来的是"顺序"，不是"哪几条语句"。

## 状态谓词的约定

    at_i    我站在第 i 个点上（互斥：走到 j 会划掉 at_i）
    chk_i   第 i 个点我已经搜过了

起点是"当前所站的位置"，1 号点是"最后已知位置"，2..n 是候选藏身点。
目标 = 每个点都被搜过。可行的计划只有"怎么把这几处串成一条路"这一个自由度，
而这正好就是需要算的东西。

## 代价怎么定的

移动代价用曼哈顿距离近似脚程。为什么不用 A* 真算：一次规划要算 n² 对路径
（n=3 时 9 次全图搜索），而规划发生在"跟丢"这一瞬间，放在帧里就是一次卡顿。
顺序决策对距离误差不敏感（点都在 6 格内），所以取近似、换掉这次卡顿。
真要走不到，执行阶段 `_follow_path` 会返回 "unreachable"，那一步会被跳过。
"""

from typing import List, Sequence, Tuple

from .. import constants as C
from ..core.grid import raycast_blocks
from .goap import Action, plan

Tile = Tuple[int, int]

# 八邻域：判断一个格子被几面墙围住（角落/箱子背面）
_NEI = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)


def _at(i: int) -> str:
    return f"at_{i}"


def _chk(i: int) -> str:
    return f"chk_{i}"


def find_hiding_spots(
    grid,
    origin,
    limit: int = None,
    radius: float = None,
) -> List[Tile]:
    """在 origin 周围找"可疑的藏身点"。

    两个条件同时成立才算：
    1. **从 origin 看不见它**（raycast 被挡）—— 看得见的地方不用搜，抬眼就是；
    2. **至少三面贴着墙**（墙角、箱子背面、柱子侧面）—— 空旷地中间不是藏身处。

    返回按曼哈顿距离从近到远、最多 limit 个。找不到就返回空列表，
    调用方应当退化成"只在最后已知位置环视一圈"。
    """
    limit = C.SEARCH_SPOT_LIMIT if limit is None else limit
    radius = C.SEARCH_SPOT_RADIUS if radius is None else radius
    ox, oy = int(origin[0]), int(origin[1])
    r = int(radius)
    cands: List[Tuple[int, int, int]] = []

    for y in range(oy - r, oy + r + 1):
        for x in range(ox - r, ox + r + 1):
            if not grid.is_walkable(x, y):
                continue
            d = abs(x - ox) + abs(y - oy)
            if d < 2 or d > radius:
                continue
            walls = 0
            for ox2, oy2 in _NEI:
                if grid.is_wall(x + ox2, y + oy2):
                    walls += 1
            # 3 面就够：地图是实心岩体挖出来的，能站人的格子几乎都贴着墙，
            # 卡到 4 面（真正的凹角）在实机上只剩三分之一的概率能找到候选点，
            # 而"从记忆点看不见"这条已经把空旷地中间排除掉了。
            if walls < 3:
                continue             # 先做便宜的几何筛，再付 raycast 的钱
            if not raycast_blocks(grid, ox + 0.5, oy + 0.5, x + 0.5, y + 0.5):
                continue             # 站在记忆点就能看见 → 不算藏身处
            cands.append((d, x, y))

    cands.sort()
    return [(x, y) for _, x, y in cands[:limit]]


def _manhattan(a, b) -> float:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def build_search_plan(
    start,
    target,
    spots: Sequence[Tile],
) -> List[Tuple[str, Tile]]:
    """规划一条搜查路线，返回 [("move", 格), ("sweep", 格), ...]。

    target 不可达（听觉猜出来的格子常落在墙里）时，它自己那一站的 move
    会在执行阶段被判 unreachable 并跳过 —— 这里不提前过滤，
    因为"可达性"是执行层的事实，规划层只需要负责顺序。
    """
    start_t = (int(start[0]), int(start[1]))
    target_t = (int(target[0]), int(target[1]))

    # 起点固定是 index 0；目标点如果就是脚下，就不占一个 index
    # （否则会出现两个坐标相同的点、连着扫两次的怪计划）
    points: List[Tile] = [start_t]
    if target_t != start_t:
        points.append(target_t)
    for s in spots:
        t = (int(s[0]), int(s[1]))
        if t not in points:          # 起点/记忆点重合的点不重复列
            points.append(t)
    if len(points) < 2:
        return [("sweep", target_t)]

    n = len(points)
    actions: List[Action] = []
    tags = {}

    # 移动：任意两点之间都能走，代价 = 曼哈顿距离
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            name = f"move_{i}_{j}"
            actions.append(
                Action(
                    name,
                    _manhattan(points[i], points[j]),
                    frozenset([_at(i)]),
                    frozenset([_at(j)]),
                    frozenset([_at(i)]),   # 人走了，原来那格的 at 就不再成立
                )
            )
            tags[name] = ("move", points[j])

    # 搜查：站在点上才能搜；搜完标记 chk
    for i in range(1, n):
        name = f"sweep_{i}"
        actions.append(
            Action(
                name,
                C.SEARCH_SWEEP_COST,
                frozenset([_at(i)]),
                frozenset([_chk(i)]),
            )
        )
        tags[name] = ("sweep", points[i])

    goal = frozenset(_chk(i) for i in range(1, n))
    result = plan(frozenset([_at(0)]), goal, actions, max_depth=4 * n + 8)

    if not result:                   # 理论上不会发生（每个点都能走到）；兜底别让 AI 卡死
        return [("move", target_t), ("sweep", target_t)]

    # 合并"连续走向同一格"的动作（sweep 不移动，所以不会出现 move→sweep→move 同格）
    steps: List[Tuple[str, Tile]] = []
    for a in result:
        steps.append(tags[a.name])
    return steps
