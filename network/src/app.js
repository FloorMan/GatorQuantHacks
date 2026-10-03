// Interactive 2D view of the brief's solar system and relay backbone.
// Pipeline per frame: positions -> draw orbits/bodies -> per-link light time,
// Sun blockage, maintenance, loss -> draw edges -> route highlight -> packet.
(function () {
  'use strict';
  const P = window.Physics;
  const sys = P.createSystem(window.ORBITAL_ELEMENTS, window.NETWORK_MODEL);
  const SEC = P.SEC, HOUR = 1 / 24, MAX_T = 200 * P.JULIAN_YEAR;
  const NODES = [...P.SETTLEMENTS, ...P.RELAYS];

  const COLORS = {
    Mercury: '#a8a8a8', Venus: '#e8c27a', Earth: '#4f9dff', Mars: '#ff6a3d', Ceres: '#9c9688',
    Jupiter: '#d9a066', Saturn: '#e6d08a', Uranus: '#7fe0e6', Neptune: '#5b6dff',
    'Relay A': '#6fd3ff', 'Relay B': '#6ee7a0',
  };
  const RADIUS = { Jupiter: 7, Saturn: 6.5, Uranus: 5.5, Neptune: 5.5, Ceres: 3.5, Mercury: 3.5 };
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  const state = {
    t: 0, playing: false, dir: 1, speed: 7, view: 'compressed', linksMode: 'all', labels: false,
    zoom: 1, pan: [0, 0], from: 'Earth', to: 'Neptune', pinned: null,
    hoverLink: null, hoverBody: null, tableHover: null, packet: null,
  };

  // ------------------------------------------------------------- DOM refs
  const $ = id => document.getElementById(id);
  const canvas = $('map'), ctx = canvas.getContext('2d'), stage = $('stage'), tip = $('tooltip');
  const fromSel = $('from'), toSel = $('to');
  for (const s of P.SETTLEMENTS) { fromSel.add(new Option(s, s)); toSel.add(new Option(s, s)); }
  fromSel.value = state.from; toSel.value = state.to;

  // ------------------------------------------------------------ formatting
  function fmtDur(days) {
    const s = days * 86400;
    if (s < 60) return s.toFixed(1) + ' s';
    const m = s / 60;
    if (m < 60) return m.toFixed(1) + ' min';
    const h = Math.floor(m / 60), mm = Math.round(m - h * 60);
    if (h < 48) return `${h}h ${String(mm).padStart(2, '0')}m`;
    return (days).toFixed(2) + ' d';
  }
  const pct = p => (p * 100 < 0.01 && p > 0 ? p.toExponential(1) : (p * 100).toFixed(p < 0.001 ? 4 : 2)) + '%';
  const hourStr = t => 'h ' + (t * 24).toFixed(t * 24 < 10 ? 4 : 2);
  function dateLabel(t) {
    const d = new Date(Date.UTC(2126, 8, 22) + t * 86400000);
    return d.toISOString().slice(0, 16).replace('T', ' ');
  }
  const short = n => n.replace('Relay ', 'R-');

  // ------------------------------------------------------------ projection
  let W = 0, H = 0, DPR = 1;
  function resize() {
    const r = stage.getBoundingClientRect();
    DPR = window.devicePixelRatio || 1;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  new ResizeObserver(() => { resize(); if (frame) draw(); }).observe(stage);

  const VIEW = {
    compressed: { f: r => Math.log(1 + r / 0.35), extent: Math.log(1 + 30.5 / 0.35) },
    inner: { f: r => r, extent: 5.7 },
    full: { f: r => r, extent: 31 },
  };
  function toScreen(p) {
    const v = VIEW[state.view];
    const r = Math.hypot(p[0], p[1]);
    const k = r > 0 ? v.f(r) / r : 0;
    const scale = (Math.min(W, H) / 2 - 24) / v.extent * state.zoom;
    return [W / 2 + state.pan[0] + p[0] * k * scale, H / 2 + state.pan[1] - p[1] * k * scale];
  }
  // A straight photon path in true space, sampled so it curves correctly in the compressed view.
  function segPoints(a, b, n) {
    const pts = [];
    const N = state.view === 'compressed' ? (n || 40) : 1;
    for (let i = 0; i <= N; i++) {
      const s = i / N;
      pts.push(toScreen([a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s, 0]));
    }
    return pts;
  }
  function strokePts(pts) {
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke();
  }

  const orbitPaths = {};
  for (const n of NODES) orbitPaths[n] = sys.orbitPath(n, n === 'Mercury' ? 240 : 360);

  // ------------------------------------------------------------ model at t
  let frame = null; // computed snapshot for current t
  function compute() {
    const t = state.t;
    const pos = {};
    for (const n of NODES) pos[n] = sys.position(n, t);
    const links = sys.links.map(([a, b]) => {
      const fwd = sys.launch(a, b, t + SEC, 0.02);
      const rev = sys.launch(b, a, t + SEC, 0.02);
      const rank = s => ({ open: 0, maintenance: 1, 'sun-blocked': 2 })[s];
      const status = rank(fwd.status) >= rank(rev.status) ? fwd.status : rev.status;
      return { a, b, key: a + '|' + b, fwd, rev, status };
    });
    const routes = state.from === state.to ? [] : sys.routesAt(state.from, state.to, t);
    let route = routes[0] || null;
    if (state.pinned) route = routes.find(r => r.path.join('>') === state.pinned) || route;
    const direct = state.from === state.to ? null : sys.direct(state.from, state.to, t);
    frame = { t, pos, links, routes, route, direct };
  }

  // ------------------------------------------------------------ drawing
  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = css('--bg'); ctx.fillRect(0, 0, W, H);
    drawStars();
    const { pos, links, route, direct } = frame;

    // Distance rings (compressed view only, as a scale reference)
    if (state.view !== 'inner') {
      ctx.font = '10px ' + css('--mono'); ctx.fillStyle = '#3a4a68';
      for (const r of state.view === 'compressed' ? [1, 5, 10, 20, 30] : [10, 20, 30]) {
        const c = toScreen([0, 0, 0]), e = toScreen([r, 0, 0]);
        ctx.strokeStyle = 'rgba(58,74,104,.25)'; ctx.lineWidth = 1; ctx.setLineDash([1, 6]);
        ctx.beginPath(); ctx.arc(c[0], c[1], e[0] - c[0], 0, 7); ctx.stroke(); ctx.setLineDash([]);
        const p = toScreen([r * Math.SQRT1_2, -r * Math.SQRT1_2, 0]);
        ctx.fillText(r + ' AU', p[0] + 4, p[1] + 4);
      }
    }

    // Orbits
    ctx.lineWidth = 1;
    for (const n of NODES) {
      ctx.strokeStyle = P.RELAYS.includes(n) ? 'rgba(111,211,255,.18)' : 'rgba(150,170,210,.16)';
      ctx.setLineDash(P.RELAYS.includes(n) ? [4, 5] : []);
      strokePts(orbitPaths[n].map(toScreen));
    }
    ctx.setLineDash([]);

    // Sun + exclusion sphere
    const sun = toScreen([0, 0, 0]);
    const ex = toScreen([P.SUN_EXCLUSION_AU, 0, 0]);
    const exR = Math.max(4, ex[0] - sun[0]);
    const g = ctx.createRadialGradient(sun[0], sun[1], 0, sun[0], sun[1], exR * 3);
    g.addColorStop(0, 'rgba(255,220,120,.55)'); g.addColorStop(1, 'rgba(255,180,60,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sun[0], sun[1], exR * 3, 0, 7); ctx.fill();
    ctx.strokeStyle = 'rgba(255,77,106,.8)'; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.arc(sun[0], sun[1], exR, 0, 7); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#fff3c4'; ctx.beginPath(); ctx.arc(sun[0], sun[1], Math.min(6, exR * 0.6), 0, 7); ctx.fill();

    // Links
    const routeKeys = new Set();
    if (route) for (const h of route.hops) routeKeys.add([h.from, h.to].sort().join('|'));
    const hl = state.hoverLink || state.tableHover;
    for (const L of links) {
      const onRoute = routeKeys.has([L.a, L.b].sort().join('|'));
      if (state.linksMode === 'route' && !onRoute && hl !== L.key) continue;
      if (onRoute) continue; // drawn on top later
      styleLink(L.status, hl === L.key);
      L.pts = segPoints(pos[L.a], pos[L.b]);
      strokePts(L.pts);
    }
    ctx.setLineDash([]);

    // Direct path (dashed gold), for comparison only
    if (direct) {
      ctx.strokeStyle = direct.sunBlocked ? 'rgba(255,77,106,.5)' : 'rgba(201,166,74,.55)';
      ctx.lineWidth = 1; ctx.setLineDash([6, 5]);
      strokePts(segPoints(pos[state.from], pos[state.to]));
      ctx.setLineDash([]);
    }

    // Selected route on top
    if (route) {
      for (const h of route.hops) {
        const L = links.find(x => [x.a, x.b].sort().join('|') === [h.from, h.to].sort().join('|'));
        L.pts = segPoints(pos[L.a], pos[L.b]);
        ctx.strokeStyle = h.open ? css('--route') : css('--blocked');
        ctx.lineWidth = hl === L.key ? 4 : 3;
        ctx.setLineDash(h.open ? [] : [4, 4]);
        ctx.shadowColor = css('--route'); ctx.shadowBlur = h.open ? 8 : 0;
        strokePts(L.pts);
        ctx.shadowBlur = 0; ctx.setLineDash([]);
      }
    }

    // Link labels
    ctx.font = '10px ' + css('--mono');
    for (const L of links) {
      const onRoute = routeKeys.has([L.a, L.b].sort().join('|'));
      if (!L.pts) continue;
      if (!(state.labels || onRoute || hl === L.key)) continue;
      const m = L.pts[Math.floor(L.pts.length / 2)];
      const text = `${L.fwd.d.toFixed(2)} AU · ${fmtDur(L.fwd.flight)} · ${pct(L.fwd.loss)}${L.status === 'open' ? '' : ' · ' + L.status.toUpperCase()}`;
      const w = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(7,10,18,.8)'; ctx.fillRect(m[0] - w / 2 - 3, m[1] - 8, w + 6, 13);
      ctx.fillStyle = onRoute ? css('--route') : statusColor(L.status, true);
      ctx.fillText(text, m[0] - w / 2, m[1] + 2);
    }

    // Bodies
    for (const n of NODES) {
      const p = toScreen(pos[n]);
      const isRelay = P.RELAYS.includes(n);
      const r = RADIUS[n] || 4.5;
      const sel = n === state.from || n === state.to;
      ctx.fillStyle = COLORS[n];
      ctx.shadowColor = COLORS[n]; ctx.shadowBlur = 10;
      if (isRelay) {
        ctx.beginPath(); ctx.moveTo(p[0], p[1] - 7); ctx.lineTo(p[0] + 7, p[1]); ctx.lineTo(p[0], p[1] + 7); ctx.lineTo(p[0] - 7, p[1]); ctx.closePath(); ctx.fill();
      } else {
        ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 7); ctx.fill();
        if (n === 'Saturn') { ctx.strokeStyle = COLORS[n]; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.ellipse(p[0], p[1], r * 2, r * 0.7, -0.4, 0, 7); ctx.stroke(); }
      }
      ctx.shadowBlur = 0;
      if (sel || state.hoverBody === n) {
        ctx.strokeStyle = n === state.from ? css('--accent') : css('--route');
        ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.arc(p[0], p[1], r + 7, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.font = (sel ? '600 ' : '') + '11px ' + css('--mono');
      ctx.fillStyle = sel ? '#fff' : '#b8c3d8';
      let label = n.toUpperCase();
      if (n === state.from) label += ' · TX'; else if (n === state.to) label += ' · RX';
      ctx.fillText(label, p[0] + r + 6, p[1] - r - 2);
    }

    drawPacket();
  }

  function statusColor(s, text) {
    if (s === 'sun-blocked') return css('--blocked');
    if (s === 'maintenance') return css('--maint');
    return text ? '#9fb3d6' : css('--open');
  }
  function styleLink(status, hover) {
    ctx.strokeStyle = statusColor(status);
    ctx.lineWidth = hover ? 2.5 : 1;
    ctx.setLineDash(status === 'sun-blocked' ? [2, 4] : status === 'maintenance' ? [7, 5] : []);
    if (status === 'open' && !hover) ctx.globalAlpha = 0.75;
    else ctx.globalAlpha = 1;
  }

  let stars = null;
  function drawStars() {
    if (!stars || stars.w !== W || stars.h !== H) {
      stars = { w: W, h: H, pts: Array.from({ length: 160 }, () => [Math.random() * W, Math.random() * H, Math.random()]) };
    }
    ctx.globalAlpha = 1;
    for (const [x, y, b] of stars.pts) { ctx.fillStyle = `rgba(200,210,255,${0.12 + b * 0.3})`; ctx.fillRect(x, y, 1, 1); }
  }

  // ------------------------------------------------------------ packet
  function drawPacket() {
    ctx.globalAlpha = 1;
    const pk = state.packet;
    if (!pk) return;
    const t = state.t;
    let head = null;
    for (const h of pk.route.hops) {
      if (t >= h.ta) {
        // Completed hop: the frozen photon path from sender@t_e to receiver@t_a.
        ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 1.5;
        strokePts(segPoints(h.rs, h.rr));
        mark(h.rs, '#fff', 2.5); mark(h.rr, '#fff', 2.5);
      } else if (t >= h.te) {
        const f = (t - h.te) / (h.ta - h.te);
        const cur = [h.rs[0] + (h.rr[0] - h.rs[0]) * f, h.rs[1] + (h.rr[1] - h.rs[1]) * f, 0];
        ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = 1.5;
        strokePts(segPoints(h.rs, cur));
        // ghost of where the receiver will be on arrival
        ctx.strokeStyle = 'rgba(255,255,255,.25)'; ctx.setLineDash([2, 4]);
        strokePts(segPoints(cur, h.rr)); ctx.setLineDash([]);
        mark(h.rr, 'rgba(255,255,255,.4)', 4, true);
        head = cur;
      } else if (!head) {
        head = frame.pos[h.from]; // waiting: serialization / relay processing
        break;
      }
    }
    if (!head && t >= pk.route.arrival) head = frame.pos[pk.route.path[pk.route.path.length - 1]];
    if (head) {
      const s = toScreen(head);
      ctx.fillStyle = '#fff'; ctx.shadowColor = '#fff'; ctx.shadowBlur = 14;
      ctx.beginPath(); ctx.arc(s[0], s[1], 4, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
    }
  }
  function mark(p, color, r, ring) {
    const s = toScreen(p);
    ctx.beginPath(); ctx.arc(s[0], s[1], r, 0, 7);
    if (ring) { ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.stroke(); } else { ctx.fillStyle = color; ctx.fill(); }
  }

  function sendPacket() {
    if (!frame.route) return;
    const route = sys.evaluateRoute(frame.route.path, state.t);
    const realSeconds = Math.min(14, Math.max(6, route.oneWay * 1440 / 25));
    state.playing = false; syncPlay();
    state.packet = { route, t0: state.t, start: performance.now(), rate: route.oneWay / realSeconds, done: false, logged: 0 };
    const log = $('log'); log.innerHTML = '';
    const add = html => { const d = document.createElement('div'); d.innerHTML = html; log.appendChild(d); };
    add(`<b>${route.path.map(short).join(' → ')}</b> ready at ${hourStr(route.depart)} (${dateLabel(route.depart)})`);
    route.hops.forEach((h, i) => {
      const relayNote = i > 0 ? ' (after 1 s relay processing + 1 s serialization)' : ' (after 1 s serialization)';
      add(`Hop ${i + 1}: ${short(h.from)} emits at ${hourStr(h.te)}${relayNote}`);
      add(`&nbsp;&nbsp;${short(h.to)} receives at ${hourStr(h.ta)} · flight ${fmtDur(h.flight)} · d ${h.d.toFixed(4)} AU · loss ${pct(h.loss)} · <span class="st-${h.status}">${h.status.toUpperCase()}</span>`);
      add(`&nbsp;&nbsp;receiver moved ${(P.dist(sys.position(h.to, h.te), h.rr) * 149597870.7).toLocaleString(undefined, { maximumFractionDigits: 0 })} km during flight`);
    });
    add(`Arrives ${hourStr(route.arrival)} · one-way ${fmtDur(route.oneWay)} · P(all hops succeed first try) ${pct(route.pAllFirstTry)}`);
    add(`<span class="note">Conditional (no-loss) timeline. Loss is not simulated.</span>`);
  }

  // ------------------------------------------------------------ side panels
  function renderPanels() {
    const { route, routes, direct, links } = frame;
    $('clock').textContent = dateLabel(state.t);
    $('elapsed').textContent = `T+ ${state.t.toFixed(3)} d · ${(state.t / P.JULIAN_YEAR).toFixed(3)} yr · h ${(state.t * 24).toFixed(2)}`;
    if (document.activeElement !== $('hourIn')) $('hourIn').value = (state.t * 24).toFixed(2);
    $('slider').value = state.t;

    // Summary
    if (!route) { $('summary').textContent = 'Pick two different settlements.'; }
    else {
      const lines = [route.path.map(n => n.toUpperCase()).join(' → '), ''];
      lines.push(`One-way time:   ${fmtDur(route.oneWay)}`);
      route.hops.forEach((h, i) => lines.push(`Hop ${i + 1} loss:     ${pct(h.loss).padEnd(7)} ${short(h.from)}→${short(h.to)}`,
        `                ${h.d.toFixed(3)} AU · ${fmtDur(h.flight)}`));
      lines.push(`All hops, 1st:  ${pct(route.pAllFirstTry)}`);
      const pAband = Math.max(...route.hops.map(h => Math.pow(h.loss, 4)));
      lines.push(`Hop abandoned:  ${pct(pAband)}  (worst hop, ≈p⁴)`);
      const st = route.open ? 'OPEN' : 'BLOCKED — ' + route.blockedBy.map(h => `${short(h.from)}→${short(h.to)} ${h.status}`).join(', ');
      lines.push(`Status:         ${st}`);
      lines.push(`Choice:         ${state.pinned ? 'pinned by you' : 'fastest open route'}`);
      $('summary').innerHTML = lines.join('\n').replace(/(OPEN|BLOCKED[^\n]*)/, m => `<span class="${route.open ? 'st-open' : 'st-sun-blocked'}">${m}</span>`);
    }

    // Candidates
    const ct = $('cands');
    ct.innerHTML = '<tr><th>Route</th><th class="num">One-way</th><th class="num">P 1st try</th><th>Status</th></tr>';
    for (const r of routes) {
      const tr = document.createElement('tr');
      tr.className = 'click' + (r === route ? ' sel' : '');
      const st = r.open ? 'open' : r.blockedBy[0].status;
      tr.innerHTML = `<td>${r.path.map(short).join('→')}</td><td class="num">${fmtDur(r.oneWay)}</td><td class="num">${pct(r.pAllFirstTry)}</td><td class="st-${st}">${st}</td>`;
      tr.onclick = () => { const k = r.path.join('>'); state.pinned = state.pinned === k ? null : k; refresh(); };
      ct.appendChild(tr);
    }

    // Direct
    if (direct) {
      $('direct').innerHTML = [
        `${state.from.toUpperCase()} ⇢ ${state.to.toUpperCase()} (straight line)`,
        `One-way:     ${fmtDur(direct.oneWay)}  (incl. 3 s access+ser)`,
        `Distance:    ${direct.d.toFixed(3)} AU`,
        `Loss:        ${pct(direct.loss)}  (1 − e^(−0.08·d))`,
        `             no receipt, no retry`,
        `Sun clear.:  ${direct.clearance.toFixed(3)} AU`,
        `Status:      <span class="st-${direct.sunBlocked ? 'sun-blocked' : 'open'}">${direct.sunBlocked ? 'SUN-BLOCKED' : 'CLEAR'}</span>`,
      ].join('\n');
    } else $('direct').textContent = '—';

    // Links table
    const lt = $('links');
    const routeKeys = new Set(route ? route.hops.map(h => [h.from, h.to].sort().join('|')) : []);
    lt.innerHTML = '<tr><th>Link</th><th class="num">d (AU)</th><th class="num">Light</th><th class="num">Loss</th><th class="num">Sun clr</th><th>Status</th></tr>';
    for (const L of links) {
      const tr = document.createElement('tr');
      tr.className = 'click' + (routeKeys.has([L.a, L.b].sort().join('|')) ? ' onroute' : '');
      const clr = Math.min(L.fwd.clearance, L.rev.clearance);
      tr.innerHTML = `<td>${short(L.a)}–${short(L.b)}</td><td class="num">${L.fwd.d.toFixed(3)}</td><td class="num">${fmtDur(L.fwd.flight)}</td><td class="num">${pct(L.fwd.loss)}</td><td class="num">${clr.toFixed(2)}</td><td class="st-${L.status}">${L.status}</td>`;
      tr.onmouseenter = () => { state.tableHover = L.key; draw(); };
      tr.onmouseleave = () => { state.tableHover = null; draw(); };
      lt.appendChild(tr);
    }
  }

  // ------------------------------------------------------------ hit testing
  function nearestBody(mx, my) {
    let best = null, bd = 12;
    for (const n of NODES) {
      const p = toScreen(frame.pos[n]);
      const d = Math.hypot(p[0] - mx, p[1] - my);
      if (d < bd) { bd = d; best = n; }
    }
    return best;
  }
  function nearestLink(mx, my) {
    let best = null, bd = 6;
    for (const L of frame.links) {
      if (!L.pts) continue;
      for (let i = 1; i < L.pts.length; i++) {
        const d = pointSeg(mx, my, L.pts[i - 1], L.pts[i]);
        if (d < bd) { bd = d; best = L; }
      }
    }
    return best;
  }
  function pointSeg(x, y, a, b) {
    const vx = b[0] - a[0], vy = b[1] - a[1];
    const l = vx * vx + vy * vy;
    let s = l ? ((x - a[0]) * vx + (y - a[1]) * vy) / l : 0;
    s = Math.max(0, Math.min(1, s));
    return Math.hypot(x - a[0] - s * vx, y - a[1] - s * vy);
  }

  function showTip(mx, my) {
    const b = nearestBody(mx, my);
    const L = b ? null : nearestLink(mx, my);
    state.hoverBody = b; state.hoverLink = L ? L.key : null;
    let text = '';
    if (b) {
      const p = frame.pos[b];
      text = `${b}${P.SETTLEMENTS.includes(b) ? '  (node ' + P.NODE_ID[b] + ')' : '  (relay, node ' + P.NODE_ID[b] + ')'}\n` +
        `r from Sun  ${Math.hypot(...p).toFixed(4)} AU\n` +
        `x y z       ${p.map(v => v.toFixed(4)).join('  ')}\n` +
        `to Sun      ${fmtDur(Math.hypot(...p) * P.LIGHT_MIN_PER_AU / 1440)} light`;
    } else if (L) {
      const f = L.fwd, r = L.rev;
      text = `${L.a} ⇄ ${L.b}   ${L.status.toUpperCase()}\n` +
        `→ d ${f.d.toFixed(4)} AU  ${fmtDur(f.flight)}  loss ${pct(f.loss)}  ${f.status}\n` +
        `← d ${r.d.toFixed(4)} AU  ${fmtDur(r.flight)}  loss ${pct(r.loss)}  ${r.status}\n` +
        `Sun clearance ${Math.min(f.clearance, r.clearance).toFixed(4)} AU (limit 0.10)` +
        (f.maintenance || r.maintenance ? `\nMaintenance h[${(f.maintenance || r.maintenance).startH}, ${(f.maintenance || r.maintenance).endH})` : '');
    }
    if (text) {
      tip.textContent = text; tip.style.display = 'block';
      const x = Math.min(mx + 14, W - tip.offsetWidth - 6), y = Math.min(my + 14, H - tip.offsetHeight - 6);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    } else tip.style.display = 'none';
    canvas.style.cursor = b ? 'pointer' : 'crosshair';
  }

  // ------------------------------------------------------------ interaction
  let drag = null;
  canvas.addEventListener('mousedown', e => { drag = { x: e.offsetX, y: e.offsetY, pan: state.pan.slice(), moved: false }; });
  window.addEventListener('mouseup', e => {
    if (drag && !drag.moved && e.target === canvas) {
      const b = nearestBody(e.offsetX, e.offsetY);
      if (b && P.SETTLEMENTS.includes(b)) {
        if (e.shiftKey) state.from = b; else state.to = b;
        if (state.from === state.to) { if (e.shiftKey) state.to = state.to === 'Earth' ? 'Mars' : 'Earth'; else state.from = state.from === 'Earth' ? 'Mars' : 'Earth'; }
        fromSel.value = state.from; toSel.value = state.to; state.pinned = null; refresh();
      }
    }
    drag = null;
  });
  canvas.addEventListener('mousemove', e => {
    if (drag) {
      const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.moved) { state.pan = [drag.pan[0] + dx, drag.pan[1] + dy]; draw(); return; }
    }
    showTip(e.offsetX, e.offsetY); draw();
  });
  canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; state.hoverBody = state.hoverLink = null; draw(); });
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const k = Math.exp(-e.deltaY * 0.0015);
    const nz = Math.max(0.3, Math.min(40, state.zoom * k));
    const f = nz / state.zoom;
    const cx = e.offsetX - W / 2, cy = e.offsetY - H / 2;
    state.pan = [cx - (cx - state.pan[0]) * f, cy - (cy - state.pan[1]) * f];
    state.zoom = nz; draw();
  }, { passive: false });
  canvas.addEventListener('dblclick', () => { state.zoom = 1; state.pan = [0, 0]; draw(); });

  fromSel.onchange = () => { state.from = fromSel.value; state.pinned = null; refresh(); };
  toSel.onchange = () => { state.to = toSel.value; state.pinned = null; refresh(); };
  $('swap').onclick = () => { [state.from, state.to] = [state.to, state.from]; fromSel.value = state.from; toSel.value = state.to; state.pinned = null; refresh(); };
  $('send').onclick = sendPacket;
  $('nextOpen').onclick = () => {
    const out = $('nextOpenOut');
    if (state.from === state.to) return;
    const tn = sys.nextOpenDeparture(state.from, state.to, state.t, 400, HOUR);
    if (tn === null) { out.textContent = 'No open route within 400 days of now.'; return; }
    if (tn === state.t) { out.textContent = 'A route is open right now.'; return; }
    out.innerHTML = `Next open departure: ${hourStr(tn)} (${dateLabel(tn)}), wait ${fmtDur(tn - state.t)}. <button class="btn" id="jumpOpen">Jump</button>`;
    $('jumpOpen').onclick = () => setT(tn);
  };

  document.querySelectorAll('#viewSeg button').forEach(b => b.onclick = () => {
    state.view = b.dataset.view; state.zoom = 1; state.pan = [0, 0];
    document.querySelectorAll('#viewSeg button').forEach(x => x.classList.toggle('on', x === b)); draw();
  });
  document.querySelectorAll('#linkSeg button').forEach(b => b.onclick = () => {
    if (b.dataset.labels) { state.labels = !state.labels; b.classList.toggle('on', state.labels); }
    else {
      state.linksMode = b.dataset.links;
      document.querySelectorAll('#linkSeg button[data-links]').forEach(x => x.classList.toggle('on', x === b));
    }
    draw();
  });
  document.querySelectorAll('#speedSeg button').forEach(b => b.onclick = () => {
    state.speed = +b.dataset.speed;
    document.querySelectorAll('#speedSeg button').forEach(x => x.classList.toggle('on', x === b));
  });
  document.querySelectorAll('[data-step]').forEach(b => b.onclick = () => setT(state.t + +b.dataset.step));
  document.querySelectorAll('[data-jump]').forEach(b => b.onclick = () => setT(+b.dataset.jump));
  document.querySelectorAll('[data-jumph]').forEach(b => b.onclick = () => setT(+b.dataset.jumph / 24));
  $('slider').oninput = e => setT(+e.target.value);
  $('hourIn').onchange = e => setT(+e.target.value / 24);
  $('play').onclick = () => { state.playing = !state.playing; if (state.playing) state.packet = null; syncPlay(); };
  $('dir').onclick = () => { state.dir *= -1; $('dir').textContent = state.dir > 0 ? '→ Forward' : '← Backward'; };
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.code === 'Space') { e.preventDefault(); $('play').click(); }
    if (e.code === 'ArrowRight') setT(state.t + (e.shiftKey ? 1 : HOUR));
    if (e.code === 'ArrowLeft') setT(state.t - (e.shiftKey ? 1 : HOUR));
  });
  function syncPlay() { $('play').textContent = state.playing ? '❚❚ Pause' : '▶ Play'; }

  function setT(t) {
    state.t = Math.max(0, Math.min(MAX_T, t));
    state.packet = null;
    refresh();
  }
  function refresh() { compute(); renderPanels(); draw(); }

  // ------------------------------------------------------------ loop
  let last = performance.now(), panelClock = 0;
  function tick(now) {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    let changed = false;
    const pk = state.packet;
    if (pk && !pk.done) {
      pk.animating = true;
      state.t = Math.min(pk.route.arrival, pk.t0 + Math.max(0, now - pk.start) / 1000 * pk.rate);
      if (state.t >= pk.route.arrival) pk.done = true;
      changed = true;
    } else if (state.playing) {
      state.t += state.dir * state.speed * dt;
      if (state.t <= 0 || state.t >= MAX_T) { state.t = Math.max(0, Math.min(MAX_T, state.t)); state.playing = false; syncPlay(); }
      changed = true;
    }
    if (changed) {
      compute(); draw();
      panelClock += dt;
      if (panelClock > 0.12 || (pk && pk.done)) { renderPanels(); panelClock = 0; }
      if (pk && pk.done) pk.animating = false;
    }
    requestAnimationFrame(tick);
  }

  // Optional deep link: index.html?from=Earth&to=Neptune&h=240&view=inner&send=1
  const q = new URLSearchParams(location.search);
  if (P.SETTLEMENTS.includes(q.get('from'))) state.from = fromSel.value = q.get('from');
  if (P.SETTLEMENTS.includes(q.get('to'))) state.to = toSel.value = q.get('to');
  if (q.has('h')) state.t = Math.max(0, Math.min(MAX_T, +q.get('h') / 24));
  if (VIEW[q.get('view')]) document.querySelector(`#viewSeg button[data-view="${q.get('view')}"]`).click();

  resize(); refresh(); requestAnimationFrame(tick);
  if (q.get('send') === '1') sendPacket();
})();
