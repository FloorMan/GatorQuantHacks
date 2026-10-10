// Verifies the propagator against the brief's epoch check positions (Section 3)
// and the period-return / radius-bound checks. Run: node scripts/check_epoch.js
const Physics = require('../src/physics.js');
const sys = Physics.createSystem(
  require('../../Data/orbital_elements.json'),
  require('../../Data/network_model.json'));

const expected = {
  Mercury: [-0.213426, -0.410281, -0.013955],
  Venus: [0.679067, -0.257950, -0.042725],
  Earth: [1.003581, -0.023693, -0.000003],
  Mars: [0.247476, 1.526869, 0.025929],
  Ceres: [0.375786, 2.653941, 0.014791],
  Jupiter: [-3.438179, 4.038309, 0.060149],
  Saturn: [9.271221, 1.717485, -0.398962],
  Uranus: [8.962475, 17.253919, -0.052129],
  Neptune: [29.839098, 1.351785, -0.715470],
  'Relay A': [2, 2, 0],
  'Relay B': [-2, 2, 0],
};

let ok = true;
console.log('Epoch check (tolerance 1e-5 AU)');
for (const [name, exp] of Object.entries(expected)) {
  const p = sys.position(name, 0);
  const err = Physics.dist(p, exp);
  const pass = err < 1e-5;
  ok = ok && pass;
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name.padEnd(8)} ${p.map(v => v.toFixed(6).padStart(11)).join(' ')}  err=${err.toExponential(2)}`);
}

const els = require('../../Data/orbital_elements.json').bodies;
console.log('\nPeriod return and radius bounds');
for (const el of els) {
  const p0 = sys.position(el.name, 0), p1 = sys.position(el.name, el.period_days);
  let rmin = Infinity, rmax = 0;
  for (let k = 0; k <= 2000; k++) {
    const r = Math.hypot(...sys.position(el.name, el.period_days * k / 2000));
    rmin = Math.min(rmin, r); rmax = Math.max(rmax, r);
  }
  const q = el.a_au * (1 - el.e), Q = el.a_au * (1 + el.e);
  const pass = Physics.dist(p0, p1) < 1e-8 && rmin >= q - 1e-9 && rmax <= Q + 1e-9;
  ok = ok && pass;
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${el.name.padEnd(8)} return=${Physics.dist(p0, p1).toExponential(2)}  r in [${rmin.toFixed(5)}, ${rmax.toFixed(5)}]  bounds [${q.toFixed(5)}, ${Q.toFixed(5)}]`);
}

console.log('\nSample routes at t = 0');
for (const [a, b] of [['Earth', 'Mars'], ['Earth', 'Neptune'], ['Ceres', 'Mars']]) {
  const r = sys.routesAt(a, b, 0)[0];
  console.log(`${r.path.join(' -> ')}  one-way ${(r.oneWay * 1440).toFixed(2)} min  ` +
    r.hops.map(h => `${h.from}->${h.to} d=${h.d.toFixed(4)} loss=${(h.loss * 100).toFixed(2)}% ${h.status}`).join(' | '));
}
process.exit(ok ? 0 : 1);
