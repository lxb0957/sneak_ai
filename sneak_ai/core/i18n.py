"""界面双语：中文是源语言，英文是译文，翻译发生在**绘制入口**。

## 为什么不在调用点包函数

界面上的字不是 199 个调用点各写一句 —— 大量是 `levels.py`、`enemy.py`、
`world.py` 里拼好了再交给 HUD 画的。所以在绘制入口拦一道（`ui.render`、
`TextCache.get`、5 处直接 `font().render`），**一处改动覆盖全部来源**，
包括以后新加的字：新加的字符串只要在表里就有译文，不在表里就原样显示，
不会因为漏了某个调用点而半中半英。

## 为什么按整句译，不按片段

中文 f-string 会被切成碎片：

    f"第 {lv.idx + 1} 关完成"   →   "第 " + " 关完成"

按碎片译出来是 "Rank 3 cleared"（"第 " 一个方块字，词序还全反）。
所以 `TABLE` 的 key 是**完整的一句话**，带变量的走 `TEMPLATES`：
模板里的 `{0}` 编译成正则捕获组，运行时拿最终字符串去匹配，
译文里用 `{0}` 引用回去 —— 词序可以随便调。

## 中文模式必须是恒等

`t()` 在中文下**原样返回同一个对象**，所以中文画面逐字节不变。
这不是"应该不会变"，`test_w39` 会拿 199 条上屏文本逐条验证。
"""

from __future__ import annotations

import re

ZH = "zh"
EN = "en"

_LANG = ZH
LANG_NAMES = {ZH: "中文", EN: "English"}

# 探针：设成 set 就记录所有经过 t() 的字符串（抓字脚本与测试用）
RECORD: set | None = None


def lang() -> str:
    return _LANG


def set_lang(value: str) -> None:
    global _LANG
    _LANG = EN if value == EN else ZH


def other_lang() -> str:
    return EN if _LANG == ZH else ZH


def t(text: str) -> str:
    """中→英。中文模式下原样返回（同一个对象，零开销）。

    注意 `RECORD` 记的是**输出**不是输入：抓字脚本要回答的是
    "屏幕上到底出现了什么"，记输入会把"已经翻好的句子"也算成漏译
    （第一版就是这么误报了 157 条）。
    """
    out = _translate(text) if (_LANG == EN and text) else text
    if RECORD is not None:
        RECORD.add(out)
    return out


def _translate(text: str) -> str:
    if not text:
        return text
    hit = TABLE.get(text)
    if hit is not None:
        return hit
    for pattern, template in _RULES:
        m = pattern.match(text)
        if m is not None:
            return _fill(template, m)
    # 拼接兜底：界面上大量句子是"基句 + 可选后缀"拼出来的，例如
    #   f"M 音效：{on}    A 辅助：{on}    ESC 退出" + "    · 辅助模式不记纪录"
    # 后缀有 2×2×2 种组合，给每种都写一条规则等于把组合爆炸抄进表里。
    # 所以按分隔符切开，逐段翻，再拼回去 —— 段数是 1~3，代价可以忽略。
    if "·" in text:
        return _translate_parts(text)
    return text


_SPLIT = re.compile(r"(\s*·\s*)")


def _translate_parts(text: str) -> str:
    out = []
    changed = False
    for part in _SPLIT.split(text):
        if part.strip() == "·":
            out.append(" · ")
            continue
        core = part.strip()
        done = _translate(core)
        changed = changed or (done != core)
        out.append(done)
    if not changed:
        return text
    return "".join(out).strip()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _fill(template: str, m: re.Match) -> str:
    """把模板里的 {n} 换成第 n 个捕获组。

    捕获组自己也要翻一遍 —— 中文字符串是**套着拼**的：

        f"第 {i} 关 · {L.LEVELS[i].name}"   →   "第 1 关 · 哨站"

    外层翻成 "Level 1 · {0}"，而 {0} 是关卡名"哨站"，它自己也在表里。
    只在整句上翻一次的话，屏幕上会变成 "Level 1 · 哨站"（半英半中）。
    """
    def sub(mm):
        idx = int(mm.group(1))
        try:
            value = m.group(idx + 1)
        except IndexError:
            return mm.group(0)
        # 译文里不会有中文，所以这里不会自己套自己
        return _translate(value) if _has_cjk(value) else value

    return re.sub(r"\{(\d)\}", sub, template)


