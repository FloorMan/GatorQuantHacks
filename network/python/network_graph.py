"""2D weighted-graph model of the MultiPlanetary Exchange System backbone.

A standalone Python model, separate from the browser model in src/. It reads the
repo's Data/*.json files and follows the brief's launch rules (Sections 2, 4 and 5):

  light time   t_a - t_e = 8.317 min/AU x |r_receiver(t_a) - r_sender(t_e)|,
               fixed-point iteration to 1 ms (moving receiver)
  sun-blocked  the segment from the sender at t_e to the receiver at t_a passes
               within 0.10 AU of the Sun
  maintenance  the flight interval [t_e, t_a] overlaps a maintenance window
  timing       1 s serialization before every launch, 1 s processing at a relay
  loss         backbone 1 - e^(-0.02 d), direct 1 - e^(-0.08 d), d = photon path
  hop abandon  p^4 (all four hop launches lost, from the same geometry)

The graph is a snapshot at time t:

  nodes  the 9 settlements and Relay A / Relay B at their (x, y) position in AU
         (z is kept for every calculation and only dropped for drawing)
  edges  the 19 backbone links. Values are for a launch emitted at t, from the
         settlement to the relay (and A to B); status is the worse of the two
         directions.

Routes are the brief's four simple backbone routes of at most 3 links, timed hop
by hop from the departure time, as in src/physics.js.

Usage:
  python3 network/python/network_graph.py                       # edge table at t = 0
  python3 network/python/network_graph.py --hours 240 --from Ceres --to Mars
  python3 network/python/network_graph.py --weight cost --plot graph.png
  python3 network/python/network_graph.py --from Earth --to Mars --show   # open a window
"""
import argparse
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "Data"

SEC = 1 / 86400                       # one second, in days
LIGHT_MIN_PER_AU = 8.317
LIGHT_DAYS_PER_AU = LIGHT_MIN_PER_AU / 1440
LT_TOL_DAYS = 0.001 * SEC             # 1 ms tolerance on t_a
SUN_EXCLUSION_AU = 0.1
BACKBONE_LOSS = 0.02
DIRECT_LOSS = 0.08
HOP_LAUNCHES = 4
RELAY_RADIUS = math.sqrt(8)
RELAY_PERIOD = 365.2568983 * math.sqrt(8) ** 1.5

SETTLEMENTS = ["Mercury", "Venus", "Earth", "Mars", "Ceres",
               "Jupiter", "Saturn", "Uranus", "Neptune"]
RELAYS = ["Relay A", "Relay B"]
WEIGHTS = ("light_min", "distance_au", "loss", "cost")
STATUS_RANK = {"open": 0, "maintenance": 1, "sun-blocked": 2}


# ------------------------------------------------------------------ orbits

