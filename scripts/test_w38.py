"""W38：渲染层"只改成本、不改画面"的等价性，外加一个真实的播放速度 bug。

## 为什么需要这个

这一轮的性能改动全在渲染层，而动渲染层的风险不在"快不快"，在**画面会不会
悄悄变样** —— 锥体偏一像素、面板少一层透明，没有任何断言会红，但玩家看得见。
所以每个改法都要跟"原写法"逐字节对拍。

顺带查出并修掉一个真 bug：目标点周围的漂浮光点原来是在 `draw_goal()` 里推进
并绘制的，而 `draw_goal()` 会为**每一个**未取走的数据终端调一次。第 3 关有
两个终端，于是同一批光点每帧推进两次、绘制两次 —— 飘移速度翻倍、寿命减半，
玩家取走一个终端后同屏光点会突然慢一半。

第 3 关的清点结果就是这么露出来的（改动前后各跑 60 帧，数同屏光点）：
别的关 42 个，它 40 个 —— 因为寿命被砍了一半，稳态数量也跟着砍半。

这个脚本钉四件事：

1. 面板缓存 == 每次新画（`draw_panel` 现在缓存成品）；
2. 进度条复用 == 每次新画（`draw_bar` 复用同一张表面，必须先清空）；
3. 视野锥只贴包围盒 == 整屏贴（这是本轮最大的一笔省）；
4. 光点的推进次数**只取决于帧数**，不取决于同屏有几个目标。

和前几个脚本一样，它不断言速度：CI 机器负载不稳，把"快"写成断言只会得到
一个爱红不红的门禁。
"""

import inspect
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core.renderer import PixelRenderer, vision_polygon  # noqa: E402
from sneak_ai.core.ui import draw_bar, draw_panel  # noqa: E402

W, H = C.WINDOW_W, C.WINDOW_H


def _canvas():
    """一张与游戏画面同格式的画布：两次绘制都从同样的底色开始比较。"""
    s = pygame.Surface((W, H))
    s.fill((37, 41, 46))
    return s


def _same(a, b) -> bool:
    return pygame.image.tobytes(a, "RGB") == pygame.image.tobytes(b, "RGB")


# ============ 1. 面板缓存 ============
def test_panel_cache_matches_direct():
    """面板缓存：缓存出来的那张，必须和每次现画的一模一样。

    面板内容是"尺寸 + 透明度"的函数，与画在哪儿无关 —— 这正是不变式，
    也是它敢被缓存的前提。把它写进断言，是为了防止将来有人往面板里加
    和时间、位置相关的东西（那样缓存就变成"面板冻在第一次画的位置"）。
    """
    rects = [
        pygame.Rect(12, 12, 322, 128),
        pygame.Rect(252, H - 62, 156, 50),
        pygame.Rect(596, H - 62, 150, 50),
    ]
    cases = 0
    for alpha in (160, 170, 185, 205, 235):
        for rect in rects:
            cached = _canvas()
            direct = _canvas()
            # 连画两次：走一遍缓存命中路径
            for _ in range(2):
                draw_panel(cached, rect, alpha=alpha)
            # 每次现画（改动之前的写法）
            for _ in range(2):
                surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
                local = surf.get_rect()
                pygame.draw.rect(surf, (*C.COL_HUD_PANEL, alpha), local,
                                 border_radius=8)
                pygame.draw.rect(surf, (*C.COL_HUD_LINE, 220), local, width=1,
                                 border_radius=8)
                direct.blit(surf, rect.topleft)
            assert _same(cached, direct), (
                f"面板缓存和现画的像素不一致（{rect} alpha={alpha}）")
            cases += 1
    print(f"[OK] 面板缓存与每次现画逐字节相同（{cases} 组尺寸×透明度）")


# ============ 2. 进度条复用 ============
def test_bar_cache_matches_direct():
    """进度条：复用同一张表面，画面必须和每次新建的一致。

    这里容易漏的是"上一帧的填充比这一帧长"—— 复用时必须先清空，
    否则短条子里会露出长条子的尾巴。所以每组都先按满值画一遍再画目标值。
    """
    rect = pygame.Rect(76, H - 53, 152, 12)
    color = (232, 70, 58)
    for ratio in (0.0, 1.0, 0.0, 0.5, 0.07, 0.999):
        cached = _canvas()
        direct = _canvas()
        draw_bar(cached, rect, 1.0, color)      # 制造"残留尾巴"
        draw_bar(cached, rect, ratio, color)
        for r in (1.0, ratio):
            surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            local = surf.get_rect()
            pygame.draw.rect(surf, (40, 46, 56), local, border_radius=3)
            if r > 0:
                fill = pygame.Rect(0, 0, max(2, int(rect.width * r)), rect.height)
                pygame.draw.rect(surf, color, fill, border_radius=3)
            pygame.draw.rect(surf, (70, 78, 92), local, width=1, border_radius=3)
            direct.blit(surf, rect.topleft)
        assert _same(cached, direct), f"进度条复用的像素不一致（ratio={ratio}）"
    print("[OK] 进度条复用与每次新建逐字节相同（含由满到空的残留）")


