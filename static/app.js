/**
 * Bot Local Web — Tích hợp chuẩn xác thư viện thinking-orbs v0.3.2 (by Jakub Antalik)
 * Giữ nguyên 100% thông số PRESETS[mode][64] gốc của libraries.dev/orbs
 * kèm hiệu ứng chuyển mượt (crossfade) và vòng đời trạng thái AI tự động:
 * Idle (breathing) -> Typing (listening) -> Thinking (searching/working/solving/connecting) -> Replying (composing) -> Idle (breathing).
 */

/* ==========================================================================
   PHẦN 1: ENGINE THINKING-ORBS V0.3.2 NGUYÊN BẢN (VIRTUAL 64PX SPEC)
   ========================================================================== */

function lerp(a, b, f) {
  return a + (b - a) * f;
}
function frac(x) {
  return x - Math.floor(x);
}
function hashD(a, b) {
  const h = Math.sin(a * 12.9898 + b * 78.233) * 43758.5453;
  return h - Math.floor(h);
}
function vnoise(x, y) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  let fx = x - xi;
  let fy = y - yi;
  fx = fx * fx * (3 - 2 * fx);
  fy = fy * fy * (3 - 2 * fy);
  const a = hashD(xi, yi);
  const b = hashD(xi + 1, yi);
  const c = hashD(xi, yi + 1);
  const d = hashD(xi + 1, yi + 1);
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
}
function fibDir(i, n) {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (2 * (i + 0.5)) / n;
  const rad = Math.sqrt(Math.max(0, 1 - y * y));
  const a = i * golden;
  return [rad * Math.cos(a), y, rad * Math.sin(a)];
}
function angleDelta(a, b) {
  return Math.atan2(Math.sin(a - b), Math.cos(a - b));
}
function makeProj(yaw, tilt, cx, cy, scale) {
  const st = Math.sin(tilt);
  const ct = Math.cos(tilt);
  const sy = Math.sin(yaw);
  const cyw = Math.cos(yaw);
  return (x, y, z) => {
    const x1 = x * cyw + z * sy;
    const z1 = -x * sy + z * cyw;
    const y1 = y * ct - z1 * st;
    const z2 = y * st + z1 * ct;
    return [cx + x1 * scale, cy - y1 * scale, z2];
  };
}
function inkColor(w, alpha, dark, tint) {
  if (!tint) {
    const g = Math.round((dark ? 1 - w : w) * 255);
    return `rgba(${g},${g},${g},${alpha})`;
  }
  const ramp = (c) => Math.round(dark ? c * (1 - w) : c + (255 - c) * w);
  return `rgba(${ramp(tint.r)},${ramp(tint.g)},${ramp(tint.b)},${alpha})`;
}
function paint(ctx, dots, dark, rMin = 0.3, tint, alphaMul = 1) {
  for (const d of dots) {
    const alpha = (d.a ?? 1) * alphaMul;
    if (alpha < 0.015) continue;
    const w = Math.min(1, Math.max(0, d.white));
    ctx.fillStyle = inkColor(w, alpha, dark, tint);
    ctx.beginPath();
    ctx.arc(d.x, d.y, Math.max(rMin, d.r), 0, Math.PI * 2);
    ctx.fill();
  }
}
function paintLines(ctx, lines, dark, tint, alphaMul = 1) {
  for (const l of lines) {
    const alpha = (l.a ?? 1) * alphaMul;
    if (alpha < 0.015) continue;
    const w = Math.min(1, Math.max(0, l.white));
    ctx.strokeStyle = inkColor(w, alpha, dark, tint);
    ctx.lineWidth = l.w;
    ctx.beginPath();
    ctx.moveTo(l.x1, l.y1);
    ctx.lineTo(l.x2, l.y2);
    ctx.stroke();
  }
}
function finalizeFrame(dots, lines, rMin = 0.3) {
  const visible = [];
  for (const d of dots) {
    if ((d.a ?? 1) < 0.02) continue;
    d.r = Math.max(rMin, d.r);
    visible.push(d);
  }
  visible.sort((a, b) => a.z - b.z);
  return { dots: visible, lines: lines.filter((l) => (l.a ?? 1) >= 0.02) };
}
function paintFrame(ctx, frame, dark, tint, alphaMul = 1) {
  if (frame.lines.length) paintLines(ctx, frame.lines, dark, tint, alphaMul);
  paint(ctx, frame.dots, dark, 0.3, tint, alphaMul);
}
function radiusScale(size, pow) {
  return (size / 300) ** pow;
}

const COUNT_PAIRS = [
  ["latRings", "lonDensity"],
  ["rings", "lonDensity"],
  ["lanes", "segs"],
];
const COUNT_KEYS = ["orbitN", "ghostN", "nodeN", "strandN", "signals"];
const ICON_DENSITY_KEYS = ["iconD"];
const RADIUS_KEYS = [
  "rBase",
  "rDepth",
  "rActive",
  "rDot",
  "ghostR",
  "partR",
  "partRDepth",
  "nodeR",
  "nodeRDepth",
];

function scaleCounts(opts, scale) {
  const out = { ...opts };
  const done = new Set();
  const rt = Math.sqrt(scale);
  for (const [a, b] of COUNT_PAIRS) {
    const va = out[a];
    const vb = out[b];
    if (va != null && vb != null && !done.has(a) && !done.has(b)) {
      out[a] = Math.max(2, Math.round(va * rt));
      out[b] = Math.max(2, Math.round(vb * rt));
      done.add(a);
      done.add(b);
    }
  }
  for (const k of COUNT_KEYS) {
    const v = out[k];
    if (v != null && v !== 0 && !done.has(k)) out[k] = Math.max(1, Math.round(v * scale));
  }
  for (const k of ICON_DENSITY_KEYS) {
    const v = out[k];
    if (v != null) out[k] = Math.max(0.02, v * scale);
  }
  return out;
}

function scaleRadii(opts, scale) {
  const out = { ...opts };
  for (const k of RADIUS_KEYS) {
    const v = out[k];
    if (v != null) out[k] = v * scale;
  }
  out.rSizeMul = (out.rSizeMul ?? 1) * scale;
  return out;
}

