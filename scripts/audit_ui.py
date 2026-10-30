"""UI 重叠体检：拦截真实渲染时每一次 blit，把所有文字的落点矩形收集起来求交。

为什么不能用"脚本自己猜坐标"的写法：第一版就是那样，改完 HUD 之后
审计结果一个字都没变 —— 因为它测的是脚本里的硬编码，不是游戏真正画的位置。
审计工具的价值全在于"读真实值"，否则它只会给你虚假的安全感。

用法：
    SDL_VIDEODRIVER=dummy python scripts/audit_ui.py

退出码：0 = 无重叠，1 = 有重叠（可挂进 CI）。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402

# 只关心"必须可读"的层；地图/角色/特效随便压，不算重叠
_SKIP = ("地图", "角色", "特效")


class RecordingSurface(pygame.Surface):
    """记录每一次绘制落点的画布。

    不能直接给 `g.screen.blit` 打猴子补丁：pygame 的 Surface 是 C 类型，
    `blit` 在实例上是只读属性，赋值直接抛 AttributeError。
    唯一可行的办法是**换一个 Surface 子类来做渲染目标** —— 方法可以在
    子类里覆写，而 HUD 完全不知道自己画到了别的地方。

    但只录 blit 是不够的：进度条、图标、雷达点全是 `pygame.draw.*` 直接
    画在画布上的，一个 blit 都不产生。于是"暴露度条压住了巡逻状态行"
    这种最典型的遮挡，审计完全看不见 —— 记录得越多，越像"全都查过了"。
    所以这里同时盯住 draw 层：`screen` 若是这个子类，就把绘制区域也记下来。
    """

    def __init__(self, size, flags=0, label: str = "?"):
        super().__init__(size, flags)
        self.log = []
        self.label = label
        self._rec = False          # 只在审计主动包起来的那段时间记账

    def record(self):
        self._rec = True
        return self

    def stop(self):
        self._rec = False

    def blit(self, src, dest=None, *a, **kw):
        try:
            r = src.get_rect()
            if isinstance(dest, pygame.Rect):
                r = dest.copy()
            elif isinstance(dest, (tuple, list)):
                r = r.move(dest[0], dest[1])
            if not (r.w == C.WINDOW_W and r.h == C.WINDOW_H):   # 跳过全屏遮罩
                self.log.append(r)
        except Exception:
            pass
        return super().blit(src, dest, *a, **kw)


class DrawProxy:
    """包住 `pygame.draw`：往记账画布上画的，同时把区域记下来。

    用代理而不是改 pygame 模块本身，是为了让这个副作用只活在审计进程里。
    """

    def __init__(self, real, target):
        self._real = real
        self._target = target

    def __getattr__(self, name):
        fn = getattr(self._real, name)
        if not callable(fn):
            return fn

        def wrapped(surface, *args, **kw):
            if surface is self._target and self._target._rec:
                r = _draw_rect(name, args, kw)
                if r is not None and r.w and r.h:
                    self._target.log.append(r)
            return fn(surface, *args, **kw)

        return wrapped


def _draw_rect(name, args, kw):
    """从 pygame.draw.* 的参数里还原出"这次画在哪"。拿不准就返回 None。"""
    try:
        if name in ("rect", "polygon", "lines"):
            return pygame.Rect(args[0])
        if name in ("circle", "ellipse"):
            return pygame.Rect(args[0] - args[2], args[1] - args[2],
                               args[2] * 2, args[2] * 2)
        if name == "line":
            x1, y1, x2, y2 = args[:4]
            return pygame.Rect(min(x1, x2), min(y1, y2),
                               abs(x2 - x1) + 1, abs(y2 - y1) + 1)
        if name == "arc":
            x, y, w, h = args[:4]
            return pygame.Rect(x, y, w, h)
    except Exception:
        pass
    return None



# 每个 UI 元素单独录一次，好给它贴个名字。
# 为什么要按层分开录：点状图标（弹药数、石子数）会被自己所在面板**完全包住**，
# 原来的"包含即父子"规则就把它们一起跳过了 —— 于是"石子小圆点压住'投石'字样"
# 这种真正的遮挡永远查不出来。父子关系不能靠"谁包住谁"猜，得靠来源。
_HUD_PARTS = [
    ("状态面板", "_draw_status_panel", ("world",)),
    ("暴露度", "_draw_exposure", ("world", "t")),
    ("体力", "_draw_stamina", ("player",)),
    ("弹药", "_draw_ammo", ("player",)),
    ("石子", "_draw_stones", ("player",)),
    ("护盾", "_draw_shield", ("player",)),
    ("处境提示", "_draw_center_notice", ("world", "t", "muted")),
    ("引导气泡", "_draw_tip", ("dt",)),
]


def _run(g, label, fn, *args):
    """在独立记账画布上跑一个绘制函数，返回 {(x,y,w,h): [label...]}。"""
    canvas = RecordingSurface((C.WINDOW_W, C.WINDOW_H), label=label)
    real = g.screen
    g.screen = canvas
    canvas.record()
    try:
        fn(canvas, *args)          # 每个 _draw_* 的第一个参数都是画布
    finally:
        canvas.stop()
        g.screen = real
    out = {}
    for r in canvas.log:
        out.setdefault((r.x, r.y, r.w, r.h), []).append(label)
    return out


def _merge(dst, src):
    for k, labels in src.items():
        dst.setdefault(k, []).extend(labels)


def _hud_rects(g, args):
    """录一整轮 HUD + 小地图（不含结算页）。"""
    rects = {}
    for label, meth, params in _HUD_PARTS:
        _merge(rects, _run(g, label, getattr(g.hud, meth),
                           *[args[p] for p in params]))
    _merge(rects, _run(g, "威胁箭头", g.hud.draw_threat_markers,
                       g.cam, args["world"], args["t"]))
    _merge(rects, _run(g, "小地图", g.minimap.draw,
                       args["world"], g.cam, g._pulse()))
    return rects


def collect(g: Game):
    """让所有 UI 元素同时出现在屏幕上，然后按层录下真实落点。

    只录 HUD + 小地图：世界层（地图块、角色、特效）本来就该互相压，
    混进来只会淹没真正的问题。做法是先把世界渲染到普通画布上"预热"，
    再逐个元素换记账画布重画一遍。
    """
    g.menu.active = False
    # 把所有"条件出现"的元素一次性打开
    g.world.bodies_found = 3
    g.world.alert = True
    g.world._alert_timer = 1.0
    g.world.seeing = 3
    g.world.threat = 0.9
    g.player.sprinting = True
    g.player.shield = 1
    g.sfx.muted = True
    g.hud.show_tip("护盾：捡起后能挡下一次抓捕，碰到你的哨兵会倒下", life=9.0)

    g._render(1 / 60)                      # 预热：让相机、缓存都就位

    args = {"world": g.world, "player": g.player, "t": g._time_accum,
            "dt": 1 / 60, "muted": g.sfx.muted}
    rects = {}
    _merge(rects, _hud_rects(g, args))

    # ---- 结算页单独扫一遍 ----
    # 结算页是最容易出重叠的地方：它是**纵向堆叠**的，行数又随内容变化
    # （风格分拿到几项就有几行）。之前漏扫这里，一加风格分就和底部提示撞上了。
    # 注意：结算页停在 STATE_VICTORY，而 HUD 主面板这时仍然画在屏幕上
    # （`_center_notice` 判的是"不是结算页"），所以这两批**必须**放在同一张
    # 坐标系里比 —— 分开孤立地看，会把"弹窗压住底栏"整个漏掉。
    w = g.world
    w.state = C.STATE_VICTORY
    w.elapsed = 68.0
    w.spotted = 2
    w.kills = 1
    w.peak_threat = 0.2        # 让"全身而退"这一项也出现 → 行数取最坏情况
    w.decoy_hits = 4
    w.shield_kills = 1
    w.bodies_found = 0
    args.update(world=w)
    _merge(rects, _hud_rects(g, args))
    _merge(rects, _run(g, "结算页", g.hud.draw_end,
                       w, g.best, True, g._time_accum))
    return rects


def find_overlaps(rects):
    """返回 [(rectA, labelsA, rectB, labelsB, 交集)]。

    判据：两块**不是同一次绘制**的区域相交，且**没有任何一层同时出现在
    两块里**（同层 = 同一个面板/同一段文字，压住自己是正常的）。
    这样既放过"面板 + 面板里的文字"，又抓得住"石子圆点 + 投石字样"。
    """
    keys = list(rects.keys())
    bad = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            r1, r2 = pygame.Rect(keys[i]), pygame.Rect(keys[j])
            inter = r1.clip(r2)
            if inter.width <= 0 or inter.height <= 0:
                continue
            if set(rects[keys[i]]) & set(rects[keys[j]]):
                continue
            bad.append((r1, rects[keys[i]], r2, rects[keys[j]], inter))
    return bad


def main() -> int:
    g = Game()
    rects = collect(g)
    return report(rects)


def report(rects) -> int:
    print(f"\n录到 {len(rects)} 个 UI 落点")
    bad = find_overlaps(rects)
    if not bad:
        print("没有重叠 ✅")
        return 0

    print(f"发现 {len(bad)} 处重叠：")
    for r1, l1, r2, l2, inter in bad:
        print(f"  {'/'.join(l1)} [{r1.w}x{r1.h} @{r1.topleft}]"
              f" × {'/'.join(l2)} [{r2.w}x{r2.h} @{r2.topleft}]"
              f"  → 重叠 {inter.width}x{inter.height} @{inter.topleft}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
