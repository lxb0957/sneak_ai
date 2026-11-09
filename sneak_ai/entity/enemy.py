"""敌人实体：行为树驱动的哨兵。

决策链路（每帧每个敌人走一遍）：
    感知 → 可疑度 → 行为树选行为 → 执行（流场 / A* / 巡逻）

行为树的优先级（Selector 从左到右）：
    1. engage       —— 已被协同分配器分配了角色（CHASE / FLANK / BLOCK）
    2. investigate  —— 起疑（suspicion >= 0.35）：去"最后已知位置"看看，到了环视一圈
    3. patrol       —— 什么都没发生，按巡逻点走

为什么用行为树而不是继续写 if/else：
- "调查"这种跨多帧的任务天然表达为 RUNNING，不需要额外状态变量；
- 想插入新行为（比如"呼叫增援""守门"）只需在 Selector 里加一个分支，
  不用改任何既有状态转移 —— 这就是 BT 相对 FSM 的可组合性。
"""

import math

from .. import constants as C
from ..ai.behavior_tree import (
    Action,
    Blackboard,
    Condition,
    Selector,
    Sequence,
    Status,
)
from ..ai.coordination import Role
from ..ai.search_plan import build_search_plan, find_hiding_spots
from ..pathing.astar import astar


class Enemy:
    def __init__(self, x: float, y: float, patrol: list = None, perception=None,
                 chase_speed: float = None,
                 alert_speed: float = None, alert_view: float = None):
        self.x = x
        self.y = y
        self.radius = 0.35
        self.speed = C.ENEMY_PATROL_SPEED
        # 追击速度由**关卡**给（`Level.chase`），不是全局常量 ——
        # 它是这三关唯一一条"越往后越凶"的曲线：第 1 关追兵跑不过你（3.4），
        # 第 2 关要你跑起来（4.0），第 3 关和你一样快（4.4，走路绝对甩不掉）。
        # 之前三关共用 3.0，而玩家走路是 4.4 —— 走就比追兵快 47%，
        # 被发现等于没被发现。难度曲线从"追兵速度"这一根轴上才立得起来。
        self.speed_chase = chase_speed if chase_speed is not None else C.ENEMY_CHASE_SPEED
        # 警报后的数值同样是**关卡**给的。理由和 speed_chase 一样，但方向相反：
        # 之前三关共用「8.5 视野 + 4.3 追击」，第 1 关路线最长（235 格）还要原路
        # 折返，等于教学关背了最重的惩罚 —— 12 相位只剩 3 胜，比后两关都难。
        # 现在第 1 关的警报只是"提醒你该走了"，末关才是真的封锁。
        self.alert_speed = (alert_speed if alert_speed is not None
                            else C.ALERT_SPEED_CHASE)
        self.alert_view = (alert_view if alert_view is not None
                           else C.ALERT_VIEW_RANGE)
        self.facing = 0.0
        self.patrol = patrol or []
        # 初始巡逻相位按坐标打散：否则所有哨兵开场同步从第 0 个点出发，
        # 整张图的巡逻节奏会像列队行进（玩家一眼就看穿，也不像"值班"）。
        self._patrol_idx = (
            int(abs(x) * 7 + abs(y) * 13) % len(self.patrol) if self.patrol else 0
        )
        # 上一次站岗的巡逻点下标。环视基准只在**换点**时更新 —— 见 _start_dwell。
        self._dwell_point = -1
        # 固定岗的"岗位朝向"（第 5 关起由关卡数据给）：None = 普通巡逻兵，
        # 朝向跟着走；有值 = 这个人守着一个点，回到岗位就该面朝来路。
        # 为什么必须单独存一份：环视基准取的是"我到点时朝哪儿"，而一个被骗
        # 走又走回来的岗，回到岗位时朝的是**他走回来的方向**（多半是斜着，
        # 甚至正对着你离开的那条走廊）。见 _start_dwell。
        self.post_facing = None

        self.perception = perception
        self.suspicion = 0.0       # 每个敌人独立的可疑度
        self.role = Role.PATROL    # 当前协同角色
        self.target_tile = None    # 协同分配的目标格 (tx, ty)
        self.alerted = False       # 全局警觉（拿数据后为 True）
        # 全局视野系数（黑灯期 <1），由 World 每帧下发。
        # 放在敌人身上而不是每次判定时去摸 world：渲染器、机器人、
        # 调试视图都在不同的地方问"这个哨兵现在能看多远"，
        # 让它们各自去乘一次系数，迟早会有一处忘了乘。
        self.vis_scale = 1.0

        # 本帧感知结果（供 HUD / 渲染 / 报警广播复用，避免重复 raycast）
        self.sees_player = False
        self.hears_player = False

        # 调查状态
        self.state_name = "patrol"
        # 决策流里的身份编号（1 起，由 World 建完敌人后统一编号）。
        # 不用 id() 或坐标：前者每次运行都不同、没法对着日志复盘，
        # 后者会随移动变化，同一个人在日志里会换好几个名字。
        self.tag = 0
        self.searching = False      # 是否处于"主动搜索"流程（跨多帧，由 BT 的 RUNNING 语义表达）
        self._last_seen = None      # 最后看到玩家的位置（记忆）
        self._investigate_time = 0.0
        self._look_timer = 0.0
        self._scan_dir = 1.0
        # GOAP 搜索计划：查哪几处、按什么顺序查，由 ai/search_plan.py 规划
        self._search_steps = None   # [("move"|"sweep", (tx, ty)), ...]
        self._search_i = 0
        self._search_origin = None  # 计划依据的最后已知位置（漂了要重规划）
        self._plan_saw = None       # 排计划时"看见过没有"，用来判断计划是否过期
        # 协同分配的搜索位：跟丢之后由 World 把几个哨兵的搜索点沿"他跑的方向"
        # 铺开，避免三个人扎堆搜同一片。None = 没被分工，用我自己的记忆点。
        self.search_slot = None
        self._plan_slot = None      # 排计划时用的是哪个搜索位（变了要重排）
        self._belief_heading = None # 从队友情报里接过来的"他往哪跑"
        # 本轮"起疑事件"里有没有真的看见过人。
        # 只有真看见过，才值得去翻角落；只是听到脚步，看到记忆点就够了 ——
        # 否则一次路过就能让半个走廊的哨兵离岗去搜箱子，玩家读不出规律。
        self._seen_this_episode = False
        # "正在查某个声音"的锁：石子落地后的一小段时间里，
        # 不拿玩家自己的脚步声去覆盖调查点（否则扔了石头也等于没扔）
        self._noise_lock = 0.0
        # 尸体警戒剩余时间：这段时间可疑度不掉回安全区，人会原地张望 ——
        # 但不**移动**。让他跑去看现场会把玩家唯一的路堵死（见 world._alert_body）
        self.body_alert = 0.0

        # 站岗环视（到巡逻点后停一下左右看）
        self._dwell = 0.0
        self._dwell_face = 0.0
        self._dwell_t = 0.0

        # A* 路径（调查 / 包抄 / 封路 / 巡逻用；追击用共享流场）
        self._path = None
        self._path_i = 0
        self._path_target = None
        self._path_failed_for = None   # 该目标已确认不可达，别每帧重算
        self._repath_cd = 0.0

        # 移动状态（渲染动画用"真的在动"，卡死看门狗用"想动"）
        self.moving = False
        self._intent_move = False
        self._stuck_t = 0.0
        self._stuck_x = x
        self._stuck_y = y
        self._stuck_n = 0

        # 行为树：黑板每帧刷新，树负责"选行为"
        self._bb = Blackboard()
        self._tree = self._build_tree()

        # ---- 生死（武器系统）----
        # 阵亡不是"移除"，而是"下线 5 秒再补位"：
        # 移除会让关卡越打越空，玩家只要子弹够就能清场；
        # 补位逼着玩家把开枪当成"买 5 秒时间"，而不是"消灭一个敌人"。
        self.alive = True
        self.dead_timer = 0.0        # 剩余复活时间（秒）
        self.grace = 0.0             # 重生后的"苏醒"延迟：这几秒不感知、不抓人
        self.home = (x, y)           # 原始驻地（复活点的兜底）
        self.respawn_at = None       # 预警环的位置（复活前一帧才有值）
        self.deaths = 0
        # 这具尸体有没有被同僚认出来（一具只报一次警，否则每次扫描都重刷一遍调查）
        self.body_found = False

    # ============ 对外属性 ============
    @property
    def pos(self) -> tuple:
        return (self.x, self.y)

    @property
    def state(self) -> str:
        """当前行为名（调试渲染 / HUD 用）。"""
        return self.state_name

    @property
    def icon(self) -> str:
        """头顶状态图标：! = 已发现，? = 起疑调查。"""
        if not self.alive:
            return ""
        if self.role != Role.PATROL:
            return "!"
        if self.state_name == "investigate" or self.suspicion >= C.SUSPICION_CURIOUS:
            return "?"
        return ""

    def view_range(self) -> float:
        """当前有效视野（警觉状态更远），供渲染和感知共用。

        `vis_scale` 是黑灯的全局系数（世界每帧下发）。它必须同时作用于
        **感知判定**和**屏幕上的视锥**：如果只改判定不改绘制，玩家会看到
        视锥照着自己却被抓；只改绘制不改判定，则是反过来 —— 两种都是
        "黑灯到底有没有用"完全无法判断的体验。
        """
        base = self.perception.view_range if self.perception else 0.0
        base = self.alert_view if self.alerted else base
        return base * self.vis_scale

    @property
    def dwelling(self) -> bool:
        """是否正在巡逻点站岗环视（此时朝向左扫，视野锥会摆）。"""
        return self._dwell > 0.0

    # ============ 决策流 ============
    def _enter_state(self, world, name: str) -> None:
        """切换行为名，并**只在真的改变时**记一条决策流。

        为什么不能每帧都记："我现在在巡逻"这句话玩家看屏幕就知道，
        真正需要解释的是"我从巡逻变成了调查，因为什么"。所以这里比较前后，
        没变就直接返回（顺带省掉一次字符串格式化）。

        注意记录发生在 `self.state_name` **改完之后**：trace 读到的永远是
        新状态，它不参与任何判定，也就不会因为阅读顺序反过来影响行为。
        """
        if name == self.state_name:
            return
        prev = self.state_name
        self.state_name = name
        trace = getattr(world, "trace", None)
        if trace is not None:
            trace.push("state", self._state_note(prev, name), who=self.tag,
                       frm=prev, to=name, susp=round(self.suspicion, 2))

    def _state_note(self, prev: str, name: str) -> str:
        """给人看的一句话。只在换状态时调用，所以可以放心地拼字符串。"""
        if name == "investigate":
            if self._last_seen is None:
                where = "原地张望"
            else:
                # 整格而不是一位小数：面板一行只有 430 像素，小数点是纯占位，
                # 而"他往哪走"在格级别上已经足够回答。
                where = f"去查 ({int(self._last_seen[0])},{int(self._last_seen[1])})"
            how = "看见过" if self._seen_this_episode else "听/被喊"
            return f"{prev} → 调查：{where}（{how}，可疑 {self.suspicion:.0%}）"
        if name == "chase":
            by = "还看得见" if self.sees_player else "按最后已知位置"
            return f"{prev} → 追击：{by}（可疑 {self.suspicion:.0%}）"
        if name in ("flank", "block"):
            return f"{prev} → {name}：去 {self.target_tile}"
        return f"{prev} → {name}"

    def _note_suspicion(self, world, prev: float) -> None:
        """可疑度跨过阈值时记一条。

        这是"他什么时候开始怀疑你"的唯一证据：状态机只在 0.35 / 0.75 两条线
        上改变主意，玩家看到的"哨兵突然回头"其实早在那之前就开始了。
        """
        trace = getattr(world, "trace", None)
        if trace is None:
            return
        now = self.suspicion
        if prev < C.SUSPICION_CURIOUS <= now:
            trace.push("suspicion", f"起疑 {now:.0%}：开始留意异常",
                       who=self.tag, at=round(now, 2))
        if prev < C.SUSPICION_CONFIRM <= now:
            trace.push("suspicion", f"确认 {now:.0%}：他知道你在这",
                       who=self.tag, at=round(now, 2))
        if prev >= C.SUSPICION_CONFIRM > now:
            trace.push("suspicion", f"跟丢 {now:.0%}：退回调查",
                       who=self.tag, at=round(now, 2))

    # ============ 行为树 ============
    def _build_tree(self) -> Selector:
        return Selector([
            Sequence([
                Condition("has_role", lambda bb: bb.get("role") != Role.PATROL),
                Action("engage", self._act_engage),
            ]),
            Sequence([
                Condition(
                    "curious",
                    lambda bb: bb.get("searching")
                    or bb.get("suspicion", 0.0) >= C.SUSPICION_CURIOUS,
                ),
                Action("investigate", self._act_investigate),
            ]),
            Action("patrol", self._act_patrol),
        ])

    def update(self, dt: float, world) -> None:
        # 阵亡：只走倒计时，不感知、不巡逻、不移动
        if not self.alive:
            self._tick_dead(dt, world)
            return

        if self.grace > 0.0:
            self.grace -= dt

        self._repath_cd -= dt
        self.moving = False
        self._intent_move = False
        self._update_senses(dt, world, world.player)

        bb = self._bb
        bb.set("role", self.role)
        bb.set("suspicion", self.suspicion)
        bb.set("searching", self.searching)
        bb.set("world", world)
        bb.set("dt", dt)
        self._tree.tick(bb)

        # 行为树跑完之后再看"我到底有没有挪窝"，任何行为都受这道兜底保护
        self._watchdog(dt)

    # ============ 生死 ============
    def kill(self) -> None:
        """被击中：立即倒下，开始复活倒计时。

        这里必须把**所有**跨帧状态清干净：角色、记忆、路径、调查计时。
        漏掉任何一项，复活后的哨兵会带着上一轮的追击目标直接奔向你 ——
        那不是"重生"，那是"墓碑定位打击"。
        """
        if not self.alive:
            return
        self.alive = False
        self.deaths += 1
        self.dead_timer = C.ENEMY_RESPAWN
        self.respawn_at = None
        self.body_found = False
        self.role = Role.PATROL
        self.target_tile = None
        self.suspicion = 0.0
        self.searching = False
        self.sees_player = False
        self.hears_player = False
        self._last_seen = None
        self._path = None
        self._path_target = None
        self._path_failed_for = None
        self._investigate_time = 0.0
        self._look_timer = 0.0
        self._dwell = 0.0
        self._noise_lock = 0.0
        self.body_alert = 0.0
        self._stuck_n = 0
        self._clear_search_plan()
        self._seen_this_episode = False
        self.moving = False
        self._intent_move = False
        self.state_name = "down"

    def _tick_dead(self, dt: float, world) -> None:
        self.moving = False
        self._intent_move = False
        self.dead_timer = max(0.0, self.dead_timer - dt)
        # 进入预警窗口：先把"我要在这冒出来"标出来，别让玩家被凭空出现吓到
        if self.respawn_at is None and self.dead_timer <= C.RESPAWN_WARN:
            self.respawn_at = world.pick_respawn_point(self)
        if self.dead_timer <= 0.0:
            self.respawn(world)

    def respawn(self, world) -> None:
        """满 5 秒：在预警点复活，并把状态重置成"刚上岗"。"""
        spot = self.respawn_at or self.home
        if world.grid.is_wall(int(spot[0]), int(spot[1])):
            spot = self.home
        self.x, self.y = spot
        self.alive = True
        self.respawn_at = None
        self.suspicion = 0.0
        self.role = Role.PATROL
        self.target_tile = None
        self.searching = False
        self._last_seen = None
        self._path = None
        self._path_target = None
        self._path_failed_for = None
        self._investigate_time = 0.0
        self._look_timer = 0.0
        self._dwell = 0.0
        self.state_name = "patrol"
        self.body_found = False
        self.body_alert = 0.0
        self._clear_search_plan()
        self._seen_this_episode = False
        # 复活点离玩家太近时给一段"苏醒"时间：否则等于在玩家脸上刷出一个
        # 立刻能抓人的哨兵，玩家连反应的机会都没有。
        p = world.player
        if p is not None and math.hypot(p.x - self.x, p.y - self.y) < C.RESPAWN_SAFE_DIST:
            self.grace = C.RESPAWN_GRACE
        world.events.append("respawn")

    def note_belief(self, pos, heading=None) -> None:
        """从队友的报警里接过一条情报：他刚才在这儿、往那边跑。

        为什么不覆盖"我自己看见过"的记忆：目击者的坐标比转述的更新，
        而谣言的时效性只由转过几手决定 —— 这一条不写清楚，就会出现
        "自己明明贴脸看见了，却被队友的旧坐标叫走"。

        **固定岗不接转述。** 这是给第 5 关留的适用条件，不是通用优化：
        那一关的动词是"让守着门的那个人走开" —— 岗哨的全部难度来自
        "他站在那儿，你只能骗他"。一旦允许他接队友的坐标，他就自己走了，
        这道门也就不存在了。实测（96 局）：L5 从 51.0% 掉到 32.3%，
        区间 [24,42] 与改动前的 [41,61] 不重叠 —— 不是噪声，是关卡语法被抵掉了。
        单人岗哨"只听得见自己的耳朵"，这一条同时也符合关卡数据里的语义
        （单点巡逻 = 站住 + 环视，`_start_dwell` 回岗时还要恢复岗位朝向）。
        """
        if pos is None or self.is_fixed_post:
            return
        if self._last_seen is None or not self._seen_this_episode:
            self._last_seen = (float(pos[0]), float(pos[1]))
            self._seen_this_episode = False
            if heading is not None:
                self._belief_heading = tuple(heading)

    @property
    def is_fixed_post(self) -> bool:
        """固定岗 = 只有一个巡逻点的哨兵。

        复用 `Enemy._act_patrol` 的退化行为（下一个点就是自己 → 站住 + 环视），
        而不是新加一个 `is_fixed` 字段：字段要在关卡数据里逐个标、还要和
        `post_facing` 保持同步，两处说法迟早会不一致。
        """
        return len(self.patrol) == 1

    # ============ 感知 ============
    def _update_senses(self, dt, world, player) -> None:
        self.sees_player = False
        self.hears_player = False
        # 这一帧的起点值：跨阈值要"从哪跨到哪"，只看新值判断不出是"刚起疑"
        # 还是"已经起疑三秒了"。
        prev_susp = self.suspicion
        self._noise_lock = max(0.0, self._noise_lock - dt)
        self.body_alert = max(0.0, self.body_alert - dt)
        # 刚重生的那一小段时间里闭眼：不是作弊，是给玩家"撤退"的机会
        if self.grace > 0.0:
            return
        p = self.perception
        if p is None or player is None:
            return

        # 视野随警觉状态变化：通过参数传入，不改共享 Perception 的字段
        see = p.can_see(
            self.pos, self.facing, player.pos, world.grid, view_range=self.view_range()
        )
        # 听觉要和视觉一样"过地图"：声音的实际路程由世界缓存的声音场给出，
        # 穿墙按 SOUND_WALL_COST 额外收费。没有它，隔三堵墙也听得见。
        hear = p.can_hear(
            self.pos, player.pos, player.noise, cost=world.sound_cost(self.pos)
        )
        self.sees_player = see > 0.0
        self.hears_player = hear > 0.0
        dist = math.hypot(player.x - self.x, player.y - self.y)

        stim = max(see, hear * 0.6)  # 听觉线索弱于视觉
        if stim > 0:
            self.suspicion = min(1.0, self.suspicion + stim * dt * C.SUSPICION_GAIN)
            # 看到 = 精确坐标；只听到 = 一个带偏差的猜测。
            # 之前听到脚步也直接写真实坐标，等于给 AI 开了"声呐透视"：
            # 明明隔着墙，它却能精确走到你站的那一格。听觉该给"大致方向"。
            if see > 0.0:
                self._last_seen = (player.x, player.y)
                self._seen_this_episode = True
                # 自己的眼睛优先：队友分的搜索位是"猜他在哪"，而我刚看见他。
                # 不清的话，我会先走向那个扇形点、再折回来 —— 玩家看到的就是
                # "他明明看见我了，却转身走了"。
                self.search_slot = None
                self._belief_heading = None
            elif hear >= 0.18 and self._noise_lock <= 0.0:
                guess = self._heard_point(player.pos, dist, world.grid)
                if guess is not None:
                    self._last_seen = guess
            if self.sees_player:
                self._investigate_time = 0.0
                self._look_timer = 0.0
        else:
            self.suspicion = max(0.0, self.suspicion - dt * C.SUSPICION_DECAY)

        # 尸体警戒期内托住可疑度：这就是"这片区域变敏感了"的具体含义。
        # 没有这条托底，目击者 3 秒后就把刚看见的尸体忘干净了，
        # 玩家也从"尸体已暴露"的提示里学不到任何东西。
        if self.body_alert > 0.0:
            self.suspicion = max(self.suspicion, C.SUSPICION_CURIOUS + 0.05)

        # 可疑度掉到"确认阈值"以下 = 跟丢了：退出协同追击，
        # 交给行为树的 investigate 分支去"最后已知位置"搜一遍。
        # 这样敌人不会一丢视线就瞬间遗忘（拟真），也不会永远全速追着你跑。
        if self.suspicion < C.SUSPICION_CONFIRM and self.role != Role.PATROL:
            self.role = Role.PATROL
            self.target_tile = None
            self._path = None
            self._path_target = None
            self._path_failed_for = None
            # 从"追"退到"查"是一个新的搜查行动：旧计划作废，进 investigate 时重排
            self._clear_search_plan()

        self._note_suspicion(world, prev_susp)

    # ============ 行为 1：已分配角色（协同追击） ============
    def _act_engage(self, bb) -> Status:
        world = bb.get("world")
        dt = bb.get("dt")
        if self.role == Role.CHASE:
            self._enter_state(world, "chase")
            target = None
            if self.sees_player and world.player is not None:
                target = (int(world.player.x), int(world.player.y))
            elif self._last_seen is not None:
                target = (int(self._last_seen[0]), int(self._last_seen[1]))
            elif self.target_tile is not None:
                target = self.target_tile
            if target is None:
                return Status.SUCCESS
            # 追击用共享流场（一次全图 BFS 供所有追击者复用）
            if self._move_with_flow(world, dt, target):
                return Status.SUCCESS
            self._move_toward(target[0] + 0.5, target[1] + 0.5,
                              self._chase_speed(), dt, world)
            return Status.SUCCESS

        # 包抄 / 封路：目标点分散在玩家周围与咽喉口，各自用 A* 过去
        self._enter_state(world, self.role.value)
        if self.target_tile is None:
            return Status.SUCCESS
        self._follow_path(self.target_tile, self._chase_speed() * 0.95, dt, world)
        return Status.SUCCESS

    # ============ 行为 2：调查（GOAP 规划出来的搜查路线） ============
    def _act_investigate(self, bb) -> Status:
        world = bb.get("world")
        dt = bb.get("dt")
        self._enter_state(world, "investigate")
        self.searching = True

        # 只要"正在查"，可疑度就保底在起疑阈值：
        # 否则会出现在前往调查点的路上、可疑度自然衰减到阈值以下、
        # 调查被半路打断（记忆还留着、人已经不查了）的怪现象。
        self.suspicion = max(self.suspicion, C.SUSPICION_CURIOUS)

        if self._last_seen is None:
            # 只有听觉线索、没有具体坐标：原地转头张望
            self._clear_search_plan()
            if self._scan(dt):
                self._give_up(world)
            return Status.SUCCESS

        self._investigate_time += dt
        if self._investigate_time > C.INVESTIGATE_GIVEUP:
            self._give_up(world)
            return Status.SUCCESS

        # 路线：查哪几处、先查哪个，由 GOAP 规划器按总脚程排（ai/search_plan.py）。
        # 这里只负责执行，以及"计划失效了"（记忆点漂走）就重排。
        if self._plan_is_stale():
            self._make_search_plan(world)
        if not self._search_steps:
            # 周围没有值得搜的点：退化成原地环视一圈
            if self._scan(dt):
                self._give_up(world)
            return Status.SUCCESS
        if self._search_i >= len(self._search_steps):
            self._give_up(world)   # 该查的都查过了，没找到人
            return Status.SUCCESS

        kind, tile = self._search_steps[self._search_i]
        if kind == "move":
            status = self._follow_path(
                tile, self.speed * C.SEARCH_SPEED_MUL, dt, world
            )
            if status == "arrived":
                self._search_i += 1      # 到点了，下一步是这个点的 sweep
            elif status == "unreachable":
                # 那个位置在墙里或不可达（听觉/石子线索猜出来的格子很常见）：
                # 连它后面跟着的"搜查"一起划掉，而不是站在原地对着墙环视
                self._drop_step_point(self._search_i)
        elif self._scan(dt, C.SEARCH_SWEEP_TIME):
            self._search_i += 1
        return Status.SUCCESS

    # ---- GOAP 搜索计划的辅助 ----
    def _clear_search_plan(self) -> None:
        self._search_steps = None
        self._search_i = 0
        self._search_origin = None
        self._plan_saw = None

    def _plan_is_stale(self) -> bool:
        """没排过计划、记忆点漂走、或"看见过/只听到"这件事变了 → 该重排。"""
        if not self._search_steps or self._search_origin is None:
            return True
        if self._plan_saw != self._seen_this_episode:
            return True
        # 被重新分了搜索位：旧计划走向的是别人已经负责的地方，必须重排
        if self._plan_slot != self.search_slot:
            return True
        return (
            math.hypot(
                self._last_seen[0] - self._search_origin[0],
                self._last_seen[1] - self._search_origin[1],
            )
            > C.SEARCH_REPLAN_DIST
        )

    def _make_search_plan(self, world) -> None:
        """找藏身点 + 排顺序。只在跟丢的那个瞬间跑一次，不进帧循环。

        没真看见过人的（只听到脚步 / 被队友喊了一声）只查记忆点本身：
        翻角落是有代价的（离岗、堵路），得由"差点抓到你"来换。
        """
        anchor = self.search_slot or self._last_seen
        spots = (
            find_hiding_spots(world.grid, anchor)
            if self._seen_this_episode
            else []
        )
        self._search_steps = build_search_plan(self.pos, anchor, spots)
        self._search_i = 0
        self._search_origin = self._last_seen
        self._plan_slot = self.search_slot
        self._plan_saw = self._seen_this_episode

    def _drop_step_point(self, idx: int) -> None:
        """划掉第 idx 步所在的那个点：它的 move 和紧随其后的 sweep 一起删。"""
        tile = self._search_steps[idx][1]
        j = idx + 1
        while j < len(self._search_steps) and self._search_steps[j][1] == tile:
            j += 1
        del self._search_steps[idx:j]

    def _heard_point(self, pos, dist: float, grid):
        """把"听到的脚步"换算成一个带偏差的坐标。

        误差随距离增大（近处能听出方位，远处只有个大概方向），
        偏移角度由哨兵自己的坐标决定 —— 同一只哨兵对同一个方向永远是
        同一种误判，不会出现每帧乱跳的"抽搐式搜索"。
        猜出来的格子如果在墙里，就换一个方向再猜；都不行就不更新记忆。
        """
        ang = math.sin(self.x * 12.9898 + self.y * 78.233) * 2 * math.pi
        err = min(1.4, 0.45 + 0.10 * dist)
        for k in (1.0, -1.0, 0.5):
            tx = pos[0] + math.cos(ang) * err * k
            ty = pos[1] + math.sin(ang) * err * k
            if not grid.is_wall(int(tx), int(ty)):
                return (tx, ty)
        return None

    def _scan(self, dt: float, duration: float = None) -> bool:
        """原地环视：左右摆动朝向，视野锥跟着扫过去。

        返回 True 表示这一轮看完了（调用方决定是"换下一个点"还是"放弃"）。
        duration 可传：主点认真看一圈（LOOK_AROUND_TIME），
        逐个藏身点看短一些（SEARCH_SWEEP_TIME），免得整套搜索拖十几秒。
        """
        span = C.LOOK_AROUND_TIME if duration is None else duration
        if self._look_timer <= 0.0:
            self._look_timer = span
        self._look_timer -= dt
        self.facing += self._scan_dir * dt * 2.4
        if self.facing > math.pi:
            self.facing -= 2 * math.pi
            self._scan_dir = -1.0
        elif self.facing < -math.pi:
            self.facing += 2 * math.pi
            self._scan_dir = 1.0
        return self._look_timer <= 0.0

    def _give_up(self, world) -> None:
        """搜查无果：清空记忆、把可疑度压到"起疑阈值"之下，回到巡逻。

        这条也要记进决策流：玩家最需要知道的是"他什么时候放弃的" ——
        那正是可以继续走的那一刻，而屏幕上只会看到视野锥转走了。
        """
        trace = getattr(world, "trace", None)
        if trace is not None:
            trace.push("giveup",
                       f"搜了 {self._investigate_time:.1f} 秒没找到，回巡逻",
                       who=self.tag, secs=round(self._investigate_time, 2))
        self.searching = False
        self._last_seen = None
        self._investigate_time = 0.0
        self._look_timer = 0.0
        self._path = None
        self._path_target = None
        self._path_failed_for = None
        self._clear_search_plan()
        self._seen_this_episode = False
        self.search_slot = None
        self._belief_heading = None
        self.suspicion = min(self.suspicion, C.SUSPICION_CURIOUS - 0.05)

    # ============ 行为 3：巡逻 ============
    def _act_patrol(self, bb) -> Status:
        world = bb.get("world")
        dt = bb.get("dt")
        self._enter_state(world, "patrol")
        if not self.patrol:
            return Status.SUCCESS

        # 到点站岗：停下、左右环视一会儿，再走向下一个点。
        # 为什么必须有这段：两个点之间直着来回平移的哨兵一眼就是程序，
        # 而且"停下环视"才是玩家真正能利用的时间窗（视野扫过去的瞬间溜过去）。
        if self._dwell > 0.0:
            self._dwell -= dt
            self._guard_scan(dt)
            if self._dwell <= 0.0:
                self._patrol_idx = (self._patrol_idx + 1) % len(self.patrol)
                self._path = None
                self._path_target = None
                self._path_failed_for = None
            return Status.SUCCESS

        tx, ty = self.patrol[self._patrol_idx]
        if math.hypot(tx - self.x, ty - self.y) < C.PATROL_ARRIVE_DIST:
            self._start_dwell()
            return Status.SUCCESS

        # 用 A* 走过去，而不是直线逼近 —— 直线一旦被货箱挡住，
        # 两个坐标轴同时吃碰撞，哨兵就会永远顶在墙上（这就是"卡墙"的真身）。
        if self._follow_path((int(tx), int(ty)), self.speed, dt, world) == "unreachable":
            self._skip_patrol_point()
        elif (int(tx), int(ty)) == (int(self.x), int(self.y)):
            self._start_dwell()
        return Status.SUCCESS

    def _start_dwell(self) -> None:
        """到达巡逻点：停表 + 记录基准朝向（环视从这里左右摆）。

        **基准只在"真的换了岗点"时更新。** 原来每次到点都无条件
        `_dwell_face = self.facing`，而这一刻 facing 正落在上一轮摆动的极值上，
        于是下一轮从这个偏移再摆一次 —— 基准每 1.6 秒单向漂 +40°。
        单点站岗的哨兵因此 14 秒内原地转了 231°，看上去像"慢慢转身巡视"，
        实际是个罗盘。多点巡逻看不出来（换点本来就该朝新方向），
        但第 5 关的固定岗是"守着这扇门、只在门前来回看"，基准一漂就直接破功。
        """
        self._dwell = C.PATROL_PAUSE
        if self.post_facing is not None and len(self.patrol) == 1:
            # 固定岗回岗：恢复"面朝来路"。
            #
            # 不这么做的话，基准取的是"我走回来的方向"：第 5 关实测，被骗去
            # 西北看一眼的岗走回来时朝东南 ~+45°，环视又在基准上摆 ±40°，
            # 于是他的视野锥正好盖住"你刚过去的那条走廊东侧"。玩家在 6.9 格
            # 外、本来在视野外 0.4 格，就因为这个朝向被揪住 —— 一局里 41% 的
            # 败局死在第二道闸前，全是因为岗回岗后朝东看了。
            # 关卡数据里那句 `math.pi = 朝西 = 朝来路` 必须是真的，否则
            # 玩家读到的"这扇门有人守着、他面朝你"就是一句假话。
            self.facing = self.post_facing
            self._dwell_face = self.post_facing
            self._dwell_point = self._patrol_idx
        elif self._dwell_point != self._patrol_idx:
            self._dwell_face = self.facing
            self._dwell_point = self._patrol_idx
        self._dwell_t = 0.0
        self._path = None
        self._path_target = None

    def _guard_scan(self, dt: float) -> None:
        """站岗环视：在基准朝向上做正弦摆动，视野锥跟着扫过去。"""
        self._dwell_t += dt
        self.facing = self._dwell_face + C.PATROL_SCAN_AMP * math.sin(self._dwell_t * 3.4)

    def _skip_patrol_point(self) -> None:
        """巡逻点不可达（被掩体封死 / 配置写进了墙里）：跳过，别顶墙。"""
        self._path = None
        self._path_target = None
        self._path_failed_for = None
        self._repath_cd = C.REPATH_INTERVAL
        self._patrol_idx = (self._patrol_idx + 1) % len(self.patrol)

    # ============ 移动原语 ============
    def _chase_speed(self) -> float:
        return self.alert_speed if self.alerted else self.speed_chase

    def _move_with_flow(self, world, dt: float, target) -> bool:
        """按共享流场朝下一格中心移动。成功返回 True。"""
        flow = getattr(world, "flow_field", None)
        if flow is None:
            return False
        d = flow.direction_at(int(self.x), int(self.y))
        if d is None:
            return False
        nx = int(self.x) + int(round(d[0]))
        ny = int(self.y) + int(round(d[1]))
        # 朝"下一格中心"移动，而不是沿纯方向向量，避免贴墙角卡住
        return self._move_toward(nx + 0.5, ny + 0.5, self._chase_speed(), dt, world)

    def _move_toward(self, tx: float, ty: float, speed: float, dt: float, world) -> bool:
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 0.02:
            return False
        dx, dy = dx / dist, dy / dist
        self.facing = math.atan2(dy, dx)
        self._intent_move = True
        self._move(dx, dy, speed, dt, world)
        return True

    def _follow_path(self, tile, speed: float, dt: float, world) -> str:
        """沿 A* 路径走到目标格。

        返回 "moving" / "arrived" / "unreachable" —— 调用方必须处理第三种，
        否则"目标在墙里"就会变成"顶着墙推到天荒地老"。
        """
        goal = (int(tile[0]), int(tile[1]))
        grid = world.grid
        if self._path_failed_for == goal:
            return "unreachable"

        if self._repath_cd <= 0.0 and (self._path_target != goal or self._path is None):
            self._path = astar(grid, (int(self.x), int(self.y)), goal)
            self._path_target = goal
            self._path_i = 1
            self._repath_cd = C.REPATH_INTERVAL
            if self._path is None:
                self._path_failed_for = goal
                return "unreachable"
        if self._path is None:
            return "unreachable"

        # 跳过已经到达的 waypoint
        while self._path_i < len(self._path):
            wx, wy = self._path[self._path_i]
            if (wx + 0.5 - self.x) ** 2 + (wy + 0.5 - self.y) ** 2 < 0.16:
                self._path_i += 1
            else:
                break
        if self._path_i >= len(self._path):
            return "arrived"

        wx, wy = self._path[self._path_i]
        cx, cy = int(self.x), int(self.y)

        # 斜向穿墙角：A* 的八方向允许"两个斜对格之间直接斜切"，
        # 但半径 0.35 的圆过不去两道墙夹出来的那个角（圆心刚好落在墙角上）。
        # 先走到还通的那一侧的正交格中心，再拐过去。
        if wx != cx and wy != cy:
            wall_x = grid.is_wall(wx, cy)
            wall_y = grid.is_wall(cx, wy)
            if wall_x and wall_y:
                self._path_failed_for = goal
                return "unreachable"
            if wall_x:
                self._move_toward(cx + 0.5, wy + 0.5, speed, dt, world)
                return "moving"
            if wall_y:
                self._move_toward(wx + 0.5, cy + 0.5, speed, dt, world)
                return "moving"

        self._move_toward(wx + 0.5, wy + 0.5, speed, dt, world)
        return "moving"

    def _move(self, dx: float, dy: float, speed: float, dt: float, world) -> None:
        ox, oy = self.x, self.y
        nx = self.x + dx * speed * dt
        ny = self.y + dy * speed * dt
        r = self.radius
        grid = world.grid
        if not self._collides(nx, self.y, r, grid):
            self.x = nx
        if not self._collides(self.x, ny, r, grid):
            self.y = ny
        if (self.x - ox) ** 2 + (self.y - oy) ** 2 > 1e-8:
            self.moving = True

    def _watchdog(self, dt: float) -> None:
        """卡死自愈：想动却没动 → 重算路径；连着三次没动 → 放弃当前目标。

        为什么需要这道兜底：贴墙姿态有无数种，几何修正永远会漏掉某一种，
        而一个顶着墙推 30 秒的哨兵会直接毁掉整张地图的可信度。
        宁可让它换个点巡逻，也不能让它站在墙边发抖。
        """
        self._stuck_t += dt
        if self._stuck_t < C.STUCK_CHECK_INTERVAL:
            return
        moved = math.hypot(self.x - self._stuck_x, self.y - self._stuck_y)
        self._stuck_t = 0.0
        self._stuck_x, self._stuck_y = self.x, self.y

        if not self._intent_move or moved > C.STUCK_EPS:
            self._stuck_n = 0
            return

        self._stuck_n += 1
        self._path = None
        self._path_target = None
        self._path_failed_for = None
        self._repath_cd = 0.0
        if self._stuck_n >= C.STUCK_GIVE_UP_TICKS:
            self._stuck_n = 0
            self._dwell = 0.0
            if self.patrol:
                self._patrol_idx = (self._patrol_idx + 1) % len(self.patrol)
            # 换 90° 再试：贴着墙推的朝向往往正好平行于墙面
            self.facing += math.pi / 2

    @staticmethod
    def _collides(x: float, y: float, r: float, grid) -> bool:
        """圆 vs 墙格碰撞：检查圆心周围可能相交的墙格。

        用圆心到墙格最近点的距离判定（而非四角包围盒），
        避免"贴墙但未真正碰到"时被误判为碰撞（卡墙角 bug）。

        扫描范围按半径算，不是固定 3×3：半径 0.3 格的实体，圆心在格内
        任意位置时最多只可能碰到 1~2 格宽的一条带，而 3×3 里有 5 格以上
        连"最近点距离 < r"都不可能满足 —— 白跑。这个函数每帧被调用 78 次
        （每只敌人每步最多 4 次），固定 9 格改成 1~4 格是这一帧里最便宜的优化。
        判定结果逐个坐标对拍过，与原 3×3 完全一致。
        """
        x0, x1 = math.floor(x - r), math.floor(x + r)
        y0, y1 = math.floor(y - r), math.floor(y + r)
        rr = r * r - 1e-6
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                if not grid.is_wall(tx, ty):
                    continue
                closest_x = max(tx, min(x, tx + 1))
                closest_y = max(ty, min(y, ty + 1))
                dx = x - closest_x
                dy = y - closest_y
                # 留 1e-6 容差：圆心恰好贴在墙格角点时距离为 0，
                # 严格比较会把它判成"在墙里"，导致实体被永久钉死。
                if dx * dx + dy * dy < rr:
                    return True
        return False
