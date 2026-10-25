"""Two-panel per-step monitor risk figure for the paper.

Top: an intercepted seeded episode (risk rises past tau well before the unauthorized execution).
Bottom: a missed all-hold bank episode (risk stays low; the approved-payment path evades the monitor).
Saves a print-friendly PDF+PNG into paper_work/.
"""
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TAU = 0.5
INTERCEPT = ("runs/phase2/perstep_legit_s4.json", "Seeded (executor-seed, bank) --- intercepted")
MISSED = ("runs/phase2/perstep_luna_aall_s3.json", "Emergent all-hold (bank) --- missed")


def load(path):
    d = json.load(open(path))
    c = d["curve"]
    xs = [p["event_index"] for p in c]
    ys = [p["risk"] for p in c]
    return xs, ys, d.get("exec_index"), d


fig, axes = plt.subplots(2, 1, figsize=(6.4, 4.4), sharey=True)
for ax, (path, title) in zip(axes, [INTERCEPT, MISSED]):
    xs, ys, exec_i, d = load(path)
    ax.plot(xs, ys, color="#2a4d8f", lw=1.3, zorder=3)
    ax.axhline(TAU, ls="--", color="#999", lw=1, zorder=1)
    ax.text(xs[0], TAU + 0.04, r"alert threshold $\tau=0.5$", fontsize=7, color="#666")
    # focus the x-window around the run-up to execution (drop the post-execution tail)
    if exec_i is not None:
        ax.set_xlim(xs[0], exec_i + max(25, int(0.12 * (exec_i - xs[0]))))
        ax.axvline(exec_i, ls=":", color="#c0392b", lw=1.5, zorder=2)
        ax.text(exec_i - 2, 0.98, "unauthorized execution", fontsize=7, color="#c0392b",
                ha="right", va="top", rotation=90)
    fa = next((x for x, y in zip(xs, ys) if y >= TAU and (exec_i is None or x <= exec_i)), None)
    if fa is not None and d.get("tau_0.5", {}).get("intercepted"):
        yi = next(y for x, y in zip(xs, ys) if x == fa)
        ax.scatter([fa], [yi], color="#e08a1e", s=30, zorder=5)
        ax.annotate("first alert", (fa, yi), textcoords="offset points", xytext=(5, 7),
                    fontsize=7, color="#b5660a")
        ax.annotate("", xy=(exec_i, 0.30), xytext=(fa, 0.30),
                    arrowprops=dict(arrowstyle="<->", color="#7a7a7a", lw=0.9))
        ax.text((fa + exec_i) / 2, 0.33, f"{d['tau_0.5']['lead_time_steps']}-step lead",
                fontsize=7, color="#555", ha="center", va="bottom")
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_ylim(0, 1.05); ax.set_ylabel("risk score")
    ax.grid(axis="y", color="#eee", lw=0.6)
axes[1].set_xlabel("monitor decision step (event index)")
fig.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(f"paper_work/perstep_risk.{ext}", dpi=170, bbox_inches="tight")
print("wrote paper_work/perstep_risk.{pdf,png}")
