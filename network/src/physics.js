// Orbit propagation, light-time, visibility and backbone routing for the
// MultiPlanetary Exchange System brief. Works in the browser (window.Physics)
// and in Node (require('./src/physics.js')).
//
// Units: time t is elapsed DAYS since epoch (2026-09-22 00:00 TDB, t = 0).
// Positions are heliocentric J2000 ecliptic, in AU.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Physics = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DEG = Math.PI / 180;
  const SEC = 1 / 86400;                 // one second, in days
  const LIGHT_MIN_PER_AU = 8.317;
  const LIGHT_DAYS_PER_AU = LIGHT_MIN_PER_AU / 1440;
  const SUN_EXCLUSION_AU = 0.1;
  const RELAY_RADIUS = Math.sqrt(8);
  const RELAY_PERIOD = 365.2568983 * Math.pow(Math.sqrt(8), 1.5);
  const JULIAN_YEAR = 365.25;
  const LT_TOL_DAYS = 0.001 * SEC;       // 1 ms tolerance on t_a

  const SETTLEMENTS = ['Mercury', 'Venus', 'Earth', 'Mars', 'Ceres',
                       'Jupiter', 'Saturn', 'Uranus', 'Neptune'];
  const RELAYS = ['Relay A', 'Relay B'];
  const NODE_ID = {
    Mercury: 1, Venus: 2, Earth: 3, Mars: 4, Ceres: 5, Jupiter: 6,
    Saturn: 7, Uranus: 8, Neptune: 9, 'Relay A': 10, 'Relay B': 11,
  };

  // ---------------------------------------------------------------- bodies

  function solveKepler(M, e) {
    let E = M + e * Math.sin(M);
    for (let k = 0; k < 50; k++) {
      const dE = (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
      E -= dE;
      if (Math.abs(dE) < 1e-14) break;
    }
    return E;
  }

  // Precompute the orbital-plane -> ecliptic rotation (omega, then i, then Omega).
  function makeBody(el) {
    const w = el.arg_peri_deg * DEG, i = el.i_deg * DEG, O = el.node_deg * DEG;
    const cw = Math.cos(w), sw = Math.sin(w), ci = Math.cos(i), si = Math.sin(i);
    const cO = Math.cos(O), sO = Math.sin(O);
    return {
      name: el.name,
      a: el.a_au, e: el.e,
      M0: el.mean_anomaly_deg * DEG,
      n: el.mean_motion_deg_day * DEG,
      period: el.period_days,
      P: [cO * cw - sO * sw * ci, sO * cw + cO * sw * ci, sw * si],
      Q: [-cO * sw - sO * cw * ci, -sO * sw + cO * cw * ci, cw * si],
    };
  }

  function keplerPosition(b, t) {
    let M = (b.M0 + b.n * t) % (2 * Math.PI);
    if (M < 0) M += 2 * Math.PI;
    const E = solveKepler(M, b.e);
    const xp = b.a * (Math.cos(E) - b.e);
    const yp = b.a * Math.sqrt(1 - b.e * b.e) * Math.sin(E);
    return [b.P[0] * xp + b.Q[0] * yp, b.P[1] * xp + b.Q[1] * yp, b.P[2] * xp + b.Q[2] * yp];
  }

  // Relays: prograde circles of radius sqrt(8) AU in the reference plane.
  function relayPosition(phaseDeg, t) {
    const ang = (phaseDeg + 360 * t / RELAY_PERIOD) * DEG;
    return [RELAY_RADIUS * Math.cos(ang), RELAY_RADIUS * Math.sin(ang), 0];
  }

  function createSystem(orbitalElements, networkModel) {
    const bodies = {};
    for (const el of orbitalElements.bodies) bodies[el.name] = makeBody(el);
    const relayPhase = {};
    for (const r of networkModel.relays) relayPhase[r.name] = r.mean_anomaly_deg;
    const maintenance = (networkModel.maintenance || []).map(m => ({
      edge: m.edge.slice().sort(), startH: m.start_hours, endH: m.end_hours,
    }));

    function position(name, t) {
      if (name in relayPhase) return relayPosition(relayPhase[name], t);
      return keplerPosition(bodies[name], t);
    }

    // Sample a full orbit (true AU coords) for drawing.
    function orbitPath(name, samples) {
      const pts = [];
      if (name in relayPhase) {
        for (let k = 0; k <= samples; k++) {
          const a = 2 * Math.PI * k / samples;
          pts.push([RELAY_RADIUS * Math.cos(a), RELAY_RADIUS * Math.sin(a), 0]);
        }
        return pts;
      }
      const b = bodies[name];
      for (let k = 0; k <= samples; k++) {
        const E = 2 * Math.PI * k / samples;
        const xp = b.a * (Math.cos(E) - b.e);
        const yp = b.a * Math.sqrt(1 - b.e * b.e) * Math.sin(E);
        pts.push([b.P[0] * xp + b.Q[0] * yp, b.P[1] * xp + b.Q[1] * yp, b.P[2] * xp + b.Q[2] * yp]);
      }
      return pts;
    }

    function maintenanceOn(a, b, teDays, taDays) {
      const key = [a, b].sort();
      const teH = teDays * 24, taH = taDays * 24;
      for (const m of maintenance) {
        if (m.edge[0] === key[0] && m.edge[1] === key[1] && teH < m.endH && taH >= m.startH) return m;
      }
      return null;
    }

    // One launch from `from` (emitted at te) to `to`: solve light time with a
    // moving receiver, then test solar exclusion on the emission->arrival segment.
    function launch(from, to, te, lossCoeff) {
      const rs = position(from, te);
      let ta = te, rr = position(to, ta), d = 0;
      for (let k = 0; k < 30; k++) {
        rr = position(to, ta);
        d = dist(rs, rr);
        const next = te + LIGHT_DAYS_PER_AU * d;
        if (Math.abs(next - ta) < LT_TOL_DAYS) { ta = next; break; }
        ta = next;
      }
      rr = position(to, ta);
      d = dist(rs, rr);
      const clearance = segmentOriginDistance(rs, rr);
      const sunBlocked = clearance < SUN_EXCLUSION_AU;
      const maint = maintenanceOn(from, to, te, ta);
      return {
        from, to, te, ta, d, flight: ta - te,
        rs, rr, clearance, sunBlocked, maintenance: maint,
        loss: 1 - Math.exp(-lossCoeff * d),
        open: !sunBlocked && !maint,
        status: sunBlocked ? 'sun-blocked' : (maint ? 'maintenance' : 'open'),
      };
    }

    // Backbone route timing per the brief: at each hop, 1 s serialization
    // before emission; a relay adds 1 s processing after arrival.
    function evaluateRoute(path, tReady) {
      const hops = [];
      let ready = tReady;
      for (let h = 0; h < path.length - 1; h++) {
        const te = ready + SEC;
        const hop = launch(path[h], path[h + 1], te, 0.02);
        hop.ready = ready;
        hops.push(hop);
        ready = hop.ta + (RELAYS.includes(path[h + 1]) ? SEC : 0);
      }
      const arrival = hops[hops.length - 1].ta;
      const pFirstTry = hops.reduce((p, x) => p * (1 - x.loss), 1);
      return {
        path, hops, depart: tReady, arrival, oneWay: arrival - tReady,
        open: hops.every(x => x.open),
        blockedBy: hops.filter(x => !x.open),
        pAllFirstTry: pFirstTry,
      };
    }

    // Simple routes of at most 3 backbone links between two settlements.
    function candidateRoutes(from, to) {
      return [
        [from, 'Relay A', to],
        [from, 'Relay B', to],
        [from, 'Relay A', 'Relay B', to],
        [from, 'Relay B', 'Relay A', to],
      ];
    }

    function routesAt(from, to, t) {
      const rs = candidateRoutes(from, to).map(p => evaluateRoute(p, t));
      rs.sort((x, y) => (y.open - x.open) || (x.oneWay - y.oneWay));
      return rs;
    }

    // Direct service: 1 s local access + 1 s serialization + flight + 1 s local access.
    function direct(from, to, t) {
      const hop = launch(from, to, t + 2 * SEC, 0.08);
      return Object.assign(hop, { depart: t, arrival: hop.ta + SEC, oneWay: hop.ta + SEC - t });
    }

    // Earliest departure >= t at which some route is fully open (coarse scan, then bisect).
    function nextOpenDeparture(from, to, t, horizonDays, stepDays) {
      const anyOpen = tt => routesAt(from, to, tt)[0].open;
      if (anyOpen(t)) return t;
      let prev = t;
      for (let tt = t + stepDays; tt <= t + horizonDays; tt += stepDays) {
        if (anyOpen(tt)) {
          let lo = prev, hi = tt;
          while (hi - lo > 1 / 1440) { const mid = (lo + hi) / 2; if (anyOpen(mid)) hi = mid; else lo = mid; }
          return hi;
        }
        prev = tt;
      }
      return null;
    }

    const links = [];
    for (const s of SETTLEMENTS) for (const r of RELAYS) links.push([s, r]);
    links.push(['Relay A', 'Relay B']);

    return {
      position, orbitPath, launch, evaluateRoute, candidateRoutes, routesAt,
      direct, nextOpenDeparture, links, maintenance,
    };
  }

  function dist(a, b) {
    const dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  function segmentOriginDistance(p, q) {
    const v = [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
    const vv = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    let s = vv > 0 ? -(p[0] * v[0] + p[1] * v[1] + p[2] * v[2]) / vv : 0;
    s = Math.max(0, Math.min(1, s));
    return Math.hypot(p[0] + s * v[0], p[1] + s * v[1], p[2] + s * v[2]);
  }

  return {
    createSystem, solveKepler, dist, segmentOriginDistance,
    SETTLEMENTS, RELAYS, NODE_ID, SEC, JULIAN_YEAR, RELAY_PERIOD,
    LIGHT_MIN_PER_AU, SUN_EXCLUSION_AU,
  };
});
