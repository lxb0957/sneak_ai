# 分平台发布文案

上架 itch 之后再用。**一次只发一个地方**，然后守着评论区回话 —— 同一天铺五个平台的结果是五处都没人回。

先看最后那节「事实卡」：所有数字只从那里抄，任何平台都不要现编。

---

## 一、Hacker News（Show HN）

**标题**（≤80 字符，不要写 "revolutionary"、不要带感叹号）：

```
Show HN: A 2D stealth game where every guard runs a hand-written AI
```

**首条评论自己发**（HN 的规矩是标题只报事实，解释放在评论里）：

```
Author here. This started as a portfolio project: I wanted a game AI stack I could
be asked about in detail, without a behavior-tree library, a pathfinding library, or
any learned policy. So perception (grid raycast + hearing), decision (behavior tree),
pathing (flow field) and squad coordination are all in the repo, hand-written.

Two things might be interesting here beyond the game:

1. The vision cone and the "can this guard see me" check are the same function. They
   used to be two fixed-step raymarchers, and fixed-step raycasting skips a wall when
   the ray crosses a corner diagonally. Measured on the real maps: 1.29% of sight
   lines within 8 tiles went through walls, and 0.00% went the other way. The cone on
   screen showed it happening, so it looked like a feature.
2. Difficulty is measured, not tuned by feel. A bot plays 96 games per level on fixed
   seeds; the win rates are 77/77/50/24/71%. Level 4 has stayed at ~24% through nine
   redesigns, so I wrote down why and shipped an assist mode instead of pretending.

Windows x64 only for the binary (pygame + pyinstaller); the source runs on macOS and
Linux with pip install. MIT.
```

**注意**

- 不要一开始放 itch 链接当主链接，HN 更喜欢 GitHub。把 itch 放评论第二段。
- 标题里出现 "game" 会拉低 HN 兴趣，出现 "AI" 会拉高；两者都在的时候，正文第一段必须落到「这是什么，怎么做的」。
- 发帖时间：工作日美东上午 8–10 点（北京时间 21–23 点）。
- 前 30 分钟必须在线回评论，HN 的排序靠早期互动。

---

## 二、Reddit

### r/pygame（最对口，先发这里）

**标题**

```
Five levels, one continuous run, no checkpoints — a stealth game in pygame with a hand-written AI stack
```

**正文**

```
Some context: this is a 2D top-down stealth sandbox built on pygame-ce, all art procedural (no
asset packs). The thing I actually care about is the AI: perception, behavior tree, flow-field
pathing and squad coordination are all hand-written, no libraries.

Press F1 in game and every guard shows his state, suspicion, and a timeline of his last decisions
in plain text — it's emitted by the behavior tree every frame, not a bolted-on debug print.

Two things from the write-up that this sub might find useful:

- Vision cone and line-of-sight check are one function now. They used to be two fixed-step
  raymarchers; fixed-step skips walls on diagonal corner crossings. 1.29% of sight lines within
  8 tiles went through walls, 0.00% the other way.
- Flow field went from 18 ms to 4 ms a frame after I stopped rebuilding it every tick.

Windows exe in the link, source is MIT and runs on macOS/Linux with pip install.

Happy to go into the pathing or the flow field if anyone's building something similar.
```

**附**：一张 F1 调试视图 GIF（`assets/demo.gif` 前半段更好，锥体动起来才看得出）。

### r/IndieGaming

同一份英文文案，但开头换掉 —— 那里没人关心实现，先给一句能勾住人的：

```
A stealth game where being seen isn't damage — it's information the guards now share.
```

### r/gamedev

**只在 Screenshot Saturday 发**（周六，带 `#screenshotsaturday` 的固定帖里）。规则是发一张图 + 一句话，不要在帖子里贴商店链接；有人问再回。这里发"求反馈"比发"求下载"有效。

**别做的事**

