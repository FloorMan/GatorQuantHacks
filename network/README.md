# Network model

Positions, light time, solar blockage, maintenance, and backbone routing for the brief's nine settlements, Relay A and Relay B, and the 19 backbone links. Both tools read `../Data/orbital_elements.json` and `../Data/network_model.json`.

## Network map (`index.html`)

An interactive 2D map. Open `network/index.html` in a browser. No server or build step is needed.

- Each body follows a fixed Kepler ellipse, and the relays move on √8 AU circles.
- Each link shows distance, one-way light time, backbone loss `1 − e^(−0.02·d)`, and status (open, Sun-blocked, or maintenance).
- Light time is solved with a moving receiver. Sun blockage tests the segment from the sender at t_e to the receiver at t_a against the 0.10 AU sphere. Maintenance blocks a launch when its flight interval overlaps the window.
- The route planner evaluates all four simple routes of at most 3 links. Timing includes 1 s of serialization per launch and 1 s of relay processing. It picks the fastest open route, or the route you pin.
- **Send packet** animates the route hop by hop. Each hop is emitted from the sender's position at t_e and lands at the receiver's position at t_a.
- Deep links: `index.html?from=Ceres&to=Mars&h=240&view=inner&send=1`.

## Weighted graph (`python/network_graph.py`)

The same model in Python, as a 2D weighted graph. It needs Python 3.10+ and, for plots only, matplotlib. Run from the repo root:

```
python3 network/python/network_graph.py                                    # edge table at t = 0
python3 network/python/network_graph.py --hours 240 --from Ceres --to Mars # routes, hop timings, probabilities
python3 network/python/network_graph.py --weight cost --plot graph.png     # 2D plot
python3 network/python/network_graph.py --from Earth --to Mars --show      # plot in a window
```

- Edges carry distance, light time, loss, `cost = −ln(1 − loss)`, hop-abandonment probability `p^4`, closest approach to the Sun, and status.
- Routes print each hop's t_e and t_a, the first-try success probability, and the probability that some hop is abandoned. The direct service is shown separately, for client communication only.
- `System` (positions, `launch`, `evaluate_route`, `routes`, `direct`) can be imported from other code.

Route timings and statuses match `src/physics.js` exactly (checked on 2,520 routes at 7 times, including blocked and maintenance hops).

## Layout

| Path | Purpose |
|---|---|
| `src/physics.js` | Propagation, light time, visibility, and routing. Works in the browser and in Node. |
| `src/app.js` | Canvas rendering and UI |
| `src/data.js` | Generated from `../Data/*.json` by `node network/scripts/build_data.js` |
| `scripts/check_epoch.js` | Checks the epoch positions (Section 3), period return, and radius bounds |
| `python/network_graph.py` | Python weighted-graph model, routes, probabilities, and plots |

Run `node network/scripts/check_epoch.js` to confirm the propagator still matches the brief's epoch table.