// Giữ nguyên 100% BASE_PROFILES từ thinking-orbs@0.3.2
const BASE_PROFILES = {
  globe: { latRings: 17, lonDensity: 44, rBase: 0.6, rDepth: 1.7, rBoost: 1, inkFar: 0.62, inkSpan: 0.54, rsPow: 0.6, rMin: 0.3 },
  orbits: { orbitN: 12, ghostN: 40, ghostR: 0.9, ghostA: 0.5, particles: 3, partR: 1.2, partRDepth: 1.6, rsPow: 0.6, rMin: 0.3 },
  rubik: { latRings: 15, lonDensity: 40, moveCount: 14, rBase: 0.6, rDepth: 1.7, rActive: 0.3, inkFar: 0.62, inkSpan: 0.54, rsPow: 0.6, rMin: 0.3 },
  wave: { rings: 15, lonDensity: 40, rBase: 0.6, rDepth: 1.7, rsPow: 0.6, rMin: 0.3 },
  web: { nodeN: 30, thr: 0.72, signals: 5, nodeR: 1.4, nodeRDepth: 1.8, lineW: 0.8, rsPow: 0.6, rMin: 0.3 },
  braid: { strandN: 52, turns: 3, ghostN: 150, rBase: 1.2, rDepth: 1.8, rsPow: 0.6, rMin: 0.3 },
  ribbon: { lanes: 5, segs: 88, ghostN: 150, rBase: 1.1, rDepth: 1.7, rsPow: 0.6, rMin: 0.3 },
  ring: { lanes: 5, segs: 88, ghostN: 0, faceOn: 1, rBase: 1.1, rDepth: 1.7, rsPow: 0.6, rMin: 0.3 },
  morph: { rDot: 0.021, iconD: 1, rMin: 0.25 },
};