- 不要新号连发。Reddit 对账号年龄和 karma 有隐性门槛，一个刚注册的号发三个子版会被直接过滤且不进垃圾箱提示。
- 不要同一段文案改两个词就跨版复制，Automod 会判成 spam。
- 每个子版的自荐比例（一般 1:9）看一下再发。

---

## 三、X / Twitter

一条主推 + 三条串，配 GIF。

```
A stealth game where the guards' AI is the product.

Press F1 and you watch them think: state, suspicion, hearing radius, and a timeline of
why each one changed his mind.

No behavior-tree library. No pathfinding library. Written from scratch, in Python.

[GIF]
```

```
1/ The vision cone and the "can he see me" check were two different raymarchers.

Fixed-step raycasting skips a wall when the ray cuts a corner diagonally. 1.29% of sight
lines within 8 tiles went through solid walls. 0.00% went the other way.
```

```
2/ The cone drew the same wrong ray, so the bug was visible on screen and I read it as
intended behaviour for months.
```

```
3/ Fix wasn't finer sampling — it's one exact grid traversal that both the cone and the
decision call. One geometry, two consumers.
```

**配图**：`assets/x-clip.gif`（12.1 秒 / 1.90 MB）。它是从 `assets/demo.gif` 截的 24–36 秒，
正好是"被发现 → 甩脱"那一段 —— 红边框从 25.3 秒起亮，比整段 6.4 MB 更适合信息流。

要换窗口就改参数重截，别手拖：

```bash
python scripts/trim_demo_gif.py --scan                    # 先看每帧红边框强度，找警报起点
python scripts/trim_demo_gif.py --start 24 --seconds 12   # 再截
```

**注意**：前 2 秒必须有动作（哨兵回头/追人），不要用标题卡开场；GIF 超过 5 MB
X 会再压一轮，掉帧很明显 —— 脚本会在超限时警告。

---

## 四、V2EX（分享创造节点）

**标题**

```
[分享创造] 写了一个 2D 潜行游戏，重点不是玩法，是里面那套手写 AI
```

**正文**

```
做这个的起因很功利：想要一套能在面试里被追问半小时的游戏 AI，而不是调包跑个 demo。
所以没有用任何行为树库、寻路库、也没有机器学习 —— 感知（网格射线 + 听觉）、决策（行为树）、
寻路（流场）、小队协同全部手写。

玩法是五关连续潜入，没有存档点。有意思的地方按 F1：每个哨兵头顶显示状态和可疑度，
角落是「他最近几条决策」的文本时间线。那不是事后加的调试输出，是行为树每帧吐出来的。

两个自查出来的坑：

1. 视野锥的画面和「看不看得见你」的判定，原来是两份固定步长的射线。固定步长在射线斜穿墙
   角点时会整格跳过 —— 五张真图实测，8 格内 1864 对视线里 1.29% 能隔墙看见，
   反向 0.00%。因为锥体画的是同一条错射线，画面上看着像正常行为，藏了好几个月。
   修法不是加密采样，是抽成一个精确网格步进，两个消费者共用。

2. 难度用机器人量：每关 96 局固定种子，胜率 77/77/50/24/71%。第 4 关改了九版还是 24%，
   原因写进 KNOWN_ISSUES 了，另外加了辅助模式，没有假装它是「设计上的难」。

Windows 免安装单文件，源码 MIT（Python + pygame，macOS/Linux 也能跑）。

https://github.com/lxb0957/sneak_ai
```

**注意**：V2EX 反感纯推广，第一段必须交代「为什么做」，链接放最后。发在**分享创造**，不要发在程序员节点。

---

## 五、小红书

**封面**：用 `assets/itch/cover.png` 或截图 2（F1 调试视图更像"有技术含量"）。

**标题**（≤20 字，别用书名号堆砌）

```
我用 Python 写了个潜行游戏，AI 全是手搓的
```

**正文**

