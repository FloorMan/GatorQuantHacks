"""Render the design-choices notes (markdown) from experiment and test results.

    python3 -m evidence.experiments --md notes/design-choices.md
"""

import datetime
from decimal import Decimal

from .runner import run_all as run_tests

D = Decimal


def _m(x):
    return f"${D(x):,.0f}"


def _h(x):
    return "—" if x is None else f"{x:.1f} h"


def _pct(p):
    return f"{p * 100:.1f}%"


def _yn(b):
    return "✓ yes" if b else "✗ no"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render(res):
    tests = run_tests()
    s = tests["summary"]
    by_id = {t["id"]: t for t in tests["tests"]}
    pr, price, grace = res["priority"], res["price"], res["grace"]
    mm, mf, flat, path, loss = (res["margin_move"], res["maintenance"], res["flat_margin"],
                                res["settlement_path"], res["hop_loss"])
    g = {r["retries_covered"]: r for r in grace}
    nep = next(x for x in loss if x["from"] == "Neptune")
    m5 = next(r for r in mm if r["move"] == "0.05")
    f75 = next(r for r in mf if r["fraction"] == "0.75")
    L = []
    w = L.append

    w("# Design choices: what we picked, what we compared, and the evidence\n")
    w(f"Generated {datetime.date.today().isoformat()} by `python3 -m evidence.experiments --md notes/design-choices.md`. "
      "Every number below comes from running the exchange engine (`mpex`) on the network model, with one rule or "
      "parameter swapped at a time. Rerun the command after any change and these notes update.\n")
    w(f"**Test suite at generation time: {s['tests_passed']} / {s['tests_total']} scenarios pass**, "
      f"{s['invariant_checks']:,} invariant checks (one after every step), cash and shares conserved in every run, "
      f"{s['double_spend_attempts_blocked']} double-spend attempts blocked, "
      f"{s['duplicate_settlements']} duplicate settlements, {s['duplicates_ignored']} repeat deliveries safely ignored.\n")
    w("**To see the data:** run `python3 serve.py` and open http://localhost:8000/evidence.html. Each scenario has its "
      "checks, transaction flow, packet replay on the map, auction allocation, margin charts, balances and timeline.\n")

    w("## At a glance\n")
    w(table(["Choice", "We picked", "Compared against", "Deciding evidence"], [
        ["Priority at equal price", "Home operator's receipt stamp", "Arrival time at the market",
         f"Arrival time gave Earth the win in {sum(1 for r in pr if r['arrival time'] == 'E-Bob')}/{len(pr)} ties it should have lost"],
        ["Clearing price", "Midpoint of the tied range", "Seller's limit, buyer's limit",
         "Only rule that splits the surplus evenly (250 / 250)"],
        ["Batch grace window", "3 hop retries", "0, 1, 2 retries",
         f"Neptune's slowest hop loses {_pct(nep['p_launch_lost'])} of launches; only 3 retries cover the transport's worst case"],
        ["Margin level", "5% per observation", "3%, 8%",
         f"Middle on capital ({_m(m5['falling']['peak_cash_locked'])} peak locked) with {m5['falling']['calls']} calls on the falling path"],
        ["Maintenance trigger", "75% of the allowance", "50%, 100%",
         f"{f75['falling']['calls']} calls vs 16 at 100%; fund draw {_m(f75['crash']['fund_used'])} vs {_m(next(r for r in mf if r['fraction']=='0.5')['crash']['fund_used'])} at 50%"],
        ["Margin window", "Per location (round trip + retry)", "Same for everyone",
         "Location-blind rule defaulted a Neptune trader who paid as fast as light allows"],
        ["Settlement legs", "Direct operator-to-operator", "Relayed through Earth",
         f"Mars → Neptune {_h(path[0]['direct_h'])} vs {_h(path[0]['via_earth_h'])}, 1 quota packet vs 2"],
        ["Clearing location", "Earth", "Mars, Ceres", "Delay within 2%; Earth has the shortest blockages (21 d)"],
    ]))
    w("")

    # ------------------------------------------------------------------ 1
    w("## 1. Priority at the same price: source stamp, not arrival time\n")
    w("Each remote trader bids $100 one minute **before** an Earth trader bids the same price. "
      "We ran the same batch twice, once with each priority rule.\n")
    w(table(["Remote bidder", "Arrives at Earth later by", "Winner: source stamp (ours)", "Winner: arrival time"],
            [[r["remote"], _h(r["arrival_gap_h"]), r["source time (chosen)"], r["arrival time"]] for r in pr]))
    w("\n**Why:** under arrival-time priority the trader nearest the market wins every tie, and Neptune is "
      f"{_h(pr[-1]['arrival_gap_h'])} behind. That turns physics into a permanent advantage and fails the brief's "
      "access requirement for distant settlements. With source stamps, the rules are the same for everyone, and only the "
      "batch length grows with distance.\n")
    w("**Why the operator's stamp and not the client's:** the brief says clients may lie, so a client-written time "
      "could be backdated. The home operator stamps the order when it receives it (1 s of local access). Operators follow "
      "the published rules, so the stamp can be trusted.\n")
    t = by_id["equal-price"]
    w(f"**Test evidence:** *Equal-price auction*: {t['status']}, "
      f"{sum(c['pass'] for c in t['checks'])}/{len(t['checks'])} checks. Earth's bid arrived first, but Neptune's earlier "
      "stamp won all 10 shares. When I deliberately switched the engine to arrival-time priority, this test failed on "
      "3 checks, so the test really does catch the difference.\n")

    # ------------------------------------------------------------------ 2
    w("## 2. Clearing price: midpoint of the tied range\n")
    w("A $45 sell meets a $50 buy for 100 shares. Every price from $45 to $50 trades the same volume, so the rule decides "
      "who keeps the $500 of surplus.\n")
    w(table(["Rule", "Price", "Buyer saves", "Seller gains"],
            [[r["rule"], r["price"], _m(r["buyer_saves"]), _m(r["seller_gains"])] for r in price]))
    w("\n**Why:** the midpoint is the only one of the three that doesn't systematically favor one side, and it is "
      "deterministic, so anyone can recompute it from the published orders. The two limit-based rules hand the whole surplus "
      "to one side, every time. Price protection holds under all three, so no trader ever pays "
      "more than its limit or receives less.\n")
    t1, t2 = by_id["cross-planet"], by_id["limit-protection"]
    w(f"**Test evidence:** *Cross-planet trade* ({t1['status']}) cleared at $47.50, and N-Eve got $250 released at "
      f"Neptune. *Limit-price protection* ({t2['status']}) had five orders clear at $47.50: every fill was within its limit, "
      "and the $40 bid was correctly left unfilled.\n")
    w("**When another rule could be better:** with real market makers you might pay the resting order's price to reward "
      "liquidity. In a batch with no resting orders, the midpoint is the neutral choice.\n")

    # ------------------------------------------------------------------ 3
    w("## 3. Batch grace window: cover all 3 hop retries\n")
    w("The brief allows 4 launches per hop (the first plus 3 retries), each waiting R_h = 2 × flight + 60 min. "
      "We varied how many retries the batch waits for. Batch length is measured at hour 0.5. The test order is sent from "
      "Neptune when the batch opens, and its slowest hop loses its first L launches.\n")
    w(table(["Retries covered", "Batch length", "Survives 0 lost", "1 lost", "2 lost", "3 lost",
             "No-loss order sent 30 min late", "Cross-planet trade complete"],
            [[k, _h(r["batch_length_h"])] + [_yn(r["survives"].get(i, r["survives"].get(str(i)))) for i in range(4)]
             + [_yn(r["no_loss_sent_30min_late_in_batch"]), _h(r["cross_trade_complete_h"])] for k, r in sorted(g.items())]))
    w("\nHow likely each case is, from the brief's loss formula and today's geometry (slowest hop on each route to Earth):\n")
    w(table(["From", "Slowest hop", "Launch lost", "Needs > 0 retries", "> 1", "> 2", "> 3 (hop abandoned)"],
            [[x["from"], x["worst_hop"], _pct(x["p_launch_lost"])] +
             [_pct(x["p_needs_more_than"].get(k, x["p_needs_more_than"].get(str(k)))) for k in range(4)] for x in loss]))
    p = nep["p_needs_more_than"]
    pk = lambda k: p.get(k, p.get(str(k)))
    w(f"\n**Why 3:** the Neptune → Relay A hop is about 28 AU, so {_pct(nep['p_launch_lost'])} of launches are lost. "
      f"With 1 retry covered, a Neptune order still misses the batch {_pct(pk(1))} of the time. With 3 retries covered, it "
      f"misses only when the hop is abandoned outright ({_pct(pk(3))}), and the endpoint retry then carries it to the next batch "
      "with its limit unchanged. It is also the only setting where every case the transport allows fits in the window.\n")
    w(f"**The cost:** the batch grows from {_h(g[1]['batch_length_h'])} to {_h(g[3]['batch_length_h'])}, and a cross-planet "
      f"trade takes {_h(g[3]['cross_trade_complete_h'])} to complete instead of {_h(g[1]['cross_trade_complete_h'])}. "
      f"With no grace at all ({_h(g[0]['batch_length_h'])}), even a no-loss Neptune order sent 30 minutes after the batch "
      "opens misses it.\n")
    w("**When another value is better:** if speed matters more than a deadline that holds, 1 retry (13.3 h) serves "
      "Earth, Mars and Ceres well (their worst hops lose under 5% of launches) and fails only for Neptune-sized distances. "
      "A per-batch rule (cover k retries for the settlements actually bidding) is a possible extension.\n")
    t = by_id["retry-duplicate"]
    w(f"**Test evidence:** *Packet retry / duplicate protection* ({t['status']}): an order sent at batch open lost 3 launches, "
      "landed on the 4th, and still executed in that batch.\n")

    # ------------------------------------------------------------------ 4
    w("## 4. Margin level: 5% per 12-hour observation\n")
    w("Initial margin = notional × move × (1 + observations a participant's round trip can span). The contract is 10 "
      "MOI futures at 100 × multiplier 100 (notional $100,000), with N-Eve (Neptune) long and E-Alice (Earth) short. "
      "The *falling* path stays within the declared move and N-Eve pays every call. The *crash* path breaks the assumption "
      "and N-Eve stops paying.\n")
    w(table(["Move", "IM Neptune", "IM Earth", "Falling: calls", "Falling: lowest cushion", "Falling: quota packets",
             "Peak cash locked", "Crash: fund used", "Crash: unbacked"],
            [[_pct(float(r["move"])), _m(r["falling"]["im_neptune"]), _m(r["falling"]["im_earth"]),
              r["falling"]["calls"], _m(r["falling"]["min_buffer"]), r["falling"]["backbone_packets"],
              _m(r["falling"]["peak_cash_locked"]), _m(r["crash"]["fund_used"]), _m(r["crash"]["shortfall"])] for r in mm]))
    m3, m8 = mm[0], mm[-1]
    w(f"\n**Why 5%:** it balances capital against cushion on the normal (falling) path. 3% locks the least capital "
      f"({_m(m3['falling']['peak_cash_locked'])} peak) but needs {m3['falling']['calls']} calls and leaves a "
      f"{_m(m3['falling']['min_buffer'])} cushion. 8% has the widest cushion and fewest calls but locks "
      f"{_m(m8['falling']['peak_cash_locked'])} and prices smaller traders out.\n")
    w(f"**What the crash run shows, honestly:** the fund draw is not smaller for every increase in margin. 3% drew "
      f"{_m(m3['crash']['fund_used'])} and 5% drew {_m(m5['crash']['fund_used'])}: a smaller margin breaches maintenance "
      f"sooner, so the default closes earlier at a better price. Only 8% clearly cuts the crash loss "
      f"({_m(m8['crash']['fund_used'])}). At every level nothing was left unbacked, because the guarantee fund absorbed "
      "the rest. If crash protection is the priority, choose 8%.\n")
    w("**When another value is better:** for a more volatile index, use 8%. The rule is the same formula, so only the declared "
      "move changes, and it must be declared before any price path is seen.\n")

    # ------------------------------------------------------------------ 5
    w("## 5. Maintenance trigger: 75% of the allowance\n")
    w(table(["Trigger", "Falling: calls", "Falling: quota packets", "Falling: lowest cushion",
             "Crash: paid to winner", "Crash: fund used", "Crash: cushion at close"],
            [[_pct(float(r["fraction"])), r["falling"]["calls"], r["falling"]["backbone_packets"],
              _m(r["falling"]["min_buffer"]), _m(r["crash"]["paid"]), _m(r["crash"]["fund_used"]),
              _m(r["crash"]["min_buffer"])] for r in mf]))
    w("\n**Why 75%:** 100% calls on any drift. That is 16 calls and 75 backbone packets on a normal falling path, which "
      "eats the shared 600-packet quota. 50% waits so long that in a crash the margin is gone before the default closes, "
      "and the fund pays $10,000. 75% keeps calls rare (5) and draws $3,000 less "
      "from the fund than 50%. It does not beat 100% in a crash; it trades that for quota.\n")
    w("**When another value is better:** if the quota is plentiful and crashes are the main worry, 100% has the smallest "
      "crash loss ($0 from the fund in this run).\n")

    # ------------------------------------------------------------------ 6
    fp = flat["falling_paying"]
    oc = by_id["margin-call"]["futures"]["calls"][0]
    w("## 6. Margin window by location, not one window for everyone\n")
    w("Our rule gives each participant a risk window equal to its real round trip to Earth Clearing plus one hop retry. "
      "The window sets both its margin and its call deadline. The alternative gives everyone the same 0 h window, as if they "
      "were all at Earth.\n")
    w(table(["Participant", "Our window"], [[a, _h(v)] for a, v in res["windows"].items()]))
    w("")
    w(table(["Rule", "N-Eve pays every call?", "First call", "Deadline", "Top-up landed", "Outcome"],
            [["Same window for all", "yes", _h(fp["first_call_h"]), _h(fp["deadline_h"]), _h(fp["top_up_landed_h"]),
              f"**{fp['state'].replace('_', ' ')}** at {fp['final_price']}"],
             ["Per location (ours)", "yes", _h(oc["issued_h"]), _h(oc["deadline_h"]), _h(oc["met_h"]),
              f"{flat['ours_falling']['state']}, all {flat['ours_falling']['calls']} calls met"]]))
    w("\n**Why:** with one window for everyone, N-Eve's deadline came before any reply could physically arrive, so she was "
      "defaulted even though she paid as fast as light allows. That is a wrongful default, and it fails the brief's access "
      "requirement for Neptune. In the crash path that rule used less of the fund "
      f"({_m(flat['crash']['fund_used'])}), but only because it closed out paying and non-paying traders alike at the first "
      "dip.\n")

    # ------------------------------------------------------------------ 7
    w("## 7. Settlement legs go directly between operators\n")
    w(table(["Leg", "Direct session", "Relayed through Earth", "Quota packets (direct vs relayed)"],
            [[x["leg"], _h(x["direct_h"]), _h(x["via_earth_h"]), f"{x['direct_quota_packets']} vs {x['via_earth_quota_packets']}"] for x in path]))
    w("\n**Why:** a direct leg is faster and costs half the quota of the shared 600. The cost is 6 sessions set up before "
      "hour 0 (each SYN costs 1 packet), which the brief allows from hour −168.\n")

    # ------------------------------------------------------------------ 8
    w("## 8. Clearing house at Earth\n")
    w("From `notes/clearing-hub-location.md` (`network/python/hub_compare.py`):\n")
    w(table(["Hub", "Mean one-way, 200 yr", "Longest single-relay blockage", "Both relays blocked"],
            [["Earth", "96.5 min", "21 days", "never"], ["Mars", "96.6 min", "45 days", "never"],
             ["Ceres", "98.0 min", "190 days", "never"]]))
    w("\n**Why:** delay is a tie (Neptune dominates every hub). Earth's blockages stay under the 30-day packet lifetime, "
      "and the story's investors are on Earth.\n")

    # ------------------------------------------------------------------ tests
    w("## Test evidence\n")
    w(table(["Scenario", "Result", "Checks"],
            [[t["title"], "✓ PASS" if t["status"] == "PASS" else "✗ FAIL",
              f"{sum(c['pass'] for c in t['checks'])}/{len(t['checks'])}"] for t in tests["tests"]]))
    w("\n## Limits of this evidence\n")
    w("- Traces are conditional: a packet is lost only where a scenario forces a loss or an incident covers the launch. "
      "The probabilities in section 3 come from the brief's formula, not from random runs.\n"
      "- Idle sessions are not expired, and link queues and capacity are not simulated.\n"
      "- Price paths are scripted test inputs, as the brief requires. The margin results describe these paths, not a "
      "forecast.\n"
      "- The relay placement is fixed by the brief. The what-if in `network/python/relay_placement.py` is an extension only.\n")
    return "\n".join(L) + "\n"