def solve_kepler(M, e):
    E = M + e * math.sin(M)
    for _ in range(50):
        dE = (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
        E -= dE
        if abs(dE) < 1e-14:
            break
    return E


def kepler_position(el, t):
    w, i, O = (math.radians(el[k]) for k in ("arg_peri_deg", "i_deg", "node_deg"))
    cw, sw, ci, si, cO, sO = math.cos(w), math.sin(w), math.cos(i), math.sin(i), math.cos(O), math.sin(O)
    P = (cO * cw - sO * sw * ci, sO * cw + cO * sw * ci, sw * si)
    Q = (-cO * sw - sO * cw * ci, -sO * sw + cO * cw * ci, cw * si)
    M = math.radians(el["mean_anomaly_deg"] + el["mean_motion_deg_day"] * t) % (2 * math.pi)
    a, e = el["a_au"], el["e"]
    E = solve_kepler(M, e)
    xp, yp = a * (math.cos(E) - e), a * math.sqrt(1 - e * e) * math.sin(E)
    return tuple(P[k] * xp + Q[k] * yp for k in range(3))


def relay_position(phase_deg, t):
    ang = math.radians(phase_deg + 360 * t / RELAY_PERIOD)
    return (RELAY_RADIUS * math.cos(ang), RELAY_RADIUS * math.sin(ang), 0.0)


def segment_origin_distance(p, q):
    v = [q[k] - p[k] for k in range(3)]
    vv = sum(c * c for c in v)
    s = -sum(p[k] * v[k] for k in range(3)) / vv if vv > 0 else 0.0
    s = max(0.0, min(1.0, s))
    return math.dist((0, 0, 0), [p[k] + s * v[k] for k in range(3)])


# ------------------------------------------------------------------ system

class System:
    """Positions, single launches and route timing. Times are days since epoch."""

    def __init__(self):
        elements = json.loads((DATA / "orbital_elements.json").read_text())
        network = json.loads((DATA / "network_model.json").read_text())
        self.bodies = {el["name"]: el for el in elements["bodies"]}
        self.relay_phase = {r["name"]: r["mean_anomaly_deg"] for r in network["relays"]}
        self.sun_exclusion = network.get("solar_exclusion_radius_au", SUN_EXCLUSION_AU)
        self.maintenance = [(frozenset(m["edge"]), m["start_hours"], m["end_hours"])
                            for m in network.get("maintenance", [])]

    def position(self, name, t):
        if name in self.relay_phase:
            return relay_position(self.relay_phase[name], t)
        return kepler_position(self.bodies[name], t)

    def maintenance_on(self, a, b, te, ta):
        key = frozenset((a, b))
        return any(edge == key and te * 24 < end and ta * 24 >= start
                   for edge, start, end in self.maintenance)

    def launch(self, sender, receiver, te, loss_coeff=BACKBONE_LOSS):
        """One launch emitted at te: moving-receiver light time, sun and maintenance tests."""
        rs = self.position(sender, te)
        ta = te
        for _ in range(30):
            rr = self.position(receiver, ta)
            nxt = te + LIGHT_DAYS_PER_AU * math.dist(rs, rr)
            done = abs(nxt - ta) < LT_TOL_DAYS
            ta = nxt
            if done:
                break
        rr = self.position(receiver, ta)
        d = math.dist(rs, rr)
        clearance = segment_origin_distance(rs, rr)
        if clearance < self.sun_exclusion:
            status = "sun-blocked"
        elif self.maintenance_on(sender, receiver, te, ta):
            status = "maintenance"
        else:
            status = "open"
        loss = 1 - math.exp(-loss_coeff * d)
        return {"from": sender, "to": receiver, "te": te, "ta": ta, "distance_au": d,
                "light_min": (ta - te) * 1440, "loss": loss, "cost": -math.log(1 - loss),
                "p_abandon": loss ** HOP_LAUNCHES, "clearance_au": clearance, "status": status}

    def evaluate_route(self, path, t_ready):
        """Backbone route timing: 1 s serialization per launch, 1 s processing at relays."""
        hops, ready = [], t_ready
        for a, b in zip(path, path[1:]):
            hop = self.launch(a, b, ready + SEC)
            hops.append(hop)
            ready = hop["ta"] + (SEC if b in RELAYS else 0)
        p_first = math.prod(1 - h["loss"] for h in hops)
        return {
            "path": path, "hops": hops, "depart": t_ready, "arrival": hops[-1]["ta"],
            "light_min": (hops[-1]["ta"] - t_ready) * 1440,
            "distance_au": sum(h["distance_au"] for h in hops),
            "loss": 1 - p_first,
            "cost": sum(h["cost"] for h in hops),
            "p_first_try": p_first,
            # Hops lose packets independently (random loss only), so these multiply.
            "p_any_hop_abandoned": 1 - math.prod(1 - h["p_abandon"] for h in hops),
            "open": all(h["status"] == "open" for h in hops),
        }

    def routes(self, src, dst, t, weight="light_min"):
        """The brief's four simple routes of at most 3 backbone links, best first."""
        paths = [[src, "Relay A", dst], [src, "Relay B", dst],
                 [src, "Relay A", "Relay B", dst], [src, "Relay B", "Relay A", dst]]
        rs = [self.evaluate_route(p, t) for p in paths]
        return sorted(rs, key=lambda r: (not r["open"], r[weight]))

    def direct(self, src, dst, t):
        """Direct service (client communication only): 1 s access + 1 s serialization
        + flight + 1 s access."""
        hop = self.launch(src, dst, t + 2 * SEC, DIRECT_LOSS)
        hop["arrival"] = hop["ta"] + SEC
        hop["one_way_min"] = (hop["arrival"] - t) * 1440
        return hop


# ------------------------------------------------------------------- graph

class WeightedGraph:
    """Undirected weighted graph with 2D node positions."""

    def __init__(self):
        self.pos = {}    # node -> (x, y)
        self.pos3 = {}   # node -> (x, y, z)
        self.adj = {}    # node -> {neighbor: edge attrs}

    def add_node(self, name, xyz):
        self.pos3[name] = xyz
        self.pos[name] = xyz[:2]
        self.adj.setdefault(name, {})

    def add_edge(self, a, b, **attrs):
        self.adj[a][b] = attrs
        self.adj[b][a] = attrs

    def edges(self):
        seen = set()
        for a, nbrs in self.adj.items():
            for b, attrs in nbrs.items():
                key = frozenset((a, b))
                if key not in seen:
                    seen.add(key)
                    yield a, b, attrs


def build_graph(t_days=0.0, system=None):
    sys_ = system or System()
    g = WeightedGraph()
    for name in SETTLEMENTS + RELAYS:
        g.add_node(name, sys_.position(name, t_days))

    links = [(s, r) for s in SETTLEMENTS for r in RELAYS] + [tuple(RELAYS)]
    for a, b in links:
        fwd = sys_.launch(a, b, t_days)
        back = sys_.launch(b, a, t_days)
        fwd["status"] = max(fwd["status"], back["status"], key=STATUS_RANK.get)
        fwd["clearance_au"] = min(fwd["clearance_au"], back["clearance_au"])
        g.add_edge(a, b, **fwd)
    return g


# -------------------------------------------------------------------- plot

COLORS = {"open": "#3d5a80", "sun-blocked": "#e63946", "maintenance": "#f4a261",
          "route": "#2a9d8f", "sun": "#ffb703", "zone": "#fb8500"}


def plot(g, out=None, weight="light_min", route=None, title="", show=False):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    route_edges = {frozenset(e) for e in zip(route, route[1:])} if route else set()
    vals = [attrs[weight] for _, _, attrs in g.edges()]
    lo, hi = min(vals), max(vals)

    fig, axes = plt.subplots(1, 3, figsize=(24, 8.5), gridspec_kw={"width_ratios": [1.3, 1, 1]})
    views = [(None, title or f"Backbone graph, edge weight = {weight}"),
             (4.5, "Inner system"),
             (0.6, f"Sun close-up: {SUN_EXCLUSION_AU} AU exclusion zone")]
    for ax, (zoom, ttl) in zip(axes, views):
        _draw(ax, g, weight, route_edges, lo, hi, zoom)
        if zoom:
            ax.set_xlim(-zoom, zoom)
            ax.set_ylim(-zoom, zoom)
        ax.set_title(ttl)

    blocked = [f"{a} - {b}" for a, b, e in g.edges() if e["status"] == "sun-blocked"]
    fig.text(0.5, 0.01, "Sun-blocked now: " + (", ".join(blocked) if blocked else "none"),
             ha="center", fontsize=11, color=COLORS["sun-blocked"] if blocked else "#555")
    fig.legend(handles=[
        Line2D([], [], color=COLORS["open"], lw=2.5, label="open"),
        Line2D([], [], color=COLORS["sun-blocked"], lw=3, ls=(0, (1, 1)), label="sun-blocked"),
        Line2D([], [], color=COLORS["maintenance"], lw=3, ls="--", label="maintenance"),
        Line2D([], [], color=COLORS["route"], lw=5, label="chosen route"),
        Patch(facecolor=COLORS["zone"], alpha=0.35, edgecolor=COLORS["zone"],
              label=f"solar exclusion ({SUN_EXCLUSION_AU} AU)"),
    ], loc="upper center", ncol=5, frameon=False, fontsize=10)
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    if out:
        fig.savefig(out, dpi=150)
        print(f"Saved plot to {out}")
    if show:
        plt.show()


def _draw(ax, g, weight, route_edges, lo, hi, zoom):
    import matplotlib.pyplot as plt
    sun_view = zoom is not None and zoom < 1
    for a, b, attrs in g.edges():
        (x0, y0), (x1, y1) = g.pos[a], g.pos[b]
        status = attrs["status"]
        on_route = frozenset((a, b)) in route_edges
        # Thicker = cheaper edge.
        width = 0.6 + 3.4 * (1 - (attrs[weight] - lo) / (hi - lo or 1))
        if status == "sun-blocked":
            ax.plot([x0, x1], [y0, y1], color=COLORS["sun-blocked"], lw=max(width, 2.5) + 1,
                    ls=(0, (1, 1)), zorder=4)
        elif status == "maintenance":
            ax.plot([x0, x1], [y0, y1], color=COLORS["maintenance"], lw=max(width, 2) + 1, ls="--", zorder=3)
        else:
            ax.plot([x0, x1], [y0, y1], color=COLORS["route"] if on_route else COLORS["open"],
                    lw=width + (3 if on_route else 0), alpha=0.95 if on_route else 0.7,
                    zorder=3 if on_route else 1)

        if sun_view:
            # Mark each link's closest approach to the Sun if it is near the zone.
            if attrs["clearance_au"] < zoom:
                px, py = _closest_point_2d((x0, y0), (x1, y1))
                c = COLORS["sun-blocked"] if status == "sun-blocked" else "#333"
                ax.scatter(px, py, s=18, color=c, zorder=6)
                ax.annotate(f"{a.replace('Relay ', 'R')}-{b.replace('Relay ', 'R')}  {attrs['clearance_au']:.3f} AU",
                            (px, py), xytext=(5, 3), textcoords="offset points", fontsize=7, color=c, zorder=6)
            continue
        mid = ((x0 + x1) / 2, (y0 + y1) / 2)
        if status == "sun-blocked":
            ax.annotate("BLOCKED", mid, ha="center", va="center", fontsize=7, fontweight="bold",
                        color="white", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.2", fc=COLORS["sun-blocked"], ec="none"))
        elif zoom is not None or attrs[weight] > 0.5 * hi:
            ax.annotate(f"{attrs[weight]:.3g}", mid, fontsize=7, color="#555", ha="center", zorder=2)

    # Solar exclusion zone: any link whose segment passes inside it is sun-blocked.
    # On the wide views it is drawn at a minimum visible size and labelled.
    r = SUN_EXCLUSION_AU
    if not sun_view:
        span = 2 * zoom if zoom else max(abs(v) for xy in g.pos.values() for v in xy) * 2
        r_draw = max(r, span * 0.012)
    else:
        r_draw = r
    ax.add_patch(plt.Circle((0, 0), r_draw * 1.8, color=COLORS["zone"], alpha=0.12, zorder=4))
    ax.add_patch(plt.Circle((0, 0), r_draw, facecolor=COLORS["zone"], alpha=0.35,
                            edgecolor=COLORS["zone"], lw=2, zorder=5))
    ax.add_patch(plt.Circle((0, 0), r_draw, fill=False, edgecolor=COLORS["zone"], lw=2, zorder=5))
    ax.scatter(0, 0, s=40 if sun_view else 60, color=COLORS["sun"], edgecolor="#c77d00", zorder=6)
    ax.annotate(f"Sun ({r} AU zone{'' if r_draw == r else ', enlarged'})", (0, 0),
                xytext=(10, -14), textcoords="offset points", fontsize=8, color="#b35c00",
                fontweight="bold", zorder=7)

    for name, (x, y) in g.pos.items():
        relay = name in RELAYS
        ax.scatter(x, y, s=140 if relay else 90, marker="D" if relay else "o",
                   color="#ee6c4d" if relay else "#293241", zorder=8)
        ax.annotate(name, (x, y), xytext=(6, 6), textcoords="offset points", fontsize=9, zorder=8)

    ax.set_aspect("equal")
    ax.set_xlabel("x (AU)")
    ax.set_ylabel("y (AU)")
    ax.grid(alpha=0.2)


def _closest_point_2d(p, q):
    vx, vy = q[0] - p[0], q[1] - p[1]
    vv = vx * vx + vy * vy
    s = max(0.0, min(1.0, -(p[0] * vx + p[1] * vy) / vv)) if vv else 0.0
    return p[0] + s * vx, p[1] + s * vy


# --------------------------------------------------------------------- cli

def fmt_min(m):
    return f"{int(m // 60)}h {m % 60:05.2f}m" if m >= 60 else f"{m:.2f} min"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=0.0, help="hours since epoch (default 0)")
    ap.add_argument("--weight", choices=WEIGHTS, default="light_min",
                    help="edge weight and route ranking (default light_min)")
    ap.add_argument("--from", dest="src", choices=SETTLEMENTS)
    ap.add_argument("--to", dest="dst", choices=SETTLEMENTS)
    ap.add_argument("--plot", metavar="PNG", help="write a 2D plot of the graph")
    ap.add_argument("--show", action="store_true", help="open the plot in a window")
    args = ap.parse_args()

    system = System()
    t = args.hours / 24
    g = build_graph(t, system)
    print(f"t = {args.hours} h   nodes = {len(g.pos)}   edges = {sum(1 for _ in g.edges())}")
    print("Edge values are for a launch emitted now (settlement -> relay); status is the worse direction.\n")
    print(f"{'edge':<22}{'d (AU)':>9}{'light min':>11}{'loss':>8}{'p_abandon':>11}{'clear AU':>10}  status")
    for a, b, e in sorted(g.edges(), key=lambda x: x[2][args.weight]):
        print(f"{a + ' - ' + b:<22}{e['distance_au']:>9.4f}{e['light_min']:>11.2f}"
              f"{e['loss'] * 100:>7.2f}%{e['p_abandon']:>11.2e}{e['clearance_au']:>10.3f}  {e['status']}")

    route = None
    if args.src and args.dst and args.src != args.dst:
        print(f"\nBackbone routes {args.src} -> {args.dst}, departing h {args.hours}, ranked by {args.weight}:")
        rs = system.routes(args.src, args.dst, t, args.weight)
        for i, r in enumerate(rs):
            tag = "BEST" if i == 0 and r["open"] else ("    " if r["open"] else "SHUT")
            print(f"  {tag} {' -> '.join(r['path']):<38} one-way {fmt_min(r['light_min']):>12}"
                  f"   first-try {r['p_first_try'] * 100:5.1f}%   P(a hop abandoned) {r['p_any_hop_abandoned']:.2e}")
            for h in r["hops"]:
                print(f"         {h['from']:>8} -> {h['to']:<8} t_e h {h['te'] * 24:9.4f}  t_a h {h['ta'] * 24:9.4f}"
                      f"  d {h['distance_au']:7.4f}  loss {h['loss'] * 100:5.2f}%  p^4 {h['p_abandon']:.2e}  {h['status']}")
        if rs[0]["open"]:
            route = rs[0]["path"]
        else:
            print(f"  No open backbone route at h {args.hours}; a sender waits for the next valid launch.")
        d = system.direct(args.src, args.dst, t)
        print(f"\nDirect service (client communication only, not for official coordination):")
        print(f"  one-way {fmt_min(d['one_way_min'])}  d {d['distance_au']:.4f} AU  "
              f"loss {d['loss'] * 100:.2f}% per packet  {d['status']}")

    if args.plot or args.show:
        plot(g, args.plot, args.weight, route,
             f"Backbone graph at t = {args.hours} h, edge weight = {args.weight}", args.show)


if __name__ == "__main__":
    main()