const frameBraid = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.76;
  const pt = makeProj(t * 0.4, 0.3, cx, cy, 1);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const dots = [];
  const ghostN = o.ghostN ?? 150;
  for (let i = 0; i < ghostN; i++) {
    const d = fibDir(i, ghostN);
    const [px, py, z] = pt(d[0] * R, d[1] * R, d[2] * R);
    const depth = (z / R + 1) / 2;
    dots.push({ x: px, y: py, z, r: 0.8 * rs, white: 0.78, a: 0.1 + 0.22 * depth });
  }
  const strandN = o.strandN ?? 52, turns = o.turns ?? 3;
  for (let s = 0; s < 3; s++) {
    const phase = (s / 3) * 2 * Math.PI;
    for (let i = 0; i < strandN; i++) {
      const u = (frac(i / strandN + t * 0.045) * 2 - 1) * 0.96;
      const surf = Math.sqrt(Math.max(0, 1 - u * u));
      const endFade = Math.min(1, (1 - Math.abs(u)) / 0.1);
      const a = u * Math.PI * turns + phase;
      const weave = 1 + 0.075 * Math.sin(u * Math.PI * turns * 2 + phase * 2 + t * 0.8);
      const rr = surf * R * weave;
      const [px, py, zr] = pt(Math.cos(a) * rr, u * R * weave, Math.sin(a) * rr);
      const depth = (zr / R + 1) / 2;
      dots.push({
        x: px, y: py, z: zr,
        r: ((o.rBase ?? 1.2) + (o.rDepth ?? 1.8) * depth) * rs,
        white: 0.55 - 0.45 * depth,
        a: endFade * (0.45 + 0.55 * depth),
      });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

function solveCycle(time, count, slotDur, rest) {
  const cyc = 2 * count * slotDur + rest;
  const tc = time % cyc;
  const amount = new Array(count).fill(0);
  let active = -1;
  if (tc < 2 * count * slotDur) {
    const slot = Math.floor(tc / slotDur);
    const p = (tc - slot * slotDur) / slotDur;
    const cl = Math.min(1, p / 0.7);
    const ep = 1 - (1 - cl) ** 3;
    if (slot < count) {
      for (let i = 0; i < slot; i++) amount[i] = 1;
      amount[slot] = ep;
      active = slot;
    } else {
      const u = 2 * count - 1 - slot;
      for (let i = 0; i < u; i++) amount[i] = 1;
      amount[u] = 1 - ep;
      active = u;
    }
  }
  return { amount, active };
}

function applyMoves(pt3, moves, sc) {
  let [x, y, z] = pt3;
  let inActive = false;
  for (let i = 0; i < moves.length; i++) {
    if (sc.amount[i] <= 0) continue;
    const mv = moves[i];
    const coord = mv.axis === 0 ? x : mv.axis === 1 ? y : z;
    if (coord < mv.lo || coord >= mv.hi) continue;
    if (i === sc.active) inActive = true;
    const a = mv.ang * sc.amount[i];
    const ca = Math.cos(a), sa = Math.sin(a);
    if (mv.axis === 0) {
      const y2 = y * ca - z * sa;
      z = y * sa + z * ca;
      y = y2;
    } else if (mv.axis === 1) {
      const x2 = x * ca + z * sa;
      z = -x * sa + z * ca;
      x = x2;
    } else {
      const x2 = x * ca - y * sa;
      y = x * sa + y * ca;
      x = x2;
    }
  }
  return [x, y, z, inActive];
}

function makeMoves(count) {
  const moves = [];
  for (let i = 0; i < count; i++) {
    const axis = Math.min(2, Math.floor(hashD(i, 2.3) * 3));
    const lo = -1 + 0.5 * Math.min(3, Math.floor(hashD(i, 5.9) * 4));
    const dir = hashD(i, 7.7) < 0.5 ? 1 : -1;
    moves.push({ axis, lo, hi: lo + 0.5, ang: dir * (Math.PI / 2) });
  }
  return moves;
}

const frameGlobe = (size, t, o) => {
  const spin = 0.5, cx = size / 2, cy = size / 2, radius = (size / 2) * 0.82;
  const tilt = 0.4 + 0.06 * Math.sin(t * 0.35);
  const pt = makeProj(t * spin, tilt, cx, cy, radius);
  const scan = t * (spin + (1.7 - spin) * (o.scanMul ?? 1));
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const dimBase = o.dimBase ?? 1;
  const dots = [];
  const latRings = o.latRings ?? 17, lonDensity = o.lonDensity ?? 44;
  for (let li = 0; li <= latRings; li++) {
    const lat = -Math.PI / 2 + (li / latRings) * Math.PI;
    const cosLat = Math.cos(lat), sinLat = Math.sin(lat);
    const lonCount = Math.max(1, Math.round(Math.abs(cosLat) * lonDensity));
    for (let lj = 0; lj < lonCount; lj++) {
      const lon = (lj / lonCount) * 2 * Math.PI;
      const [px, py, z] = pt(cosLat * Math.cos(lon), sinLat, cosLat * Math.sin(lon));
      const depth = (z + 1) / 2;
      const d = angleDelta(lon + t * spin, scan);
      const boost = Math.exp(-(d * d) / 0.18) * Math.max(0, z);
      dots.push({
        x: px, y: py, z,
        r: ((o.rBase ?? 0.6) + (o.rDepth ?? 1.7) * depth + (o.rBoost ?? 1) * boost) * rs,
        white: (o.inkFar ?? 0.62) - (o.inkSpan ?? 0.54) * depth,
        a: dimBase + (1 - dimBase) * Math.min(1, boost),
      });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

const frameRubik = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.82;
  const pt = makeProj(t * 0.55, 0.35 + 0.1 * Math.sin(t * 0.9), cx, cy, R);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const moveCount = o.moveCount ?? 14;
  const moves = makeMoves(moveCount);
  const sc = solveCycle(t, moveCount, 0.42, 1.2);
  const dots = [];
  const latRings = o.latRings ?? 15, lonDensity = o.lonDensity ?? 40;
  for (let li = 0; li <= latRings; li++) {
    const lat = -Math.PI / 2 + (li / latRings) * Math.PI;
    const cosLat = Math.cos(lat), sinLat = Math.sin(lat);
    const lonCount = Math.max(1, Math.round(Math.abs(cosLat) * lonDensity));
    for (let lj = 0; lj < lonCount; lj++) {
      const lon = (lj / lonCount) * 2 * Math.PI;
      const [x, y, z, inActive] = applyMoves([cosLat * Math.cos(lon), sinLat, cosLat * Math.sin(lon)], moves, sc);
      const [px, py, zr] = pt(x, y, z);
      const depth = (zr + 1) / 2;
      dots.push({
        x: px, y: py, z: zr,
        r: ((o.rBase ?? 0.6) + (o.rDepth ?? 1.7) * depth + (inActive ? o.rActive ?? 0.3 : 0)) * rs,
        white: (o.inkFar ?? 0.62) - (o.inkSpan ?? 0.54) * depth - (inActive ? 0.14 : 0),
      });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

const frameWave = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.874;
  const pt = makeProj(t * 0.18, 0.38, cx, cy, 1);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const dots = [];
  const rings = o.rings ?? 15, lonDensity = o.lonDensity ?? 40;
  for (let ri = 0; ri <= rings; ri++) {
    const lat = -Math.PI / 2 + (ri / rings) * Math.PI;
    const cosLat = Math.cos(lat), sinLat = Math.sin(lat);
    const w = 0.62 * Math.sin(t * 2.1 - ri * 0.52) + 0.38 * Math.sin(t * 1.27 + ri * 0.83);
    const rr = R * (0.88 + 0.105 * w);
    const lonCount = Math.max(1, Math.round(Math.abs(cosLat) * lonDensity));
    for (let lj = 0; lj < lonCount; lj++) {
      const lon = (lj / lonCount) * 2 * Math.PI;
      const [px, py, z] = pt(cosLat * Math.cos(lon) * rr, sinLat * rr, cosLat * Math.sin(lon) * rr);
      const depth = (z / R + 1) / 2;
      const crest = Math.max(0, w);
      dots.push({
        x: px, y: py, z,
        r: ((o.rBase ?? 0.6) + (o.rDepth ?? 1.7) * depth) * (1 + 0.4 * crest) * rs,
        white: 0.66 - 0.56 * depth - 0.1 * crest,
      });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

function smoothE(x) {
  return x * x * (3 - 2 * x);
}
function polyPath(verts) {
  const V = verts.length, L = [];
  let total = 0;
  for (let i = 0; i < V; i++) {
    const a = verts[i], b = verts[(i + 1) % V];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    L.push(l);
    total += l;
  }
  return (f) => {
    let target = f * total, i = 0;
    while (target > L[i] && i < V - 1) {
      target -= L[i];
      i++;
    }
    const a = verts[i], b = verts[(i + 1) % V];
    const ff = L[i] ? Math.min(1, target / L[i]) : 0;
    return [a[0] + (b[0] - a[0]) * ff, a[1] + (b[1] - a[1]) * ff];
  };
}
const CIRCLE = (f) => {
  const a = -Math.PI / 2 + f * 2 * Math.PI;
  return [Math.cos(a) * 0.24, Math.sin(a) * 0.24];
};
const TRIANGLE = polyPath([[0, -0.26], [0.24, 0.16], [-0.24, 0.16]]);
const SQUARE = polyPath([[0, -0.2], [0.2, -0.2], [0.2, 0.2], [-0.2, 0.2], [-0.2, -0.2]]);
const CYCLE = [CIRCLE, TRIANGLE, SQUARE];
const HOLD = 1.4, MORPH = 0.9, SEG = HOLD + MORPH;

const frameMorph = (size, t, o) => {
  const K = CYCLE.length;
  const tc = t % (SEG * K);
  const k = Math.floor(tc / SEG);
  const local = tc - k * SEG;
  const m = local > HOLD ? smoothE((local - HOLD) / MORPH) : 0;
  const sprd = o.spread ?? 1;
  const pA = CYCLE[k], pB = CYCLE[(k + 1) % K];
  const M = 160, pts = [];
  for (let i = 0; i < M; i++) {
    const f = i / M, a = pA(f), b = pB(f);
    pts.push([(a[0] + (b[0] - a[0]) * m) * sprd, (a[1] + (b[1] - a[1]) * m) * sprd]);
  }
  const L = [];
  let total = 0;
  for (let i = 0; i < M; i++) {
    const a = pts[i], b = pts[(i + 1) % M];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    L.push(l);
    total += l;
  }
  const n = Math.max(6, Math.round(34 * (o.iconD ?? 1)));
  const re = (o.rDot ?? 0.021) * 1.35 * sprd;
  const pulse = 1 + 0.02 * Math.sin(local * 3.1);
  const dots = [], c2 = size / 2;
  let seg = 0, acc = 0;
  for (let k2 = 0; k2 < n; k2++) {
    const target = (k2 / n) * total;
    while (acc + L[seg] < target && seg < M - 1) {
      acc += L[seg];
      seg++;
    }
    const a = pts[seg], b = pts[(seg + 1) % M];
    const f = L[seg] ? Math.min(1, (target - acc) / L[seg]) : 0;
    const x = (a[0] + (b[0] - a[0]) * f) * pulse;
    const y = (a[1] + (b[1] - a[1]) * f) * pulse;
    dots.push({ x: c2 + x * size, y: c2 + y * size, z: 0, r: Math.max(0.35, re * size), white: 0.1 });
  }
  return finalizeFrame(dots, [], o.rMin);
};

const frameOrbits = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.82;
  const pt = makeProj(t * 0.12, 0.3, cx, cy, 1);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const dots = [];
  const orbitN = o.orbitN ?? 12, ghostN = o.ghostN ?? 40, particles = o.particles ?? 3;
  for (let orb = 0; orb < orbitN; orb++) {
    const h1 = hashD(orb, 1.7), h2 = hashD(orb, 5.2), h3 = hashD(orb, 8.9);
    const ro = R * (0.45 + 0.52 * h1);
    const th = h1 * 2 * Math.PI, phi = Math.acos(2 * h2 - 1);
    const nx = Math.sin(phi) * Math.cos(th), ny = Math.cos(phi), nz = Math.sin(phi) * Math.sin(th);
    let ux = -ny, uy = nx;
    const uz = 0, ul = Math.max(1e-6, Math.sqrt(ux * ux + uy * uy));
    ux /= ul;
    uy /= ul;
    const vx = ny * uz - nz * uy, vy = nz * ux - nx * uz, vz = nx * uy - ny * ux;
    const speed = (0.25 + 0.55 * h3) * (h3 > 0.5 ? 1 : -1);
    for (let k = 0; k < ghostN; k++) {
      const a = (k / ghostN) * 2 * Math.PI;
      const [px, py, z] = pt(
        (ux * Math.cos(a) + vx * Math.sin(a)) * ro,
        (uy * Math.cos(a) + vy * Math.sin(a)) * ro,
        (uz * Math.cos(a) + vz * Math.sin(a)) * ro
      );
      const depth = (z / ro + 1) / 2;
      dots.push({ x: px, y: py, z, r: (o.ghostR ?? 0.9) * rs, white: 0.72, a: (o.ghostA ?? 0.5) * (0.4 + 0.6 * depth) });
    }
    for (let m = 0; m < particles; m++) {
      const a = t * speed + (m / particles) * 2 * Math.PI + h2 * 6;
      const [px, py, z] = pt(
        (ux * Math.cos(a) + vx * Math.sin(a)) * ro,
        (uy * Math.cos(a) + vy * Math.sin(a)) * ro,
        (uz * Math.cos(a) + vz * Math.sin(a)) * ro
      );
      const depth = (z / ro + 1) / 2;
      dots.push({ x: px, y: py, z, r: ((o.partR ?? 1.2) + (o.partRDepth ?? 1.6) * depth) * rs, white: 0.3 - 0.22 * depth });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

const frameRibbon = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.78;
  const spin = o.spin ?? 1, camTilt = 0.3;
  const pt = makeProj(t * 0.1 * spin, camTilt, cx, cy, 1);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const dots = [];
  const ghostN = o.ghostN ?? 150;
  for (let i = 0; i < ghostN; i++) {
    const d = fibDir(i, ghostN);
    const [px, py, z] = pt(d[0] * R, d[1] * R, d[2] * R);
    const depth = (z / R + 1) / 2;
    dots.push({ x: px, y: py, z, r: 0.8 * rs, white: 0.78, a: 0.1 + 0.22 * depth });
  }
  const ya = t * 0.24 * spin;
  const ta = o.faceOn ? -camTilt : 0.55 + 0.3 * Math.sin(t * 0.18) * spin;
  const ux = Math.cos(ya), uy = 0, uz = Math.sin(ya);
  const vx = -uz * Math.sin(ta), vy = Math.cos(ta), vz = ux * Math.sin(ta);
  const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
  const wobAmp = 0.23 * (o.wobMul ?? 1);
  const baseR = o.faceOn ? R / (1 + 0.85 * wobAmp) : R;
  const baseLanes = o.lanes ?? 5, segs = o.segs ?? 88;
  const lanes = Math.max(1, Math.round(baseLanes * (o.bandMul ?? 1)));
  for (let w = 0; w < lanes; w++) {
    const laneOff = (w - (lanes - 1) / 2) * 0.075;
    const edge = Math.abs(w - (lanes - 1) / 2) / Math.max(1, (lanes - 1) / 2);
    for (let k = 0; k < segs; k++) {
      const a = (k / segs) * 2 * Math.PI;
      const wob = (0.16 * Math.sin(a * 3 - t * 1.7 + w * 0.22) + 0.07 * Math.sin(a * 5 + t * 1.1)) * (o.wobMul ?? 1);
      const radial = o.faceOn ? 1 + wob : 1;
      const off = o.faceOn ? laneOff : laneOff + wob;
      const x = ux * Math.cos(a) + vx * Math.sin(a) + nx * off;
      const y = uy * Math.cos(a) + vy * Math.sin(a) + ny * off;
      const z = uz * Math.cos(a) + vz * Math.sin(a) + nz * off;
      const l = Math.sqrt(x * x + y * y + z * z);
      const rr = baseR * radial;
      const [px, py, zr] = pt((x / l) * rr, (y / l) * rr, (z / l) * rr);
      const depth = (zr / R + 1) / 2;
      dots.push({
        x: px, y: py, z: zr,
        r: ((o.rBase ?? 1.1) + (o.rDepth ?? 1.7) * depth) * (1 - 0.25 * edge) * rs,
        white: 0.52 - 0.44 * depth + 0.18 * edge,
        a: 0.4 + 0.6 * depth,
      });
    }
  }
  return finalizeFrame(dots, [], o.rMin);
};

const frameWeb = (size, t, o) => {
  const cx = size / 2, cy = size / 2, R = (size / 2) * 0.8 * (o.spread ?? 1);
  const pt = makeProj(t * 0.12, 0.32, cx, cy, R);
  const rs = radiusScale(size, o.rsPow ?? 0.6);
  const nodeN = o.nodeN ?? 30, thr = o.thr ?? 0.72, nodeR = o.nodeR ?? 1.4, nodeRDepth = o.nodeRDepth ?? 1.8;
  const nodes = [];
  for (let i = 0; i < nodeN; i++) {
    const d = fibDir(i, nodeN);
    const x = d[0] + 0.3 * (vnoise(i * 0.31 + 9, t * 0.24) - 0.5) * 2;
    const y = d[1] + 0.3 * (vnoise(i * 0.53 + 27, t * 0.21) - 0.5) * 2;
    const z = d[2] + 0.3 * (vnoise(i * 0.77 + 55, t * 0.27) - 0.5) * 2;
    const l = Math.sqrt(x * x + y * y + z * z);
    nodes.push([x / l, y / l, z / l]);
  }
  const lines = [], dots = [];
  for (let i = 0; i < nodeN; i++) {
    for (let j = i + 1; j < nodeN; j++) {
      const dx = nodes[i][0] - nodes[j][0], dy = nodes[i][1] - nodes[j][1], dz = nodes[i][2] - nodes[j][2];
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (dist >= thr) continue;
      const [x1, y1, z1] = pt(nodes[i][0], nodes[i][1], nodes[i][2]);
      const [x2, y2, z2] = pt(nodes[j][0], nodes[j][1], nodes[j][2]);
      const depth = ((z1 + z2) / 2 + 1) / 2;
      lines.push({ x1, y1, x2, y2, white: 0.42, a: (1 - dist / thr) * (0.3 + 0.55 * depth), w: Math.max(0.6, (o.lineW ?? 0.8) * rs) });
    }
  }
  for (let i = 0; i < nodeN; i++) {
    const [px, py, z] = pt(nodes[i][0], nodes[i][1], nodes[i][2]);
    const depth = (z + 1) / 2;
    const pulse = 1 + 0.25 * Math.sin(t * 1.4 + i * 2.7);
    dots.push({ x: px, y: py, z, r: (nodeR + nodeRDepth * depth) * pulse * rs, white: 0.55 - 0.45 * depth });
  }
  const signals = o.signals ?? 5;
  for (let s = 0; s < signals; s++) {
    const seg = Math.floor(t * 0.55 + s * 7.31);
    const a = Math.floor(hashD(seg, s * 3.1 + 1.7) * nodeN);
    const b = Math.floor(hashD(seg, s * 5.7 + 4.2) * nodeN);
    if (a === b) continue;
    const f = frac(t * 0.55 + s * 7.31);
    const x = lerp(nodes[a][0], nodes[b][0], f), y = lerp(nodes[a][1], nodes[b][1], f), z = lerp(nodes[a][2], nodes[b][2], f);
    const l = Math.max(1e-6, Math.sqrt(x * x + y * y + z * z));
    const [px, py, zr] = pt(x / l, y / l, z / l);
    const depth = (zr + 1) / 2;
    dots.push({ x: px, y: py, z: zr, r: (nodeR * 1.5 + nodeRDepth * depth) * rs, white: 0.05, a: 0.5 + 0.5 * depth });
  }
  return finalizeFrame(dots, lines, o.rMin);
};

const MODE_FRAMES = {
  orbits: frameOrbits,
  globe: frameGlobe,
  rubik: frameRubik,
  wave: frameWave,
  web: frameWeb,
  braid: frameBraid,
  ribbon: frameRibbon,
  ring: frameRibbon,
  morph: frameMorph,
};

const STATE_TO_MODE = {
  working: "orbits",
  searching: "globe",
  solving: "rubik",
  listening: "wave",
  connecting: "web",
  weaving: "braid",
  composing: "ribbon",
  breathing: "ring",
  shaping: "morph",
};

// Giữ nguyên 100% PRESETS 64px gốc của thinking-orbs@0.3.2
const PRESETS_64 = {
  orbits: { speed: 1.885, count: 1, size: 1 },
  globe: { speed: 2.015, count: 0.42, size: 1.15, extra: { scanMul: 4.08, dimBase: 0.45 } },
  rubik: { speed: 1.82, count: 0.35, size: 1.05 },
  wave: { speed: 4.388, count: 0.341, size: 1 },
  web: { speed: 3.315, count: 1.35, size: 0.95 },
  braid: { speed: 1.625, count: 0.5, size: 1 },
  ribbon: { speed: 2.34, count: 0.25, size: 0.85, extra: { spin: 0, bandMul: 3.9, wobMul: 1 } },
  ring: { speed: 3.24, count: 0.25, size: 0.956, extra: { spin: 0, bandMul: 3.627, wobMul: 0.368 } },
  morph: { speed: 2.405, count: 0.702, size: 0.395, extra: { spread: 1.45 } },
};

const presetCache = new Map();
function resolvePreset64(state) {
  if (presetCache.has(state)) return presetCache.get(state);
  const mode = STATE_TO_MODE[state] || "ring";
  const preset = PRESETS_64[mode];
  let opts = { ...BASE_PROFILES[mode] };
  if (preset.count !== 1) opts = scaleCounts(opts, preset.count);
  if (preset.size !== 1) opts = scaleRadii(opts, preset.size);
  if (preset.extra) opts = { ...opts, ...preset.extra };
  const resolved = { state, mode, speed: preset.speed, opts, frameFn: MODE_FRAMES[mode] };
  presetCache.set(state, resolved);
  return resolved;
}

function isDarkMode() {
  return document.documentElement.getAttribute("data-theme") !== "light";
}

/**
 * Mount ThinkingOrb render ở hệ tọa độ chuẩn VIRTUAL_SIZE = 64 của thư viện gốc
 * nhưng phóng nét vector lên displaySize (460px), có crossfade mượt khi đổi trạng thái
 * và tích phân pha liên tục (phase accumulator) để điều biến tốc độ/năng lượng (energy)
 * theo tốc độ gõ phím hoặc tốc độ sinh token GPU mà không bị giật khung hình.
 */
function mountThinkingOrb(canvas, initialState = "breathing", displaySize = 128) {
  if (!canvas) return null;
  const VIRTUAL_SIZE = 64;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(displaySize * dpr);
  canvas.height = Math.round(displaySize * dpr);
  canvas.style.width = `${displaySize}px`;
  canvas.style.height = `${displaySize}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const scale = (displaySize / VIRTUAL_SIZE) * dpr;
  let active = resolvePreset64(initialState);
  let prev = null;
  let transitionStart = 0;
  const TRANSITION_MS = 280;

  let lastFrameMs = performance.now();
  let activeTime = 0;
  let prevTime = 0;
  let currentEnergy = 1;
  let targetEnergy = 1;

  function render(now) {
    const dt = Math.min(0.05, Math.max(0, (now - lastFrameMs) / 1000));
    lastFrameMs = now;

    // Nội suy mượt mức năng lượng (energy multiplier)
    currentEnergy += (targetEnergy - currentEnergy) * Math.min(1, dt * 8);
    activeTime += dt * active.speed * currentEnergy;
    if (prev) {
      prevTime += dt * prev.speed * currentEnergy;
    }

    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.clearRect(0, 0, VIRTUAL_SIZE, VIRTUAL_SIZE);
    const dark = isDarkMode();

    if (prev) {
      const p = Math.min(1, (now - transitionStart) / TRANSITION_MS);
      const ease = p * p * (3 - 2 * p);
      paintFrame(ctx, prev.frameFn(VIRTUAL_SIZE, prevTime, prev.opts), dark, undefined, 1 - ease);
      paintFrame(ctx, active.frameFn(VIRTUAL_SIZE, activeTime, active.opts), dark, undefined, ease);
      if (p >= 1) prev = null;
    } else {
      paintFrame(ctx, active.frameFn(VIRTUAL_SIZE, activeTime, active.opts), dark);
    }

    requestAnimationFrame(render);
  }

  requestAnimationFrame(render);

  return {
    setState(nextState) {
      if (nextState === active.state) return;
      prev = active;
      prevTime = activeTime;
      active = resolvePreset64(nextState);
      transitionStart = performance.now();
    },
    setEnergy(mult) {
      targetEnergy = Math.max(0.65, Math.min(1.75, Number(mult) || 1));
    },
    getState() {
      return active.state;
    },
    getMode() {
      return active.mode;
    },
  };
}

/* ==========================================================================
   PHẦN 2: ĐIỀU KHIỂN TRẠNG THÁI THÔNG MINH THEO TELEMETRY THỜI GIAN THỰC
   - Không hardcode từ khóa ở frontend
   - Phản ứng theo động lực gõ phím (tốc độ gõ, xóa sửa, ngập ngừng suy nghĩ)
   - Đồng bộ trực tiếp với từng pha xử lý thực tế từ Backend & dòng chảy Token GPU
   ========================================================================== */

const FALLBACK_ERROR = "⚠️ Có lỗi gì đó, thử lại nhé.";
const ALL_STATES = [
  "breathing",
  "listening",
  "searching",
  "working",
  "solving",
  "connecting",
  "weaving",
  "composing",
  "shaping",
];

const STATE_DESCRIPTIONS = {
  breathing: "Sẵn sàng",
  listening: "Đang lắng nghe…",
  working: "Đang xử lý tác vụ…",
  searching: "Đang truy xuất dữ liệu thời gian thực…",
  solving: "Đang suy luận trên GPU…",
  connecting: "Đang kết nối ngữ cảnh…",
  weaving: "Đang tổng hợp dữ liệu…",
  composing: "Đang phản hồi…",
  shaping: "Đang định hình ý tưởng…",
};

const chatEl = document.getElementById("chat");
const msgInput = document.getElementById("msg");
const sendBtn = document.getElementById("sendBtn");
const typingText = document.getElementById("typingText");
const centerOrbCanvas = document.getElementById("centerOrb");
const orbModeBtn = document.getElementById("orbModeBtn");
const themeToggleBtn = document.getElementById("themeToggleBtn");

let isSending = false;
let typingIdleTimer = 0;
let typingShapeTimer = 0;
let previewResetTimer = 0;

// Telemetry theo dõi nhịp gõ phím của người dùng
const keystrokeTimes = [];
let prevInputLength = 0;
let consecutiveDeletes = 0;

const centerOrb = mountThinkingOrb(centerOrbCanvas, "breathing", 460);

function setOrbVisualState(state, customCaption, energy = 1) {
  if (!centerOrb) return;
  centerOrb.setState(state);
  centerOrb.setEnergy(energy);
  if (typingText) {
    typingText.textContent = customCaption || STATE_DESCRIPTIONS[state] || "";
    typingText.classList.add("visible");
  }
}

function syncIdleState() {
  consecutiveDeletes = 0;
  setOrbVisualState("breathing", STATE_DESCRIPTIONS.breathing, 1);
}

/**
 * Escape HTML để chống XSS tuyệt đối, sau đó định dạng các cụm trong dấu `...`
 * thành thẻ <code class="inline-code"> có thể bấm để gửi lệnh ngay.
 */
function formatSafeMessageHtml(text) {
  const escaped = String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
  return escaped.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
}

function scrollToBottom() {
  if (!chatEl) return;
  chatEl.scrollTop = chatEl.scrollHeight;
}

function appendUserMessage(text) {
  if (!chatEl) return;
  const row = document.createElement("div");
  row.className = "message-row user-row";
  const bubble = document.createElement("div");
  bubble.className = "message user-message";
  bubble.textContent = text;
  row.appendChild(bubble);
  chatEl.appendChild(row);
  scrollToBottom();
}

function createBotBubble() {
  if (!chatEl) return null;
  const row = document.createElement("div");
  row.className = "message-row bot-row";
  const bubble = document.createElement("div");
  bubble.className = "message bot-message";
  row.appendChild(bubble);
  chatEl.appendChild(row);
  return { row, bubble };
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Phân tích cấu trúc văn bản đang được AI sinh ra theo thời gian thực (live token stream):
 * - Nếu dòng chảy token đang mở khối code ```...``` -> chuyển sang `working` (orbits: viết mã nguồn)
 * - Nếu đang liệt kê danh sách nhiều mục / phân tích cấu trúc -> chuyển sang `weaving` (braid: đan dệt ý tưởng)
 * - Nếu đang viết câu trả lời tự nhiên -> giữ `composing` (ribbon: phản hồi mượt)
 * Đồng thời điều chỉnh năng lượng (energy) của quả cầu dựa trên tốc độ sinh token thực tế của GPU (tok/s).
 */
function adaptOrbToLiveTokenStream(accumulatedText, tokensPerSec) {
  const energy = Math.max(0.95, Math.min(1.5, 0.9 + tokensPerSec / 45));
  const fenceCount = (accumulatedText.match(/```/g) || []).length;

  // Đang ở bên trong một khối code block chưa đóng
  if (fenceCount % 2 === 1) {
    setOrbVisualState("working", "Đang viết mã nguồn…", energy * 1.08);
    return;
  }

  // Kiểm tra dòng hiện tại hoặc các dòng gần nhất có phải danh sách / bước cấu trúc không
  const recentWindow = accumulatedText.slice(-180);
  const bulletMatches = recentWindow.match(/(?:^|\n)\s*(?:[-*•]|\d+\.)\s+/g);
  if (bulletMatches && bulletMatches.length >= 2) {
    setOrbVisualState("weaving", "Đang hệ thống hóa nội dung…", energy);
    return;
  }

  setOrbVisualState("composing", STATE_DESCRIPTIONS.composing, energy);
}

/**
 * Hiệu ứng gõ chữ mượt (dùng khi câu trả lời đến từ Fast-path hoặc fallback JSON).
 */
async function streamBotMessage(fullText, existingBubble = null) {
  const target = existingBubble || createBotBubble();
  if (!target) return;
  const { bubble } = target;

  const tokens = fullText.split(/(\s+)/);
  let current = "";
  const step = Math.max(1, Math.ceil(tokens.length / 26));

  for (let i = 0; i < tokens.length; i += step) {
    current += tokens.slice(i, i + step).join("");
    bubble.innerHTML = formatSafeMessageHtml(current);
    scrollToBottom();
    await delay(16);
  }
  bubble.innerHTML = formatSafeMessageHtml(fullText);
  scrollToBottom();
}

/**
 * Gửi tin nhắn và đọc luồng sự kiện NDJSON thời gian thực từ `/api/chat/stream`.
 * Quả cầu tự động chuyển trạng thái theo đúng bước thực thi thật của Backend & GPU:
 * - Backend nạp lịch sử -> `connecting`
 * - Backend gọi Open-Meteo API -> `searching`
 * - Backend ghép dữ liệu công cụ vào ngữ cảnh LLM -> `weaving`
 * - Ollama nạp prompt lên GPU và suy luận -> `solving`
 * - Từng token trả về -> `composing` / `working` (khi gõ code) / `weaving` (khi lập danh sách)
 */
async function sendMessage(presetText) {
  if (isSending) return;

  const rawText = typeof presetText === "string" ? presetText : (msgInput ? msgInput.value : "");
  const message = rawText.trim();
  if (!message) {
    if (msgInput) msgInput.focus();
    return;
  }

  clearTimeout(typingIdleTimer);
  clearTimeout(typingShapeTimer);
  clearTimeout(previewResetTimer);

  appendUserMessage(message);
  if (typeof presetText !== "string" && msgInput) {
    msgInput.value = "";
  } else if (msgInput && msgInput.value.trim() === message) {
    msgInput.value = "";
  }
  prevInputLength = 0;
  consecutiveDeletes = 0;

  isSending = true;
  if (sendBtn) sendBtn.disabled = true;
  if (msgInput) msgInput.disabled = true;

  let lastStateSwitchMs = performance.now();
  async function transitionOrbWithMinVisibility(nextState, label, minHoldMs = 260) {
    const now = performance.now();
    const elapsed = now - lastStateSwitchMs;
    if (elapsed < minHoldMs && centerOrb && centerOrb.getState() !== nextState) {
      await delay(minHoldMs - elapsed);
    }
    setOrbVisualState(nextState, label, 1.05);
    lastStateSwitchMs = performance.now();
  }

  let botBubbleObj = null;
  let accumulatedText = "";
  let streamedTokenCount = 0;
  let firstTokenMs = 0;

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    if (!response.ok || !response.body) {
      throw new Error("Stream unavailable");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let pendingStateText = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIdx;
      while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newlineIdx).trim();
        buffer = buffer.slice(newlineIdx + 1);
        if (!line) continue;

        let evt;
        try {
          evt = JSON.parse(line);
        } catch (_e) {
          continue;
        }

        if (evt.type === "state" && evt.state) {
          // Nếu belum có token nào được in ra, giữ nhịp chuyển trạng thái mượt để mắt người nhìn rõ pha xử lý
          const holdMs = streamedTokenCount === 0 ? 320 : 0;
          await transitionOrbWithMinVisibility(evt.state, evt.label, holdMs);
        } else if (evt.type === "token" && typeof evt.delta === "string") {
          if (!firstTokenMs) {
            firstTokenMs = performance.now();
            // Nếu pha suy nghĩ trước đó vừa mới bật quá nhanh (fast-path), chờ nhẹ để người dùng thấy rõ pha xử lý
            const sinceLastState = firstTokenMs - lastStateSwitchMs;
            if (sinceLastState < 420 && streamedTokenCount === 0) {
              await delay(420 - sinceLastState);
            }
          }

          streamedTokenCount += 1;
          accumulatedText += evt.delta;

          // Nếu chỉ có 1 cục văn bản lớn trả về ngay (Fast-path), chạy hiệu ứng gõ chữ mượt
          if (streamedTokenCount === 1 && evt.delta.length > 28) {
            pendingStateText = evt.delta;
          } else {
            if (pendingStateText !== null) {
              botBubbleObj = botBubbleObj || createBotBubble();
              pendingStateText = null;
            }
            botBubbleObj = botBubbleObj || createBotBubble();
            if (botBubbleObj) {
              botBubbleObj.bubble.innerHTML = formatSafeMessageHtml(accumulatedText);
              scrollToBottom();
            }
            const elapsedSec = Math.max(0.1, (performance.now() - firstTokenMs) / 1000);
            const tokPerSec = streamedTokenCount / elapsedSec;
            adaptOrbToLiveTokenStream(accumulatedText, tokPerSec);
          }
        }
      }
    }

    // Nếu là câu trả lời Fast-path (1 chunk duy nhất), hiển thị bằng hiệu ứng typewriter mượt
    if (pendingStateText !== null) {
      setOrbVisualState("composing", STATE_DESCRIPTIONS.composing, 1.1);
      await streamBotMessage(pendingStateText);
    } else if (!accumulatedText.trim()) {
      await streamBotMessage(FALLBACK_ERROR);
    }
  } catch (_err) {
    // Fallback an toàn về /api/chat nếu trình duyệt không hỗ trợ ReadableStream
    try {
      setOrbVisualState("connecting", STATE_DESCRIPTIONS.connecting, 1);
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = res.ok ? await res.json() : null;
      const reply = data && typeof data.reply === "string" && data.reply.trim() ? data.reply : FALLBACK_ERROR;
      setOrbVisualState("composing", STATE_DESCRIPTIONS.composing, 1.05);
      await streamBotMessage(reply);
    } catch (_fallbackErr) {
      await streamBotMessage(FALLBACK_ERROR);
    }
  }

  // Hoàn tất phản hồi -> dịu mượt về trạng thái Idle (breathing)
  await delay(220);
  isSending = false;
  if (sendBtn) sendBtn.disabled = false;
  if (msgInput) {
    msgInput.disabled = false;
    msgInput.focus();
  }
  syncIdleState();
}

/**
 * Phản ứng thông minh theo động lực gõ phím thời gian thực của người dùng:
 * - Gõ liên tục: `listening` (wave) với tốc độ sóng tỉ lệ thuận với tốc độ gõ (keys/sec).
 * - Xóa sửa nhiều ký tự liên tiếp (Backspace/Delete): chuyển sang `shaping` (morph: đang cấu trúc lại ý).
 * - Đang gõ dở câu mà dừng lại ngẫm nghĩ (> 900ms): chuyển nhẹ sang `shaping` (morph: chờ bạn định hình ý),
 *   sau đó nếu nghỉ hẳn (> 2800ms) mới dịu về `breathing`.
 */
if (msgInput) {
  msgInput.addEventListener("input", () => {
    if (isSending) return;
    clearTimeout(typingIdleTimer);
    clearTimeout(typingShapeTimer);
    clearTimeout(previewResetTimer);

    const now = performance.now();
    const val = msgInput.value;
    const trimmedLen = val.trim().length;

    if (trimmedLen === 0) {
      prevInputLength = 0;
      consecutiveDeletes = 0;
      syncIdleState();
      return;
    }

    // Tính tốc độ gõ phím trong cửa sổ 1.2 giây gần nhất
    keystrokeTimes.push(now);
    while (keystrokeTimes.length > 0 && now - keystrokeTimes[0] > 1200) {
      keystrokeTimes.shift();
    }
    const keysPerSec = keystrokeTimes.length / 1.2;
    const waveEnergy = Math.max(0.88, Math.min(1.55, 0.85 + keysPerSec * 0.085));

    // Phát hiện hành vi xóa/viết lại câu hỏi
    if (val.length < prevInputLength) {
      consecutiveDeletes += prevInputLength - val.length;
    } else {
      consecutiveDeletes = Math.max(0, consecutiveDeletes - 1);
    }
    prevInputLength = val.length;

    if (consecutiveDeletes >= 5) {
      setOrbVisualState("shaping", "Đang chỉnh sửa câu hỏi…", 1.15);
    } else {
      setOrbVisualState("listening", STATE_DESCRIPTIONS.listening, waveEnergy);
    }

    // Nếu người dùng đang gõ dở câu dài và tạm dừng suy nghĩ -> chuyển sang `shaping`
    if (trimmedLen >= 6) {
      typingShapeTimer = setTimeout(() => {
        if (!isSending && msgInput.value.trim().length >= 6) {
          setOrbVisualState("shaping", STATE_DESCRIPTIONS.shaping, 0.92);
        }
      }, 950);
    }

    typingIdleTimer = setTimeout(() => {
      if (!isSending) {
        syncIdleState();
      }
    }, 2800);
  });

  msgInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendMessage();
    }
  });
}

// Bấm trực tiếp vào quả cầu để xem thử hình thái tiếp theo (tự quay về Idle sau 4 giây)
if (orbModeBtn) {
  orbModeBtn.addEventListener("click", () => {
    if (isSending) return;
    clearTimeout(previewResetTimer);
    const cur = centerOrb ? centerOrb.getState() : "breathing";
    const idx = (ALL_STATES.indexOf(cur) + 1) % ALL_STATES.length;
    const next = ALL_STATES[idx];
    setOrbVisualState(next, `${STATE_DESCRIPTIONS[next]}`, 1);
    previewResetTimer = setTimeout(() => {
      if (!isSending) syncIdleState();
    }, 4000);
  });
}

// Đổi giao diện Sáng / Tối
if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const nextTheme = isDarkMode() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", nextTheme);
  });
}

// Bấm vào các thẻ `code` để gửi lệnh ngay
if (chatEl) {
  chatEl.addEventListener("click", (e) => {
    const codeEl = e.target.closest(".inline-code");
    if (codeEl && codeEl.textContent) {
      sendMessage(codeEl.textContent.trim());
    }
  });
}

if (sendBtn) {
  sendBtn.addEventListener("click", () => {
    sendMessage();
  });
}

window.sendMessage = sendMessage;
