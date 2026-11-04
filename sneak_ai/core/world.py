"""世界：持有地图、玩家、敌人列表，驱动每帧更新。

关卡数据（地图 / 巡逻 / 补给 / 门 / 目标点）从 W17 起搬到 `core/levels.py`：
`World` 只关心"这一帧模拟成什么样"，不关心"这一关长什么样"。
下面那层兼容包装是给十几个仍在 `import build_demo_map` 的脚本用的，
新代码请直接用 `levels.LEVELS`。
"""

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List

from .. import constants as C
from ..ai.coordination import Role
from ..entity.stone import Stone, plan_landing
from ..perception.sound import SoundField
from .grid import GridMap, WALL
from .levels import (DOORS, LEVELS, PICKUPS, level_pickups, spawn_and_patrols)
from .records import WHEN_FMT


# ---- 兼容层：第 1 关的老名字 ----
# 这些名字被 14 个测试脚本和 4 个审计脚本引用着。改名一次要动 18 个文件、
# 每个都得重新跑一遍才知道有没有写错，收益为零 —— 留着，只加注释。
def build_demo_map() -> GridMap:
    """第 1 关（哨站）的地图。等价于 `levels.LEVELS[0].build()`。"""
    return LEVELS[0].build()


def build_spawn_and_patrols(grid=None):
    """第 1 关的出生点与巡逻配置。"""
    return spawn_and_patrols(LEVELS[0], grid)


def build_pickups(grid=None):
    """第 1 关的补给点。"""
    return level_pickups(LEVELS[0], grid)


