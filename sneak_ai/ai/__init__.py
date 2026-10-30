"""AI 决策栈：行为树（BT）、GOAP 规划器、协同任务分配。

这是整个项目最能讲深度的部分 —— 从零手写三层决策系统：
- behavior_tree.py：行为树（组合节点 + 装饰器 + 黑板）
- goap.py：GOAP 规划器（目标-行动-前置条件-效果，A* 搜状态空间）
- coordination.py：多敌人协同分工（追击/包抄/封路）
"""

from .behavior_tree import (
    BTNode,
    Selector,
    Sequence,
    Inverter,
    Condition,
    Action,
    Blackboard,
)
from .coordination import Coordinator, Role

__all__ = [
    "BTNode", "Selector", "Sequence", "Inverter",
    "Condition", "Action", "Blackboard",
    "Coordinator", "Role",
]
