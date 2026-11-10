Stealth Protocol is a 2D top-down stealth sandbox: five connected levels, one continuous run, no checkpoints.

Every guard on the map is running a full AI stack written from scratch — no behavior-tree library, no pathfinding library, no machine learning. They **see** (a real line-of-sight ray that walls actually block), they **hear** (footsteps, gunshots, thrown stones), they **remember** where you were last seen, and they **tell each other**.

**Press F1 and you can watch them think.** Every guard shows his current state, his suspicion, and his hearing radius. The panel in the corner logs his last decisions in plain language:

```
34.6  #17  noticed something
34.6  #17  patrol → investigate (61,38)   [seen before, 35% suspicious]
35.5  #17  confirmed 70%: he knows you are here
35.5        a witness shouted; nearby guards are heading over
35.5  #17  investigate → chase: still visible (71% suspicious)
```

That log is not a debug printout added afterwards. It is what the behavior tree emits every frame, on the same code path the game plays on.

## What is in it

- **5 levels**, each built around one idea: a corridor you cannot outrun, a hub you have to circle, a pillar hall with no straight lines, a power station where you can kill the lights, an inspection post with a 3-second window.
- **Stealth that is not a health bar.** Being seen is not damage — it is information the guards now share. A body gets reported. A gunshot is heard 9.5 tiles away.
- **No shooting required.** The fastest recorded run finishes in 66 seconds with zero kills, and scores higher than the run that shoots. Guns buy time, not points.
- **Full English UI.** Chinese is the source language; press **L** in the menu to switch. (English text is a first pass by a non-native speaker — see the known-issues note in the repo.)
- **Difficulty measured, not guessed.** A bot plays 96 games per level; win rates are 77% / 77% / 50% / 24% / 71%. Level 4 is the wall — there is an assist mode in the main menu (press **A**) if you get stuck.

## Controls

| | |
|---|---|
| WASD | move |
| Shift | sprint — fast, but loud |
| Ctrl | crouch — slow, but quiet |
| E | interact (terminals, breaker) |
| Q | throw a stone (noise, no exposure) |
| Left mouse | aim / shoot |
| F1 | **AI debug view** |
| Tab | full level map |
| L (menu) | switch language: 中文 / English |

## Download

Windows 64-bit. A single 28 MB `.exe` — no installer, no Python, no dependencies. Double-click and play. Your records are written to `records.json` next to the executable.

To check it runs on your machine first: `StealthProtocol-v1.8.13-win64.exe --selfcheck`

## Source

Python + pygame, everything hand-written. The repository also contains the engineering write-up: how the vision cone was broken for months (1.29% of sight lines went through walls), how the flow field was rebuilt from 18 ms to 4 ms, and why one level stayed at 24% after nine different redesigns.

https://github.com/lxb0957/sneak_ai

---

## 中文说明

2D 俯视角潜行沙盒，五关连续潜入，没有存档点。地图上每个哨兵都跑着一套从零手写的 AI：感知、决策、寻路、小队协同，没有用任何现成库。

按 **F1** 能看见他们怎么想 —— 每个哨兵头顶是他当前的状态和可疑度，角落的面板是他最近几条决策的自然语言日志。那份日志不是事后加的调试输出，是行为树每帧吐出来的。

枪不是必需的：最快的一局 66 秒通关、零击倒，分数比开枪那局还高。五关的难度是机器人各跑 96 局量出来的（77% / 77% / 50% / 24% / 71%），第 4 关卡住了就在主菜单按 **A** 开辅助。

支持中英双语界面（菜单里按 **L** 切换，中文是默认）。
Windows 64 位免安装，28 MB 单文件，双击即玩。
