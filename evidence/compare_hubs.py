"""Run every scenario with the hub at Earth and at Ceres, and compare what the judges score.

    python3 -m evidence.compare_hubs          # figure + notes for Earth vs Ceres
    python3 -m evidence.compare_hubs --hubs Earth Ceres Mars

Each hub runs in its own process (MPEX_HUB=<hub>), so the batch market, clearing
house, margin windows and sessions all move together. Nothing else changes: same
accounts, orders, price paths, incidents and rules.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ inside one hub's process

def collect():
    from decimal import Decimal as D

    import network_graph as ng
    from mpex import EventType  # noqa: F401
    from . import cases
    from .runner import run_all
    from .scenarios import HUB, MARGIN_POLICY, OPERATORS, Run

    out = {"hub": HUB}
    suite = run_all()
    out["suite"] = {k: suite["summary"][k] for k in ("tests_passed", "tests_total", "invariant_checks")}
    out["quota_packets"] = sum(t["network"]["backbone_quota_packets"] for t in suite["tests"] if t.get("network"))
    out["launches"] = sum(t["network"]["launches"] for t in suite["tests"] if t.get("network"))

    out["windows"] = {a: MARGIN_POLICY.window(a) for a, *_ in cases.HOME.items()}
    out["batch_h0"] = Run("x", "x").batch_timing(0.5)

    r = cases.cross_planet()
    t = next(iter(r.ex.state.trades.values()))
    legs = r.ex.state.trade_legs[t.id]
    out["cross"] = {"executed_h": t.executed_h - 1.0, "complete_h": t.settled_h - 1.0,
                    "shares_usable_h": r.ex.state.transfers[legs["shares"]["transfer_id"]].completed_h - 1.0,
                    "cash_usable_h": r.ex.state.transfers[legs["cash"]["transfer_id"]].completed_h - 1.0}

    r = cases.futures_opening()
    out["futures_open"] = {"opened_after_h": r.extra["opened_h"] - 0.5, "fund_ready_h": r.extra.get("fund_ready_h"),
                           "im": r.extra["im"]}

    r, *_ = cases._futures_run("x", "x", cases.FALLING, respond=True, until=320)
    pos = r.ex.state.positions[r.extra["position"]]
    calls = r.extra["calls"]
    out["margin_call"] = {"calls": len(calls), "met": sum(c["met_h"] is not None and c["met_h"] <= c["deadline_h"] for c in calls),
                          "mean_response_h": sum(c["met_h"] - c["issued_h"] for c in calls) / len(calls),
                          "peak_cash_locked": float(D(r.ex.snapshot()["metrics"]["peak_encumbered"]["NEO"])),
                          "winner_spendable_delay_h": pos.spendable_h - pos.discharged_h,
                          "state": pos.state.value}

    r, *_ = cases._futures_run("x", "x", cases.CRASH, respond=False, until=320)
    pos = r.ex.state.positions[r.extra["position"]]
    out["crash"] = {"fund_used": float(pos.guarantee_used), "default_h": pos.discharged_h,
                    "winner_spendable_delay_h": pos.spendable_h - pos.discharged_h, "state": pos.state.value}

    tests = {t["id"]: t for t in suite["tests"]}
    o = tests["clearing-outage"]["extra"]["outage"]
    out["outage"] = {"lost_service_h": o["lost_service_h"], "restored_h": o["service_restored_h"]}
    d = cases.disconnection()
    tr = [t for t in d.ex.state.trades.values() if t.symbol == "ARES"][0]
    out["disconnection_complete_h"] = tr.settled_h

    # Batch length over 200 years (best routes at each epoch; grace = 3 hop retries).
    net = ng.System()
    years, lengths = [], []
    for y in range(0, 201):
        t = y * 365.25
        slow, retry = 0.0, 0.0
        for s in sorted({h for h in cases.HOME.values() if h != HUB}):
            r0 = net.routes(s, HUB, t)[0]
            slow = max(slow, r0["light_min"] / 60)
            retry = max(retry, max(2 * (h["ta"] - h["te"]) * 24 + 1 for h in r0["hops"]))
        years.append(y)
        lengths.append(1.1 * slow + 3 * retry)
    out["batch_by_year"] = {"years": years, "hours": lengths}
    return out


# ------------------------------------------------------------------ driver

def run_hub(hub):
    env = dict(os.environ, MPEX_HUB=hub, PYTHONPATH=f"{ROOT}:{ROOT / 'network' / 'python'}")
    code = "import json; from evidence.compare_hubs import collect; print('JSON' + json.dumps(collect()))"
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(res.stderr[-3000:])
    return json.loads(res.stdout.split("JSON", 1)[1])


def figure(data, out_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    color = {"Earth": "#2a78d6", "Ceres": "#1baf7a", "Mars": "#eb6834"}
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    hubs = list(data)
    metrics = [
        ("Cross-planet trade complete", lambda d: d["cross"]["complete_h"], "hours"),
        ("Batch length at hour 0", lambda d: d["batch_h0"]["duration_h"], "hours"),
        ("Futures position opens after", lambda d: d["futures_open"]["opened_after_h"], "hours"),
        ("Margin-call response time", lambda d: d["margin_call"]["mean_response_h"], "hours, mean"),
        ("Winner can spend, after close", lambda d: d["margin_call"]["winner_spendable_delay_h"], "hours"),
        ("Peak cash locked (falling path)", lambda d: d["margin_call"]["peak_cash_locked"] / 1000, "$ thousand"),
        ("Clearing outage: service lost", lambda d: d["outage"]["lost_service_h"], "hours"),
        ("Backbone quota packets, all 17", lambda d: d["quota_packets"], "packets"),
    ]
    fig = plt.figure(figsize=(15, 11), facecolor="#fcfcfb")
    gs = fig.add_gridspec(3, 4, height_ratios=[1, 1, 1.25], hspace=0.75, wspace=0.45,
                          left=0.06, right=0.97, top=0.86, bottom=0.07)
    fig.suptitle(f"Clearing house at {' vs '.join(hubs)}: the same 17 scenarios, rerun",
                 x=0.06, ha="left", fontsize=16, fontweight="bold", color=ink)
    fig.text(0.06, 0.905, "Every number below comes from running the exchange engine on the network model with the hub "
             "moved; lower is better in every panel.", fontsize=11, color=muted)
    for i, (title, fn, unit) in enumerate(metrics):
        ax = fig.add_subplot(gs[i // 4, i % 4])
        vals = [fn(data[h]) for h in hubs]
        bars = ax.bar(hubs, vals, 0.6, color=[color.get(h, "#888") for h in hubs])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,.1f}" if v < 1000 else f"{v:,.0f}",
                    ha="center", va="bottom", fontsize=9, color=ink)
        best = hubs[int(np.argmin(vals))]
        spread = (max(vals) - min(vals)) / max(max(vals), 1e-9)
        verdict = f"better: {best}" if spread >= 0.02 else "tie (within 2%)"
        ax.set_title(title, loc="left", fontsize=10, fontweight="bold", color=ink, pad=16)
        ax.text(0, 1.03, f"{unit} · {verdict}", transform=ax.transAxes, fontsize=8,
                color=ink if spread >= 0.02 else muted, fontweight="bold" if spread >= 0.02 else "normal")
        ax.set_ylim(0, max(vals) * 1.22 if max(vals) > 0 else 1)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(colors=muted, labelsize=8.5)
        ax.grid(axis="y", color=grid, lw=0.8)
        ax.set_axisbelow(True)
    ax = fig.add_subplot(gs[2, :])
    for h in hubs:
        y = data[h]["batch_by_year"]
        ax.plot(y["years"], y["hours"], color=color.get(h, "#888"), lw=2, label=h)
        ax.text(y["years"][-1] + 1.5, y["hours"][-1], f"{h} (mean {np.mean(y['hours']):.1f} h)",
                fontsize=8.5, color=ink, va="center")
    ax.set_xlim(0, 228)
    ax.set_xlabel("Years from epoch", color=muted, fontsize=9)
    ax.set_ylabel("Batch length, hours", color=muted, fontsize=9)
    ax.set_title("Batch length over 200 years (sets how long every cross-planet trade waits)", loc="left",
                 fontsize=10.5, fontweight="bold", color=ink, pad=16)
    ax.text(0, 1.03, "1.1 × slowest one-way + 3 hop retries, from the best route to the hub each year",
            transform=ax.transAxes, fontsize=8, color=muted)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=muted, labelsize=8.5)
    ax.grid(axis="y", color=grid, lw=0.8)
    ax.legend(frameon=False, fontsize=9, loc="upper left", ncol=len(hubs))
    fig.text(0.06, 0.015, "Source: python3 -m evidence.compare_hubs (MPEX_HUB per run). Accounts, orders, price paths "
             "and incidents are identical across hubs.", fontsize=8, color=muted)
    fig.savefig(out_png, dpi=150, facecolor=fig.get_facecolor())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hubs", nargs="+", default=["Earth", "Ceres"])
    ap.add_argument("--png", default=str(ROOT / "notes" / "hub-earth-vs-ceres.png"))
    ap.add_argument("--json", default=str(ROOT / "notes" / "hub-earth-vs-ceres.json"))
    args = ap.parse_args()
    data = {h: run_hub(h) for h in args.hubs}
    Path(args.json).write_text(json.dumps(data, indent=1))
    figure(data, args.png)
    print(json.dumps({h: {k: v for k, v in d.items() if k != "batch_by_year"} for h, d in data.items()}, indent=1))
    print(f"wrote {args.png} and {args.json}")


if __name__ == "__main__":
    main()
