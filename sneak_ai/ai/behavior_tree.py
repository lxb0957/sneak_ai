"""行为树（Behavior Tree）：组合节点 + 装饰器 + 黑板。

设计要点（面试能讲的深度）：
1. 三种返回状态 SUCCESS / FAILURE / RUNNING，这是 BT 与 FSM 的本质区别
   —— BT 允许"跨多帧的任务"自然表达为 RUNNING，不需要显式状态变量。
2. Selector（或节点）：子节点从左到右依次尝试，第一个非 FAILURE 即返回。
   Sequence（与节点）：全部成功才成功，任一失败即失败。
3. 黑板（Blackboard）：节点间共享数据的键值存储，避免全局变量耦合。
4. 相比 FSM：BT 是"组合式"的，能复用子树、局部修改行为；FSM 是"状态式"的，
   加一个状态要改一堆转移边。这是"为什么从 FSM 升级到 BT"的标准答案。
"""

from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class Status(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class Blackboard:
    """行为树/GOAP 共享的黑板：键值存储，节点读写统一走这里。"""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data


class BTNode:
    """行为树节点基类。tick() 返回 Status。"""

    def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError


class Selector(BTNode):
    """或节点：依次 tick 子节点，返回第一个非 FAILURE 的结果。

    语义 = "优先级从高到低选一个能执行的分支"。
    """

    def __init__(self, children: List[BTNode]):
        self.children = children

    def tick(self, bb: Blackboard) -> Status:
        for c in self.children:
            s = c.tick(bb)
            if s != Status.FAILURE:
                return s
        return Status.FAILURE


class Sequence(BTNode):
    """与节点：依次 tick，全部 SUCCESS 才 SUCCESS，遇到 FAILURE 立即返回。"""

    def __init__(self, children: List[BTNode]):
        self.children = children

    def tick(self, bb: Blackboard) -> Status:
        for c in self.children:
            s = c.tick(bb)
            if s != Status.SUCCESS:
                return s
        return Status.SUCCESS


class Inverter(BTNode):
    """装饰器：取反。SUCCESS↔FAILURE，RUNNING 不变。"""

    def __init__(self, child: BTNode):
        self.child = child

    def tick(self, bb: Blackboard) -> Status:
        s = self.child.tick(bb)
        if s == Status.SUCCESS:
            return Status.FAILURE
        if s == Status.FAILURE:
            return Status.SUCCESS
        return Status.RUNNING


class Condition(BTNode):
    """叶子：条件节点，传入谓词函数 bb -> bool。"""

    def __init__(self, name: str, predicate: Callable[[Blackboard], bool]):
        self.name = name
        self.predicate = predicate

    def tick(self, bb: Blackboard) -> Status:
        return Status.SUCCESS if self.predicate(bb) else Status.FAILURE


class Action(BTNode):
    """叶子：动作节点，传入动作函数 bb -> Status。

    动作可以返回 RUNNING 表示"这帧没做完，下帧继续"。
    """

    def __init__(self, name: str, fn: Callable[[Blackboard], Status]):
        self.name = name
        self.fn = fn

    def tick(self, bb: Blackboard) -> Status:
        return self.fn(bb)
