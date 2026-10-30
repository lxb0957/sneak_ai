"""GOAP（Goal-Oriented Action Planning）规划器。

核心思想（面试深度）：
1. 不用写死"发现→追→抓"的状态转移，而是声明"目标"和"动作"，
   规划器自己在状态空间里搜索出一条能达成目标的动作序列。
2. 每个 Action 有 前置条件(Preconditions) 和 效果(Effects)，
   都是"世界状态"（键值对）的集合运算。
3. 用 A* 在"状态空间"上搜索：节点 = 一个世界状态快照，
   边 = 一个动作，代价 = 动作 cost。启发式 = 未满足目标的数量。
4. 为什么不会状态爆炸：GOAP 的动作数量通常很少（个位数到几十），
   且 A* 有剪枝；对比"穷举所有状态组合"是指数级，GOAP 把它变成可控的搜索。

这是"决策 AI 三件套"里最"算法感"的一块，也是面试官最爱追问的。
"""

from typing import Dict, FrozenSet, List, Optional, Tuple


class Action:
    """一个 GOAP 动作。world_state 用 frozenset 表示（不可变、可哈希、可比较）。"""

    def __init__(
        self,
        name: str,
        cost: float,
        preconditions: FrozenSet[str],
        effects: FrozenSet[str],
    ):
        self.name = name
        self.cost = cost
        self.preconditions = preconditions  # 必须全部满足才能执行
        self.effects = effects  # 执行后加入世界状态的谓词

    def can_execute(self, state: FrozenSet[str]) -> bool:
        return self.preconditions <= state  # 前置条件是状态的子集

    def apply(self, state: FrozenSet[str]) -> FrozenSet[str]:
        return state | self.effects

    def __repr__(self):
        return f"Action({self.name})"


class Goal:
    """一个目标：需要满足的谓词集合 + 权重。"""

    def __init__(self, name: str, desired: FrozenSet[str], priority: float = 1.0):
        self.name = name
        self.desired = desired  # 需要达成的谓词
        self.priority = priority


def _heuristic(state: FrozenSet[str], goal: FrozenSet[str]) -> int:
    """启发式：还差多少个目标谓词未满足。admissible（每步最多满足一个）。"""
    return len(goal - state)


def plan(
    start_state: FrozenSet[str],
    goal: FrozenSet[str],
    actions: List[Action],
    max_depth: int = 32,
) -> Optional[List[Action]]:
    """用 A* 在状态空间搜索达成 goal 的动作序列。返回动作列表或 None。"""
    import heapq

    open_heap = []
    g_score = {start_state: 0.0}
    parent: Dict[FrozenSet[str], Tuple[Optional[FrozenSet[str]], Optional[Action]]] = {
        start_state: (None, None)
    }
    closed = set()

    heapq.heappush(open_heap, (_heuristic(start_state, goal), 0.0, start_state, 0))

    while open_heap:
        f, g, state, depth = heapq.heappop(open_heap)
        if state in closed:
            continue
        closed.add(state)

        if goal <= state:  # 目标达成
            return _reconstruct(parent, state)

        if depth >= max_depth:
            continue

        for action in actions:
            if not action.can_execute(state):
                continue
            nxt = action.apply(state)
            if nxt in closed:
                continue
            ng = g + action.cost
            if ng < g_score.get(nxt, float("inf")):
                g_score[nxt] = ng
                parent[nxt] = (state, action)
                heapq.heappush(
                    open_heap,
                    (ng + _heuristic(nxt, goal), ng, nxt, depth + 1),
                )

    return None


def _reconstruct(parent, state) -> List[Action]:
    actions = []
    cur = state
    while True:
        prev_state, action = parent[cur]
        if action is None:
            break
        actions.append(action)
        cur = prev_state
    actions.reverse()
    return actions