```
五关连续潜入，没有存档点。

最想让你按的是 F1 —— 按下去能看见每个哨兵在想什么：他现在是巡逻还是追击、有多怀疑你、
他刚才为什么突然回头。左下角那串字不是截图配文，是游戏真的每帧在算的东西。

没有用任何 AI 库：看得见（射线被墙挡）、听得见（脚步、枪声、扔石头）、记得住你最后
出现的位置、还会互相喊人。这些全是手写的。

比较好玩的一个细节：最快通关的一局 66 秒、一个人没打，分数比开枪那局还高。枪能买时间，
买不到分。

Windows 双击就能玩，不用装 Python。

#独立游戏 #Python #游戏开发 #潜行游戏 #AI
```

**注意**：正文里不要放链接（限流），链接放**第一条评论**。图 3:4 比 16:9 占屏大。

---

## 六、B 站（视频简介）

```
一个 2D 潜行游戏，但重点是里面那套手写 AI。

五关连续潜入，没有存档点。按 F1 能看到每个哨兵的视野锥、听觉圈，以及「他最近几条决策」
的文本时间线 —— 巡逻为什么变成追击、追击为什么退回调查，因果链在屏幕上摊开。

没有行为树库、没有寻路库、没有机器学习。感知、决策、流场寻路、小队协同全部手写，
Python + pygame，源码 MIT。

00:00 玩法
00:32 F1 调试视图
01:05 一个藏了几个月的视线 bug（1.29% 的视线能隔墙看见）
01:48 难度是机器人量出来的：77 / 77 / 50 / 24 / 71

下载（Windows 免安装）：GitHub Release 链接放评论置顶
```

**注意**：前 3 秒必须有画面动作，别用静态标题；封面用 F1 视图 + 大字「他们在想什么」。

---

## 七、发布顺序（照这个来）

| 顺序 | 平台 | 时机 | 为什么 |
|---|---|---|---|
| 1 | itch 上架 + devlog | 先做 | 所有链接的落点，其他平台都指这儿 |
| 2 | r/pygame | 同日 | 最对口，反馈最具体，能先抓到真 bug |
| 3 | V2EX 分享创造 | +1 天 | 中文圈第一批真玩家 |
| 4 | X 串 | +1 天 | 主要是留档，别期待流量 |
| 5 | 小红书 | +2 天 | 图文，和别的平台不冲突 |
| 6 | r/gamedev Screenshot Saturday | 周六 | 只有周六能发 |
| 7 | Hacker News | 看前几步反馈 | HN 最挑，技术争议点被问过一遍之后再上 |
| 8 | B 站 | 有录像再说 | 没视频别硬发 |

---

## 八、事实卡（只从这里抄数字）

| 项 | 值 |
|---|---|
| 版本 | v1.8.11 |
| 关卡 | 5 关连续，无存档点 |
| 最快零击倒通关 | 66.3 秒 / 846 分 / A（`scripts/record_demo.py` 录的） |
| 同机器人允许开枪 | 71.2 秒 / 603 分（慢 4.9 秒、少 243 分） |
| 机器人胜率 | 77.1 / 77.1 / 50.0 / 24.0 / 70.8 %（每关 96 局） |
| 视线 bug | 隔墙看见 1.29%，反向 0.00%，样本 1864 对 / 8 格内 |
| 流场优化 | 18 ms → 4 ms |
| 每帧 p50（第 1 关） | 2.50 ms → 1.18 ms |
| 体积 | 28 MB 单文件（27,781,996 B） |
| 平台 | Windows x64 免安装；源码 macOS/Linux 可跑 |
| 许可 | MIT |
| 仓库 | https://github.com/lxb0957/sneak_ai |

**不要说的**

- 不要写"零依赖"（有 pygame，是打包进 exe 的）。
- 不要说"AI 会学习"（没有机器学习，说错一次会被追着问）。
- 不要说第 4 关"故意设计得难"（它是没解决，文档里认了）。
- 不要给未发布的版本号。
