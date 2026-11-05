"""决策流：把"谁、在什么时候、因为什么、改了什么主意"记成一条时间线。

F1 调试视图能看见**此刻**（视野锥、行为标签、可疑度），但看不见**为什么变**。
而 AI 最经不起追问的恰恰是那一次变化："他从巡逻转成调查，是因为 0.4 秒前
那颗石子落地，还是因为视线扫到了你？"—— 屏幕上只有结果，没有因果。

三条设计约束：

1. **只读**。trace 不参与任何判定：每处 push 都发生在状态**改完之后**，
   读的都是既有变量。`test_w26` 用"把 push 换成空操作，逐帧轨迹哈希必须
   一位不差"钉住这一条 —— 观测不该改变被观测的东西。
2. **只在变化时记**。记的不是"我现在在巡逻"，是"我刚从巡逻变成调查"。
   否则一秒 60 条，屏幕刷成瀑布，日志也没法 grep。
3. **一份数据两个出口**：F1 面板（最近 9 条）和 `--trace x.jsonl`（全部），
   同一份 Row，不写两套格式化代码。
"""

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Row:
    """一条决策事件。t 是**模拟时间**（world.elapsed），不是墙钟。"""

    t: float
    who: int                    # 敌人编号（1 起）；0 = 世界事件
    kind: str                   # state / role / suspicion / giveup / world
    text: str
    data: dict = field(default_factory=dict)


# 世界事件的中文说法。`world.events` 是给音效用的短标签（"shot"），
# 直接打给人看会是中英混排的日志；在这里翻译一次，两边都省事。
_WORLD_TEXT = {
    "power_cut": "总闸被拉下：全图黑灯，但所有人都听见了",
    "power_back": "电力恢复",
    "radio": "目击者喊了一声，附近的人过去看",
    "body_found": "有人发现了尸体",
    "pickup_ammo": "捡到弹药",
    "pickup_stone": "捡到石子",
    "pickup_shield": "捡到护盾",
    "pickup": "拿到了数据节点",
    "shield_break": "护盾替他挡了一下",
    "gun_empty": "空仓",
    "shot": "开枪",
    "hit": "命中",
    "stone_empty": "没有石子了",
    "stone_throw": "扔出石子",
    "stone_land": "石子落地",
    "spotted": "被看见：进入追击",
    "victory": "抵达撤离点",
    "defeat": "被抓",
    "alert_end": "警报解除",
    "respawn": "哨兵补位",
}

# 面板配色：状态变化最显眼（它是"改主意"本身），
# 世界事件偏冷色（它是外部输入），放弃/跟丢偏灰（它是"什么都没发生"）。
KIND_COLORS = {
    "state": (232, 190, 110),
    "suspicion": (214, 142, 92),
    "role": (206, 120, 120),
    "giveup": (140, 140, 148),
    "world": (146, 178, 210),
}


def describe(event_name: str) -> str:
    """把 world.events 的短标签翻成人话；不认识的原样返回（别吞掉信息）。"""
    return _WORLD_TEXT.get(event_name, event_name)


class Trace:
    """固定容量的决策事件环形缓冲。

    容量 4000 是"够一整局、又不至于吃内存"的折中：一局 200 秒的实测事件量
    在 150~300 条量级（只在变化时记），4000 已经够覆盖十局。
    """

    def __init__(self, keep: int = 4000):
        self.rows = deque(maxlen=keep)
        self.t = 0.0        # 模拟时间；由 World 每帧 tick
        self.total = 0      # 累计条数（环形缓冲会覆盖，这个不会）

    def tick(self, dt: float) -> None:
        self.t += dt

    def push(self, kind: str, text: str, who: int = 0, **data) -> None:
        self.rows.append(Row(self.t, who, kind, text, data))
        self.total += 1

    def recent(self, n: int = 9) -> list:
        rows = list(self.rows)
        return rows[-n:] if n > 0 else rows

    def dump(self, path, level: int = None) -> int:
        """追加写 JSONL；返回写了多少条。

        用**追加**而不是覆盖，是因为一局会跨五关：每关换世界就 dump 一次，
        覆盖的话 `--trace` 只能留下最后一关，而玩家想复盘的往往是"我是在
        第 2 关开始崩的"。
        """
        rows = list(self.rows)
        if not rows:
            return 0
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            if level is not None:
                head = {"t": 0.0, "who": 0, "kind": "level",
                        "text": f"第 {level + 1} 关", "data": {"level": level + 1}}
                f.write(json.dumps(head, ensure_ascii=False) + "\n")
            for r in rows:
                f.write(json.dumps({
                    "t": round(r.t, 3),
                    "who": r.who,
                    "kind": r.kind,
                    "text": r.text,
                    "data": r.data,
                }, ensure_ascii=False) + "\n")
        return len(rows)