# ============ 3. 视野锥包围盒 ============
def _old_vision_cones(screen, cam, world, fx):
    """改动之前的写法：全屏清零 + 逐多边形 + 整屏加法叠加。"""
    fx.fill((0, 0, 0, 0))
    ts = C.TILE_SIZE
    grid = world.grid
    px0, py0 = world.player.x, world.player.y
    for e in world.enemies:
        if (e.x - px0) ** 2 + (e.y - py0) ** 2 > 15.0 ** 2:
            continue
        view = e.view_range()
        if view <= 0:
            continue
        rng = view * ts
        ex, ey = cam.world_to_screen(e.x, e.y)
        if ex < -rng or ey < -rng or ex > W + rng or ey > H + rng:
            continue
        hot = e.suspicion >= C.SUSPICION_CURIOUS or e.role.value != "patrol"
        base = (255, 92, 74) if hot else (255, 205, 105)
        k = 0.34 if hot else 0.20
        fill = (int(base[0] * k), int(base[1] * k), int(base[2] * k), 255)
        edge = (int(base[0] * k * 2.4), int(base[1] * k * 2.4),
                int(base[2] * k * 2.4), 255)
        pts = vision_polygon(e, grid, cam)
        if len(pts) >= 3:
            pygame.draw.polygon(fx, fill, pts)
            pygame.draw.polygon(fx, edge, pts, 1)
    screen.blit(fx, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)


def test_cone_bbox_matches_fullscreen():
    """视野锥：只贴包围盒 == 整屏贴。

    对拍时特意让敌人一直转朝向，这样每帧的包围盒形状都不一样 ——
    固定的包围盒就算写错（比如少算一个角）也测不出来。
    """
    ts = C.TILE_SIZE
    frames = 0
    for idx in range(5):
        g = Game(start_level=idx)
        world, cam, px = g.world, g.cam, g.px
        fx = pygame.Surface((W, H), pygame.SRCALPHA)
        e0 = next((e for e in world.enemies if e.alive), None)
        if e0 is None:
            continue
        # 让玩家贴着第一个敌人站：视锥的 15 格筛选按玩家位置算，
        # 不挪玩家的话第 1 关开局一个锥体都不会画，对拍等于没测。
        world.player.x, world.player.y = e0.x, e0.y
        cam.follow(world.player.x, world.player.y, 0.0)
        for _ in range(24):
            for e in world.enemies:
                e.facing += 0.31
            a = _canvas()
            b = _canvas()
            _old_vision_cones(a, cam, world, fx)
            px.draw_vision_cones(b, cam, world)
            assert _same(a, b), f"第 {idx + 1} 关：包围盒写法和整屏写法像素不同"
            frames += 1
    assert frames >= 100, f"样本太少（{frames} 帧）"
    print(f"[OK] 视野锥包围盒与整屏叠加逐字节相同（{frames} 帧，五关）")


# ============ 4. 光点推进速率 ============
def test_particles_advance_once_per_frame():
    """光点每帧只推进一次，与同屏有几个目标无关。

    这是修掉的那个 bug 的可执行版本：旧写法在 `draw_goal()` 里推进，
    两个终端就推进两次。这里直接按"两个终端"的场景摆一遍。
    """
    screen = _canvas()
    px = PixelRenderer()
    px.draw_goal(screen, 100, 100, "terminal", 0.5)
    px.draw_goal(screen, 400, 100, "terminal", 0.5)
    assert len(px._particles) == 2, "两个目标应该各撒一粒光点"
    px.draw_goal_particles(screen)
    for p in px._particles:
        assert abs(p["age"] - 1 / 60) < 1e-9, (
            f"两个目标时单帧推进了 {p['age'] * 60:.2f} 帧的量 —— "
            f"光点被重复推进了")

    # 再跑一段：age 的上界必须是"帧数/60"，翻倍就是 bug 回来了
    frames = 30
    for _ in range(frames):
        px.draw_goal(screen, 100, 100, "terminal", 0.5)
        px.draw_goal(screen, 400, 100, "terminal", 0.5)
        px.draw_goal_particles(screen)
    top = max((p["age"] for p in px._particles), default=0.0)
    limit = (frames + 1) / 60
    assert top <= limit + 1e-9, (
        f"光点最多推进了 {top:.3f} 秒，但只过了 {limit:.3f} 秒")

    # 结构约束：draw_goal 不许自己推进光点 —— 它会被每个终端各调一次。
    # 没有这条，上面两条只测到"现在是对的"，挡不住有人再把调用放回去。
    # 查的是调用点（`self.`）而不是源码里出现过这个名字：docstring 里本来
    # 就写着"推进和绘制交给 draw_goal_particles"，按名字查会自己撞自己。
    src = inspect.getsource(PixelRenderer.draw_goal)
    assert "self.draw_goal_particles(" not in src, (
        "draw_goal 又开始自己推进光点了 —— 它会被每个终端各调一次")
    print("[OK] 光点每帧只推进一次（两个终端时也是），且 draw_goal 不自己推进")


def main() -> int:
    pygame.init()
    test_panel_cache_matches_direct()
    test_bar_cache_matches_direct()
    test_cone_bbox_matches_fullscreen()
    test_particles_advance_once_per_frame()
    print("\nW38 全部通过：渲染层的成本优化没有改动画面，"
          "光点速度不再随目标数变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
