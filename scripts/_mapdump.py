"""把地图按 ASCII 打印出来，方便检查房间/走廊/敌人分布是否合理。"""
import sys

sys.path.insert(0, ".")

from sneak_ai import constants as C
from sneak_ai.core.world import build_demo_map, build_spawn_and_patrols

g = build_demo_map()
spawn, cfg = build_spawn_and_patrols(g)

marks = {}
for i, (x, y, pts) in enumerate(cfg):
    marks[(int(x), int(y))] = str(i % 10)
marks[(int(spawn[0]), int(spawn[1]))] = "P"
marks[(int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))] = "T"
marks[(int(C.EXFIL_POINT[0]), int(C.EXFIL_POINT[1]))] = "X"

for y in range(60):
    row = ""
    for x in range(80):
        k = (x, y)
        if k in marks:
            row += marks[k]
        else:
            row += "#" if g.is_wall(x, y) else "."
    print(f"{y:2d} {row}")
