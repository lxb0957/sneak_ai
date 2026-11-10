# Devlog #1 — A bot decides how hard my levels are

*Posted with the v1.8.13 build. ~5 min read. Code: [github.com/lxb0957/sneak_ai](https://github.com/lxb0957/sneak_ai)*

Every difficulty number on this page was produced by a bot, not by me playing.

That is not a humblebrag. It is the only way I could keep the numbers honest. When you tune five levels yourself, you stop being able to tell "this is hard" from "I have played this level four hundred times and I know where the guard turns." So I wrote a bot that plays the game through the same input path a human does, ran it 96 times per level on fixed seeds, and let the win rate be the difficulty.

| Level | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Bot win rate | 77% | 77% | 50% | **24%** | 71% |

Level 4 is the wall. It has been the wall for nine redesigns.

## Nine attempts at level 4

The level is a power station. There is a breaker you can hit to kill the lights. That was the idea: darkness as a tool, a level you solve by changing the rules rather than by moving faster.

What actually happened is that the bot found the one line through it, and everything else was death. Redesigns I tried, in order, each measured over the same 96 games:

1. Widen the main corridor. → 26%. The bot just sprints it.
2. Add a second breaker. → 25%. More choices, same single line.
3. Move the objective deeper. → 23%. Longer line, same line.
4. Two guards on the dark route. → 27%. Now it kills them.
5. Remove the guns from that level. → 22%. Still one line, just a slower one.
6. Shrink the guard patrol radius. → 25%.
7. Add a vent that skips one room. → 28%, but it felt like a cheat door.
8. Make the lights *stay* off. → 29%. Now it is a walk.
9. Revert to the version before attempt 1. → 24%.

Nine changes, net zero. The level is not unbalanced in the sense of "too hard" — it is *thin*. There is one correct solution, it is narrow, and every guard placement that does not sit on it does nothing. Adding difficulty just makes the one line narrower; removing it just makes the line wider. You cannot fix "one solution" by turning a knob.

I left it at 24% and shipped an assist mode (press **A** in the main menu) instead. That decision is written down in `docs/KNOWN_ISSUES.md` with instructions for how to re-check it, because a level that stays unsolved for nine attempts is a claim that deserves an audit trail.

## The bug that made 1.29% of the walls not exist

A more embarrassing find, from the same measurement habit.

The guards' vision had two implementations of the same idea: one to decide whether a guard can see you, one to draw the cone on screen. Both cast a ray and stepped along it at a fixed interval — 0.5 tiles for the decision, 0.25 for the drawing.

Fixed-step raycasting has a classic hole. When a ray crosses a wall's *corner* diagonally, one sample lands on this side, the next lands on the far side, and the wall cell in between is skipped entirely. The first counterexample I caught jumped from `(69,24)` straight to `(70,25)` — and `(69,25)` is solid rock.

Across 1864 sight-line pairs within 8 tiles, on the five real maps:

| | share |
|---|---|
| old and precise agree | 96.24% |
| differ only on corner strictness | 2.47% |
| **old says clear, precise says blocked** | **1.29%** |
| old says blocked, precise says clear | **0.00%** |

That last row matters. The old code only ever *under*-blocked. Every error was a guard seeing through a wall — and you could not see why, because the cone really did reach that spot. It just skipped a wall on the way.

The fix is not denser sampling. It is one exact grid-traversal function that both consumers call: step from cell boundary to cell boundary, and when the ray passes exactly through a corner, treat the corner as blocking if either of the two cells it touches is solid. One geometry, two consumers, physically impossible for the cone and the decision to disagree again. The side effect is that every guard now sees slightly less, so all five win rates moved:

| | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|
| before | 67.7% | 69.8% | 54.2% | 25.0% | 52.1% |
| **v1.8.11** | **77.1%** | **77.1%** | **50.0%** | **24.0%** | **70.8%** |

A geometry bug was worth more difficulty tuning than nine redesigns of level 4.

## Why the debug view is a feature and not a debug view

Press **F1** in game and every guard shows his state, his suspicion, and his hearing radius — plus a timeline in the corner of his last decisions in plain language:

```
34.6  #17  noticed something
34.6  #17  patrol → investigate (61,38)   [seen before, 35% suspicious]
35.5  #17  confirmed 70%: he knows you are here
35.5        a witness shouted; nearby guards are heading over
```

That timeline is not a print statement bolted on afterwards. It is what the behavior tree emits every frame, on the same code path the game plays on. If it lied, the game would lie.

I put it on a keybind instead of hiding it in a dev build because the AI is the actual product here. There is no behavior-tree library, no pathfinding library, no learned policy — perception, decision, pathing and squad coordination are all hand-written, and the interesting part of a stealth game is the moment a guard changes his mind. A vision cone can show you *what he sees*. It cannot show you *why he turned around*, and that is a cause chain spanning several seconds. Text can. So: F1.

## What is in the build

- 5 levels, one continuous run, no checkpoints
- Guards that see (real ray, walls actually block), hear (footsteps, gunshots, thrown stones), remember your last known position, and share it
- A 66-second zero-kill route that scores higher than the run that shoots
- Full source, MIT, Python + pygame, 100% procedural art — no asset packs
- Windows x64, single 28 MB exe, no installer

The repo also has the engineering write-ups: how the flow field went from 18 ms to 4 ms a frame, and the nine level-4 experiments above in more detail.

[Download](https://github.com/lxb0957/sneak_ai/releases/latest) · [Source](https://github.com/lxb0957/sneak_ai) · Bug reports welcome, especially "the bot is wrong about this level".
