"""新手引导：把"这一关的新动词"在正确的时机说出来。

## 为什么单独一个模块

引导文案有三条硬约束，写散了必然违反其中一条：

1. **必须点名按键**。"这一关要让守着门的人走开"是设计意图，不是操作说明 ——
   第一次来的人读完仍然不知道按什么。第 5 关以前就在这句字幕里卡住过：
   开场不点名 "Q"，玩家会在第一道 1 格闸前面站到超时，然后以为关坏了；
2. **只能在时机上说**。开局倒一堆字是说明书，玩家不会读；走近了才说，
   他才正好用得上。所以开场句只说"这一关的新东西"，工具用法交给触发条件；
3. **只说一次**。同一个提示每局都弹，老玩家会把它当噪声，
   真需要看的人反而学会了无视屏幕底部的字。

前两条落在这里的 `intro_for()` / `once_tips()`，第三条落在 settings 的
`taught` 列表里 —— 说过的话记在存档里，换一局不再说。

模块是**纯函数**，不 import pygame：文案和触发条件要能被测试直接断言，
不需要开窗口跑一局。消费点在 `__main__._update_tips()`。
"""

from .. import constants as C

# 说过的提示。key 进存档，文案在这里 —— 改文案不需要迁移存档。
SPOTTED = "spotted"
STAMINA = "stamina"
OBJ_DONE = "obj_done"

TEXT = {
    SPOTTED: "被看见了：Shift 蹲进掩体，断掉视线他就会回去搜 —— 硬跑跑不过",
    STAMINA: "体力空了：站着不动会回满 —— 冲刺是用来脱身的，不是用来赶路的",
    OBJ_DONE: "数据到手，警报会响 —— 这时候该往撤离点走，不是继续摸补给",
}


def intro_for(level) -> str:
    """这一关开场的**一句**话：先报身份，再点这一关的新动词。

    只点名"这一关独有的东西"：第 4 关的总闸、第 5 关的固定岗、第 3 关的
    两个数据舱。基础操作（WASD / Shift / 左键）在菜单里，开局再念一遍
    等于把玩家的第一眼浪费在他已经知道的事情上。
    """
    if level.idx == 0:
        return "前往绿色标记的数据终端 —— 蹲下（Shift）移动更安静"
    msg = f"第 {level.idx + 1} 关 · {level.name}　{level.subtitle}"
    if len(level.terminals) > 1:
        # 第 3 关：不说这句，玩家摸完第一个会以为该撤了
        msg += "　·　两个舱都要摸"
    if level.power:
        # 第 4 关：直廊走不动的时候，"等"是等不出窗口的，动词是拉闸。
        # 这一句以前没有 —— 玩家得自己发现墙上那个东西能按 E。
        msg += "　·　按 E 拉总闸：全场视野减半"
    if level.posts:
        # 第 5 关：1 格闸里站着一个不会走开的人，动词是骗。
        msg += "　·　Q 扔石头，把他引开"
    return msg


def once_tips(world, player, taught) -> list:
    """按当前局面给出**还没说过**的提示：[(key, text), ...]。

    返回列表而不是直接弹一条：调用方决定"这次只弹第一条"（屏幕底部只有
    一个位置），但决定"什么条件下该说"的逻辑留在这里，测试才断言得到。
    """
    out = []
    if world.spotted > 0 and SPOTTED not in taught:
        out.append((SPOTTED, TEXT[SPOTTED]))
    if getattr(player, "stamina", C.STAMINA_MAX) <= 0.5 and STAMINA not in taught:
        out.append((STAMINA, TEXT[STAMINA]))
    # 拿到第一份数据、还没拿完：这是"警报响了，但路还没走完"的那一瞬。
    # 第 3 关有两个节点，所以不能按"任务完成"判，只能按"刚拿到一份"判。
    if world.has_data and world.remaining() and OBJ_DONE not in taught:
        out.append((OBJ_DONE, TEXT[OBJ_DONE]))
    return out