@dataclass
class World:
    grid: GridMap
    # 关卡身份：一局（World 实例）只对应一关。目标点、评级基准都随关卡走，
    # 不再读 constants 里的全局常量 —— 否则第二关的终端会画在第一关的坐标上。
    level_index: int = 0
    level_name: str = ""
    # 数据节点**是一组**：第 1、2 关各 1 个，第 3 关 2 个。
    # 世界只认"这一组节点"，不认"那个终端" —— 加节点是关卡数据的事，
    # 判定逻辑不该为每一关改一次。
    terminals: tuple = (C.DATA_TERMINAL,)
    exfil: tuple = C.EXFIL_POINT
    par: tuple = (C.RANK_S, C.RANK_A, C.RANK_B)
    # 警报时长也按关卡给（`Level.alert_duration`）。第 1 关是教学关，
    # 封锁短一点、给玩家"等一等就能走"的余地；末关拿第一个节点就响警报，
    # 封锁期要足够长，才逼得出"后半段在警报下跑完"这段节奏。
    alert_duration: float = C.ALERT_DURATION
    player: object = None
    enemies: List[object] = field(default_factory=list)
    coordinator: object = None  # 协同分配器
    flow_field: object = None  # 当前共享的流场（追击玩家时构建）
    _flow_repel: float = 0.0  # 流场重建节流

    stones: List[object] = field(default_factory=list)  # 飞行中的石子
    pings: List[dict] = field(default_factory=list)     # 噪音可视化（落点声波）
    shots: List[dict] = field(default_factory=list)     # 弹道曳光（短命特效）
    hit_marks: List[dict] = field(default_factory=list)  # 命中记号（打中才有的瞬时反馈）
    pickups: List[dict] = field(default_factory=list)   # 沿途补给（弹药 / 石子 / 护盾）

    # ---- 总闸 / 黑灯（第 4 关起）----
    # 世界只认"一组闸"，和 terminals 一样：加闸是关卡数据的事，
    # 判定逻辑不该为"这关有几个闸"改一次。
    # 每项 = {"x", "y", "used"}；used 之后闸就废了 —— 限次是为了让它成为
    # **一个决定**（什么时候用），而不是一个"每 14 秒按一下"的循环技能。
    power: List[dict] = field(default_factory=list)
    blackout_left: float = 0.0      # 黑灯剩余时间
    blackout_duration: float = 0.0
    blackout_view: float = 1.0      # 黑灯期敌人视野系数
    blackout_noise: float = 0.0     # 拉闸噪音半径

    # ---- 游戏状态 ----
    state: str = C.STATE_INFILTRATE
    alert: bool = False        # 警觉状态（拿数据后为 True，敌人更强）
    alert_left: float = 0.0    # 警觉封锁剩余时间（秒）
    last_known: tuple = None   # 全队最后确知玩家位置（流场目标，不用真实坐标）
    last_known_age: float = 0.0  # 距上次确知过了多久（秒）
    has_data: bool = False     # 是否已取得数据
    taken: List[int] = field(default_factory=list)  # 已取的数据节点下标（按取得顺序）
    elapsed: float = 0.0       # 本局用时（秒）
    captured: bool = False     # 是否被抓住
    # 抓住你的那个哨兵（结算页用它画“谁抓的 + 从哪来”）：
    # 没有它，失败画面只是一句“任务失败”，玩家学不到任何东西。
    captured_by: object = None
    _alert_timer: float = 0.0  # 警觉触发后的短暂提示计时

    # ---- 反馈 / 统计 ----
    threat: float = 0.0        # 全场最高可疑度（HUD 暴露条）
    seeing: int = 0            # 当前正看着玩家的敌人数
    engaged: int = 0           # 已参与追击/包抄/封路的敌人数
    spotted: int = 0           # 本局被发现次数（每次"进入追击"计一次）
    events: List[str] = field(default_factory=list)  # 本帧事件（给音效用）
    kills: int = 0             # 击倒次数（含重复击倒同一人）
    bodies_found: int = 0      # 尸体被目击的次数（直接影响"开枪划不划算"）

    # ---- 风格统计（只影响风格分，不影响玩法判定）----
    # 为什么这些要单独计数而不是从别的字段推：
    # "扔了 3 次石子"和"真的引开了 3 个人"是两件事 —— 往空地扔石子没人听见，
    # 前者能刷分、后者不能。所以必须记 _emit_noise 真正惊动的人次。
    decoy_hits: int = 0        # 被石子真的引去调查的人次
    shield_kills: int = 0      # 靠护盾反杀的次数
    peak_threat: float = 0.0   # 本局暴露度峰值（判"全身而退"）
    spots_by_enemy: Dict[str, int] = field(default_factory=dict)  # 每个哨兵的发现次数

    _body_scan_cd: float = 0.0  # 尸体扫描节流
    _chasing_flag: bool = False
    _alarm_cd: float = 0.0     # 报警广播节流
    _engaged_sig: tuple = ()   # 参与者集合签名（变化时强制重算角色）

    # ---- 声音传播 ----
    # 以玩家所在格为源的代价场。玩家每跨一格才需要重建，而一帧里
    # 18 个哨兵都要查一次 —— 所以必须缓存，不能"每个敌人建一个场"。
    _sound_field: object = None
    _sound_tile: tuple = None
    sound_builds: int = 0      # 累计建场次数（测试用它证明"没有逐帧重算"）

    @property
    def terminal(self) -> tuple:
        """第一个数据节点。老脚本只认单目标，保留这个别名（只读）。"""
        return self.terminals[0]

    def remaining(self) -> list:
        """还没拿到的数据节点：[(下标, 坐标), ...]，顺序即关卡建议顺序。"""
        return [(i, t) for i, t in enumerate(self.terminals) if i not in self.taken]

    # ---- 总闸 ----
    @property
    def in_blackout(self) -> bool:
        return self.blackout_left > 0.0

    @property
    def visibility_scale(self) -> float:
        """敌人视野的全局系数（黑灯期 <1）。敌人的 `view_range` 乘上它。

        为什么是"乘视野"而不是"调暗屏幕就当看不见"：光影是表现，
        视线是规则。只有把系数砸进 `can_see` 的入参，黑灯才真的改变玩法；
        否则玩家只会看到屏幕变暗，然后被发现时一脸问号。
        """
        return self.blackout_view if self.blackout_left > 0.0 else 1.0

    def nearby_power(self, radius: float = 1.8):
        """玩家脚下能按到的闸（下标），没有就是 None。

        已经黑灯时不再返回：防止玩家在 14 秒里把两个闸连着按光 ——
        黑灯期按闸没有意义，让它直接"不可交互"比报错更清楚。
        """
        if self.player is None or self.in_blackout:
            return None
        for i, node in enumerate(self.power):
            if node.get("used"):
                continue
            if math.hypot(node["x"] - self.player.x,
                          node["y"] - self.player.y) <= radius:
                return i
        return None

    def activate_power(self) -> str:
        """拉闸。返回 "ok" / "used" / "far" / "blackout"，给 HUD 和测试用。

        三个动作绑在一起，缺一个都会退化成"免费技能"：
        1. 开黑灯（收益）—— 全图敌人视野 ×0.45，14 秒；
        2. 拉响噪音（代价）—— 半径 16 格，把这间屋子附近的人都叫过来；
        3. 闸烧掉（限次）—— 这一关就一次。
        """
        if self.in_blackout:
            return "blackout"
        i = self.nearby_power()
        if i is None:
            return "used" if any(n.get("used") for n in self.power) else "far"
        node = self.power[i]
        node["used"] = True
        self.blackout_left = self.blackout_duration
        # 噪音只抬到"起疑"，不越过确认阈值 —— 拉闸是"他们在查"，不是"他们看见你了"。
        self._emit_noise(node["x"], node["y"], self.blackout_noise,
                         C.SUSPICION_CURIOUS + 0.05, kind="power")
        self.events.append("power_cut")
        return "ok"

    def _update_blackout(self, dt: float) -> None:
        if self.blackout_left <= 0.0:
            return
        self.blackout_left = max(0.0, self.blackout_left - dt)
        if self.blackout_left <= 0.0:
            self.events.append("power_back")

    def update(self, dt: float) -> None:
        # 结算状态：冻结世界更新，等待玩家操作
        if self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return

        self.elapsed += dt
        self._alarm_cd = max(0.0, self._alarm_cd - dt)
        self._update_alert(dt)
        self._update_blackout(dt)
        self.events.clear()  # 每帧清空，主循环消费后即丢弃

        if self.player is not None:
            self.player.update(dt, self)
            self._update_stones(dt)
            self._update_pickups()
            self._update_pings(dt)
        self._update_shots(dt)
        self._update_hit_marks(dt)

        # 玩法判定：接触数据终端 → 进入逃脱；接触撤离点 → 胜利
        if self.player is not None:
            self._check_objectives()
        # 警觉提示计时递减
        if self._alert_timer > 0:
            self._alert_timer -= dt

        # 协同角色分配：只给"已参与"的敌人分配（不是全图 18 个一起追）
        self._assign_roles()
        # 目击者向附近队友报警（局部广播，而非全图开图）
        self._maybe_broadcast()
        # 尸体目击：谁看见同僚倒下，谁就去拉警报
        self._check_bodies(dt)

        # 更新流场：有敌人在追击时，每 0.5s 重建一次（玩家 4格/s，0.5s 仅动 2 格，
        # 方向场无需更频繁重建，这是性能关键）
        #
        # 关键修正：流场的目标必须是「全队最后已知位置」，而不是玩家的真实坐标。
        # 之前直接拿 player.pos 建场，等于给所有追兵开了透视 ——
        # 只要你被看见过一次，即使已经绕到墙后，追兵依然精确知道你在哪，
        # 追击永远甩不掉（实测机器人被一路追着跑 60 秒不脱身）。
        self._flow_repel -= dt
        if self._flow_repel <= 0:
            self._flow_repel = 0.5
            if self.player is not None and self.engaged > 0 and self.last_known is not None:
                from ..pathing.flowfield import FlowField
                self.flow_field = FlowField(
                    self.grid, (int(self.last_known[0]), int(self.last_known[1]))
                )
            else:
                self.flow_field = None

        for e in self.enemies:
            e.vis_scale = self.visibility_scale   # 黑灯系数，渲染与判定共用
            e.update(dt, self)

        # 「最后已知位置」必须在敌人刷新完感知之后再更新：
        # 放在敌人更新之前，读到的是上一帧的 sees_player，
        # 玩家瞬移/绕墙后仍会被记成"刚被看见"，等于延迟一帧的透视。
        #
        # 注意：这里曾经留着第二遍 `e.update(dt, self)`，等于敌人每帧跑两步 ——
        # 哨兵实际移速是配置的 2 倍（2.2 格/s 变成 4.4，比玩家走路还快），
        # 可疑度也是双倍速上涨。肉眼看就是"哨兵像开了加速外挂"，
        # 而这种 bug 不会让任何测试变红，只会让手感和数值表对不上。
        if self.player is not None:
            if any(e.sees_player for e in self.enemies):
                self.last_known = (self.player.x, self.player.y)
                self.last_known_age = 0.0
            else:
                self.last_known_age += dt

        # 捕获判定必须放在敌人刷新完感知**之后**：
        # 它要读"这个哨兵知不知道你在附近"（sees_player / suspicion），
        # 放在前面读到的是上一帧的感知 —— 玩家贴到哨兵身上时，第一帧永远抓不到。
        if self.player is not None and not self.captured:
            self._check_capture()

        self._aggregate_threat()

    # ---- 协同 ----
    def _assign_roles(self) -> None:
        """给"已经参与"的敌人分配协同角色。

        "参与" = 已确认发现（suspicion >= 阈值）或还挂着上一轮角色。
        没参与的哨兵继续保持巡逻 —— 这是关键：一个敌人看到你，
        不应该让全图 18 个敌人同时精确知道你在哪。
        """
        if self.player is None:
            self.engaged = 0
            return

        engaged = [
            e for e in self.enemies
            if e.alive and (e.role != Role.PATROL
                            or e.suspicion >= C.SUSPICION_CONFIRM)
        ]
        self.engaged = len(engaged)
        if not engaged:
            self._engaged_sig = ()
            return

        px, py = int(self.player.x), int(self.player.y)
        if self.coordinator is None:
            for e in engaged:
                e.role = Role.CHASE
                e.target_tile = (px, py)
            return

        # 参与人数变了就立刻重算，否则「第 19 个敌人」要等 0.3s 节流才拿到角色
        sig = tuple(sorted(id(e) for e in engaged))
        if sig != self._engaged_sig:
            self._engaged_sig = sig
            self.coordinator.invalidate()

        self.coordinator.assign(engaged, self.player.pos, self.grid, now=self.elapsed)
        for e in engaged:
            # 缓存里没有这个敌人（刚参与、还没轮到重算）就先保持原角色
            item = self.coordinator.assignments.get(id(e))
            if item is not None:
                e.role, e.target_tile = item

    def _maybe_broadcast(self) -> None:
        """目击者向 RADIO_RANGE 内的队友喊一声：附近哨兵会"过去看看"。

        注意只把可疑度抬到"起疑"级别（<确认阈值），
        所以队友是**调查**而不是瞬间精确追击 —— 拟真且难以被玩家察觉规律。
        """
        if self._alarm_cd > 0.0 or self.player is None:
            return
        witnesses = [
            e for e in self.enemies
            if e.alive and e.sees_player and e.suspicion >= C.SUSPICION_CONFIRM
        ]
        if not witnesses:
            return
        self._alarm_cd = 1.5
        fired = False
        for e in self.enemies:
            if not e.alive:
                continue
            if e.suspicion >= C.SUSPICION_CURIOUS:
                continue
            for w in witnesses:
                if (e.x - w.x) ** 2 + (e.y - w.y) ** 2 <= C.RADIO_RANGE ** 2:
                    e.suspicion = C.SUSPICION_CURIOUS + 0.05
                    e._last_seen = (self.player.x, self.player.y)
                    fired = True
                    break
        if fired:
            self.events.append("radio")

    # ---- 尸体目击：让"开枪"的代价可以被延迟结算 ----
    def _check_bodies(self, dt: float) -> None:
        """看见同僚的尸体 → 拉警报。

        这是开枪的第二种代价。枪声只说明"有人开了枪"（还会被定位误差糊掉），
        而尸体是**物证**：谁看见谁就明白"自己人被干掉了"，这块区域立刻被搜一遍。

        时机上故意卡得很紧 —— 尸体只存在 ENEMY_RESPAWN（5 秒），
        所以这条规则只在"当着别人的面开枪"时才触发。
        躲进空房间开枪依旧安全：这不是随机惩罚，是给玩家的判断题。
        """
        self._body_scan_cd -= dt
        if self._body_scan_cd > 0.0:
            return
        self._body_scan_cd = C.BODY_SCAN_INTERVAL
        if self.player is None:
            return

        corpses = [e for e in self.enemies if not e.alive and not e.body_found]
        if not corpses:
            return

        for corpse in corpses:
            finder = None
            for e in self.enemies:
                if not e.alive or e.grace > 0.0 or e.perception is None:
                    continue
                if math.hypot(e.x - corpse.x, e.y - corpse.y) > C.BODY_SPOT_DIST:
                    continue
                if e.perception.can_see(
                    e.pos, e.facing, (corpse.x, corpse.y), self.grid,
                    view_range=e.view_range(),
                ) > 0.0:
                    finder = e
                    break
            if finder is None:
                continue

            corpse.body_found = True
            self.bodies_found += 1
            self.events.append("body_found")
            # 红色扩散环：玩家必须能看见"我这一枪被记账了"，否则只会觉得莫名其妙被围
            self.pings.append({
                "x": corpse.x, "y": corpse.y, "r": C.BODY_SPOT_DIST,
                "age": 0.0, "life": C.BODY_PING_LIFE, "kind": "body",
            })
            self._alert_body(finder, corpse)

    def _alert_body(self, finder, corpse) -> None:
        """目击者拉警报：他和附近的队友都进入一段"警戒期"。

        刻意**不让他们跑向尸体**（这是踩过坑之后改的）：
        玩家杀人的位置几乎总是一道门，让哨兵走过去等于自动堵死唯一的路，
        带枪玩法直接变成必输。改成"原地警戒 + 可疑度抬到起疑"之后，
        惩罚从"堵你的路"变成"这片区域短时间内很敏感" ——
        玩家能读出来（暴露条变红、头顶 ? 亮起），也能绕开，但不能再赖在这里。

        同样刻意**不给确认级可疑度**：发现尸体不等于发现入侵者。
        """
        rr = C.BODY_RADIO_RANGE ** 2
        for e in self.enemies:
            if not e.alive:
                continue
            if e is not finder:
                near_finder = (e.x - finder.x) ** 2 + (e.y - finder.y) ** 2 <= rr
                near_body = (e.x - corpse.x) ** 2 + (e.y - corpse.y) ** 2 <= rr
                if not (near_finder or near_body):
                    continue
            # 已经在追真人的，不该被一具尸体打断
            if e.suspicion >= C.SUSPICION_CONFIRM:
                continue
            if e is finder:
                e.suspicion = max(e.suspicion, C.BODY_SPOT_SUSPICION)
                e.body_alert = C.BODY_ALERT_TIME
            else:
                e.suspicion = max(e.suspicion, C.SUSPICION_CURIOUS + 0.04)
                e.body_alert = max(e.body_alert, C.BODY_ALLY_ALERT)
            # 警戒时会给一个短促的"转身张望"：不是走来走去，就是原地转头
            e._look_timer = 0.0
            e._scan_dir = 1.0 if e.x >= corpse.x else -1.0

    def _update_hit_marks(self, dt: float) -> None:
        for h in self.hit_marks:
            h["age"] += dt
        self.hit_marks = [h for h in self.hit_marks if h["age"] < h["life"]]

    # ---- 沿途补给 ----
    def _update_pickups(self) -> None:
        """玩家走到补给点上就自动拾取（走过去就够，不要求按键）。

        不给"按住 E 拾取"这类操作：潜行游戏里玩家的注意力全在视锥上，
        多一个按键只会让它变成"忘了按所以没拿到"。
        """
        p = self.player
        r2 = C.PICKUP_RADIUS ** 2
        for it in self.pickups:
            if it["taken"]:
                continue
            if (it["x"] - p.x) ** 2 + (it["y"] - p.y) ** 2 > r2:
                continue
            kind = it["kind"]
            if kind == "ammo":
                if p.ammo >= C.AMMO_CAP:
                    continue          # 满了就不浪费：留着路上可能需要
                p.ammo = min(C.AMMO_CAP, p.ammo + C.PICKUP_AMMO)
                self.events.append("pickup_ammo")
            elif kind == "stone":
                if p.stones >= C.STONE_CAP:
                    continue
                p.stones = min(C.STONE_CAP, p.stones + C.PICKUP_STONE)
                self.events.append("pickup_stone")
            elif kind == "shield":
                if p.shield >= C.SHIELD_CAP:
                    continue
                p.shield += 1
                self.events.append("pickup_shield")
            else:
                continue
            it["taken"] = True
            self.pings.append({
                "x": it["x"], "y": it["y"], "r": 2.2, "age": 0.0,
                "life": C.PICKUP_PING_LIFE, "kind": "pickup_" + kind,
            })

    def _apply_shield_break(self, e) -> None:
        """护盾挡下一次抓捕：贴上来的人当场倒下，玩家活着但暴露了。

        为什么是"反杀"而不是"弹开"：弹开的话玩家会站在原地和哨兵贴脸，
        下一帧再被抓一次，护盾等于没有。而反杀把那个人从场上拿掉 5 秒
        （走正常重生流程），玩家才有"立刻撤离"的机会窗口 ——
        护盾买的是时间，不是无敌。

        代价和开枪完全一致：记一次击倒（扣分）、留下一具可能被目击的尸体、
        动静会惊动附近的人。想拿 S 档（零击倒）就不能靠它。
        """
        p = self.player
        p.shield = max(0, p.shield - 1)
        e.kill()
        self.kills += 1
        self.shield_kills += 1
        # 动静：比枪声小，但绝不是无声（否则护盾就成了零成本的免死金牌）
        self._emit_noise(p.x, p.y, C.SHIELD_NOISE_RADIUS, C.SHIELD_SUSPICION,
                         jitter=C.WEAPON_NOISE_JITTER)
        self.events.append("shield_break")

    # ---- 手枪：hitscan 射击 ----
    def aim_target(self, target):
        """把"鼠标点在世界里的位置"翻译成"玩家其实想打的那个人"。

        为什么需要这一步（这是玩家反馈"瞄着人打却一直打不死"的根因）：
        角色的命中判定点是**脚底** (e.x, e.y)，但精灵是脚底对齐往上画的
        （48px 高 ≈ 1.5 格，身体中心在脚底上方 0.7 格）。
        于是玩家瞄准眼睛看到的那个人 → 实际射线从它头顶飞过去 → 打在背后的墙上。
        判定和画面不一致，玩家只会得到一个结论："这枪是坏的"。

        所以点击先做一次"身份判定"：落在身体轮廓里就吸附到本体中心，
        然后照常走射线。**只吸附身份，不豁免遮挡** —— 隔着墙点人依然打不中，
        因为 _los 会否掉。
        """
        if self.player is None:
            return None
        px, py = self.player.pos
        best, best_d = None, 1e9
        for e in self.enemies:
            if not e.alive or e.grace > 0.0:
                continue
            if math.hypot(e.x - px, e.y - py) > C.WEAPON_RANGE:
                continue
            bx, by = e.x, e.y - C.WEAPON_AIM_OFFSET
            d = math.hypot(target[0] - bx, target[1] - by)
            if d > C.WEAPON_AIM_SNAP or d >= best_d:
                continue
            if not self._los(px, py, e.x, e.y):
                continue
            best, best_d = e, d
        return best

    def _los(self, x0, y0, x1, y1) -> bool:
        """两点之间有没有墙挡着（步长与 _trace_shot 一致，结论不会打架）。"""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return True
        dx, dy = dx / length, dy / length
        t = 0.0
        while t < length:
            t += 0.08
            if self.grid.is_wall(int(x0 + dx * t), int(y0 + dy * t)):
                return False
        return True

    def shoot(self, target) -> str:
        """朝 target（世界坐标）开一枪。返回结果字符串，供音效/测试使用：

        "wall" / "enemy" / "max"（没打中任何东西） / "empty" / "idle"

        为什么用 hitscan 而不是子弹实体：
        这是"消音手枪"，子弹速度远快于实体移动，做实体只会引入
        "子弹追不上贴墙的哨兵"这种莫名其妙的判定；射线求交既准又便宜。
        """
        if self.player is None or self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return "idle"
        if not self.player.can_fire():
            self.events.append("gun_empty")
            return "empty"

        # 先认人再打枪：点中的是"那个人"，不是"那一格地面"
        snapped = self.aim_target(target)
        if snapped is not None:
            target = (snapped.x, snapped.y)

        ox, oy = self.player.pos
        dx, dy = target[0] - ox, target[1] - oy
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            dx, dy, dist = 1.0, 0.0, 1.0
        dx, dy = dx / dist, dy / dist

        # 朝向跟着枪口走：开枪时人是朝着那个方向的
        self.player.facing = math.atan2(dy, dx)
        self.player.consume_ammo()

        kind, hx, hy, hit_enemy = self._trace_shot(ox, oy, dx, dy, C.WEAPON_RANGE)

        self.shots.append({
            "x0": ox, "y0": oy, "x1": hx, "y1": hy,
            "age": 0.0, "life": C.WEAPON_TRACER_LIFE, "kind": kind,
        })
        self.events.append("shot")

        if hit_enemy is not None:
            # 命中记号记在**身体中心**而不是脚底：玩家盯着的是"人"，
            # 反馈画在脚下会让人以为打中的是地板。
            self.hit_marks.append({
                "x": hit_enemy.x,
                "y": hit_enemy.y - C.WEAPON_AIM_OFFSET,
                "age": 0.0,
                "life": C.HITMARK_LIFE,
            })
            hit_enemy.kill()
            self.kills += 1
            self.events.append("hit")

        # 枪声比石子响一个数量级，而且方向明确（误差 0.35 格）：
        # 这是玩家为"清掉一个门卫"付的价，不是免费的。
        # 注意仍然只抬到"起疑"级别 —— 听到不等于看到，
        # 否则一枪就等于给自己画了个全图可见的红点。
        self._emit_noise(
            ox, oy, C.WEAPON_NOISE_RADIUS, C.WEAPON_SUSPICION,
            "shot", jitter=C.WEAPON_NOISE_JITTER,
        )
        return kind

    def _trace_shot(self, ox, oy, dx, dy, max_len):
        """沿射线步进：先撞墙就停，先碰到活着的敌人就命中。

        步长 0.08 格 ≈ 2.5 像素，比最薄的墙（1 格）细得多，
        不会出现"穿墙打人"；命中半径用 WEAPON_SPREAD 略放宽，
        避免玩家在第 30 帧才看到哨兵拐角、鼠标还差两像素就判空。
        """
        step = 0.08
        t = 0.0
        spread2 = C.WEAPON_SPREAD ** 2
        while t < max_len:
            t = min(max_len, t + step)
            x, y = ox + dx * t, oy + dy * t
            if self.grid.is_wall(int(x), int(y)):
                return ("wall", x - dx * step, y - dy * step, None)
            for e in self.enemies:
                if not e.alive or e.grace > 0.0:
                    continue
                if (e.x - x) ** 2 + (e.y - y) ** 2 <= spread2:
                    return ("enemy", x, y, e)
        x, y = ox + dx * max_len, oy + dy * max_len
        return ("max", x, y, None)

    def _update_shots(self, dt: float) -> None:
        for s in self.shots:
            s["age"] += dt
        self.shots = [s for s in self.shots if s["age"] < s["life"]]

    # ---- 敌人重生 ----
    def pick_respawn_point(self, enemy) -> tuple:
        """给即将复活的哨兵挑一个落点：**自己的巡逻网里离玩家最远的那一个**。

        为什么不原地复活：原地重生等于在玩家刚走过的地方刷人，
        玩家没有任何手段预期，只能被随机背刺。
        为什么不是全图最远点：哨兵应该回到"自己的岗位"，
        被击倒的是 G4 的人，就不该从 G7 冒出来。
        """
        cands = [enemy.home] + [p for p in enemy.patrol if p != enemy.home]
        cands = [c for c in cands if not self.grid.is_wall(int(c[0]), int(c[1]))]
        if not cands:
            return enemy.home
        px, py = (self.player.x, self.player.y) if self.player else (-1e9, -1e9)
        return max(cands, key=lambda c: (c[0] - px) ** 2 + (c[1] - py) ** 2)

    # ---- 投石诱敌 ----
    def throw_stone(self, target=None) -> bool:
        """扔一颗石子。target 为空时朝角色朝向扔（Q），否则朝目标点扔（鼠标右键）。

        返回是否真的扔出去了（没石子 / 冷却中 / 已经结算 → False）。
        """
        if self.player is None or self.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return False
        if not self.player.can_throw():
            self.events.append("stone_empty")
            return False

        landing = plan_landing(
            self.grid, self.player.pos, self.player.facing, target
        )
        # 朝投掷方向转身：扔出去的东西从背后飞出来会很怪
        dx, dy = landing[0] - self.player.x, landing[1] - self.player.y
        if abs(dx) + abs(dy) > 1e-6:
            self.player.facing = math.atan2(dy, dx)

        self.player.consume_stone()
        self.stones.append(Stone(self.player.x, self.player.y, *landing))
        self.events.append("stone_throw")
        return True

    def _update_stones(self, dt: float) -> None:
        for st in list(self.stones):
            if st.update(dt):
                self.stones.remove(st)
                n = self._emit_noise(
                    st.tx, st.ty, C.STONE_NOISE_RADIUS, C.STONE_SUSPICION, "stone"
                )
                # 只有"真的把人引过去"才记风格分：往空地扔石子不算本事
                self.decoy_hits += n
                self.events.append("stone_land")

    def _update_pings(self, dt: float) -> None:
        for p in self.pings:
            p["age"] += dt
        self.pings = [p for p in self.pings if p["age"] < p["life"]]

    def _emit_noise(self, x: float, y: float, radius: float,
                    level: float, kind: str = "noise",
                    jitter: float = None) -> int:
        """在某处制造一次响动，让半径内的哨兵过去查看。返回被惊动的人数。

        三个设计约束（缺一个这机制就会变成"群体嘲讽"或"精确制导"）：
        1. 只抬到"起疑"级别，绝不越过确认阈值 —— 听到不等于看到；
        2. 落点带定位误差 —— 声音只能给出"大概在那片"，哨兵得自己搜；
        3. 已经在追你的、或正看着你的哨兵不受影响 —— 视觉信息优先于听觉。

        第四个约束是后来补的：**响动也要过地图**。原来这里按直线距离筛人，
        隔着整片岩层扔颗石子，另一侧的哨兵也会跑过来 —— 玩家会以为
        地图根本没有墙。现在用和脚步同一套代价场（`SoundField`），
        一次性的，不参与逐帧循环。
        """
        self.pings.append({
            "x": x, "y": y, "r": radius,
            "age": 0.0, "life": C.STONE_PING_LIFE, "kind": kind,
        })
        field = SoundField(self.grid, (x, y), radius)
        jit = C.STONE_INVESTIGATE_JITTER if jitter is None else jitter
        alerted = 0
        for e in self.enemies:
            if not e.alive or e.role != Role.PATROL or e.sees_player:
                continue
            d = field.cost_at(e.pos)
            if d > radius:
                continue
            fall = 1.0 - d / radius

            # 确定性"伪随机"偏移：用坐标当种子，同一局同一个哨兵每次猜得一样偏，
            # 测试可复现，也不会出现"每次落点都被精确知道"的机器感。
            jx = math.sin(e.x * 12.9898 + e.y * 78.233) * jit
            jy = math.cos(e.x * 51.137 + e.y * 27.619) * jit
            tx, ty = x + jx, y + jy
            if self.grid.is_wall(int(tx), int(ty)):
                tx, ty = x, y

            e.suspicion = min(
                C.SUSPICION_CONFIRM - 0.08,
                max(e.suspicion, level + 0.10 * fall),
            )
            e._last_seen = (tx, ty)
            e._noise_lock = C.STONE_MEMORY_LOCK
            e._investigate_time = 0.0
            e._look_timer = 0.0
            e._path = None
            e._path_target = None
            e._path_failed_for = None
            alerted += 1
        return alerted

    # ---- 声音传播 ----
    def _sound_limit(self) -> float:
        """代价场展开到多远：敌人里最大的有效听觉半径，留一点余量。

        上限跟着**实际配置**走而不是写死 C.HEAR_RANGE × 1.5：测试里经常
        构造 `Perception(hear_range=12)` 的"大耳朵"哨兵，场要是只展开 7.5，
        它们会以为自己聋了 —— 这种错不会报错，只会让断言莫名其妙。
        """
        limit = C.HEAR_RANGE * 1.5
        for e in self.enemies:
            p = getattr(e, "perception", None)
            if p is not None:
                limit = max(limit, p.hear_range * 1.5)
        return limit

    def sound_cost(self, pos) -> float:
        """玩家脚下到 `pos` 的声音传播代价（格）；传不过去就是 inf。

        缓存键是**玩家所在格**：玩家一帧可能只挪 0.1 格，但哨兵每帧都要问
        一次，不缓存就等于每帧给 18 个人建一张图。
        """
        p = self.player
        if p is None:
            return math.inf
        tile = (int(p.x), int(p.y))
        if self._sound_field is None or self._sound_tile != tile:
            self._sound_field = SoundField(self.grid, tile, self._sound_limit())
            self._sound_tile = tile
            self.sound_builds += 1
        return self._sound_field.cost_at(pos)

    # ---- 反馈聚合 ----
    def _aggregate_threat(self) -> None:
        """把每个敌人的感知结果汇总为 HUD 需要的两个数字。"""
        self.threat = 0.0
        self.seeing = 0
        chasing = False
        for e in self.enemies:
            if e.suspicion > self.threat:
                self.threat = e.suspicion
            if e.sees_player:
                self.seeing += 1
            if e.role is not None and e.role.value != "patrol":
                chasing = True

        # 暴露度峰值：判"全身而退"用。不能用当前 threat，因为结算那一刻
        # 可能已经降下来了 —— 玩家中途被惊到过一次就算破功。
        if self.threat > self.peak_threat:
            self.peak_threat = self.threat

        # 被发现次数：每次"从安全进入追击"计一次，避免同一轮追击反复计数
        if chasing and not self._chasing_flag:
            self.spotted += 1
            self.events.append("spotted")
            # 记下"是谁发现的"：用来识别"在一个哨兵面前反复晃"这种刷分行为
            for e in self.enemies:
                if e.alive and e.role is not None and e.role.value != "patrol":
                    key = f"{e.x:.1f},{e.y:.1f}"
                    self.spots_by_enemy[key] = self.spots_by_enemy.get(key, 0) + 1
        self._chasing_flag = chasing

    # ---- 计分 ----
    def style_bonus(self) -> List[tuple]:
        """风格分：返回 [(名称, 分值)]，只列真的做到的项。

        每一项都是**可验证的行为**而不是"尝试"：
        - "全身而退"看 peak_threat —— 一次都没让谁确认看到你；
        - 引开看 decoy_hits（真的有人被引过去），不看扔了几颗；
        - 干净开枪要求 kills>0 且 bodies_found==0 —— 没开枪的人不该拿这一项。
        """
        out = []
        if self.peak_threat < C.SUSPICION_CONFIRM:
            out.append(("全身而退", C.SCORE_STYLE_UNSEEN))
        if self.kills == 0:
            out.append(("零击倒", C.SCORE_STYLE_NO_KILL))
        if self.decoy_hits >= 3:
            out.append((f"引开 {self.decoy_hits} 人次", C.SCORE_STYLE_DECOY))
        if self.shield_kills > 0:
            out.append(("护盾反杀", C.SCORE_STYLE_SHIELD_KILL))
        if self.kills > 0 and self.bodies_found == 0:
            out.append(("开枪未被目击", C.SCORE_STYLE_CLEAN_SHOT))
        return out

    def style_total(self) -> int:
        total = sum(v for _, v in self.style_bonus())
        # 反向补丁：在同一个哨兵面前反复被发现，说明不是"潜行"而是"刷"。
        # 没有这一条，玩家可以靠反复进出视野吃满"全身而退"以外的所有分项。
        if any(n >= C.REPEAT_SPOT_LIMIT for n in self.spots_by_enemy.values()):
            total -= C.SCORE_REPEAT_PENALTY
        return total

    def base_score(self) -> int:
        """基础分：只衡量"完成得多快、多干净、多克制"。

        时间项**封顶**：超过 SCORE_TIME_FREE 的部分不再扣分。
        不封顶的代价是这条曲线决定了全部玩法 —— 慢就是差、快就是好，
        风格分再怎么调也只是在"快"里面挑赢家（score_table 里"为快冒险"
        比"静默流"高 140 分，就是这么来的）。封顶之后时间变成
        "够快就行"的门槛，超出部分归风格分去裁决。
        """
        t = min(self.elapsed, C.SCORE_TIME_FREE) + \
            max(0.0, self.elapsed - C.SCORE_TIME_FREE) * C.SCORE_TIME_OVER_RATE
        return (
            C.SCORE_BASE
            - t * C.SCORE_TIME_PENALTY
            - self.spotted * C.SCORE_SPOTTED_PENALTY
            - self.kills * C.SCORE_KILL_PENALTY
        )

    def score(self) -> int:
        """基础分 + 风格分。

        基础分 = 效率（快 + 少被发现 + 少击倒）；
        风格分 = 方式（全身而退 / 零击倒 / 引开 / 反弹杀 / 干净开枪）。

        只留基础分时最优解唯一（直线冲），风格分让"慢一点但漂亮"也值得打。
        另一个意义：明细可以在结算页逐条列出 —— 玩家能看到
        "这次差 60 分就能到 A"，这比一个孤零零的总分有说服力得多。

        没通关就是 0 分。这一条必须写在 `score()` 里，不能只在结算页
        "不显示分数"：测试、脚本、最佳纪录判定全都会调这个函数，
        结算页藏着不改，存档里照样会存进一个 827 分的失败局。
        之前 score_table 就抓到了这个 bug —— 失败局拿了全场最高分。
        """
        if self.state != C.STATE_VICTORY:
            return 0
        return max(0, int(round(self.base_score() + self.style_total())))

    def record(self) -> dict:
        return {
            "score": self.score(),
            "time": round(self.elapsed, 1),
            "spotted": self.spotted,
            "kills": self.kills,
            "rank": self.rank(),
            # 记录诞生的本地时间：结算页和主菜单都要回答"这个纪录是什么时候的"，
            # 否则玩家只看到一个分数，无从判断是不是自己刚刚刷出来的。
            # 用本地时间而不是 UTC/时间戳：这个字符串只给人看，不做比较。
            "when": time.strftime(WHEN_FMT),
            # 这一局打了几关。只有最后一关才提交成绩，所以 level_index+1
            # 就是整局的关卡数 —— 存档靠它区分"单关时代"和"两关时代"的成绩。
            "levels": self.level_index + 1,
            # 风格分明细也存下来：结算页和"最佳纪录"都能说清分数从哪来
            "style": [{"name": n, "value": v} for n, v in self.style_bonus()],
            "style_total": self.style_total(),
        }

    def rank(self) -> str:
        """按用时评级（S/A/B/C）。

        S 档额外要求"一枪没开、一次没被发现"：S 是"干净潜行"，
        不是"跑得快的屠夫"。把这条写进评级而不是只写进分数，
        是因为评级比分数更容易被玩家当成目标。

        阈值不再写死：多关之后用时是**整局累计**的，基准也必须累计，
        所以 `par` 由 Game 在开局时按"前 N 关基准之和"填进来。
        只算单关的时候它就是 C.RANK_S/A/B，行为和以前完全一致。
        """
        t = self.elapsed
        par_s, par_a, par_b = self.par
        if t <= par_s and self.kills == 0 and self.spotted == 0:
            return "S"
        if t <= par_a:
            return "A"
        if t <= par_b:
            return "B"
        return "C"

    # ---- 玩法判定 ----
    def _check_objectives(self) -> None:
        """检测玩家是否接触目标点，推进游戏状态。

        多节点之后这里有一条关键规则：**拿第一个节点就拉响警报**。
        第 1、2 关只有一个节点，所以"拿数据 → 警报 → 撤离"对他们完全没变；
        第 3 关两个节点，玩家会在封锁状态下走第二条腿 —— 前半段安静、
        后半段被追，整关的节奏因此分成两截，而不是"一条长走廊的加长版"。
        """
        px, py = self.player.x, self.player.y
        # 潜入阶段：接触任意一个未取的数据节点 → 记一笔
        if self.state == C.STATE_INFILTRATE:
            for i, (tx, ty) in enumerate(self.terminals):
                if i in self.taken:
                    continue
                if (px - tx) ** 2 + (py - ty) ** 2 >= 0.8 ** 2:
                    continue
                self.taken.append(i)
                self.events.append("pickup")
                # 还有节点没拿：警报先响，剩下的路在被封锁的状态下走
                if len(self.taken) < len(self.terminals):
                    self.alert = True
                    self._alert_timer = 3.0
                    self._apply_alert()
                    break
                # 全部拿到 → 进入逃脱，触发全图警觉
                self.has_data = True
                self.state = C.STATE_EXFILTRATE
                self.alert = True
                self._alert_timer = 3.0  # 屏幕顶部短暂提示
                self._apply_alert()
                break
        # 逃脱阶段：接触撤离点 → 胜利
        elif self.state == C.STATE_EXFILTRATE:
            tx, ty = self.exfil
            if (px - tx) ** 2 + (py - ty) ** 2 < 0.8 ** 2:
                self.state = C.STATE_VICTORY
                self.events.append("victory")

    def _check_capture(self) -> None:
        """哨兵**知道你在附近**且贴到 0.6 格内 → 被抓住。

        这里必须带一个"知情"条件（看得见 / 已经起疑 / 已在追）。
        旧版只看距离，于是出现了一个极不公平的场景：一个压根没注意到你的
        哨兵从走廊穿过去，只是刚好从你身上踩过去，就把你抓住了 ——
        玩家结算页上"被发现 0 次"和"任务失败"同时出现，完全无法理解。
        尸体警报会把哨兵往"尸体附近"引，这个漏洞会被高频踩到，必须堵。

        阵亡的和刚复活（grace）的哨兵不参与：尸体抓人不算，
        而"在玩家脸上刷出来的哨兵立刻抓人"更是纯粹的随机惩罚。
        """
        px, py = self.player.x, self.player.y
        for e in self.enemies:
            if not e.alive or e.grace > 0.0:
                continue
            aware = (e.sees_player or e.suspicion >= C.SUSPICION_CURIOUS
                     or e.role != Role.PATROL)
            if not aware:
                continue
            if (e.x - px) ** 2 + (e.y - py) ** 2 < C.CAPTURE_DIST ** 2:
                # 护盾优先于失败判定：贴上来的人被当场击倒，玩家活着但暴露了
                if getattr(self.player, "shield", 0) > 0:
                    self._apply_shield_break(e)
                    return
                self.captured = True
                self.captured_by = e
                self.state = C.STATE_DEFEAT
                # 结算页的"被发现"必须和"被抓住"对得上：
                # 一个只是起疑、还没进入追击的人抓住你，也要记一次发现，
                # 否则玩家会看到"被发现 0 次 · 任务失败"。
                if e.role == Role.PATROL and e.suspicion < C.SUSPICION_CONFIRM:
                    self.spotted += 1
                self.events.append("defeat")
                break

    def _apply_alert(self) -> None:
        """警觉状态：全图敌人视野更远、追击更快。"""
        for e in self.enemies:
            e.alerted = True
        self.alert_left = self.alert_duration

    def _update_alert(self, dt: float) -> None:
        """警觉是**限时封锁**，不是永久 debuff。

        原本拿完数据后全图永久 +2 格视野 +0.6 追击速度，
        等于返程必须全程硬扛 —— 而返程要横穿整张地图，
        结果就是"潜入做得再好，撤离必死"。真实设施也是这样：
        警报拉响 → 搜索一阵 → 没找到人 → 恢复常态。
        这样撤离阶段有了两种解法：抢在封锁期内冲出去，或者等封锁解除再走。
        """
        if self.alert_left <= 0.0:
            return
        self.alert_left = max(0.0, self.alert_left - dt)
        # 最后 3 秒闪烁提示（HUD 用 alert_left 画倒计时）
        if self.alert_left == 0.0:
            self.alert = False
            for e in self.enemies:
                e.alerted = False
            self.events.append("alert_end")

