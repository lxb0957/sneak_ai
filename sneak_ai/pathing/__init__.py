"""寻路模块：A* / JPS / 流场（Flow Field）。

统一接口约定：
- 每个算法暴露一个函数，输入 (grid, start, goal) 返回路径。
- 路径是瓦片坐标列表 [(x,y), ...]，含起点不含终点（或含终点）。
- start / goal 为 (x, y) 整数瓦片坐标。
"""

from .astar import astar
from .jps import jps
from .flowfield import FlowField, flowfield_path

__all__ = ["astar", "jps", "FlowField", "flowfield_path"]
