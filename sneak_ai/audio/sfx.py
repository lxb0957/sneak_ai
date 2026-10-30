"""程序化音效：用 numpy 合成波形，不依赖任何音频素材文件。

为什么自己合成：
1. 零素材 = 零版权风险，也省掉"找音效"这种和 AI 无关的工作；
2. 每个音效的参数（频率、包络、噪声）都写在代码里，面试时能讲清楚
   "脚步声为什么是低频噪声 + 快速衰减"（脚步是宽带冲击，不是纯音）；
3. 没有音频设备（服务器 / 无头测试）时自动降级为静默，不影响游戏逻辑。

音效清单：脚步、无线电报警、被发现、取得数据、胜利、失败。
"""

import math

import numpy as np
import pygame

SAMPLE_RATE = 22050
MASTER_VOLUME = 0.45


class SFX:
    """极简音效播放器：合成一次，缓存为 pygame Sound，按名字播放。"""

    def __init__(self, enabled: bool = True):
        self.ok = False
        self.muted = False
        self._sounds = {}
        self._last_play = {}
        self._rate = SAMPLE_RATE
        self._channels = 1
        if not enabled:
            return
        try:
            # pygame.init() 通常已经用默认参数（44100/-16/2）把 mixer 打开了，
            # 这里不跟它对着干：直接读实际参数，按它的声道数生成 PCM。
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
            info = pygame.mixer.get_init()
            if not info:
                raise RuntimeError("mixer unavailable")
            self._rate, size, self._channels = info
            if size != -16:
                raise RuntimeError(f"unsupported mixer sample size: {size}")
            self._sounds = self._build_all()
            self.ok = True
        except Exception:
            # 没声卡 / mixer 初始化失败：静默降级，游戏照常跑
            self.ok = False

    # ============ 对外接口 ============
    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        return self.muted

    def play(self, name: str, cooldown: float = 0.0, volume: float = 1.0) -> None:
        """播放音效。cooldown 用于脚步这类高频音效的节流。"""
        if not self.ok or self.muted:
            return
        snd = self._sounds.get(name)
        if snd is None:
            return
        if cooldown > 0.0:
            now = pygame.time.get_ticks() / 1000.0
            if now - self._last_play.get(name, -999.0) < cooldown:
                return
            self._last_play[name] = now
        snd.set_volume(max(0.0, min(1.0, MASTER_VOLUME * volume)))
        snd.play()

    # ============ 合成 ============
    def _build_all(self) -> dict:
        return {
            "step": self._make(*self._step_wave()),
            "radio": self._make(*self._radio_wave()),
            "spotted": self._make(*self._spotted_wave()),
            "pickup": self._make(*self._arp([523.25, 659.25, 783.99], 0.09, 0.35)),
            "victory": self._make(*self._arp([523.25, 659.25, 783.99, 1046.5], 0.11, 0.6)),
            "defeat": self._make(*self._sweep(260, 90, 0.55, 0.5, "saw")),
            "stamina_out": self._make(*self._sweep(300, 180, 0.12, 0.25, "sine")),
            "throw": self._make(*self._throw_wave()),
            "stone_land": self._make(*self._stone_land_wave()),
            "empty": self._make(*self._sweep(220, 170, 0.06, 0.2, "sine")),
            "shot": self._make(*self._shot_wave()),
            "hit": self._make(*self._hit_wave()),
            "gun_empty": self._make(*self._click_wave()),
            "respawn": self._make(*self._sweep(300, 660, 0.22, 0.28, "sine")),
            "body_found": self._make(*self._body_found_wave()),
            # 补给：拾取是"上行"的两声，护盾再多一层亮色 —— 听到就知道是好东西
            "pickup_shield": self._make(
                *self._arp([659.25, 987.77, 1318.5], 0.07, 0.34)),
            # 破盾：一段短促的下行"放电"，和枪声一样是代价，不是奖励
            "shield_break": self._make(*self._shield_break_wave()),
        }

    def _make(self, samples: np.ndarray, volume: float = 1.0):
        """把 float 波形转成 pygame Sound（按 mixer 实际声道数补齐）。"""
        data = np.clip(samples * volume, -1.0, 1.0)
        pcm = (data * 32767).astype(np.int16)
        if self._channels > 1:
            pcm = np.repeat(pcm.reshape(-1, 1), self._channels, axis=1)
        return pygame.sndarray.make_sound(pcm)

    def _t_axis(self, dur: float) -> np.ndarray:
        return np.linspace(0.0, dur, int(self._rate * dur), endpoint=False)

    @staticmethod
    def _adsr(n: int, attack: float = 0.01, release: float = 0.6) -> np.ndarray:
        """线性 attack + 指数衰减包络，避免爆音（click）。"""
        t = np.linspace(0.0, 1.0, n)
        env = np.ones(n)
        a = max(1, int(n * attack))
        env[:a] = np.linspace(0.0, 1.0, a)
        env[a:] = np.exp(-(t[a:] - t[a]) / max(1e-6, release))
        return env

    def _sweep(self, f0: float, f1: float, dur: float, vol: float, wave: str = "sine"):
        """频率扫描音：f0 → f1。用于"失败"这种下坠感。"""
        t = self._t_axis(dur)
        freq = np.linspace(f0, f1, len(t))
        phase = 2 * np.pi * np.cumsum(freq) / self._rate
        if wave == "saw":
            osc = 2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0
        else:
            osc = np.sin(phase)
        return osc * self._adsr(len(t), 0.02, dur * 0.45), vol

    def _arp(self, freqs, step: float, vol: float):
        """琶音：一串短音，用于"取得数据 / 胜利"。"""
        parts = []
        for f in freqs:
            t = self._t_axis(step)
            parts.append(np.sin(2 * np.pi * f * t) * self._adsr(len(t), 0.05, 0.35))
        tail = self._t_axis(0.18)
        parts.append(np.sin(2 * np.pi * freqs[-1] * tail) * self._adsr(len(tail), 0.02, 0.5))
        return np.concatenate(parts), vol

    def _step_wave(self):
        """脚步：宽带噪声 + 极快衰减 + 一点低频 → 听起来像"咚"而非"嘀"。"""
        rng = np.random.default_rng(11)
        dur = 0.075
        t = self._t_axis(dur)
        noise = rng.normal(0.0, 1.0, len(t))
        # 一阶低通（累积平均）把白噪声压成闷响
        low = np.convolve(noise, np.ones(8) / 8, mode="same")
        thump = np.sin(2 * np.pi * 95 * t) * 0.6
        env = np.exp(-t / 0.018)
        return (low * 0.8 + thump) * env, 0.5

    def _radio_wave(self):
        """无线电报警：两声短促的方波"嘀嘀"，带一点噪声质感。"""
        parts = []
        for _ in range(2):
            t = self._t_axis(0.055)
            sq = np.sign(np.sin(2 * np.pi * 1180 * t)) * 0.5
            parts.append(sq * self._adsr(len(t), 0.1, 0.2))
            parts.append(np.zeros(int(self._rate * 0.035)))
        return np.concatenate(parts), 0.35

    def _throw_wave(self):
        """投掷：一段很短的"呼呼"声 —— 带通噪声 + 中间隆起的包络。

        纯噪声会像电流声，纯正弦会像电子音；只留中频段（去掉低频轰隆
        和高频嘶声）才有"东西从耳边飞过去"的物理感。
        """
        rng = np.random.default_rng(23)
        dur = 0.18
        t = self._t_axis(dur)
        noise = rng.normal(0.0, 1.0, len(t))
        band = np.convolve(noise, np.ones(4) / 4, mode="same")
        band = band - np.convolve(band, np.ones(12) / 12, mode="same")
        env = np.sin(np.pi * t / dur) ** 2
        return band * env, 0.4

    def _stone_land_wave(self):
        """落地：一声又短又脆的"嗒"。

        高频正弦给"石头磕在地上"的音高，极短噪声给撞击的颗粒感。
        衰减必须快（<40ms），否则会听成"敲了一下桌子"。
        """
        rng = np.random.default_rng(31)
        dur = 0.085
        t = self._t_axis(dur)
        click = np.sin(2 * np.pi * 1900 * t) * np.exp(-t / 0.011)
        grain = rng.normal(0.0, 1.0, len(t)) * np.exp(-t / 0.018)
        return click * 0.55 + grain * 0.45, 0.55

    def _shot_wave(self):
        """消音手枪：一声"噗"。

        消音器不是"静音"，它砍掉的是高频爆音、留下低频的"体感"。
        所以这里刻意**去掉**高频段（白噪声压成中低频），
        只留一个 60ms 的低频冲击 + 一点机械"咔"：
        听起来闷，但明显是枪，不是纸袋。
        """
        rng = np.random.default_rng(41)
        dur = 0.16
        t = self._t_axis(dur)
        noise = rng.normal(0.0, 1.0, len(t))
        body = np.convolve(noise, np.ones(10) / 10, mode="same")
        body = body - np.convolve(body, np.ones(40) / 40, mode="same")
        thump = np.sin(2 * np.pi * 118 * t) * np.exp(-t / 0.030)
        mech = np.sin(2 * np.pi * 2400 * t) * np.exp(-t / 0.008)
        env = np.exp(-t / 0.045)
        return (body * 0.75 + thump * 0.95) * env + mech * 0.12, 0.6

    def _hit_wave(self):
        """命中：一声很闷的"咚"，比枪声更低更短 —— 打在胸甲上。"""
        rng = np.random.default_rng(53)
        dur = 0.13
        t = self._t_axis(dur)
        low = np.sin(2 * np.pi * 78 * t) * np.exp(-t / 0.028)
        grain = np.convolve(rng.normal(0.0, 1.0, len(t)), np.ones(6) / 6, "same")
        return low + grain * np.exp(-t / 0.02) * 0.35, 0.45

    def _click_wave(self):
        """空仓：撞针打在空膛上的"咔"，极短、极干。"""
        dur = 0.05
        t = self._t_axis(dur)
        click = np.sin(2 * np.pi * 1600 * t) * np.exp(-t / 0.006)
        click += np.sin(2 * np.pi * 900 * t) * np.exp(-t / 0.004) * 0.5
        return click, 0.35

    def _body_found_wave(self):
        """尸体被发现：两声干硬的"嘟、嘟"，像对讲机里的呼叫。

        刻意不做 spotted 那种上行旋律 —— 这不是"你被锁定了"，
        而是"有人在呼叫增援"：机械、重复、没有情绪，听着更冷。
        每个音都带一层二次谐波，才有小喇叭的塑料感而不是纯电子音。
        """
        parts = []
        for hz in (720.0, 590.0):
            t = self._t_axis(0.078)
            env = np.minimum(1.0, t / 0.009) * np.exp(-t / 0.050)
            tone = np.sin(2 * np.pi * hz * t) + 0.32 * np.sin(4 * np.pi * hz * t)
            parts.append(tone * env)
            parts.append(np.zeros(int(self._rate * 0.052)))
        return np.concatenate(parts), 0.42

    def _shield_break_wave(self):
        """破盾：一段短促的放电声（高频快速下行 + 一点噪声尾巴）。

        它是"你用掉了一张牌"的确认音，必须和开枪一样带代价感 ——
        上行音会让人以为赚了，所以这里刻意下行。
        """
        t = self._t_axis(0.28)
        env = np.minimum(1.0, t / 0.006) * np.exp(-t / 0.075)
        f = 1500.0 * np.exp(-t / 0.085) + 180.0
        phase = 2 * np.pi * np.cumsum(f) / self._rate
        zap = np.sin(phase) * env
        # 叠一点方波谐波 + 收尾的噪声，才有"电弧"的颗粒感
        zap += 0.28 * np.sign(np.sin(phase * 0.5)) * env
        tail = np.random.default_rng(7).normal(0, 1, len(t)) * env * 0.10
        return zap + tail, 0.45

    def _spotted_wave(self):
        """被发现：快速上行的双音，制造"被锁定"的紧张感。"""
        up = self._sweep(520, 880, 0.16, 0.55, "saw")[0]
        gap = np.zeros(int(self._rate * 0.02))
        up2 = self._sweep(760, 1180, 0.14, 0.45, "saw")[0]
        return np.concatenate([up, gap, up2]), 0.5