def _compile(template: str) -> re.Pattern:
    """把 '第 {0} 关完成' 编成 '^第 (.+?) 关完成$'。

    占位符一律用非贪婪捕获：译文里的词序由译文自己决定，
    这里只负责"哪一段是变量"。
    """
    parts = re.split(r"\{(\d)\}", template)
    pattern = ""
    for i, part in enumerate(parts):
        pattern += r"(.+?)" if i % 2 == 1 else re.escape(part)
    return re.compile("^" + pattern + "$")


# ============ 静态整句 ============
# key 是源码里原封不动的那句话，value 是英文。HUD 空间紧，英文尽量短。
TABLE = {
    # ---- HUD：关卡信息行 ----
    "辅助｜": "Assist | ",
    "关｜": " | ",
    "目标：": "Objective: ",
    "数据节点 ": "Nodes ",
    "潜入数据终端": "Infiltrate the data terminal",
    "返回撤离点": "Return to exfil",
    "用时 ": "Time ",
    "s    被发现 ": "s    spotted ",
    "在岗 ": "On duty ",
    "    追击/包抄 ": "    chasing/flanking ",
    "    倒地 ": "    down ",
    "尸体已暴露 ": "Body found ",
    "全图警觉 ": "Global alert ",
    # ---- HUD：总闸 ----
    "断电 ": "Blackout ",
    "s · 哨兵视野 ×": "s · guard vision ×",
    "总闸 ": "Breaker ",
    " · 靠近按 E": " · press E nearby",
    "总闸已用尽 · 剩下的路只能靠等": "Breaker spent · now you wait",
    # ---- HUD：暴露度 ----
    # 这里有坑：中文"暴露"是**进度条的名字**（低威胁时的常态标签），
    # 不是"你被发现了"。译成 EXPOSED 会和下面的 "Unseen" 自相矛盾 ——
    # 第一版就是这么错的，靠截图看出来的。
    "暴露": "EXPOSURE",
    "起疑": "SUSPECT",
    "已被发现": "SPOTTED",
    " 个哨兵正看着你": " guards watching",
    "有哨兵正看着你": "A guard is watching you",
    "有哨兵在找你": "A guard is hunting you",
    "未被察觉": "Unseen",
    # ---- HUD：资源 ----
    "投石": "Stones",
    "用尽": "empty",
    # ---- HUD：顶栏提示 ----
    "警报 · 敌人已警觉": "ALARM · guards alerted",
    "暴露！快找掩体": "SPOTTED! Get behind cover",
    "有人在起疑…": "Someone is suspicious…",
    "潜行中": "Sneaking",
    "已静音 (M)": "Muted (M)",
    # ---- HUD：威胁标记 ----
    "巡逻哨兵": "Patrol",
    "追兵": "Chaser",
    "包抄手": "Flanker",
    "搜索队": "Search party",
    "哨兵": "Guard",
    "东边": "east",
    "西边": "west",
    "南边": "south",
    "北边": "north",
    "它还没确认你的位置，是你在它眼皮底下走过去了":
        "It has not confirmed you — you slipped past",
    "它已经盯着你，直到把你堵住": "It has eyes on you and will not let go",
    "从你": "From ",
    "贴上来抓住了你 —— ": " closed in and caught you — ",
    # ---- HUD：状态行 ----
    "已拿到数据 · 回撤途中": "Data secured · heading out",
    "刚开局": "Just started",
    "潜入途中": "In progress",
    "最佳纪录": "Best",
    # ---- HUD：结算页 ----
    "任务完成": "MISSION COMPLETE",
    "任务失败": "MISSION FAILED",
    "评级": "Rank",
    "用时": "Time",
    "被发现": "Spotted",
    "击倒": "Downed",
    "TAB 复盘    ·    R 再来一局    ·    ESC 回主菜单":
        "TAB debrief    ·    R retry run    ·    ESC main menu",
    " 分  ·  ": " pts  ·  ",
    " 秒  ·  ": " s  ·  ",
    " 评级": " rank",
    "  暂无": "  —",
    "★ 新纪录 ★": "★ NEW RECORD ★",
    "风格分": "Style",
    "反复被发现  −40": "Repeatedly spotted  −40",
    "通关": "Cleared",
    "失败": "Failed",
    " · 累计 ": " · total ",
    " 秒 · ": " s · ",
    "关卡": "Level",
    "本关发生": "This level",
    "（未完成）": "(incomplete)",
    "石子": "Stones",
    "开枪": "Shots",
    "没被发现过，也没动手": "Never spotted, never fired",
    # ---- HUD：体力 / 弹药 / 护盾 ----
    "体力": "Stamina",
    "弹药": "Ammo",
    "空仓": "EMPTY",
    "无护盾": "No shield",
    "力竭 · 停下恢复": "Exhausted · stop to recover",
    # ---- 复盘页 ----
    "复 盘": "DEBRIEF",
    "得分": "Score",
    "进度": "Progress",
    "逐关": "Per level",
    "关键时刻": "Key moments",
    "这一局没有触发任何值得记录的事件":
        "Nothing worth recording happened this run",
    "（还没有事件：开局头几秒通常只有巡逻）":
        "(no events yet — the opening seconds are patrols)",
    "TAB 返回结算    ·    R 再来一局    ·    ESC 回主菜单":
        "TAB debrief    ·    R retry    ·    ESC main menu",
    # ---- 菜单 ----
    "本关最佳 ": "Level best ",
    "正式开局 · ": "Fresh run · ",
    "正式开局": "Fresh run",
    "已通关 · ": "Cleared · ",
    "已通关": "Cleared",
    "未通关 · ": "Not cleared · ",
    "未通关 · 可试玩": "Not cleared · trial",
    "M 音效：": "M sound: ",
    "    A 辅助：": "    A assist: ",
    "    ESC 退出": "    ESC quit",
    "    · 辅助模式不记纪录": "    · assist runs are not recorded",
    "    · 跳关只记本关成绩，不进总榜":
        "    · starting from a level only records that level",
    "连续潜入 ": "Infiltrate ",
    " 处设施，拿到数据，活着回来。":
        " facilities, take the data, come back alive.",
    "　· 辅助：敌人更弱，成绩不记": " · Assist: weaker guards, no records",
    "最佳纪录  ": "Best  ",
    "WASD 移动 · Shift 潜行 · Space 冲刺 · 左键 射击":
        "WASD move · Shift sneak · Space sprint · LMB shoot",
    "Q / 右键 投石 · Tab 整张地图 · F1 AI 视图":
        "Q / RMB stone · Tab full map · F1 AI view",
    "E 拉总闸（第 4 关） · 可捡弹药 / 石子 / 护盾":
        "E breaker (level 4) · ammo / stones / shield pickups",
    " 关 · ": " · L",
    "回车 潜入第 ": "ENTER — infiltrate level ",
    # ---- 菜单标题（英文下不再用竖排中文标题） ----
    "潜 行 协 议": "STEALTH  PROTOCOL",
    "STEALTH PROTOCOL": "five levels · one run · no checkpoints",
    "潜行协议 Stealth Protocol": "Stealth Protocol",
    # ---- 风格分明细 ----
    "全身而退": "Never seen",
    "零击倒": "No kills",
    "护盾反杀": "Shield takedown",
    "开枪未被目击": "Unseen shots",
    "引开 {0} 人次": "Drew {0} away",
    # ---- 开关状态（菜单底栏用单字） ----
    "开": "On",
    "关": "Off",
    # ---- 菜单底栏的附加说明（会被拼在基句后面） ----
    "辅助模式不记纪录": "assist runs are not recorded",
    "跳关只记本关成绩，不进总榜":
        "level select records only that level",
    "辅助：敌人更弱，成绩不记": "Assist: weaker guards, no records",
    # ---- 关卡名与副标题 ----
    "哨站": "Outpost",
    "θ 形设施，两个舱室之间只有三条通路":
        "A theta-shaped facility; three routes between the two rooms",
    "环形枢纽": "Ring Junction",
    "一圈外环 + 四条辐条，走廊宽 4 格，视线也是":
        "Outer ring plus four spokes; 4-tile corridors, and sightlines to match",
    "柱林中庭": "Pillar Court",
    "两个数据舱摆在柱林两端 —— 拿到第一个，警报就响":
        "Two data rooms at either end — take the first and the alarm goes off",
    "配电所": "Power Station",
    "一条直廊到底 —— 走不动的时候，去把灯关掉":
        "One long corridor — when you are stuck, kill the lights",
    "查验站": "Inspection Post",
    "五道关闸，每道闸只有一个人 —— 他不走开，你就让他走开":
        "Five checkpoints, one man each — if he will not move, make him",
    " 道闸": " checkpoint",
    # ---- 决策链（F1 面板与气泡共用）、世界事件 ----
    "总闸被拉下：全图黑灯，但所有人都听见了":
        "Breaker thrown: the map goes dark, and everyone heard it",
    "电力恢复": "power restored",
    "目击者喊了一声，附近的人过去看":
        "A witness shouted; nearby guards went to look",
    "捡到弹药": "picked up ammo",
    "捡到石子": "picked up stones",
    "捡到护盾": "picked up a shield",
    "拿到了数据节点": "took the data node",
    "护盾替他挡了一下": "the shield took the hit",
    "命中": "hit",
    "没有石子了": "out of stones",
    "扔出石子": "threw a stone",
    "石子落地": "stone landed",
    "被看见：进入追击": "spotted: chase begins",
    "抵达撤离点": "reached exfil",
    "被抓": "caught",
    "警报解除": "alert cleared",
    "哨兵补位": "guard respawned",
    "原地张望": "looking around",
    "去查 (": "checking (",
    "看见过": "seen before",
    "听/被喊": "heard/called",
    " → 调查：": " → investigate: ",
    "，可疑 ": ", suspicion ",
    "还看得见": "still visible",
    "按最后已知位置": "last known position",
    " → 追击：": " → chase: ",
    "（可疑 ": " (suspicion ",
    "：去 ": ": go to ",
    # ---- 复盘事件 ----
    "被看见 → 转入追击": "Spotted → chase",
    "目击者喊了一声，附近的人都过来了":
        "A witness shouted; nearby guards came running",
    "有人发现了尸体": "A body was found",
    "总闸被拉下，全图黑灯": "Breaker thrown; the map went dark",
    "被抓": "Caught",
    "抵达撤离点": "Reached exfil",
    # ---- 教学提示 ----
    "被看见了：Shift 蹲进掩体，断掉视线他就会回去搜 —— 硬跑跑不过":
        "Seen: Shift into cover. Break his line of sight and he will search — "
        "you cannot outrun him",
    "体力空了：站着不动会回满 —— 冲刺是用来脱身的，不是用来赶路的":
        "Out of stamina: stand still and it refills — sprint is for escaping, "
        "not travelling",
    "数据到手，警报会响 —— 这时候该往撤离点走，不是继续摸补给":
        "Data secured, the alarm will sound — head for exfil, not for supplies",
    "前往绿色标记的数据终端 —— 蹲下（Shift）移动更安静":
        "Go to the green data terminal — Shift to move quietly",
    "　·　两个舱都要摸": " · Both rooms need hitting",
    "　·　按 E 拉总闸：全场视野减半":
        " · Press E for the breaker: halve every sightline",
    "　·　Q 扔石头，把他引开": " · Q throws a stone to pull him away",
    # ---- 全图 ----
    "TAB 关闭  ·  地图不含敌人位置":
        "TAB to close  ·  no enemy positions on this map",
    # ---- 纪录 ----
    "旧版 ": "Legacy ",
    "创于 ": "set ",
    # ---- 拾取提示 / 启动 ----
    "没有可用的显示设备": "No display device available",
    "弹药包：走近自动补 2 发（上限 8）":
        "Ammo: walk over it to top up 2 (max 8)",
    "石子堆：走近捡 2 颗，Q / 右键扔出去引开哨兵":
        "Stones: pick up 2, then Q / RMB to pull a guard away",
    "护盾：捡起后能挡下一次抓捕，碰到你的哨兵会倒下":
        "Shield: blocks one grab — the guard who touches you goes down",
    "本关最佳": "Level best",
    " 关完成": " cleared",
    "下一关 · ": "Next · ",
    "累计用时 ": "Total time ",
    " 秒    被发现 ": "s    spotted ",
    " 次    弹药 ": "×    ammo ",
    " · 石子 ": " · stones ",
    "本关新纪录 · ": "New level record · ",
    "游戏出错了，堆栈已写入 crash.log":
        "The game crashed; the traceback is in crash.log",
    "把这个文件发给开发者就能定位；按任意键或关闭窗口退出。":
        "Send that file to the developer. Press any key to quit.",
}

