# Clearing hub location: Earth vs Mars vs Ceres

Analysis from 2026-10-03. Reproduce with `python3 network/python/hub_compare.py`
(routes from `network/python/network_graph.py`, which follows the brief's launch rules).

## Decision

**Earth** (Mars is a near-tie). Choose by where most accounts and the price source sit,
because local access is free (1 s, no loss, no quota). Brief's story, "Earth investors fund
Mars construction", fits an Earth hub and a Mars price source. If accounts end up mostly
at Mars, pick Mars and add a re-pin rule (below).

## Delay: a tie

Best open backbone route from every other settlement to the hub.

| Hub | Mean one-way, h 0–720 | Mean one-way, 200 yr | Mean round trip, 200 yr | Worst one-way |
|---|---|---|---|---|
| Earth | 85.2 min | 96.5 min | 3.22 h | 5.19 h |
| Mars | 82.7 min | 96.6 min | 3.22 h | 5.18 h |
| Ceres | 82.1 min | 98.0 min | 3.27 h | 5.15 h |

- Neptune is the worst-served settlement for every hub (~4.4 h one way) and dominates.
- No hub ever had zero open routes (sampled at 4 h and 30 d steps, so short closures may be missed).

## Reliability: decides it

200-year scan, daily steps, Sun test at one instant (approximate).

| Hub | Time with one relay blocked | Longest single-relay spell | Both relays blocked |
|---|---|---|---|
| Earth | 8.6% | 21 days | never |
| Mars | 6.2% | 45 days | never |
| Ceres | 0.8% | 190 days | never |

- Spell length matters more than frequency. A session pinned to a blocked relay goes silent:
  sessions expire after 7 days with no session packet, packets after 30 days.
- Earth's spells stay under the 30-day packet lifetime, so queued messages survive.
- Mars's 45-day spells can outlast it, so it needs a rule to re-pin those sessions through
  the other relay (each new handshake costs 1 backbone quota packet).
- Ceres: rare but very long blockages, and Relay B to Ceres maintenance (hours 240–264) falls inside
  the 240-hour obligation window.

## Figure

![Earth vs Mars vs Ceres](hub-comparison.png)

Regenerate with `python3 network/python/hub_plots.py`. The deciding panel is C: over 200 years,
**0 of Earth's 316** single-relay blockages pass 30 days, against **88 of Mars's 128** and
**4 of Ceres's 4**. A blockage longer than the 30-day packet lifetime means packets queued on the
blocked relay expire, so sessions must be re-routed (one quota packet per new handshake).

## Line for the paper

Hub location barely changes delay (under 2%). We chose Earth for its shorter blockages
(max 21 days; none of 316 pass the 30-day packet lifetime, vs 88 of 128 for Mars) and local access to our accounts.

## To firm up for E4

Rerun the reliability scan with the brief's moving-receiver rule and refined spell
boundaries before quoting the numbers as evidence.
