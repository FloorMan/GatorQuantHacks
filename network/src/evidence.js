// Tests & Evidence dashboard. Every number shown comes from /api/run, which runs
// the mpex scenarios on the network model when asked. Nothing here is hard-coded.
(function () {
  'use strict';

  // ------------------------------------------------------------ helpers
  const $ = id => document.getElementById(id);
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on')) e[k] = v;
      else e.setAttribute(k, v);
    }
    for (const k of kids.flat()) {
      if (k == null || k === false) continue;
      e.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
    }
    return e;
  }
  const SVG = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, text) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  }
  const pad = n => String(n).padStart(2, '0');
  const hfmt = h => h == null ? '—' : 'h ' + Number(h).toFixed(Math.abs(h) < 100 ? 4 : 3);
  function clock(h) {
    if (h == null) return '';
    const s = Math.round(h * 3600);
    return `T+${Math.floor(s / 3600)}:${pad(Math.floor(s % 3600 / 60))}:${pad(s % 60)}`;
  }
  const money = x => '$' + Number(x).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (x, d = 0) => Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const dur = h => h == null ? '—' : (h < 1 ? (h * 60).toFixed(1) + ' min' : Number(h).toFixed(2) + ' h');
  const card = (title, ...kids) => el('div', { class: 'card' }, title ? el('h2', {}, title) : null, ...kids);
  const passBadge = ok => el('span', { class: 'badge ' + (ok ? 'pass' : 'fail') }, ok ? '✓ PASS' : '✗ FAIL');

  let DATA = null, TESTS = [], SEL = null, ANIM = null;

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }
  const status = t => { $('status').textContent = t; };
  function busy(on) {
    $('runAll').disabled = on;
    document.querySelectorAll('.item .btn, #runOne').forEach(b => { b.disabled = on; });
  }

  // ------------------------------------------------------------ load / run
  async function init() {
    if (!location.protocol.startsWith('http')) {
      $('detail').replaceChildren(el('div', { class: 'error' },
        'The dashboard runs the test suite through its server. From the repo root run ',
        el('code', {}, 'python3 serve.py'), ', then open http://localhost:8000/evidence.html'));
      status('Server not running');
      $('runAll').disabled = true;
      return;
    }
    $('runAll').onclick = runAll;
    try {
      TESTS = await api('/api/tests');
      renderList();
      await runAll('/api/results');
    } catch (e) {
      $('detail').replaceChildren(el('div', { class: 'error' }, 'Could not reach the test server: ' + e.message));
      status('Error');
    }
  }

  // On page load: the server's latest run (it runs the suite if there is none yet).
  // "Run All Tests": a fresh run of every scenario.
  async function runAll(latest) {
    busy(true);
    const fresh = typeof latest !== 'string';
    status(fresh ? `Running ${TESTS.length} scenarios on the market engine and network model…` : 'Loading latest results…');
    try {
      DATA = fresh ? await api('/api/run', { method: 'POST' }) : await api(latest);
      status(`Hub: ${DATA.hub || 'Earth'} · ${fresh ? 'Ran' : 'Latest run:'} ${DATA.tests.length} scenarios in ${DATA.ran_at_s} s` +
        (fresh ? ` · ${new Date().toLocaleTimeString()}` : ' · press Run All Tests to re-run'));
      renderSummary();
      renderList();
      const want = decodeURIComponent(location.hash.slice(1));
      const firstFail = DATA.tests.find(t => t.status !== 'PASS');
      select(SEL || (TESTS.some(t => t.id === want) ? want : (firstFail || DATA.tests[0]).id));
    } catch (e) {
      status('Run failed: ' + e.message);
    } finally { busy(false); }
  }

  async function runOne(id) {
    busy(true);
    status(`Running ${id}…`);
    try {
      const res = await api('/api/run/' + id, { method: 'POST' });
      if (DATA) {
        DATA.tests = DATA.tests.map(t => t.id === id ? res.test : t);
        if (res.summary) DATA.summary = res.summary;
      }
      status(`Re-ran ${res.test.title}: ${res.test.status} · ${new Date().toLocaleTimeString()}`);
      renderSummary();
      renderList();
      if (SEL === id) renderDetail(res.test);
    } catch (e) { status('Run failed: ' + e.message); } finally { busy(false); }
  }

  // ------------------------------------------------------------ summary + list
  function renderSummary() {
    const s = DATA.summary;
    const allPass = s.tests_passed === s.tests_total;
    const tile = (k, ok, v, sub, big) => el('div', { class: 'tile' },
      el('div', { class: 'k' }, k),
      el('div', { class: 'v ' + (big ? 'big ' : '') + (ok ? 'ok' : 'bad') }, v),
      sub ? el('div', { class: 's' }, sub) : null);
    $('summary').replaceChildren(
      tile('Tests passed', allPass, `${allPass ? '✓' : '✗'} ${s.tests_passed} / ${s.tests_total}`, 'scenarios on the real engine + network', true),
      tile('Financial invariants', s.financial_invariants, s.financial_invariants ? '✓ PASS' : '✗ FAIL',
        `${num(s.invariant_checks)} checks, one after every step`),
      tile('Cash conserved', s.cash_conserved, s.cash_conserved ? '✓ PASS' : '✗ FAIL', 'ledgers + in transit = opening'),
      tile('Shares conserved', s.shares_conserved, s.shares_conserved ? '✓ PASS' : '✗ FAIL', 'ledgers + in transit = opening'),
      tile('Double spending', s.double_spending === 0, s.double_spending === 0 ? '✓ NONE' : `✗ ${s.double_spending} FOUND`,
        `${s.double_spend_attempts_blocked} attempts tried and blocked`),
      tile('Duplicate settlements', s.duplicate_settlements === 0, s.duplicate_settlements === 0 ? '✓ NONE' : `✗ ${s.duplicate_settlements} FOUND`,
        `${s.duplicates_ignored} repeat deliveries safely ignored`));
  }

  function renderList() {
    const items = TESTS.map(t => {
      const r = DATA && DATA.tests.find(x => x.id === t.id);
      const ok = r && r.status === 'PASS';
      const passed = r ? r.checks.filter(c => c.pass).length : 0;
      return el('div', { class: 'item' + (SEL === t.id ? ' sel' : ''), onclick: () => select(t.id) },
        el('span', { class: 'mark ' + (r ? (ok ? 'ok' : 'bad') : ''), title: r ? r.status : 'not run' }, r ? (ok ? '✓' : '✗') : '•'),
        el('div', {}, el('div', { class: 't' }, t.title),
          el('div', { class: 'c' }, r ? `${r.status} · ${passed}/${r.checks.length} checks · ${r.duration_s ?? '?'} s` : 'not run')),
        el('button', { class: 'btn', title: 'Run this test', onclick: ev => { ev.stopPropagation(); runOne(t.id); } }, 'Run'));
    });
    $('list').replaceChildren(...items);
  }

  function select(id) {
    SEL = id;
    history.replaceState(null, '', '#' + id);
    renderList();
    const t = DATA && DATA.tests.find(x => x.id === id);
    if (t) renderDetail(t);
  }

  // ------------------------------------------------------------ detail
  function renderDetail(t) {
    if (ANIM) { ANIM.stop(); ANIM = null; }
    const parts = [];
    const passed = t.checks.filter(c => c.pass).length;
    parts.push(card(null, el('div', { class: 'title-row' },
      el('span', { class: 'name' }, t.title), passBadge(t.status === 'PASS'),
      el('button', { class: 'btn', id: 'runOne', onclick: () => runOne(t.id) }, '▶ Run Test'),
      el('span', { class: 'sub' }, `${passed}/${t.checks.length} checks · ` +
        (t.invariants ? `${num(t.invariants.steps_checked)} steps, invariants checked after each` : '') +
        ` · ${t.duration_s ?? '?'} s`))));

    if (t.failures && t.failures.length) {
      parts.push(el('div', { class: 'failbox' }, el('h2', {}, '✗ FAILED · expected vs actual'),
        table(['What failed', 'Expected', 'Actual'], t.failures.map(f => [f.name, fmtVal(f.expected), fmtVal(f.actual)]), 'wrap'),
        t.traceback ? el('pre', { class: 'sub' }, t.traceback.join('\n')) : null));
    }
    if (!t.timeline) { $('detail').replaceChildren(...parts); return; }

    parts.push(card('Checks', table(['', 'Check', 'Expected', 'Actual'],
      t.checks.map(c => [el('span', { class: c.pass ? 'ok' : 'bad' }, c.pass ? '✓' : '✗'), c.name, fmtVal(c.expected), fmtVal(c.actual)]), 'wrap')));

    const flow = flowCard(t);
    if (flow) parts.push(flow);
    if (t.network && t.network.messages.length) parts.push(animCard(t));
    if (t.auction) parts.push(auctionCard(t));
    if (t.futures) parts.push(futuresCard(t));
    parts.push(balanceCard(t));
    parts.push(extraCard(t));
    parts.push(timelineCard(t));
    $('detail').replaceChildren(...parts.filter(Boolean));
    if (ANIM) ANIM.start();
  }

  function fmtVal(v) {
    if (v == null) return 'none';
    if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4);
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  function table(head, rows, cls) {
    return el('div', { class: 'scroll' }, el('table', { class: cls || '' },
      el('thead', {}, el('tr', {}, head.map(h => el('th', {}, h)))),
      el('tbody', {}, rows.map(r => el('tr', {}, r.map(c => el('td', {}, c)))))));
  }

  // ------------------------------------------------------------ flow + transaction
  function holdingTotals(snap, who) {
    const p = snap.principals.find(x => x.principal === who);
    const out = { cash: 0, cashAvail: 0, cashLocked: 0, coll: 0, shares: 0, sharesAvail: 0, where: {} };
    if (!p) return out;
    for (const h of p.holdings) {
      if (h.asset === 'cash') {
        out.cash += +h.balance; out.cashAvail += +h.available; out.cashLocked += +h.locked; out.coll += +h.collateral;
      } else { out.shares += +h.balance; out.sharesAvail += +h.available; }
      out.where[h.asset + '@' + h.settlement] = +h.balance;
    }
    return out;
  }

  function flowCard(t) {
    const p = t.primary;
    if (!p || !p.steps.length) return null;
    const tx = p.transaction;
    let heading = 'Transaction flow';
    if (tx) {
      const what = tx.trade ? (tx.asset === 'shares' ? 'SHARE TRADE' : 'TRADE · CASH LEG') : (tx.asset === 'cash' ? 'CASH TRANSFER' : 'SHARE TRANSFER');
      heading = `${tx.from.toUpperCase()} → ${tx.to.toUpperCase()} ${what}`;
    }
    const steps = [];
    if (tx) steps.push(el('div', { class: 'step' }, el('div', { class: 'l' },
      `${tx.from_owner} · ${tx.asset === 'cash' ? money(tx.amount) : num(tx.amount) + ' shares'}`),
      el('div', { class: 'd' }, `to ${tx.to_owner} at ${tx.to}`)));
    p.steps.forEach((s, i) => {
      if (steps.length) steps.push(el('div', { class: 'arrow' }, '↓'));
      const cls = 'step' + (/LOST/.test(s.label) ? ' lost' : '') + (/FINAL|RECONCILED/.test(s.label) ? ' final' : '');
      steps.push(el('div', { class: cls, 'data-t': s.t, 'data-i': i },
        el('span', { class: 'tm' }, `${hfmt(s.t)}`), el('div', { class: 'l' }, s.label),
        s.detail ? el('div', { class: 'd' }, s.detail) : null));
    });
    const kv = [];
    if (tx) {
      kv.push(['Route', (tx.route || []).join(' → ')],
        ['Distance', tx.distance_au != null ? tx.distance_au.toFixed(4) + ' AU (first successful copy per hop)' : '—'],
        ['Send time', `${hfmt(tx.send_h)}  ${clock(tx.send_h)}`],
        ['Arrival time', `${hfmt(tx.arrival_h)}  ${clock(tx.arrival_h)}`],
        ['Total delay', dur(tx.total_delay_h) + (tx.trade ? ' (order stamp → usable)' : ' (initiated → usable)')],
        ['Retries', String(tx.retries)], ['Packet losses', String(tx.losses)],
        ['Transaction ID', tx.id + (tx.trade ? ` (leg of ${tx.trade})` : '')],
        ['Duplicates ignored', String(tx.duplicates_ignored)]);
    }
    for (const who of p.accounts) {
      const b = holdingTotals(t.balances.before, who), a = holdingTotals(t.balances.after, who);
      if (b.cash !== a.cash || b.cashAvail !== a.cashAvail || b.coll !== a.coll)
        kv.push([`${who} cash`, `${money(b.cash)} → ${money(a.cash)}` + (a.coll ? ` (collateral ${money(a.coll)})` : '')]);
      if (b.shares !== a.shares || b.sharesAvail !== a.sharesAvail)
        kv.push([`${who} shares`, `${num(b.shares)} → ${num(a.shares)}` + (a.shares !== a.sharesAvail ? ` (${num(a.shares - a.sharesAvail)} locked)` : '')]);
    }
    if (tx) kv.push(['Final state', tx.final_state.toUpperCase() + (p.second_leg ? ` · other leg ${p.second_leg.id} (${p.second_leg.asset}) final ${hfmt(p.second_leg.completed_h)}` : '')]);
    return card(heading, el('div', { class: 'two' },
      el('div', { class: 'flow', id: 'flowSteps' }, steps),
      el('div', {}, el('table', { class: 'kv' }, el('tbody', {}, kv.map(([k, v]) => el('tr', {}, el('td', {}, k), el('td', {}, v))))))));
  }

  // ------------------------------------------------------------ network + finance animation
  const COLORS = {
    Mercury: '#a8a8a8', Venus: '#e8c27a', Earth: '#4f9dff', Mars: '#ff6a3d', Ceres: '#9c9688',
    Jupiter: '#d9a066', Saturn: '#e6d08a', Uranus: '#7fe0e6', Neptune: '#5b6dff',
    'Relay A': '#6fd3ff', 'Relay B': '#6ee7a0',
  };
  const KIND_COLOR = { batch_order: '#3987e5', batch_result: '#199e70', transfer: '#ff7a45', margin_transfer: '#ff7a45',
    transfer_status: '#9fb3d6', status_query: '#f4b740', mark: '#d55181', margin_call: '#e66767', cancel: '#f4b740',
    cancel_confirmed: '#9fb3d6', state_summary: '#9085e9', summary_ack: '#9085e9' };
  const STATES = ['RESERVED', 'SENT', 'RECEIVED', 'VALIDATED', 'FINAL'];
  let SYS = null;

  function stateTimes(p) {
    const find = re => (p.steps.find(s => re.test(s.label)) || {}).t;
    const fin = find(/VALIDATED/);
    return { RESERVED: find(/RESERVED|LOCKED/), SENT: find(/PACKET SENT/), RECEIVED: find(/RECEIVED/), VALIDATED: fin, FINAL: fin };
  }

  function animCard(t) {
    if (!SYS && window.Physics) SYS = window.Physics.createSystem(window.ORBITAL_ELEMENTS, window.NETWORK_MODEL);
    const canvas = el('canvas', {});
    const chip = el('span', { class: 'chip' }, '—');
    const statesRow = el('div', { class: 'states' });
    const clockOut = el('div', { class: 'sub' });
    const slider = el('input', { type: 'range', min: 0, max: 1000, value: 0, style: 'width:100%' });
    const playBtn = el('button', { class: 'btn' }, '❚❚ Pause');
    const msgs = t.network.messages;
    const primaryMsg = (t.primary.transaction || {}).id;
    const launches = [];
    for (const m of msgs) for (const l of m.launches)
      launches.push({ ...l, kind: m.kind, primary: !!(primaryMsg && t.primary.transaction.launches.some(x => x.te === l.te && x.packet === l.packet)) });
    const times = launches.flatMap(l => [l.te, l.ta]).concat(t.primary.steps.map(s => s.t)).filter(x => x != null);
    const t0 = Math.max(0, Math.min(...times) - 0.2), t1 = Math.max(...times) + 0.3;
    const ST = t.primary.transaction ? stateTimes(t.primary) : null;
    const route = t.primary.transaction ? t.primary.transaction.route : null;
    const legend = el('div', { class: 'legend' }, Object.entries(
      msgs.reduce((acc, m) => { acc[m.kind] = KIND_COLOR[m.kind] || '#b8c3d8'; return acc; }, {}))
      .map(([k, c]) => el('span', {}, el('i', { style: `border-color:${c}` }), k.replace(/_/g, ' '))),
      el('span', {}, el('i', { style: 'border-color:#ff4d6a;border-top-style:dotted' }), 'lost launch'));

    let tNow = t0, playing = true, raf = 0, last = 0;
    const span = t1 - t0, playSecs = Math.min(16, Math.max(8, span / 4));
    function draw() {
      const ctx = canvas.getContext('2d');
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(r.width * dpr)) { canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const W = r.width, H = r.height;
      ctx.clearRect(0, 0, W, H);
      const ext = Math.log(1 + 30.5 / 0.35), scale = (Math.min(W, H) / 2 - 18) / ext;
      const P = p => { const rr = Math.hypot(p[0], p[1]); const k = rr > 0 ? Math.log(1 + rr / 0.35) / rr : 0; return [W / 2 + p[0] * k * scale, H / 2 - p[1] * k * scale]; };
      const pos = n => SYS.position(n, tNow / 24);
      // orbits
      ctx.lineWidth = 1;
      for (const n of Object.keys(COLORS)) {
        const pts = SYS.orbitPath(n, 96).map(P);
        ctx.strokeStyle = '#162036'; ctx.beginPath(); pts.forEach((q, i) => i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])); ctx.stroke();
      }
      const sun = P([0, 0, 0]);
      ctx.fillStyle = '#fff3c4'; ctx.beginPath(); ctx.arc(sun[0], sun[1], 4, 0, 7); ctx.fill();
      // pinned route of the primary transaction, at the current geometry
      if (route) {
        ctx.strokeStyle = 'rgba(255,122,69,.55)'; ctx.lineWidth = 3; ctx.beginPath();
        route.forEach((n, i) => { const q = P(pos(n)); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
        ctx.stroke();
      }
      // packets in flight: emitted at the sender's t_e position, landing at the receiver's t_a position
      for (const l of launches) {
        if (tNow < l.te || tNow > l.ta) continue;
        const a = SYS.position(l.from, l.te / 24), b = SYS.position(l.to, l.ta / 24);
        const s = (tNow - l.te) / Math.max(1e-9, l.ta - l.te);
        const q = P([a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s, 0]);
        const p0 = P(a);
        ctx.setLineDash(l.lost ? [3, 3] : []);
        ctx.strokeStyle = l.lost ? '#ff4d6a' : (KIND_COLOR[l.kind] || '#b8c3d8'); ctx.lineWidth = l.primary ? 2.5 : 1.5;
        ctx.beginPath(); ctx.moveTo(p0[0], p0[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = l.lost ? '#ff4d6a' : (l.primary ? '#ffffff' : (KIND_COLOR[l.kind] || '#b8c3d8'));
        ctx.beginPath(); ctx.arc(q[0], q[1], l.primary ? 5 : 3.5, 0, 7); ctx.fill();
        if (l.lost && s > 0.6) { ctx.font = 'bold 13px sans-serif'; ctx.fillText('✕', q[0] + 6, q[1] - 6); }
      }
      // bodies
      ctx.font = '11px -apple-system, sans-serif';
      for (const n of Object.keys(COLORS)) {
        const q = P(pos(n));
        const relay = n.startsWith('Relay');
        ctx.fillStyle = COLORS[n];
        if (relay) { ctx.fillRect(q[0] - 4, q[1] - 4, 8, 8); } else { ctx.beginPath(); ctx.arc(q[0], q[1], 5, 0, 7); ctx.fill(); }
        const on = route && route.includes(n);
        ctx.fillStyle = on ? '#ffffff' : '#9fb0cc';
        ctx.fillText(n, q[0] + 8, q[1] + 4);
      }
      // finance state follows the same clock
      clockOut.textContent = `${hfmt(tNow)}   ${clock(tNow)}`;
      slider.value = Math.round((tNow - t0) / span * 1000);
      if (ST) {
        let cur = null;
        for (const s of STATES) if (ST[s] != null && ST[s] <= tNow) cur = s;
        chip.textContent = cur || 'NOT STARTED';
        statesRow.replaceChildren(...STATES.map(s => el('span', { class: ST[s] != null && ST[s] <= tNow ? (s === cur ? 'cur' : 'done') : '' }, s)));
      } else {
        const done = (t.primary.steps || []).filter(s => s.t <= tNow);
        chip.textContent = done.length ? done[done.length - 1].label : 'NOT STARTED';
      }
      document.querySelectorAll('#flowSteps .step[data-t]').forEach(d => {
        const st = +d.dataset.t;
        d.classList.toggle('now', st <= tNow && !(+d.dataset.i + 1 < t.primary.steps.length && t.primary.steps[+d.dataset.i + 1].t <= tNow));
      });
    }
    function frame(ts) {
      if (!last) last = ts;
      const dt = (ts - last) / 1000; last = ts;
      if (playing) { tNow += dt * span / playSecs; if (tNow > t1) tNow = t0; }
      draw();
      raf = requestAnimationFrame(frame);
    }
    slider.oninput = () => { tNow = t0 + span * slider.value / 1000; playing = false; playBtn.textContent = '▶ Play'; };
    playBtn.onclick = () => { playing = !playing; playBtn.textContent = playing ? '❚❚ Pause' : '▶ Play'; };
    ANIM = {
      start: () => { if (SYS) raf = requestAnimationFrame(frame); },
      stop: () => cancelAnimationFrame(raf),
    };
    return card('Network + finance · replay of this run',
      el('div', { class: 'anim' },
        el('div', {}, canvas, legend),
        el('div', {},
          el('div', { class: 'sub' }, 'Transaction state'), chip, ST ? statesRow : null, clockOut,
          el('div', { style: 'margin:10px 0 6px; display:flex; gap:6px' }, playBtn),
          slider,
          el('p', { class: 'sub', style: 'white-space:normal' },
            `${launches.length} data launches from ${msgs.length} messages. Compressed radial scale; each hop leaves where the sender is at t_e and lands where the receiver is at t_a. Replays ${dur(span)} of model time.`))));
  }

  // ------------------------------------------------------------ auction
  function auctionCard(t) {
    const kids = [];
    t.auction.forEach(b => {
      const inBatch = b.orders.filter(o => o.in_batch);
      // Arrival order versus priority order, among same-side, same-limit orders.
      const groups = {};
      inBatch.forEach(o => { (groups[o.side + o.limit] = groups[o.side + o.limit] || []).push(o); });
      Object.values(groups).filter(g => g.length > 1).forEach(g => {
        const byArr = [...g].sort((x, y) => x.arrived_h - y.arrived_h).map(o => o.account);
        const bySrc = [...g].sort((x, y) => x.source_h - y.source_h || 0).map(o => o.account);
        const same = g.every(o => o.source_h === g[0].source_h);
        kids.push(el('div', { class: 'banner' }, same
          ? `Exact tie at ${g[0].limit}: same limit and same source time (${hfmt(g[0].source_h)}) → pro rata, spare share by hash.`
          : `Same price ${g[0].limit}: arrived at the market ${byArr.join(' → ')}; priority by source time ${bySrc.join(' → ')}. Network arrival time did not decide.`));
      });
      const bidCard = o => {
        const won = +o.allocated > 0;
        return el('div', { class: 'bid ' + (won ? 'win' : 'lose') },
          el('div', { class: 'h' }, `${o.account} @ ${o.home} · ${o.side.toUpperCase()} ${o.quantity} @ max ${o.limit}`.replace('max', o.side === 'buy' ? 'max' : 'min')),
          `source ${hfmt(o.source_h)} · arrived ${hfmt(o.arrived_h)}`);
      };
      const result = o => el('div', { class: 'bid ' + (+o.allocated > 0 ? 'win' : 'lose') },
        el('div', { class: 'h' }, `${+o.allocated > 0 ? '✓' : '✗'} ${o.account}: ${o.allocated} / ${o.quantity}` + (o.rank ? ` · rank ${o.rank}` : '')),
        o.reason);
      const tm = b.timing || {};
      kids.push(el('h3', {}, `${b.id} · opens ${hfmt(b.opened_h)} · closes ${hfmt(b.closes_h)}` + (b.price ? ` · clears @ ${b.price}` : ' · no trade')));
      kids.push(el('div', { class: 'auction' },
        el('div', { class: 'bids' }, inBatch.length ? inBatch.map(bidCard) : el('div', { class: 'sub' }, 'no orders in time')),
        el('div', { class: 'hub' }, `GLOBAL BATCH @ ${b.market}`, el('div', { class: 'sub' }, '↓ match'), el('div', { class: 'p' }, b.price ? '@ ' + b.price : '—')),
        el('div', { class: 'bids' }, inBatch.map(result))));
      if (tm.duration_h) kids.push(el('div', { class: 'sub', style: 'white-space:normal;margin-bottom:6px' },
        `Batch length = 1.1 × slowest one-way (${tm.slowest_from} ${dur(tm.slowest_one_way_h)}) + grace for ${tm.hop_retries || 1} hop retr${(tm.hop_retries || 1) > 1 ? 'ies' : 'y'} (${dur(tm.retry_allowance_h)}) = ${dur(tm.duration_h)}.`));
      kids.push(table(['Order', 'Account', 'Side', 'Qty', 'Limit', 'Source time', 'Arrival', 'Allocated', 'Reason'],
        b.orders.map(o => [o.id, `${o.account} @ ${o.home}`, o.side, o.quantity, o.limit, hfmt(o.source_h), hfmt(o.arrived_h),
          o.in_batch ? o.allocated : '— (missed)', o.reason]), 'wrap'));
      if (b.table.length) {
        const det = el('details', {}, el('summary', { class: 'sub' }, 'Clearing table (price → demand / supply / volume)'),
          table(['Price', 'Demand', 'Supply', 'Volume', 'Imbalance'], b.table.map(r => [r.price, r.demand, r.supply, r.volume, r.imbalance])));
        kids.push(det);
      }
    });
    return card('Global batch auction', kids);
  }

  // ------------------------------------------------------------ futures
  function futuresCard(t) {
    const f = t.futures;
    const s = f.series;
    const lastRow = s[s.length - 1];
    const openCall = f.calls.find(c => c.met_h == null);
    const tile = (k, v, sub) => el('div', { class: 'tile' }, el('div', { class: 'k' }, k), el('div', { class: 'v', style: 'font-size:16px' }, v), sub ? el('div', { class: 's' }, sub) : null);
    const kids = [el('div', { class: 'tiles' },
      tile('Contract price', f.contract_price, `${f.quantity} × ${f.symbol}, multiplier ${f.multiplier}`),
      tile(f.final_price ? 'Final index' : 'Current index', lastRow ? lastRow.price : '—', lastRow ? `observed ${hfmt(lastRow.observed_h)}` : ''),
      tile('Long', f.long, `window ${dur(f.risk_window_h[f.long])}`),
      tile('Short', f.short, `window ${dur(f.risk_window_h[f.short])}`),
      tile('Initial margin', f.initial_margin ? `${money(f.initial_margin[f.long])} / ${money(f.initial_margin[f.short])}` : '—', 'long / short at opening'),
      tile('Collateral (long)', lastRow ? money(lastRow.long.posted) : '—', 'posted at the clearing house'),
      tile('Maintenance (long)', lastRow ? money(lastRow.long.maintenance) : '—', `${Number(f.rule.maintenance_fraction) * 100}% of the allowance + loss`),
      tile('Unrealized P/L (long)', lastRow ? money(lastRow.long.pnl) : '—', lastRow && lastRow.event !== 'marked' ? 'realized at close' : 'at last mark'),
      tile('Margin call', f.calls.length ? (openCall ? '✗ OPEN · unmet' : `✓ ${f.calls.length} met`) : 'none', openCall ? `deadline ${hfmt(openCall.deadline_h)}` : ''),
      tile('Status', f.state.replace('_', ' ').toUpperCase(), f.state !== 'open' ? `paid ${money(f.paid)}${+f.guarantee_used ? `, ${money(f.guarantee_used)} from fund` : ''}` : ''),
      tile('Guarantee fund', money(f.guarantee_fund_now), 'contributed after hour 0'))];
    const markers = f.calls.map(c => ({ x: c.issued_h, label: `call ${money(c.amount)}` }));
    if (f.state !== 'open' && f.discharged_h) markers.push({ x: f.discharged_h, label: f.state === 'funded_default' ? 'default' : 'settled' });
    const breach = s.find(r => +r.long.posted < +r.long.maintenance);
    kids.push(lineChart({
      title: 'MOI index as marked at the clearing house',
      series: [{ name: 'MOI index', color: 'var(--series-1)', points: s.map(r => [r.t, +r.price]) }],
      markers, yFmt: v => num(v, 0), refLine: { y: +f.contract_price, label: `contract ${f.contract_price}` },
    }));
    kids.push(lineChart({
      title: `${f.long} (long) margin vs requirement, $`,
      series: [
        { name: 'Posted collateral', color: 'var(--series-1)', points: s.map(r => [r.t, +r.long.posted]) },
        { name: 'Maintenance requirement', color: 'var(--series-2)', dash: '6 4', points: s.map(r => [r.t, +r.long.maintenance]) },
        { name: 'Unrealized P/L', color: 'var(--series-3)', points: s.map(r => [r.t, +r.long.pnl]) },
      ],
      markers: breach ? [{ x: breach.t, label: 'maintenance crossed' }, ...markers.slice(1)] : markers,
      yFmt: v => '$' + num(v, 0), zero: true,
    }));
    if (f.calls.length) kids.push(el('h3', {}, 'Margin calls'), table(['Account', 'Issued', 'Amount', 'Deadline', 'Top-up validated', 'Result'],
      f.calls.map(c => [c.account, hfmt(c.issued_h), money(c.amount), hfmt(c.deadline_h), c.met_h != null ? hfmt(c.met_h) : '—',
        c.met_h != null && c.met_h <= c.deadline_h ? '✓ met in time' : '✗ unmet → default']), 'wrap'));
    if (f.state !== 'open') kids.push(el('h3', {}, 'Settlement moments'), table(['Discharge', 'Backed claim', 'Spendable', 'Winner', 'Paid', 'From guarantee fund', 'Unbacked'],
      [[hfmt(f.discharged_h), hfmt(f.backed_claim_h), hfmt(f.spendable_h), f.winner || '—', money(f.paid), money(f.guarantee_used), money(f.shortfall)]]));
    return card('Futures · ' + f.symbol, kids);
  }

  function lineChart(o) {
    const W = 820, H = 220, m = { l: 66, r: 150, t: 14, b: 26 };
    const all = o.series.flatMap(s => s.points);
    if (!all.length) return el('div', { class: 'sub' }, 'No observations marked.');
    const xs = all.map(p => p[0]).concat((o.markers || []).map(k => k.x));
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    if (x1 === x0) x1 = x0 + 1;
    let ys = all.map(p => p[1]);
    if (o.zero) ys.push(0);
    if (o.refLine) ys.push(o.refLine.y);
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    const padY = (y1 - y0) * 0.08 || 1; y0 -= padY; y1 += padY;
    const X = x => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r);
    const Y = y => H - m.b - (y - y0) / (y1 - y0) * (H - m.t - m.b);
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.title });
    const step = niceStep((y1 - y0) / 4);
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) {
      svg.appendChild(sv('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: '#18223a' }));
      svg.appendChild(sv('text', { x: m.l - 6, y: Y(v) + 3, 'text-anchor': 'end', fill: '#7d8aa3', 'font-size': 10 }, o.yFmt(v)));
    }
    const xstep = niceStep((x1 - x0) / 6);
    for (let v = Math.ceil(x0 / xstep) * xstep; v <= x1; v += xstep)
      svg.appendChild(sv('text', { x: X(v), y: H - 8, 'text-anchor': 'middle', fill: '#7d8aa3', 'font-size': 10 }, 'h ' + num(v)));
    if (o.zero && y0 < 0 && y1 > 0) svg.appendChild(sv('line', { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: '#33415e' }));
    if (o.refLine) {
      svg.appendChild(sv('line', { x1: m.l, x2: W - m.r, y1: Y(o.refLine.y), y2: Y(o.refLine.y), stroke: '#33415e', 'stroke-dasharray': '2 3' }));
      svg.appendChild(sv('text', { x: m.l + 4, y: Y(o.refLine.y) - 4, fill: '#7d8aa3', 'font-size': 10 }, o.refLine.label));
    }
    (o.markers || []).forEach((k, i) => {
      svg.appendChild(sv('line', { x1: X(k.x), x2: X(k.x), y1: m.t, y2: H - m.b, stroke: '#7d8aa3', 'stroke-dasharray': '3 3' }));
      svg.appendChild(sv('text', { x: X(k.x) + 3, y: m.t + 9 + (i % 3) * 11, fill: '#b8c3d8', 'font-size': 10 }, k.label));
    });
    const ends = [];
    o.series.forEach(s => {
      const d = s.points.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
      svg.appendChild(sv('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-dasharray': s.dash, 'stroke-linejoin': 'round' }));
      s.points.forEach(p => svg.appendChild(sv('circle', { cx: X(p[0]), cy: Y(p[1]), r: 2.5, fill: s.color })));
      const lp = s.points[s.points.length - 1];
      ends.push({ y: Y(lp[1]), x: X(lp[0]), s, v: lp[1] });
    });
    // Direct end labels, nudged apart (text in ink, a short key in the series color).
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
    if (o.series.length > 1) ends.forEach(e => {
      svg.appendChild(sv('line', { x1: e.x + 6, x2: e.x + 16, y1: e.y, y2: e.y, stroke: e.s.color, 'stroke-width': 2, 'stroke-dasharray': e.s.dash }));
      svg.appendChild(sv('text', { x: e.x + 20, y: e.y + 3, fill: '#d7deea', 'font-size': 10 }, `${e.s.name} ${o.yFmt(e.v)}`));
    });
    // Hover: crosshair snaps to the nearest observation; one tooltip lists every series.
    const xsData = [...new Set(all.map(p => p[0]))].sort((a, b) => a - b);
    const cross = sv('line', { y1: m.t, y2: H - m.b, stroke: '#d7deea', 'stroke-width': 1, visibility: 'hidden' });
    svg.appendChild(cross);
    const dots = o.series.map(s => { const c = sv('circle', { r: 4.5, fill: s.color, stroke: '#0c1220', 'stroke-width': 2, visibility: 'hidden' }); svg.appendChild(c); return c; });
    const hit = sv('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent', tabindex: 0 });
    svg.appendChild(hit);
    const tip = el('div', { class: 'tip' });
    const wrap = el('div', { class: 'chart' });
    function show(xv, clientX) {
      cross.setAttribute('x1', X(xv)); cross.setAttribute('x2', X(xv)); cross.setAttribute('visibility', 'visible');
      const rows = [el('div', { class: 'sub' }, `${hfmt(xv)}  ${clock(xv)}`)];
      o.series.forEach((s, i) => {
        const p = s.points.reduce((b, q) => Math.abs(q[0] - xv) < Math.abs(b[0] - xv) ? q : b);
        dots[i].setAttribute('cx', X(p[0])); dots[i].setAttribute('cy', Y(p[1])); dots[i].setAttribute('visibility', 'visible');
        rows.push(el('div', { class: 'row' }, el('i', { style: `border-color:${s.color}` }), el('b', {}, o.yFmt(p[1])), el('span', { class: 'sub' }, s.name)));
      });
      tip.replaceChildren(...rows);
      const r = svg.getBoundingClientRect();
      tip.style.display = 'block';
      tip.style.left = Math.min(r.width - 170, Math.max(0, (clientX ?? (r.left + X(xv) / W * r.width)) - r.left + 12)) + 'px';
      tip.style.top = '30px';
    }
    const hide = () => { tip.style.display = 'none'; cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); };
    hit.addEventListener('pointermove', ev => {
      const r = svg.getBoundingClientRect();
      const xv = x0 + ((ev.clientX - r.left) / r.width * W - m.l) / (W - m.l - m.r) * (x1 - x0);
      const near = xsData.reduce((b, q) => Math.abs(q - xv) < Math.abs(b - xv) ? q : b);
      show(near, ev.clientX);
    });
    hit.addEventListener('pointerleave', hide);
    let fi = 0;
    hit.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowRight') fi = Math.min(xsData.length - 1, fi + 1); else if (ev.key === 'ArrowLeft') fi = Math.max(0, fi - 1); else return;
      show(xsData[fi]); ev.preventDefault();
    });
    hit.addEventListener('focus', () => show(xsData[fi]));
    hit.addEventListener('blur', hide);
    // Table view, so no value depends on hovering.
    const tbl = table(['Time'].concat(o.series.map(s => s.name)),
      xsData.map(xv => [hfmt(xv)].concat(o.series.map(s => { const p = s.points.find(q => q[0] === xv); return p ? o.yFmt(p[1]) : '—'; }))));
    tbl.style.display = 'none';
    const toggle = el('button', { class: 'linkbtn', onclick: () => { const on = tbl.style.display === 'none'; tbl.style.display = on ? 'block' : 'none'; toggle.textContent = on ? 'Hide table' : 'Show table'; } }, 'Show table');
    const legend = o.series.length > 1 ? el('div', { class: 'legend' }, o.series.map(s =>
      el('span', {}, el('i', { style: `border-color:${s.color};${s.dash ? 'border-top-style:dashed' : ''}` }), s.name))) : null;
    wrap.append(...[el('h3', {}, o.title, ' ', toggle), legend, svg, tip, tbl].filter(Boolean));
    return wrap;
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const f = raw / p;
    return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p;
  }

  // ------------------------------------------------------------ balances
  function balanceCard(t) {
    const rows = snap => {
      const out = [];
      for (const p of snap.principals) for (const h of p.holdings)
        out.push({ key: `${p.principal}|${h.asset}|${h.settlement}`, who: p.principal, ...h });
      return out;
    };
    const before = rows(t.balances.before), after = rows(t.balances.after);
    const keys = [...new Set(before.concat(after).map(r => r.key))];
    const get = (list, k) => list.find(r => r.key === k);
    const fmt = (asset, v) => asset === 'cash' ? money(v) : num(v);
    const changed = keys.filter(k => JSON.stringify(get(before, k) || {}) !== JSON.stringify(get(after, k) || {}));
    const show = changed.length ? changed : keys;
    const mk = (list, title) => el('div', {}, el('h3', {}, title), table(['Principal', 'Asset @ where', 'Balance', 'Available', 'Locked', 'Collateral'],
      show.map(k => {
        const r = get(list, k); const [who, asset, where] = k.split('|');
        return r ? [who, `${asset} @ ${where}`, fmt(asset, r.balance), fmt(asset, r.available), fmt(asset, r.locked), fmt(asset, r.collateral)]
          : [who, `${asset} @ ${where}`, '—', '—', '—', '—'];
      })));
    const tot = (snap, a) => snap.totals[a];
    const sys = table(['System totals', 'On ledgers', 'In transit', 'Total', 'Opening', 'Conserved'],
      ['cash', 'shares'].flatMap(a => [['before', 'after'].map(w => {
        const x = tot(t.balances[w], a);
        return [`${a} · ${w}`, fmt(a, x.on_ledgers), fmt(a, x.in_transit), fmt(a, x.total), fmt(a, x.opening),
          el('span', { class: x.conserved ? 'ok' : 'bad' }, x.conserved ? '✓ yes' : '✗ NO')];
      })]).flat());
    return card('Balances · before → after' + (changed.length ? ` (${changed.length} changed holdings)` : ''),
      el('div', { class: 'bal' }, mk(before, 'Before (hour 0)'), mk(after, 'After')), el('h3', {}, 'Conservation'), sys);
  }

  // ------------------------------------------------------------ extras + timeline
  function extraCard(t) {
    const kids = [];
    const integ = t.integrity || {};
    if (integ.double_spend_attempts && integ.double_spend_attempts.length)
      kids.push(el('h3', {}, 'Double-spend attempts'), table(['Time', 'Attempt', 'Result', 'Rule that stopped it'],
        integ.double_spend_attempts.map(b => [hfmt(b.t), b.attempt, el('span', { class: b.rejected ? 'ok' : 'bad' }, b.rejected ? '✓ REJECTED' : '✗ ALLOWED'), b.error || '—']), 'wrap'));
    const ex = t.extra || {};
    if (ex.cancel_results) kids.push(el('h3', {}, 'Cancel requests at the market'), table(['Order', 'Arrived', 'Result'],
      Object.entries(ex.cancel_results).map(([k, v]) => [k, hfmt(v.t), v.result.toUpperCase()])));
    if (ex.tiebreak) kids.push(el('h3', {}, 'Tie-break hashes sha256(batch:order)'), table(['Order', 'Hash prefix'], Object.entries(ex.tiebreak)));
    if (ex.outage) kids.push(el('h3', {}, 'Outage'), table(['Node', 'Start', 'End', 'Financial service restored', 'Lost service'],
      [[ex.outage.node, hfmt(ex.outage.start_h), hfmt(ex.outage.end_h), hfmt(ex.outage.service_restored_h), dur(ex.outage.lost_service_h)]]));
    const n = t.network;
    if (n) kids.push(el('h3', {}, 'Communication'), table(['Launches', 'Lost', 'Hop retries', 'Backbone quota packets (h ≥ 0)', 'Session setup (before h 0)', 'Loss causes'],
      [[n.launches, n.lost, n.hop_retries, n.backbone_quota_packets, n.setup_quota_packets,
        Object.entries(n.loss_reasons).map(([k, v]) => `${v} × ${k}`).join('; ') || 'none (conditional no-loss trace)']], 'wrap'));
    (t.notes || []).forEach(x => kids.push(el('div', { class: 'banner' }, x)));
    return kids.length ? card('Integrity & communication', kids) : null;
  }

  function timelineCard(t) {
    const cats = { finance: true, network: true, local: true, transport: false, note: true };
    const body = el('tbody');
    const render = () => body.replaceChildren(...t.timeline.filter(r => cats[r.category] ?? true).map(r =>
      el('tr', { class: `cat-${r.category}` + (/LOST/.test(r.label) ? ' lostrow' : '') },
        el('td', {}, hfmt(r.t)), el('td', { class: 'sub' }, clock(r.t)), el('td', {}, r.actor || ''),
        el('td', { class: 'lbl' }, r.label), el('td', {}, r.detail + (r.note ? ` — ${r.note}` : '')))));
    const filters = el('div', { class: 'filters' }, Object.keys(cats).map(k => {
      const cb = el('input', { type: 'checkbox' }); cb.checked = cats[k];
      cb.onchange = () => { cats[k] = cb.checked; render(); };
      return el('label', {}, cb, k);
    }));
    render();
    return card(`Settlement timeline · ${t.timeline.length} events from hour 0`, filters,
      el('div', { class: 'tl scroll' }, el('table', { class: 'wrap' },
        el('thead', {}, el('tr', {}, ['Hour', 'Clock', 'Actor', 'Event', 'Detail'].map(h => el('th', {}, h)))), body)));
  }

  init();
})();