# ============ 带变量的模板 ============
# 运行时拿最终字符串匹配，所以这里写的是"已经填好数字的样子"。
TEMPLATES = {
    "    倒地 {0}": "    down {0}",
    "M 音效：{0}    A 辅助：{1}    L 语言：{2}    ESC 退出":
        "M sound: {0}    A assist: {1}    L lang: {2}    ESC quit",
    "{0}  {1} 分  ·  {2} 秒  ·  {3} 评级": "{0}  {1} pts  ·  {2} s  ·  rank {3}",
    "{0}  暂无": "{0}  —",
    "{0} · 累计 {1} 秒 · {2} 关": "{0} · total {1} s · level {2}",
    "{0} → {1}：去 {2}": "{0} → {1}: go to {2}",
    "{0} → 调查：{1}（{2}，可疑 {3}）": "{0} → investigate: {1} ({2}, suspicion {3})",
    "{0} → 追击：{1}（可疑 {2}）": "{0} → chase: {1} (suspicion {2})",
    "{0} 个哨兵正看着你": "{0} guards watching you",
    "{0} 人": "{0} down",
    "{0} 次": "{0}×",
    "{0} 秒": "{0} s",
    "{0}从你{1}贴上来抓住了你 —— {2}": "{0} closed in from {1} and caught you — {2}",
    "下一关 · {0}": "Next · {0}",
    "全图警觉 {0}s": "Global alert {0}s",
    "关卡不连通：{0} 个可走格从出生点不可达，例如 {1}":
        "Level is disconnected: {0} walkable tiles unreachable from spawn, e.g. {1}",
    "创于 {0}": "set {0}",
    "去查 ({0},{1})": "check ({0},{1})",
    "回车 潜入第 {0} 关": "ENTER — infiltrate level {0}",
    "在岗 {0}/{1}    追击/包抄 {2}": "On duty {0}/{1}    chasing/flanking {2}",
    "尸体已暴露 {0} 次": "Body found {0}×",
    "已通关 · {0}": "Cleared · {0}",
    "总闸 {0}/{1} · 靠近按 E": "Breaker {0}/{1} · press E nearby",
    "数据节点 {0}/{1}": "Nodes {0}/{1}",
    "断电 {0}s · 哨兵视野 ×{1}": "Blackout {0}s · guard vision ×{1}",
    "旧版 {0} 关": "Legacy level {0}",
    "最佳纪录  {0} 分 · {1} 秒 · {2} 评级": "Best  {0} pts · {1} s · rank {2}",
    "未通关 · {0}": "Not cleared · {0}",
    "本关新纪录 · {0} 分": "New level record · {0} pts",
    "本关最佳 {0} 分": "Level best {0} pts",
    "正式开局 · {0}": "Fresh run · {0}",
    "用时 {0}s    被发现 {1} 次": "Time {0}s    spotted {1}×",
    "第 {0} 关": "Level {0}",
    "第 {0} 关 · {1}": "Level {0} · {1}",
    "第 {0} 关 · {1}　{2}": "Level {0} · {1} {2}",
    "第 {0} 关完成": "Level {0} cleared",
    "第 {0} 道闸": "Checkpoint {0}",
    "第{0}关｜": "L{0} | ",
    "第{0}关｜{1}": "L{0} | {1}",
    "累计用时 {0} 秒    被发现 {1} 次    弹药 {2} · 石子 {3}":
        "Total {0} s    spotted {1}×    ammo {2} · stones {3}",
    "连续潜入 {0} 处设施，拿到数据，活着回来。":
        "Infiltrate {0} facilities, take the data, come back alive.",
    # 风格分明细行："全身而退  +120" / "零击倒  +80"
    "{0}  +{1}": "{0}  +{1}",
    # 被分隔符切开后的片段："A 评级" / "最佳纪录  1200 分"
    "{0} 评级": "rank {0}",
    "最佳纪录  {0} 分": "Best  {0} pts",
    "AI 决策流 · 最近 {0} 条": "AI decisions · last {0}",
    # 复盘页"本关发生"的计数（hud._review_counters 拼的 label + 数字）
    "被发现 {0}": "spotted {0}",
    "击倒 {0}": "downed {0}",
    "石子 {0}": "stone throws {0}",
    "开枪 {0}": "shots {0}",
    # 决策流的可疑度三行（enemy.py 里 push 时拼好）
    "起疑 {0}：开始留意异常": "curious {0}: something is off",
    "确认 {0}：他知道你在这": "confirmed {0}: he knows you are here",
    "跟丢 {0}：退回调查": "lost him {0}: back to investigate",
}

_RULES = [(_compile(tpl), en) for tpl, en in TEMPLATES.items()]
# 长的先匹配：'第 {0} 关 · {1}　{2}' 必须先于 '第 {0} 关'
_RULES.sort(key=lambda item: -len(item[0].pattern))


def reset_for_test() -> None:
    global RECORD
    set_lang(ZH)
    RECORD = None
