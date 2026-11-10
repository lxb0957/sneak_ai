"""潜行协议 —— 从零手写游戏 AI 决策栈的 2D 潜行沙盒。

包结构：
- core/        世界、地图、相机、事件
- entity/      玩家与敌人实体
- perception/  视觉锥、射线遮挡、听觉
- pathing/     寻路（A*/JPS/流场，W2）
- decision/    决策（FSM/行为树/GOAP，W3）
- debug/       调试渲染层
- config/      配置加载
"""

__version__ = "1.8.12"
