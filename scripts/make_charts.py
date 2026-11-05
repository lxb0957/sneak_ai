"""生成 benchmark 图表（PNG），供 README 和简历引用。

产出两张图：
1. assets/chart_pathing.png —— 多敌人寻路耗时对比（A*/JPS/流场）
2. assets/chart_metrics.png —— AI 行为指标（发现时间/捕获率/绕后率/分散度）

用法：python scripts/make_charts.py
"""

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体（Windows 微软雅黑），避免中文显示成方块
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def chart_pathing():
    """寻路性能对比（数据来自 benchmark_pathing.py 的实测）。"""
    # 80x60 地图，目标固定，不同敌人数量下的寻路耗时（ms）
    agent_counts = [1, 10, 50, 100, 200]
    # 数据来自 scripts/benchmark_pathing.py 的实际输出（禁止切角版本的 A*）
    # 每个规模 3 轮取最小值，压制 GC / 系统调度带来的 ±20% 抖动
    astar_ms = [2.19, 17.49, 117.66, 151.71, 449.17]
    jps_ms = [1.24, 11.81, 70.56, 100.48, 275.47]
    flow_ms = [7.43, 8.92, 9.21, 9.80, 8.60]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = range(len(agent_counts))
    w = 0.25
    ax.bar([i - w for i in x], astar_ms, w, label="A* (逐敌人)", color="#E24B4A")
    ax.bar(x, jps_ms, w, label="JPS", color="#BA7517")
    ax.bar([i + w for i in x], flow_ms, w, label="Flow Field (共享)", color="#1D9E75")

    ax.set_xlabel("敌人数量")
    ax.set_ylabel("寻路耗时 (ms)")
    ax.set_title("多敌人寻路性能对比（80x60 复杂地图）")
    ax.set_xticks(list(x))
    ax.set_xticklabels(agent_counts)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # 标注核心结论
    ax.annotate(
        f"200 敌人：流场 {flow_ms[-1]:.1f}ms\nvs A* {astar_ms[-1]:.1f}ms（快 {astar_ms[-1]/flow_ms[-1]:.1f}x）",
        xy=(4, flow_ms[-1]), xytext=(2.35, 310),
        arrowprops=dict(arrowstyle="->", color="#1D9E75"),
        fontsize=9, color="#085041",
    )

    plt.tight_layout()
    out = Path(__file__).resolve().parent.parent / "assets" / "chart_pathing.png"
    plt.savefig(out, dpi=120)
    plt.close()
    print(f"已生成 {out}")


def chart_metrics():
    """AI 行为指标（来自 benchmark_ai_metrics.py 的实测）。"""
    metrics = ["平均发现时间\n(秒)", "追击捕获率\n(%)", "潜行绕后\n成功率(%)", "协同分散度\n(格)"]
    # 数值来自 scripts/benchmark_ai_metrics.py（30 局平均）
    # 数据来自 scripts/benchmark_ai_metrics.py 的实际输出
    values = [1.62, 100.0, 100.0, 3.81]
    colors = ["#378ADD", "#1D9E75", "#639922", "#BA7517"]

    fig, ax = plt.subplots(figsize=(6.5, 4))
    bars = ax.bar(metrics, values, color=colors, width=0.55)

    # 数值标注
    for b, v in zip(bars, values):
        label = f"{v:.2f}" if v < 10 else f"{v:.0f}"
        ax.text(b.get_x() + b.get_width() / 2, v + 2, label,
                ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_ylabel("数值")
    ax.set_title("AI 行为指标（30 局取平均）")
    ax.set_ylim(0, 115)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out = Path(__file__).resolve().parent.parent / "assets" / "chart_metrics.png"
    plt.savefig(out, dpi=120)
    plt.close()
    print(f"已生成 {out}")


if __name__ == "__main__":
    chart_pathing()
    chart_metrics()
