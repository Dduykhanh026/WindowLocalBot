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

const appLayout = document.getElementById("appLayout");
const modeChatBtn = document.getElementById("modeChatBtn");
const modeVoiceBtn = document.getElementById("modeVoiceBtn");
const quickVoiceBtn = document.getElementById("quickVoiceBtn");
const voiceSelectEl = document.getElementById("voiceSelect");
const voiceSpeedSelectEl = document.getElementById("voiceSpeedSelect");
const voicePreviewBtn = document.getElementById("voicePreviewBtn");
const micSensitivitySelectEl = document.getElementById("micSensitivitySelect");
const voiceTranscriptEl = document.getElementById("voiceTranscript");
const voiceWaveCanvas = document.getElementById("voiceWaveCanvas");
const voiceWaveWrap = document.getElementById("voiceWaveWrap");

const aiConfigWrap = document.getElementById("aiConfigWrap");
const aiStyleIconWrap = document.getElementById("aiStyleIconWrap");
const aiStyleSelectEl = document.getElementById("aiStyleSelect");
const aiStyleBtnLabelEl = document.getElementById("aiStyleBtnLabel");
const aiConfigBtn = document.getElementById("aiConfigBtn");
const aiConfigPanel = document.getElementById("aiConfigPanel");
const aiRulesBadgeEl = document.getElementById("aiRulesBadge");
const resetAiPrefsBtn = document.getElementById("resetAiPrefsBtn");
const styleSegmentGroupEl = document.getElementById("styleSegmentGroup");
const personaSegmentGroupEl = document.getElementById("personaSegmentGroup");
const aiPersonaSelectEl = document.getElementById("aiPersonaSelect");
const prefNoGreetingEl = document.getElementById("prefNoGreeting");
const prefNoApologyEl = document.getElementById("prefNoApology");
const prefNoOutroEl = document.getElementById("prefNoOutro");
const prefCodeFirstEl = document.getElementById("prefCodeFirst");
const prefPlainTextEl = document.getElementById("prefPlainText");

// Các phần tử Hồ sơ Chủ nhân & Bộ nhớ Trợ lý Wisp
const userProfileWrap = document.getElementById("userProfileWrap");
const userProfileBtn = document.getElementById("userProfileBtn");
const userProfilePanel = document.getElementById("userProfilePanel");
const profileTopNameEl = document.getElementById("profileTopName");
const profileTopMetaEl = document.getElementById("profileTopMeta");
const resetProfileBtn = document.getElementById("resetProfileBtn");
const saveProfileBtn = document.getElementById("saveProfileBtn");
const profileSaveStatusEl = document.getElementById("profileSaveStatus");
const bmiSummaryCardEl = document.getElementById("bmiSummaryCard");
const bmiValueTextEl = document.getElementById("bmiValueText");
const bmiStatusBadgeEl = document.getElementById("bmiStatusBadge");
const waterRecommendTextEl = document.getElementById("waterRecommendText");
const profNameInput = document.getElementById("profNameInput");
const profGenderInput = document.getElementById("profGenderInput");
const profAgeInput = document.getElementById("profAgeInput");
const profHeightInput = document.getElementById("profHeightInput");
const profWeightInput = document.getElementById("profWeightInput");
const profCityInput = document.getElementById("profCityInput");
const profJobInput = document.getElementById("profJobInput");
const profNotesInput = document.getElementById("profNotesInput");
const profAutoLearnToggle = document.getElementById("profAutoLearnToggle");
const assistantMemoryToastEl = document.getElementById("assistantMemoryToast");
const assistantMemoryToastTextEl = document.getElementById("assistantMemoryToastText");

// Các phần tử Trung tâm Bộ nhớ Dài hạn 3 Tab (Milestone M4)
const memoryCountBadgeEl = document.getElementById("memoryCountBadge");
const memoryTabsBarEl = document.getElementById("memoryTabsBar");
const memTabProfileBtn = document.getElementById("memTabProfileBtn");
const memTabMemoriesBtn = document.getElementById("memTabMemoriesBtn");
const memTabTasksBtn = document.getElementById("memTabTasksBtn");
const memTabMemoriesCountEl = document.getElementById("memTabMemoriesCount");
const memTabTasksCountEl = document.getElementById("memTabTasksCount");
const memPaneProfileEl = document.getElementById("memPaneProfile");
const memPaneMemoriesEl = document.getElementById("memPaneMemories");
const memPaneTasksEl = document.getElementById("memPaneTasks");
const memoryCategorySelectEl = document.getElementById("memoryCategorySelect");
const memoryAddInputEl = document.getElementById("memoryAddInput");
const memoryAddBtnEl = document.getElementById("memoryAddBtn");
const memorySearchInputEl = document.getElementById("memorySearchInput");
const memoryListContainerEl = document.getElementById("memoryListContainer");
const memoryPaneStatusEl = document.getElementById("memoryPaneStatus");
const clearAllMemoriesBtnEl = document.getElementById("clearAllMemoriesBtn");
const taskAddInputEl = document.getElementById("taskAddInput");
const taskAddBtnEl = document.getElementById("taskAddBtn");
const taskListContainerEl = document.getElementById("taskListContainer");
const taskPaneStatusEl = document.getElementById("taskPaneStatus");
const clearDoneTasksBtnEl = document.getElementById("clearDoneTasksBtn");
const clearAllTasksBtnEl = document.getElementById("clearAllTasksBtn");

const voiceChatEl = document.getElementById("voiceChat");
const voiceMicToggleBtn = document.getElementById("voiceMicToggleBtn");
const voiceMicDot = document.getElementById("voiceMicDot");
const voiceMicStatusLabel = document.getElementById("voiceMicStatusLabel");
const clearVoiceChatBtn = document.getElementById("clearVoiceChatBtn");
const clearChatBtn = document.getElementById("clearChatBtn");

const chatEl = document.getElementById("chat");
const msgInput = document.getElementById("msg");
const sendBtn = document.getElementById("sendBtn");
const typingText = document.getElementById("typingText");
const centerOrbCanvas = document.getElementById("centerOrb");
const orbModeBtn = document.getElementById("orbModeBtn");
const themeToggleBtn = document.getElementById("themeToggleBtn");

let currentAppMode = "chat"; // "chat" | "voice"
let isSending = false;
let typingIdleTimer = 0;
let typingShapeTimer = 0;
let previewResetTimer = 0;
let memoryToastTimer = 0;

// Lưu & đồng bộ giọng đọc Tiếng Việt người dùng chọn
const VOICE_STORAGE_KEY = "wisp_voice_id";
let selectedVoiceId = localStorage.getItem(VOICE_STORAGE_KEY) || "hoaimy";
if (voiceSelectEl) {
  voiceSelectEl.value = selectedVoiceId;
}

// Lưu & đồng bộ tốc độ đọc giọng nói (TTS Playback Rate)
const VOICE_SPEED_KEY = "wisp_voice_speed";
const VALID_VOICE_SPEEDS = ["0.85", "1.0", "1.15", "1.25", "1.5", "1.75"];
let selectedVoiceSpeed = localStorage.getItem(VOICE_SPEED_KEY) || "1.15";
if (!VALID_VOICE_SPEEDS.includes(selectedVoiceSpeed)) {
  selectedVoiceSpeed = "1.15";
}
if (voiceSpeedSelectEl) {
  voiceSpeedSelectEl.value = selectedVoiceSpeed;
}

function getPlaybackRateNumber() {
  const n = parseFloat(selectedVoiceSpeed);
  return Number.isFinite(n) ? Math.max(0.5, Math.min(2.5, n)) : 1.15;
}

// Lưu & đồng bộ Cấu hình Phong cách trả lời AI (Response Style & No-Fluff Preferences)
const AI_PREFS_KEY = "wisp_ai_prefs_v2";
const DEFAULT_AI_PREFS = {
  style: "balanced",       // "concise" | "balanced" | "detailed"
  persona: "minh_nguyen",  // "minh_nguyen" | "em_anh" | "direct"
  no_greeting: true,
  no_apology: true,
  no_outro: true,
  code_first: true,
  plain_text: false,
};

const STYLE_LABELS = {
  concise: "Ngắn gọn",
  balanced: "Tự nhiên",
  detailed: "Chi tiết",
};

function loadAiPrefs() {
  try {
    const raw = localStorage.getItem(AI_PREFS_KEY);
    if (!raw) return { ...DEFAULT_AI_PREFS };
    const parsed = JSON.parse(raw);
    return {
      style: ["concise", "balanced", "detailed"].includes(parsed.style) ? parsed.style : DEFAULT_AI_PREFS.style,
      persona: ["minh_nguyen", "em_anh", "direct"].includes(parsed.persona) ? parsed.persona : DEFAULT_AI_PREFS.persona,
      no_greeting: typeof parsed.no_greeting === "boolean" ? parsed.no_greeting : DEFAULT_AI_PREFS.no_greeting,
      no_apology: typeof parsed.no_apology === "boolean" ? parsed.no_apology : DEFAULT_AI_PREFS.no_apology,
      no_outro: typeof parsed.no_outro === "boolean" ? parsed.no_outro : DEFAULT_AI_PREFS.no_outro,
      code_first: typeof parsed.code_first === "boolean" ? parsed.code_first : DEFAULT_AI_PREFS.code_first,
      plain_text: typeof parsed.plain_text === "boolean" ? parsed.plain_text : DEFAULT_AI_PREFS.plain_text,
    };
  } catch (_e) {
    return { ...DEFAULT_AI_PREFS };
  }
}

let currentAiPrefs = loadAiPrefs();

function syncAiPrefsUI() {
  if (aiStyleSelectEl) aiStyleSelectEl.value = currentAiPrefs.style;
  if (aiStyleIconWrap) aiStyleIconWrap.setAttribute("data-style-icon", currentAiPrefs.style);
  if (aiStyleBtnLabelEl) {
    aiStyleBtnLabelEl.textContent = STYLE_LABELS[currentAiPrefs.style] || "Tự nhiên";
  }
  if (aiPersonaSelectEl) aiPersonaSelectEl.value = currentAiPrefs.persona;

  if (styleSegmentGroupEl) {
    styleSegmentGroupEl.querySelectorAll(".ai-segment-btn[data-style-val]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-style-val") === currentAiPrefs.style);
    });
  }
  if (personaSegmentGroupEl) {
    personaSegmentGroupEl.querySelectorAll(".ai-segment-btn[data-persona-val]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-persona-val") === currentAiPrefs.persona);
    });
  }

  if (prefNoGreetingEl) prefNoGreetingEl.checked = Boolean(currentAiPrefs.no_greeting);
  if (prefNoApologyEl) prefNoApologyEl.checked = Boolean(currentAiPrefs.no_apology);
  if (prefNoOutroEl) prefNoOutroEl.checked = Boolean(currentAiPrefs.no_outro);
  if (prefCodeFirstEl) prefCodeFirstEl.checked = Boolean(currentAiPrefs.code_first);
  if (prefPlainTextEl) prefPlainTextEl.checked = Boolean(currentAiPrefs.plain_text);

  if (aiRulesBadgeEl) {
    const activeCount = [
      currentAiPrefs.no_greeting,
      currentAiPrefs.no_apology,
      currentAiPrefs.no_outro,
      currentAiPrefs.code_first,
      currentAiPrefs.plain_text,
    ].filter(Boolean).length;
    aiRulesBadgeEl.textContent = String(activeCount);
  }
}

function saveAiPrefs() {
  try {
    localStorage.setItem(AI_PREFS_KEY, JSON.stringify(currentAiPrefs));
  } catch (_e) {}
  syncAiPrefsUI();
}

function getAiPrefs() {
  return { ...currentAiPrefs };
}

syncAiPrefsUI();

/* ==========================================================================
   QUẢN LÝ HỒ SƠ CÁ NHÂN & BỘ NHỚ TRỢ LÝ WISP (/api/profile)
   ========================================================================== */

const USER_PROFILE_KEY = "wisp_user_profile_v1";
const DEFAULT_USER_PROFILE = {
  name: "Bạn",
  gender: "Nam",
  age: "",
  height_cm: "",
  weight_kg: "",
  city: "TP. Hồ Chí Minh",
  job: "iOS/SwiftUI & Flutter Developer",
  notes: "",
  auto_learn: true,
};

let currentUserProfile = { ...DEFAULT_USER_PROFILE };

function shortenCityLabel(cityStr) {
  const c = String(cityStr || "").trim();
  if (!c) return "TP.HCM";
  if (/hồ chí minh|sài gòn|tphcm|hcm/i.test(c)) return "TP.HCM";
  return c;
}

function computeClientBmiMetrics(heightCm, weightKg) {
  const h = parseFloat(String(heightCm || "").replace(",", "."));
  const w = parseFloat(String(weightKg || "").replace(",", "."));
  if (!Number.isFinite(h) || !Number.isFinite(w) || h < 100 || h > 230 || w < 25 || w > 220) {
    return null;
  }
  const bmi = Math.round((w / Math.pow(h / 100, 2)) * 10) / 10;
  let label = "Cân đối";
  if (bmi < 18.5) label = "Thiếu cân";
  else if (bmi < 23.0) label = "Cân đối";
  else if (bmi < 25.0) label = "Hơi thừa cân";
  else label = "Thừa cân";
  const waterLiters = Math.round(w * 0.035 * 10) / 10;
  return { bmi, label, waterLiters };
}

function syncUserProfileUI(profileObj) {
  if (profileObj && typeof profileObj === "object") {
    currentUserProfile = {
      ...DEFAULT_USER_PROFILE,
      ...profileObj,
    };
  }
  const p = currentUserProfile;
  const displayName = String(p.name || "Bạn").trim() || "Bạn";
  if (profileTopNameEl) {
    profileTopNameEl.textContent = displayName;
  }
  if (profileTopMetaEl) {
    const metaParts = [];
    if (p.age) metaParts.push(`${p.age}t`);
    if (p.weight_kg) metaParts.push(`${p.weight_kg}kg`);
    metaParts.push(shortenCityLabel(p.city));
    profileTopMetaEl.textContent = metaParts.join(" · ");
  }

  if (profNameInput && document.activeElement !== profNameInput) profNameInput.value = p.name || "";
  if (profGenderInput && document.activeElement !== profGenderInput) profGenderInput.value = p.gender || "Nam";
  if (profAgeInput && document.activeElement !== profAgeInput) profAgeInput.value = p.age || "";
  if (profHeightInput && document.activeElement !== profHeightInput) profHeightInput.value = p.height_cm || "";
  if (profWeightInput && document.activeElement !== profWeightInput) profWeightInput.value = p.weight_kg || "";
  if (profCityInput && document.activeElement !== profCityInput) profCityInput.value = p.city || "";
  if (profJobInput && document.activeElement !== profJobInput) profJobInput.value = p.job || "";
  if (profNotesInput && document.activeElement !== profNotesInput) profNotesInput.value = p.notes || "";
  if (profAutoLearnToggle) profAutoLearnToggle.checked = p.auto_learn !== false;

  updateLiveBmiCard(p.height_cm, p.weight_kg);
}

function updateLiveBmiCard(heightVal, weightVal) {
  if (!bmiSummaryCardEl) return;
  const metrics = computeClientBmiMetrics(heightVal, weightVal);
  if (!metrics) {
    bmiSummaryCardEl.setAttribute("hidden", "");
    return;
  }
  bmiSummaryCardEl.removeAttribute("hidden");
  if (bmiValueTextEl) bmiValueTextEl.textContent = String(metrics.bmi);
  if (bmiStatusBadgeEl) bmiStatusBadgeEl.textContent = metrics.label;
  if (waterRecommendTextEl) {
    waterRecommendTextEl.textContent = `💧 Nên uống ~${metrics.waterLiters}L nước/ngày`;
  }
}

function showAssistantMemoryToast(messageText) {
  if (!assistantMemoryToastEl || !assistantMemoryToastTextEl) return;
  clearTimeout(memoryToastTimer);
  assistantMemoryToastTextEl.textContent = messageText;
  assistantMemoryToastEl.removeAttribute("hidden");
  memoryToastTimer = setTimeout(() => {
    assistantMemoryToastEl.setAttribute("hidden", "");
  }, 4600);
}

async function fetchUserProfileFromServer() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.profile) {
      localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(data.profile));
      syncUserProfileUI(data.profile);
    }
  } catch (_e) {
    try {
      const cached = localStorage.getItem(USER_PROFILE_KEY);
      if (cached) syncUserProfileUI(JSON.parse(cached));
    } catch (_err) {}
  }
}

async function saveUserProfileToServer(customPatch = null) {
  const payload = customPatch || {
    name: profNameInput ? profNameInput.value.trim() : currentUserProfile.name,
    gender: profGenderInput ? profGenderInput.value.trim() : (currentUserProfile.gender || "Nam"),
    age: profAgeInput ? profAgeInput.value.trim() : currentUserProfile.age,
    height_cm: profHeightInput ? profHeightInput.value.trim() : currentUserProfile.height_cm,
    weight_kg: profWeightInput ? profWeightInput.value.trim() : currentUserProfile.weight_kg,
    city: profCityInput ? profCityInput.value.trim() : currentUserProfile.city,
    job: profJobInput ? profJobInput.value.trim() : currentUserProfile.job,
    notes: profNotesInput ? profNotesInput.value.trim() : currentUserProfile.notes,
    auto_learn: profAutoLearnToggle ? Boolean(profAutoLearnToggle.checked) : true,
  };
  try {
    if (profileSaveStatusEl) profileSaveStatusEl.textContent = "Đang lưu…";
    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.profile) {
        localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(data.profile));
        syncUserProfileUI(data.profile);
      }
      if (profileSaveStatusEl) profileSaveStatusEl.textContent = "✓ Đã lưu vào bộ nhớ Wisp";
    }
  } catch (_e) {
    if (profileSaveStatusEl) profileSaveStatusEl.textContent = "Đã lưu tạm trên trình duyệt";
  }
}

fetchUserProfileFromServer();

if (profHeightInput || profWeightInput) {
  const onBodyMetricInput = () => {
    updateLiveBmiCard(
      profHeightInput ? profHeightInput.value : "",
      profWeightInput ? profWeightInput.value : ""
    );
  };
  if (profHeightInput) profHeightInput.addEventListener("input", onBodyMetricInput);
  if (profWeightInput) profWeightInput.addEventListener("input", onBodyMetricInput);
}

function revertUserProfileFormInputs() {
  const p = currentUserProfile;
  if (profNameInput) profNameInput.value = p.name || "";
  if (profGenderInput) profGenderInput.value = p.gender || "Nam";
  if (profAgeInput) profAgeInput.value = p.age || "";
  if (profHeightInput) profHeightInput.value = p.height_cm || "";
  if (profWeightInput) profWeightInput.value = p.weight_kg || "";
  if (profCityInput) profCityInput.value = p.city || "";
  if (profJobInput) profJobInput.value = p.job || "";
  if (profNotesInput) profNotesInput.value = p.notes || "";
  if (profAutoLearnToggle) profAutoLearnToggle.checked = p.auto_learn !== false;
  updateLiveBmiCard(p.height_cm, p.weight_kg);
  if (profileSaveStatusEl) {
    profileSaveStatusEl.textContent = "Đã đồng bộ với bộ nhớ Wisp";
  }
}

function closeUserProfilePanel(shouldRevert = true) {
  if (!userProfilePanel) return;
  userProfilePanel.classList.remove("active");
  userProfilePanel.setAttribute("hidden", "");
  if (userProfileBtn) {
    userProfileBtn.setAttribute("aria-expanded", "false");
  }
  if (shouldRevert) {
    revertUserProfileFormInputs();
  }
}

if (saveProfileBtn) {
  saveProfileBtn.addEventListener("click", async () => {
    await saveUserProfileToServer();
    showAssistantMemoryToast(`✨ Đã cập nhật Hồ sơ của ${currentUserProfile.name || "bạn"}`);
    closeUserProfilePanel(false);
  });
}

if (resetProfileBtn) {
  resetProfileBtn.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/profile", { method: "DELETE" });
      if (res.ok) {
        const data = await res.json();
        if (data && data.profile) {
          localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(data.profile));
          syncUserProfileUI(data.profile);
          revertUserProfileFormInputs();
        }
      }
    } catch (_e) {
      syncUserProfileUI(DEFAULT_USER_PROFILE);
      revertUserProfileFormInputs();
    }
  });
}

/* ==========================================================================
   TRUNG TÂM BỘ NHỚ WISP 3 TAB (HỒ SƠ · KÝ ỨC · VIỆC CẦN NHỚ) — /api/memories
   ========================================================================== */

const MEMORY_CATEGORY_LABELS = {
  project: "Dự án",
  preference: "Sở thích",
  habit: "Thói quen",
  health: "Sức khỏe",
  skill: "Kỹ năng",
  relationship: "Quan hệ",
  fact: "Ghi chú",
};

let currentMemoryTab = "profile";
let cachedMemoriesList = [];
let cachedTasksList = [];
let cachedMemoryCounts = {
  memories_total: 0,
  pinned_total: 0,
  pending_tasks: 0,
  done_tasks: 0,
  tasks_total: 0,
};
let bgMemorySyncTimer = 0;

function syncMemoryCountsUI(countsObj) {
  if (countsObj && typeof countsObj === "object") {
    cachedMemoryCounts = {
      ...cachedMemoryCounts,
      ...countsObj,
    };
  }
  const memTotal = Number(cachedMemoryCounts.memories_total) || 0;
  const pinnedTotal = Number(cachedMemoryCounts.pinned_total) || 0;
  const pendingTasks = Number(cachedMemoryCounts.pending_tasks) || 0;
  const doneTasks = Number(cachedMemoryCounts.done_tasks) || 0;
  const badgeTotal = memTotal + pendingTasks;

  if (memoryCountBadgeEl) {
    memoryCountBadgeEl.textContent = String(badgeTotal);
    if (badgeTotal > 0) {
      memoryCountBadgeEl.removeAttribute("hidden");
    } else {
      memoryCountBadgeEl.setAttribute("hidden", "");
    }
  }
  if (memTabMemoriesCountEl) {
    memTabMemoriesCountEl.textContent = String(memTotal);
  }
  if (memTabTasksCountEl) {
    memTabTasksCountEl.textContent = String(pendingTasks);
  }
  if (memoryPaneStatusEl) {
    memoryPaneStatusEl.textContent =
      pinnedTotal > 0
        ? `${memTotal} ký ức dài hạn (${pinnedTotal} đã ghim)`
        : `${memTotal} ký ức dài hạn`;
  }
  if (taskPaneStatusEl) {
    taskPaneStatusEl.textContent =
      doneTasks > 0
        ? `${pendingTasks} việc đang chờ · ${doneTasks} đã xong`
        : `${pendingTasks} việc đang chờ`;
  }
}

function renderMemoryList() {
  if (!memoryListContainerEl) return;
  const rawQuery = memorySearchInputEl ? memorySearchInputEl.value.trim() : "";
  const normQuery = stripVietnameseAccentsForMatch(rawQuery).toLowerCase();

  const filtered = cachedMemoriesList.filter((item) => {
    if (!item || typeof item.content !== "string") return false;
    if (!normQuery) return true;
    const contentLower = item.content.toLowerCase();
    const contentNorm = stripVietnameseAccentsForMatch(item.content).toLowerCase();
    const catLabel = MEMORY_CATEGORY_LABELS[item.category] || "Ghi chú";
    const catNorm = stripVietnameseAccentsForMatch(catLabel).toLowerCase();
    return (
      contentLower.includes(rawQuery.toLowerCase()) ||
      contentNorm.includes(normQuery) ||
      catNorm.includes(normQuery)
    );
  });

  if (filtered.length === 0) {
    const emptyMsg = rawQuery
      ? "Không tìm thấy ký ức nào khớp từ khóa."
      : "Chưa có ký ức dài hạn nào. Hãy trò chuyện tự nhiên hoặc thêm ký ức ở trên.";
    memoryListContainerEl.innerHTML = `<div class="memory-empty-state">${escapeHtmlText(emptyMsg)}</div>`;
    return;
  }

  memoryListContainerEl.innerHTML = filtered
    .map((item) => {
      const id = Number(item.id);
      const cat = String(item.category || "fact");
      const catLabel = MEMORY_CATEGORY_LABELS[cat] || "Ghi chú";
      const isPinned = Boolean(item.pinned);
      return (
        `<div class="memory-item-card${isPinned ? " pinned" : ""}" data-memory-id="${id}" role="listitem">` +
        `<div class="memory-item-top">` +
        `<span class="memory-cat-badge" data-cat="${escapeHtmlText(cat)}">${escapeHtmlText(catLabel)}</span>` +
        `<div class="memory-item-actions">` +
        `<button type="button" class="pin-btn${isPinned ? " active" : ""}" data-action="pin-memory" data-id="${id}" data-pinned="${isPinned ? "1" : "0"}" title="${isPinned ? "Bỏ ghim ưu tiên" : "Ghim ký ức này luôn ưu tiên"}">` +
        `${isPinned ? "📌 Đã ghim" : "📌 Ghim"}` +
        `</button>` +
        `<button type="button" class="mem-delete-btn" data-action="delete-memory" data-id="${id}" title="Xóa ký ức này">Xóa</button>` +
        `</div>` +
        `</div>` +
        `<div class="memory-item-content">${escapeHtmlText(item.content)}</div>` +
        `</div>`
      );
    })
    .join("");
}

function renderTaskList() {
  if (!taskListContainerEl) return;
  if (!cachedTasksList || cachedTasksList.length === 0) {
    taskListContainerEl.innerHTML =
      `<div class="memory-empty-state">Chưa có việc cần nhớ nào. Bạn có thể nhắn “Nhắc mình chiều mai họp team”.</div>`;
    return;
  }

  taskListContainerEl.innerHTML = cachedTasksList
    .map((task) => {
      const id = Number(task.id);
      const isDone = task.status === "done";
      const dueText = String(task.due_text || "").trim();
      return (
        `<div class="task-item-row${isDone ? " done" : ""}" data-task-id="${id}" role="listitem">` +
        `<button type="button" class="task-check-btn${isDone ? " checked" : ""}" data-action="toggle-task" data-id="${id}" data-status="${isDone ? "done" : "pending"}" aria-label="${isDone ? "Mở lại việc" : "Đánh dấu hoàn thành"}" title="${isDone ? "Bấm để mở lại" : "Đánh dấu hoàn thành"}">` +
        `${isDone ? "✓" : ""}` +
        `</button>` +
        `<div class="task-body">` +
        `<span class="task-title-text">${escapeHtmlText(task.title || "")}</span>` +
        (dueText ? `<span class="task-due-badge">⏰ ${escapeHtmlText(dueText)}</span>` : "") +
        `</div>` +
        `<button type="button" class="mem-delete-btn" data-action="delete-task" data-id="${id}" title="Xóa việc này">Xóa</button>` +
        `</div>`
      );
    })
    .join("");
}

async function fetchMemoriesState() {
  try {
    const res = await fetch("/api/memories");
    if (!res.ok) return;
    const data = await res.json();
    if (!data || data.ok === false) return;
    if (Array.isArray(data.memories)) {
      cachedMemoriesList = data.memories;
    }
    if (Array.isArray(data.tasks)) {
      cachedTasksList = data.tasks;
    }
    if (data.counts) {
      syncMemoryCountsUI(data.counts);
    }
    renderMemoryList();
    renderTaskList();
  } catch (_e) {}
}

function scheduleBackgroundMemorySync(delayMs = 1150) {
  clearTimeout(bgMemorySyncTimer);
  bgMemorySyncTimer = setTimeout(() => {
    fetchMemoriesState();
  }, delayMs);
}

function switchMemoryTab(tabName) {
  const validTab = ["profile", "memories", "tasks"].includes(tabName) ? tabName : "profile";
  currentMemoryTab = validTab;

  [
    [memTabProfileBtn, memPaneProfileEl, "profile"],
    [memTabMemoriesBtn, memPaneMemoriesEl, "memories"],
    [memTabTasksBtn, memPaneTasksEl, "tasks"],
  ].forEach(([btn, pane, key]) => {
    const isActive = key === validTab;
    if (btn) {
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    }
    if (pane) {
      if (isActive) {
        pane.removeAttribute("hidden");
      } else {
        pane.setAttribute("hidden", "");
      }
    }
  });

  if (resetProfileBtn) {
    resetProfileBtn.style.display = validTab === "profile" ? "" : "none";
  }

  if (validTab === "memories" || validTab === "tasks") {
    fetchMemoriesState();
  }
}

async function handleAddMemoryFromPanel() {
  if (!memoryAddInputEl) return;
  const content = memoryAddInputEl.value.trim();
  if (!content) {
    memoryAddInputEl.focus();
    return;
  }
  const category = memoryCategorySelectEl ? memoryCategorySelectEl.value : "auto";
  try {
    const res = await fetch("/api/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "memory", content, category }),
    });
    const data = await res.json();
    if (res.ok && data && data.ok) {
      memoryAddInputEl.value = "";
      if (data.counts) syncMemoryCountsUI(data.counts);
      await fetchMemoriesState();
      showAssistantMemoryToast("🧠 Đã lưu vào Ký ức dài hạn của Wisp");
    }
  } catch (_e) {}
}

async function handleAddTaskFromPanel() {
  if (!taskAddInputEl) return;
  const title = taskAddInputEl.value.trim();
  if (!title) {
    taskAddInputEl.focus();
    return;
  }
  try {
    const res = await fetch("/api/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "task", title }),
    });
    const data = await res.json();
    if (res.ok && data && data.ok) {
      taskAddInputEl.value = "";
      if (data.counts) syncMemoryCountsUI(data.counts);
      await fetchMemoriesState();
      const dueSuffix = data.item && data.item.due_text ? ` (${data.item.due_text})` : "";
      showAssistantMemoryToast(`📌 Đã thêm việc cần nhớ${dueSuffix}`);
    }
  } catch (_e) {}
}

if (memoryTabsBarEl) {
  memoryTabsBarEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".mem-tab-pill[data-mem-tab]");
    if (!btn) return;
    e.stopPropagation();
    switchMemoryTab(btn.getAttribute("data-mem-tab"));
  });
}

if (memoryAddBtnEl) {
  memoryAddBtnEl.addEventListener("click", (e) => {
    e.stopPropagation();
    handleAddMemoryFromPanel();
  });
}

if (memoryAddInputEl) {
  memoryAddInputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      handleAddMemoryFromPanel();
    }
  });
}

if (memorySearchInputEl) {
  memorySearchInputEl.addEventListener("input", () => {
    renderMemoryList();
  });
}

if (memoryListContainerEl) {
  memoryListContainerEl.addEventListener("click", async (e) => {
    const actionBtn = e.target.closest("button[data-action]");
    if (!actionBtn) return;
    e.stopPropagation();
    const action = actionBtn.getAttribute("data-action");
    const id = Number(actionBtn.getAttribute("data-id"));
    if (!Number.isFinite(id) || id <= 0) return;

    if (action === "pin-memory") {
      const currentlyPinned = actionBtn.getAttribute("data-pinned") === "1";
      try {
        const res = await fetch(`/api/memories/memory/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pinned: !currentlyPinned }),
        });
        const data = await res.json();
        if (res.ok && data && data.ok) {
          if (data.counts) syncMemoryCountsUI(data.counts);
          await fetchMemoriesState();
          showAssistantMemoryToast(!currentlyPinned ? "📌 Đã ghim ưu tiên ký ức" : "Đã bỏ ghim ký ức");
        }
      } catch (_err) {}
    } else if (action === "delete-memory") {
      try {
        const res = await fetch(`/api/memories/memory/${id}`, { method: "DELETE" });
        const data = await res.json();
        if (res.ok && data && data.ok) {
          if (data.counts) syncMemoryCountsUI(data.counts);
          await fetchMemoriesState();
          showAssistantMemoryToast("🗑️ Đã xóa ký ức khỏi Bộ nhớ Wisp");
        }
      } catch (_err) {}
    }
  });
}

if (clearAllMemoriesBtnEl) {
  clearAllMemoriesBtnEl.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!cachedMemoriesList.length) return;
    try {
      const res = await fetch("/api/memories?kind=memories", { method: "DELETE" });
      const data = await res.json();
      if (res.ok && data && data.ok) {
        if (data.counts) syncMemoryCountsUI(data.counts);
        await fetchMemoriesState();
        showAssistantMemoryToast("🗑️ Đã xóa toàn bộ ký ức dài hạn");
      }
    } catch (_err) {}
  });
}

if (taskAddBtnEl) {
  taskAddBtnEl.addEventListener("click", (e) => {
    e.stopPropagation();
    handleAddTaskFromPanel();
  });
}

if (taskAddInputEl) {
  taskAddInputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      handleAddTaskFromPanel();
    }
  });
}

if (taskListContainerEl) {
  taskListContainerEl.addEventListener("click", async (e) => {
    const actionBtn = e.target.closest("button[data-action]");
    if (!actionBtn) return;
    e.stopPropagation();
    const action = actionBtn.getAttribute("data-action");
    const id = Number(actionBtn.getAttribute("data-id"));
    if (!Number.isFinite(id) || id <= 0) return;

    if (action === "toggle-task") {
      const curStatus = actionBtn.getAttribute("data-status");
      const nextStatus = curStatus === "done" ? "pending" : "done";
      try {
        const res = await fetch(`/api/memories/task/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: nextStatus }),
        });
        const data = await res.json();
        if (res.ok && data && data.ok) {
          if (data.counts) syncMemoryCountsUI(data.counts);
          await fetchMemoriesState();
          showAssistantMemoryToast(nextStatus === "done" ? "✅ Đã hoàn thành công việc" : "Đã mở lại công việc");
        }
      } catch (_err) {}
    } else if (action === "delete-task") {
      try {
        const res = await fetch(`/api/memories/task/${id}`, { method: "DELETE" });
        const data = await res.json();
        if (res.ok && data && data.ok) {
          if (data.counts) syncMemoryCountsUI(data.counts);
          await fetchMemoriesState();
          showAssistantMemoryToast("🗑️ Đã xóa việc cần nhớ");
        }
      } catch (_err) {}
    }
  });
}

if (clearDoneTasksBtnEl) {
  clearDoneTasksBtnEl.addEventListener("click", async (e) => {
    e.stopPropagation();
    const hasDone = cachedTasksList.some((t) => t && t.status === "done");
    if (!hasDone) return;
    try {
      const res = await fetch("/api/memories?kind=done_tasks", { method: "DELETE" });
      const data = await res.json();
      if (res.ok && data && data.ok) {
        if (data.counts) syncMemoryCountsUI(data.counts);
        await fetchMemoriesState();
        showAssistantMemoryToast("🧹 Đã dọn các việc đã hoàn thành");
      }
    } catch (_err) {}
  });
}

if (clearAllTasksBtnEl) {
  clearAllTasksBtnEl.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!cachedTasksList.length) return;
    try {
      const res = await fetch("/api/memories?kind=tasks", { method: "DELETE" });
      const data = await res.json();
      if (res.ok && data && data.ok) {
        if (data.counts) syncMemoryCountsUI(data.counts);
        await fetchMemoriesState();
        showAssistantMemoryToast("🗑️ Đã xóa toàn bộ danh sách việc cần nhớ");
      }
    } catch (_err) {}
  });
}

fetchMemoriesState();

if (userProfileBtn && userProfilePanel) {
  userProfileBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isHidden = userProfilePanel.hasAttribute("hidden");
    if (isHidden) {
      if (aiConfigPanel && !aiConfigPanel.hasAttribute("hidden")) {
        aiConfigPanel.setAttribute("hidden", "");
        if (aiConfigBtn) aiConfigBtn.setAttribute("aria-expanded", "false");
      }
      revertUserProfileFormInputs();
      fetchMemoriesState();
      userProfilePanel.removeAttribute("hidden");
      userProfilePanel.classList.add("active");
      userProfileBtn.setAttribute("aria-expanded", "true");
    } else {
      closeUserProfilePanel(true);
    }
  });

  document.addEventListener("click", (e) => {
    if (
      !userProfilePanel.hasAttribute("hidden") &&
      userProfileWrap &&
      document.contains(e.target) &&
      !userProfileWrap.contains(e.target)
    ) {
      closeUserProfilePanel(true);
    }
  });
}

if (aiStyleSelectEl) {
  aiStyleSelectEl.addEventListener("change", () => {
    const val = aiStyleSelectEl.value;
    currentAiPrefs.style = ["concise", "balanced", "detailed"].includes(val) ? val : "balanced";
    saveAiPrefs();
  });
}

if (styleSegmentGroupEl) {
  styleSegmentGroupEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".ai-segment-btn[data-style-val]");
    if (!btn) return;
    const val = btn.getAttribute("data-style-val");
    if (["concise", "balanced", "detailed"].includes(val)) {
      currentAiPrefs.style = val;
      saveAiPrefs();
    }
  });
}

if (aiPersonaSelectEl) {
  aiPersonaSelectEl.addEventListener("change", () => {
    const val = aiPersonaSelectEl.value;
    currentAiPrefs.persona = ["minh_nguyen", "em_anh", "direct"].includes(val) ? val : "minh_nguyen";
    saveAiPrefs();
  });
}

if (personaSegmentGroupEl) {
  personaSegmentGroupEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".ai-segment-btn[data-persona-val]");
    if (!btn) return;
    const val = btn.getAttribute("data-persona-val");
    if (["minh_nguyen", "em_anh", "direct"].includes(val)) {
      currentAiPrefs.persona = val;
      saveAiPrefs();
    }
  });
}

[
  [prefNoGreetingEl, "no_greeting"],
  [prefNoApologyEl, "no_apology"],
  [prefNoOutroEl, "no_outro"],
  [prefCodeFirstEl, "code_first"],
  [prefPlainTextEl, "plain_text"],
].forEach(([el, key]) => {
  if (el) {
    el.addEventListener("change", () => {
      currentAiPrefs[key] = Boolean(el.checked);
      saveAiPrefs();
    });
  }
});

if (resetAiPrefsBtn) {
  resetAiPrefsBtn.addEventListener("click", () => {
    currentAiPrefs = { ...DEFAULT_AI_PREFS };
    saveAiPrefs();
  });
}

if (aiConfigBtn && aiConfigPanel) {
  aiConfigBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isHidden = aiConfigPanel.hasAttribute("hidden");
    if (isHidden) {
      if (userProfilePanel && !userProfilePanel.hasAttribute("hidden")) {
        closeUserProfilePanel(true);
      }
      aiConfigPanel.removeAttribute("hidden");
      aiConfigBtn.setAttribute("aria-expanded", "true");
    } else {
      aiConfigPanel.setAttribute("hidden", "");
      aiConfigBtn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("click", (e) => {
    if (!aiConfigPanel.hasAttribute("hidden") && aiConfigWrap && !aiConfigWrap.contains(e.target)) {
      aiConfigPanel.setAttribute("hidden", "");
      aiConfigBtn.setAttribute("aria-expanded", "false");
    }
  });
}

// Cấu hình Độ nhạy Âm thanh / Microphone (Mặc định: "low" = Ít nhạy / Lọc tạp âm cao theo yêu cầu người dùng)
const MIC_SENSITIVITY_KEY = "wisp_mic_sensitivity";
const MIC_SENSITIVITY_PROFILES = {
  low: {
    noiseGate: 38,          // Ngưỡng biên độ tối thiểu (0..255) để bỏ qua tiếng quạt, gõ phím, tạp âm nền
    levelScale: 96,         // Độ chia biên độ để sóng âm đằm và không vọt mạnh
    orbTrigger: 0.18,       // Chỉ kích hoạt trạng thái quả cầu nghe khi âm lượng giọng nói rõ ràng
    minConfidence: 0.56,    // Lọc bỏ các từ nhận diện độ tin cậy thấp do tạp âm
    silenceFinalMs: 1150,   // Đợi ~1.15s sau khi ngắt nhịp để người dùng thoải mái nói tiếp vế sau
    silenceInterimMs: 2400, // Đợi ~2.4s khi đang ngập ngừng giữa câu
  },
  medium: {
    noiseGate: 26,
    levelScale: 82,
    orbTrigger: 0.12,
    minConfidence: 0.42,
    silenceFinalMs: 1050,
    silenceInterimMs: 2300,
  },
  high: {
    noiseGate: 16,
    levelScale: 70,
    orbTrigger: 0.07,
    minConfidence: 0.25,
    silenceFinalMs: 950,
    silenceInterimMs: 2200,
  },
};

let selectedMicSensitivity = localStorage.getItem(MIC_SENSITIVITY_KEY) || "low";
if (!MIC_SENSITIVITY_PROFILES[selectedMicSensitivity]) {
  selectedMicSensitivity = "low";
}
if (micSensitivitySelectEl) {
  micSensitivitySelectEl.value = selectedMicSensitivity;
  micSensitivitySelectEl.addEventListener("change", () => {
    const val = micSensitivitySelectEl.value;
    selectedMicSensitivity = MIC_SENSITIVITY_PROFILES[val] ? val : "low";
    localStorage.setItem(MIC_SENSITIVITY_KEY, selectedMicSensitivity);
  });
}

function getMicProfile() {
  return MIC_SENSITIVITY_PROFILES[selectedMicSensitivity] || MIC_SENSITIVITY_PROFILES.low;
}

// Trạng thái Chế độ Nói (2-Way Hands-free Voice Mode) & Hàng đợi Âm thanh Neural TTS Tiếng Việt
let voiceMicMuted = false;
let isRecognitionRunning = false;
let speechRec = null;
let silenceSendTimer = 0;
let pendingVoiceDraft = "";       // Các câu đã chốt qua các phiên rec trước (nếu rec tự restart giữa chừng)
let currentLiveTranscript = "";   // Toàn bộ câu nháp hiện tại (pendingVoiceDraft + phiên hiện tại)
let lastSpeechActivityMs = 0;     // Thời điểm gần nhất nhận được kết quả SpeechRecognition
let liveVoiceDraftBubble = null;
let isSpeakingTTS = false;
let activeUtteranceCount = 0;
let ttsSpokenCharIndex = 0;
let ttsPulseBoost = 0;
let ttsQueue = [];
let ttsCurrentAudio = null;
let ttsSessionToken = 0;

// Web Audio API đo âm lượng thật từ Mic & từ luồng giọng nói TTS Tiếng Việt
let sharedAudioCtx = null;
let micAnalyser = null;
let micStream = null;
let ttsAnalyser = null;
let waveLoopRaf = 0;
let smoothAudioLevel = 0; // 0..1 biên độ âm thanh đã làm mượt
let lastSoundDetectedMs = 0;

// Telemetry theo dõi nhịp gõ phím của người dùng ở Chế độ Chat
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

function stripCodeForVoiceSubtitle(text) {
  let cleaned = String(text ?? "");
  cleaned = cleaned.replace(/(?:^|\n)+\s*(?:🌐\s*)?Nguồn:\s+[^\n]+/g, "");
  cleaned = cleaned.replace(/\[([^\[\]\n]+)\]\(https?:\/\/[^\s\x29]+\)/gi, "$1");
  if (!cleaned.includes("```")) return cleaned.trim();
  cleaned = cleaned
    .replace(/(`{3,})[\s\S]*?(?:\1`*|$)/g, " [Đã hiển thị mã nguồn trên khung chat] ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned || "Đã hiển thị mã nguồn trên khung chat.";
}

function setVoiceSubtitle(text, isMuted = false) {
  if (!voiceTranscriptEl) return;
  voiceTranscriptEl.textContent = stripCodeForVoiceSubtitle(text);
  voiceTranscriptEl.classList.toggle("muted", Boolean(isMuted));
}

function updateVoiceMicBarUI() {
  if (voiceMicDot) {
    voiceMicDot.classList.toggle("muted", Boolean(voiceMicMuted));
  }
  if (voiceMicStatusLabel) {
    if (voiceMicMuted) {
      voiceMicStatusLabel.textContent = "Mic đang tạm dừng · Bấm để bật lại";
    } else if (isSpeakingTTS) {
      voiceMicStatusLabel.textContent = "Wisp đang trả lời · Chạm để ngắt lời";
    } else if (isSending) {
      voiceMicStatusLabel.textContent = "Đang xử lý câu hỏi…";
    } else if ((currentLiveTranscript && currentLiveTranscript.trim()) || (pendingVoiceDraft && pendingVoiceDraft.trim())) {
      voiceMicStatusLabel.textContent = "Đang nghe bạn nói… · Chạm để gửi ngay";
    } else {
      voiceMicStatusLabel.textContent = "Đang lắng nghe · Chạm để tạm dừng";
    }
  }
}

function syncIdleState() {
  consecutiveDeletes = 0;
  updateVoiceMicBarUI();
  if (currentAppMode === "voice") {
    if (voiceMicMuted) {
      setOrbVisualState("breathing", "Đang tạm dừng nghe (chạm quả cầu để bật lại)", 0.85);
    } else {
      setOrbVisualState("breathing", "Đang lắng nghe…", 0.95);
    }
  } else {
    setOrbVisualState("breathing", STATE_DESCRIPTIONS.breathing, 1);
  }
}

/* ==========================================================================
   CHUẨN HÓA ĐẦU VÀO TIẾNG VIỆT (CLIENT-SIDE NORMALIZATION & STT RERANKING)
   - Chuẩn hóa bảng mã Unicode dựng sẵn NFC, sửa chữ Ð/ð -> Đ/đ
   - Sửa lỗi nhận diện giọng nói (STT) hay nghe nhầm tên "Wisp" hoặc địa danh
   - Mở rộng từ viết tắt & khôi phục dấu cho các cụm từ khóa thông dụng
   - Chấm điểm đa phương án (maxAlternatives) của SpeechRecognition để lấy câu chuẩn nhất
   ========================================================================== */

const CLIENT_SHORTHAND_RULES = [
  [/(?<![\p{L}\p{N}_])(chào|ê|này|ơi|hỏi|bạn)\s+(quý|whip|wish|huyết|quýt|vip|bíp|quất)(?![\p{L}\p{N}_])/giu, "$1 Wisp"],
  [/^(quý|whip|wish|huyết|quýt)\s+(ơi|à|nhé|cho)(?![\p{L}\p{N}_])/giu, "Wisp $2"],
  [/(?<![\p{L}\p{N}_])(tp\.?\s*hcm|tphcm|hcm|sg)(?![\p{L}\p{N}_])/giu, "Sài Gòn"],
  [/(?<![\p{L}\p{N}_])bmt(?![\p{L}\p{N}_])/giu, "Buôn Ma Thuột"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)hn(?![\p{L}\p{N}_])/giu, "Hà Nội"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)(?:đn|dn)(?![\p{L}\p{N}_])/giu, "Đà Nẵng"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)(?:đl|dl)(?![\p{L}\p{N}_])/giu, "Đà Lạt"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)hp(?![\p{L}\p{N}_])/giu, "Hải Phòng"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)ct(?![\p{L}\p{N}_])/giu, "Cần Thơ"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)nt(?![\p{L}\p{N}_])/giu, "Nha Trang"],
  [/(?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)vt(?![\p{L}\p{N}_])/giu, "Vũng Tàu"],
  [/(?<![\p{L}\p{N}_])(ko|khg|hok)(?![\p{L}\p{N}_])/giu, "không"],
  [/(?<![\p{L}\p{N}_])(dc|đc)(?![\p{L}\p{N}_])/giu, "được"],
  [/(?<![\p{L}\p{N}_])ntn(?![\p{L}\p{N}_])/giu, "như thế nào"],
  [/(?<![\p{L}\p{N}_])bnhiu(?![\p{L}\p{N}_])/giu, "bao nhiêu"],
  [/(?<![\p{L}\p{N}_])(bh|bjo)(?![\p{L}\p{N}_])/giu, "bây giờ"],
  [/(?<![\p{L}\p{N}_])hnay(?![\p{L}\p{N}_])/giu, "hôm nay"],
  [/(?<![\p{L}\p{N}_])nmai(?![\p{L}\p{N}_])/giu, "ngày mai"],
];

const CLIENT_ACCENT_RESTORE_RULES = [
  [/(?<![\p{L}\p{N}_])thoi tiet(?![\p{L}\p{N}_])/giu, "thời tiết"],
  [/(?<![\p{L}\p{N}_])nhiet do(?![\p{L}\p{N}_])/giu, "nhiệt độ"],
  [/(?<![\p{L}\p{N}_])may gio roi(?![\p{L}\p{N}_])/giu, "mấy giờ rồi"],
  [/(?<![\p{L}\p{N}_])may gio(?![\p{L}\p{N}_])/giu, "mấy giờ"],
  [/(?<![\p{L}\p{N}_])bay gio(?![\p{L}\p{N}_])/giu, "bây giờ"],
  [/(?<![\p{L}\p{N}_])hien tai(?![\p{L}\p{N}_])/giu, "hiện tại"],
  [/(?<![\p{L}\p{N}_])hom nay(?![\p{L}\p{N}_])/giu, "hôm nay"],
  [/(?<![\p{L}\p{N}_])ngay mai(?![\p{L}\p{N}_])/giu, "ngày mai"],
  [/(?<![\p{L}\p{N}_])ngay may(?![\p{L}\p{N}_])/giu, "ngày mấy"],
  [/(?<![\p{L}\p{N}_])thu may(?![\p{L}\p{N}_])/giu, "thứ mấy"],
  [/(?<![\p{L}\p{N}_])xoa lich su(?![\p{L}\p{N}_])/giu, "xóa lịch sử"],
  [/(?<![\p{L}\p{N}_])sai gon(?![\p{L}\p{N}_])/giu, "Sài Gòn"],
  [/(?<![\p{L}\p{N}_])ha noi(?![\p{L}\p{N}_])/giu, "Hà Nội"],
  [/(?<![\p{L}\p{N}_])da nang(?![\p{L}\p{N}_])/giu, "Đà Nẵng"],
  [/(?<![\p{L}\p{N}_])da lat(?![\p{L}\p{N}_])/giu, "Đà Lạt"],
];

const CLIENT_CODE_SEGMENT_RE = /(`{3,})[\s\S]*?(?:\1`*|$)|``(?:(?!``)[^\n])+``|`[^`\n]+`/g;

const CLIENT_LIKELY_CODE_LINE_RE = new RegExp(
  "^\\s*(?:let|var|const|func|function|def|class|struct|enum|extension|protocol|interface|type|typedef|" +
    "import|export|return|yield|elif|else|switch|case|guard|async|await|try|catch|finally|throw|throws|" +
    "public|private|protected|internal|fileprivate|static|final|override|mutating|void|int|double|float|bool|" +
    "String|Widget|State|BuildContext|@[A-Za-z_]\\w*|//|/\\*|\\*/|\\*\\s|#include|#define|#pragma)\\b|" +
    "^\\s*(?:if|for|while)\\b(?=.*(?:[\\x28\\x7B:=<>!]|->|=>))|" +
    "^\\s*[\"'][A-Za-z0-9_.-]+[\"']\\s*:|" +
    "^\\s*</?[A-Za-z][A-Za-z0-9_.-]*(?:\\s|>|/>)|" +
    "[\\x7B\\x7D]|=>|->|===|!==|==|!=|<=|>=|\\+=|-=|\\*=|/=|:=|\\s(?:&&|\\|\\|)\\s|" +
    ";\\s*(?://|#|$)|" +
    "\\b[A-Za-z_][A-Za-z0-9_.]*\\s*=\\s*[^=]|" +
    "\\b[A-Za-z_]\\w*\\.[A-Za-z_]\\w*\\x28|" +
    "^\\s*[A-Za-z_][A-Za-z0-9_.]*\\([^()\\n]*\\)\\s*;?\\s*$"
);

const CLIENT_LIKELY_SQL_OR_CLI_RE = new RegExp(
  "^\\s*(?:SELECT\\s+.+\\s+FROM|INSERT\\s+INTO|UPDATE\\s+\\S+\\s+SET|DELETE\\s+FROM|" +
    "CREATE\\s+(?:TABLE|INDEX|VIEW|DATABASE)|ALTER\\s+TABLE|DROP\\s+(?:TABLE|INDEX|VIEW))\\b|" +
    "^\\s*(?:git\\s+(?:init|clone|status|add|commit|push|pull|fetch|checkout|switch|branch|merge|rebase|reset|stash|log|diff|tag|remote|cherry-pick)|" +
    "npm\\s+(?:install|i|run|start|test|build|init|ci|uninstall|publish|exec)|" +
    "(?:npx|yarn|pnpm)\\s+[A-Za-z0-9_./@-]+|" +
    "(?:pip|pip3)\\s+(?:install|uninstall|freeze|list|show)|" +
    "(?:python|python3)\\s+(?:-[mc]\\b|\\S+\\.py\\b)|" +
    "pytest(?:\\s|$)|" +
    "flutter\\s+(?:run|build|pub|clean|create|test|doctor|analyze|upgrade|channel)|" +
    "dart\\s+(?:run|compile|pub|analyze|test|fix|format)|" +
    "swift\\s+(?:build|run|test|package)|" +
    "docker\\s+(?:run|build|compose|ps|exec|pull|push|images|logs|stop|rm)|" +
    "(?:cd|mkdir|rmdir|chmod|chown|curl|wget)\\s+[A-Za-z0-9_./~$-])\\b",
  "i"
);

function iterClientCodeAndProseSegments(str) {
  const segments = [];
  let last = 0;
  CLIENT_CODE_SEGMENT_RE.lastIndex = 0;
  let m;
  while ((m = CLIENT_CODE_SEGMENT_RE.exec(str)) !== null) {
    if (m.index > last) {
      segments.push({ isCode: false, text: str.slice(last, m.index) });
    }
    segments.push({ isCode: true, text: m[0] });
    last = CLIENT_CODE_SEGMENT_RE.lastIndex;
    if (m[0].length === 0) {
      CLIENT_CODE_SEGMENT_RE.lastIndex += 1;
    }
  }
  if (last < str.length) {
    segments.push({ isCode: false, text: str.slice(last) });
  }
  return segments;
}

function isLikelyClientCodeLine(line, isMultiline = false) {
  if (!line || !line.trim()) return false;
  if (isMultiline && /^(?: {2,}|\t+)\S/.test(line)) return true;
  if (CLIENT_LIKELY_CODE_LINE_RE.test(line)) return true;
  const stripped = line.trim();
  if (!stripped.endsWith("?") && CLIENT_LIKELY_SQL_OR_CLI_RE.test(line)) {
    return true;
  }
  return false;
}

function normalizeClientProseFragment(fragment) {
  if (!fragment) return "";
  let s = fragment.replace(/[ \t]+/g, " ");
  if (!s.trim()) {
    return s.includes(" ") ? " " : "";
  }
  // Khử cụm >= 2 từ lặp liên tiếp do ranh giới nhận diện giọng nói interim -> final (giữ nguyên từ láy đơn)
  s = s.replace(/(?<![\p{L}\p{N}_])(\S+\s+\S+)\s+\1(?![\p{L}\p{N}_])/giu, "$1");

  for (const [pattern, replacement] of CLIENT_ACCENT_RESTORE_RULES) {
    s = s.replace(pattern, replacement);
  }
  for (const [pattern, replacement] of CLIENT_SHORTHAND_RULES) {
    s = s.replace(pattern, replacement);
  }
  return s.replace(/[ \t]+/g, " ");
}

function normalizeVietnameseClientInput(rawText) {
  if (!rawText) return "";
  let s = String(rawText).normalize("NFC");
  s = s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  s = s.replace(/Ð/g, "Đ").replace(/ð/g, "đ");
  s = s.replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\u00A0/g, " ");
  if (!s.trim()) return "";

  s = s.replace(/^(?:[ \t]*\n)+/, "").replace(/\s+$/, "");
  const isMultiline = s.includes("\n");

  const segments = iterClientCodeAndProseSegments(s);
  const outParts = [];
  for (let idx = 0; idx < segments.length; idx++) {
    const { isCode, text: part } = segments[idx];
    if (!part) continue;
    if (isCode) {
      outParts.push(part);
      continue;
    }
    const lines = part.split("\n");
    const normLines = [];
    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
      const line = lines[lineIdx];
      if (isLikelyClientCodeLine(line, isMultiline)) {
        normLines.push(line.replace(/[ \t]+$/, ""));
      } else {
        let normFrag = normalizeClientProseFragment(line);
        const atLineStart = idx === 0 || lineIdx > 0;
        const atLineEnd = idx === segments.length - 1 || lineIdx < lines.length - 1;
        if (atLineStart) {
          normFrag = normFrag.replace(/^[ \t]+/, "");
        }
        if (atLineEnd) {
          normFrag = normFrag.replace(/[ \t]+$/, "");
        }
        normLines.push(normFrag);
      }
    }
    outParts.push(normLines.join("\n"));
  }

  const result = outParts.join("");
  if (!isMultiline && !result.includes("```")) {
    return result.trim();
  }
  return result;
}

/**
 * Kiểm tra xem chuỗi thu được có phải chỉ là tạp âm / tiếng thở / từ cảm thán đơn lẻ vô nghĩa hay không.
 */
function isNoiseFillerTranscript(text) {
  const clean = String(text || "").trim().toLowerCase();
  if (!clean) return true;
  if (/^(ờ|à|ừ|ừm|ơ|ư|a|ô|ồ|hả|hở|ử|í|ê|hư|hừm|uh|um|ah|oh)+[.!?…]*$/i.test(clean)) {
    return true;
  }
  return false;
}

/**
 * Chọn phương án nhận diện tiếng Việt chuẩn xác nhất từ danh sách alternatives của SpeechRecognition.
 * Lọc bỏ các phương án có độ tin cậy (confidence) quá thấp dưới ngưỡng nhạy Mic.
 */
function pickBestVietnameseAlternative(speechResult) {
  if (!speechResult || !speechResult.length) return "";
  const micProfile = getMicProfile();
  let bestText = "";
  let bestScore = -999;

  for (let i = 0; i < speechResult.length; i++) {
    const alt = speechResult[i];
    const candidate = normalizeVietnameseClientInput(alt.transcript || "");
    if (!candidate) continue;

    const conf = typeof alt.confidence === "number" && alt.confidence > 0 ? alt.confidence : 0;
    const isImportantKeyword = /(?<![\p{L}\p{N}_])(thời tiết|nhiệt độ|mấy giờ|giờ|hôm nay|ngày mai|thứ mấy|Wisp|chào|help|Hà Nội|Sài Gòn|Đà Nẵng|Đà Lạt)(?![\p{L}\p{N}_])/iu.test(candidate);

    // Nếu trình duyệt cung cấp confidence thấp hơn ngưỡng lọc ồn và không phải từ khóa rõ ràng -> bỏ qua
    if (conf > 0 && conf < micProfile.minConfidence && !isImportantKeyword && candidate.split(" ").length <= 2) {
      continue;
    }

    let score = (conf > 0 ? conf : 0.68) * 10;
    if (/[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]/i.test(candidate)) {
      score += 3;
    }
    if (isImportantKeyword) {
      score += 0.8;
    }
    score -= i * 0.4;

    if (score > bestScore) {
      bestScore = score;
      bestText = candidate;
    }
  }

  if (isNoiseFillerTranscript(bestText)) {
    return "";
  }
  return normalizeVietnameseClientInput(bestText);
}

/**
 * Ghép phần nhận diện mới vào bản nháp mà không bị lặp từ ở điểm nối.
 */
function mergeSpeechSegments(existingText, newSegment) {
  const a = normalizeVietnameseClientInput(existingText);
  const b = normalizeVietnameseClientInput(newSegment);
  if (!a) return b;
  if (!b) return a;
  if (a.toLowerCase().endsWith(b.toLowerCase())) return a;
  if (b.toLowerCase().startsWith(a.toLowerCase())) return b;

  const wordsA = a.split(" ");
  const wordsB = b.split(" ");
  const maxOverlap = Math.min(wordsA.length, wordsB.length, 4);
  for (let ov = maxOverlap; ov >= 1; ov--) {
    const tailA = wordsA.slice(-ov).join(" ").toLowerCase();
    const headB = wordsB.slice(0, ov).join(" ").toLowerCase();
    if (tailA === headB) {
      return normalizeVietnameseClientInput(wordsA.concat(wordsB.slice(ov)).join(" "));
    }
  }
  return normalizeVietnameseClientInput(`${a} ${b}`);
}

/**
 * Escape HTML để chống XSS tuyệt đối, sau đó định dạng:
 * - Khối mã nguồn ```lang ... ``` -> <pre class="code-block"><div class="code-header"><span class="code-lang">...</span><button type="button" class="copy-btn">Sao chép</button></div><code class="language-...">...</code></pre>
 * - Cụm inline `...` -> <code class="inline-code">...</code>
 * - Cụm **...** ngoài code -> <strong>...</strong>
 */
function escapeHtmlText(raw) {
  return String(raw)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sanitizeMarkdownHref(escapedUrl) {
  const candidate = String(escapedUrl || "").trim().replace(/&#39;/g, "%27");
  if (!/^https?:\/\/[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+$/i.test(candidate)) {
    return "";
  }
  if (/(&quot;|&#39;|&lt;|&gt;)/i.test(candidate)) {
    return "";
  }
  return candidate;
}

function formatInlineSafeMarkdown(rawSegment) {
  const inlineParts = String(rawSegment).split(/(``(?:(?!``)[^\n])+``|`[^`\n]+`)/g);
  return inlineParts
    .map((piece, idx) => {
      if (!piece) return "";
      if (idx % 2 === 1 && piece.startsWith("`") && piece.endsWith("`")) {
        const rawInner =
          piece.startsWith("``") && piece.endsWith("``") && piece.length >= 4
            ? piece.slice(2, -2).trim()
            : piece.slice(1, -1);
        const innerCode = escapeHtmlText(rawInner);
        return `<code class="inline-code">${innerCode}</code>`;
      }
      const escaped = escapeHtmlText(piece);
      const withLinks = escaped.replace(
        /\[((?:\[[^\[\]\n]*\]|[^\[\]\n])+)\]\(((?:[^()\s]+|\((?:[^()\s]+|\([^()\s]*\))*\))+)\)/g,
        (fullMatch, escapedLabel, escapedUrl) => {
          const safeHref = sanitizeMarkdownHref(escapedUrl);
          if (!safeHref) return fullMatch;
          const labelHtml = escapedLabel.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
          return `<a class="chat-md-link" href="${safeHref}" target="_blank" rel="noopener noreferrer">${labelHtml}</a>`;
        }
      );
      return withLinks.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    })
    .join("");
}

function formatSafeMessageHtml(text) {
  const src = String(text ?? "");
  if (!src) return "";

  const fenceRegex = /(`{3,})([^\n`]*)\n?([\s\S]*?)(?:\1`*|$)/g;
  let lastIndex = 0;
  let endedWithFence = false;
  let html = "";
  let match;

  while ((match = fenceRegex.exec(src)) !== null) {
    if (match.index > lastIndex) {
      let beforeText = src.slice(lastIndex, match.index);
      if (endedWithFence && beforeText.startsWith("\n")) {
        beforeText = beforeText.slice(1);
      }
      if (beforeText.endsWith("\n")) {
        beforeText = beforeText.slice(0, -1);
      }
      html += formatInlineSafeMarkdown(beforeText);
    }

    const rawLang = String(match[2] || "").trim();
    const cleanLang = rawLang.replace(/[^A-Za-z0-9_+#.-]/g, "").toLowerCase();
    const displayLang = cleanLang || "code";
    const langClass = cleanLang || "plaintext";
    const rawCodeBody = String(match[3] || "").replace(/\n$/, "");
    const escapedCode = escapeHtmlText(rawCodeBody);

    html +=
      `<pre class="code-block">` +
      `<div class="code-header">` +
      `<span class="code-lang">${escapeHtmlText(displayLang)}</span>` +
      `<button type="button" class="copy-btn" aria-label="Sao chép mã nguồn">Sao chép</button>` +
      `</div>` +
      `<code class="language-${escapeHtmlText(langClass)}">${escapedCode}</code>` +
      `</pre>`;

    endedWithFence = true;
    lastIndex = fenceRegex.lastIndex;
    if (match[0].length === 0) {
      fenceRegex.lastIndex += 1;
    }
  }

  if (lastIndex < src.length) {
    let tailText = src.slice(lastIndex);
    if (endedWithFence && tailText.startsWith("\n")) {
      tailText = tailText.slice(1);
    }
    html += formatInlineSafeMarkdown(tailText);
  }

  return html;
}

function getActiveChatContainer(modeOverride) {
  const mode = modeOverride || currentAppMode;
  return mode === "voice" && voiceChatEl ? voiceChatEl : chatEl;
}

function scrollToBottom(containerEl) {
  const target = containerEl || getActiveChatContainer();
  if (!target) return;
  target.scrollTop = target.scrollHeight;
}

function ensureVoiceDraftBubble(draftText) {
  if (!voiceChatEl) return null;
  if (!liveVoiceDraftBubble || !voiceChatEl.contains(liveVoiceDraftBubble)) {
    const row = document.createElement("div");
    row.className = "message-row user-row";
    const bubble = document.createElement("div");
    bubble.className = "message user-message voice-draft-bubble";
    row.appendChild(bubble);
    voiceChatEl.appendChild(row);
    liveVoiceDraftBubble = bubble;
  }
  liveVoiceDraftBubble.textContent = draftText;
  scrollToBottom(voiceChatEl);
  return liveVoiceDraftBubble;
}

function clearVoiceDraftBubble() {
  if (liveVoiceDraftBubble) {
    const row = liveVoiceDraftBubble.parentElement;
    if (row && row.parentElement) {
      row.parentElement.removeChild(row);
    }
    liveVoiceDraftBubble = null;
  }
}

function appendUserMessage(text, containerEl) {
  const target = containerEl || getActiveChatContainer();
  if (!target) return null;

  // Nếu đang ở khung chat giọng nói và đã có bong bóng nháp khi đang nói -> chuyển luôn thành bong bóng chính thức
  if (target === voiceChatEl && liveVoiceDraftBubble && voiceChatEl.contains(liveVoiceDraftBubble)) {
    const finalized = liveVoiceDraftBubble;
    finalized.classList.remove("voice-draft-bubble");
    finalized.textContent = text;
    liveVoiceDraftBubble = null;
    scrollToBottom(voiceChatEl);
    return finalized;
  }

  const row = document.createElement("div");
  row.className = "message-row user-row";
  const bubble = document.createElement("div");
  bubble.className = "message user-message";
  bubble.textContent = text;
  row.appendChild(bubble);
  target.appendChild(row);
  scrollToBottom(target);
  return bubble;
}

function createBotBubble(containerEl) {
  const target = containerEl || getActiveChatContainer();
  if (!target) return null;
  const row = document.createElement("div");
  row.className = "message-row bot-row";
  const bubble = document.createElement("div");
  bubble.className = "message bot-message";
  row.appendChild(bubble);
  target.appendChild(row);
  return { row, bubble, container: target };
}

/* ==========================================================================
   ĐỒNG BỘ LỊCH SỬ HỘI THOẠI XUYÊN SUỐT CHAT MODE, VOICE MODE, F5 & BACKEND
   ========================================================================== */

const CHAT_HISTORY_STORAGE_KEY = "wisp_chat_history";
const MAX_STORED_HISTORY_MESSAGES = 100;
let chatHistoryStore = [];
let historyEpoch = 0;

function saveChatHistoryToStorage() {
  try {
    const validItems = chatHistoryStore.filter(
      (item) => item && (item.role === "user" || item.role === "assistant") && String(item.content || "").trim()
    );
    chatHistoryStore = validItems.slice(-MAX_STORED_HISTORY_MESSAGES);
    if (chatHistoryStore.length === 0) {
      localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
    } else {
      localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(chatHistoryStore));
    }
  } catch (_e) {}
}

function appendUnifiedUserTurn(text, recordInStore = true) {
  const chatBubble = chatEl ? appendUserMessage(text, chatEl) : null;
  const voiceBubble = voiceChatEl ? appendUserMessage(text, voiceChatEl) : null;
  let storeIndex = -1;
  if (recordInStore) {
    chatHistoryStore.push({ role: "user", content: text, ts: Date.now() });
    storeIndex = chatHistoryStore.length - 1;
    saveChatHistoryToStorage();
    storeIndex = chatHistoryStore.length - 1;
  }
  return { chatBubble, voiceBubble, storeIndex };
}

function updateUnifiedUserTurnText(userTurn, newText) {
  if (!userTurn || !newText) return;
  if (userTurn.chatBubble) userTurn.chatBubble.textContent = newText;
  if (userTurn.voiceBubble) userTurn.voiceBubble.textContent = newText;
  if (
    typeof userTurn.storeIndex === "number" &&
    userTurn.storeIndex >= 0 &&
    chatHistoryStore[userTurn.storeIndex] &&
    chatHistoryStore[userTurn.storeIndex].role === "user"
  ) {
    chatHistoryStore[userTurn.storeIndex].content = newText;
    saveChatHistoryToStorage();
  }
}

function ensureUnifiedBotTurn(existingTurn = null) {
  if (existingTurn) return existingTurn;
  const chatObj = chatEl ? createBotBubble(chatEl) : null;
  const voiceObj = voiceChatEl ? createBotBubble(voiceChatEl) : null;
  chatHistoryStore.push({ role: "assistant", content: "", ts: Date.now() });
  const storeIndex = chatHistoryStore.length - 1;
  return { chatObj, voiceObj, storeIndex };
}

function updateUnifiedBotTurnHtml(botTurn, fullText, persistNow = false) {
  const turn = ensureUnifiedBotTurn(botTurn);
  const safeHtml = formatSafeMessageHtml(fullText);
  if (turn.chatObj && turn.chatObj.bubble) {
    turn.chatObj.bubble.innerHTML = safeHtml;
  }
  if (turn.voiceObj && turn.voiceObj.bubble) {
    turn.voiceObj.bubble.innerHTML = safeHtml;
  }
  if (
    typeof turn.storeIndex === "number" &&
    turn.storeIndex >= 0 &&
    chatHistoryStore[turn.storeIndex] &&
    chatHistoryStore[turn.storeIndex].role === "assistant"
  ) {
    chatHistoryStore[turn.storeIndex].content = fullText;
  }
  if (persistNow) {
    saveChatHistoryToStorage();
  }
  scrollToBottom(chatEl);
  scrollToBottom(voiceChatEl);
  return turn;
}

function initHistory(syncBackend = true) {
  historyEpoch += 1;
  let loaded = [];
  try {
    const raw = localStorage.getItem(CHAT_HISTORY_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        loaded = parsed
          .filter((item) => item && typeof item === "object")
          .map((item) => ({
            role: item.role === "assistant" || item.role === "bot" ? "assistant" : item.role === "user" ? "user" : "",
            content: String(item.content || ""),
            ts: typeof item.ts === "number" ? item.ts : Date.now(),
          }))
          .filter((item) => (item.role === "user" || item.role === "assistant") && item.content.trim().length > 0)
          .slice(-MAX_STORED_HISTORY_MESSAGES);
      }
    }
  } catch (_e) {
    loaded = [];
    try {
      localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
    } catch (_ignore) {}
  }

  chatHistoryStore = loaded;
  clearVoiceDraftBubble();
  if (chatEl) chatEl.innerHTML = "";
  if (voiceChatEl) voiceChatEl.innerHTML = "";

  for (const item of chatHistoryStore) {
    if (item.role === "user") {
      if (chatEl) appendUserMessage(item.content, chatEl);
      if (voiceChatEl) appendUserMessage(item.content, voiceChatEl);
    } else if (item.role === "assistant") {
      const safeHtml = formatSafeMessageHtml(item.content);
      if (chatEl) {
        const cObj = createBotBubble(chatEl);
        if (cObj && cObj.bubble) cObj.bubble.innerHTML = safeHtml;
      }
      if (voiceChatEl) {
        const vObj = createBotBubble(voiceChatEl);
        if (vObj && vObj.bubble) vObj.bubble.innerHTML = safeHtml;
      }
    }
  }

  scrollToBottom(chatEl);
  scrollToBottom(voiceChatEl);

  if (syncBackend && chatHistoryStore.length > 0) {
    fetch("/api/history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: chatHistoryStore, replace_if_empty: false }),
    }).catch(() => {});
  }
}

window.addEventListener("storage", (e) => {
  if (e.key === CHAT_HISTORY_STORAGE_KEY) {
    initHistory(false);
  }
});

function stripVietnameseAccentsForMatch(str) {
  return String(str || "")
    .replace(/[ĐÐ]/g, "D")
    .replace(/[đð]/g, "d")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function isClearHistoryCommand(text) {
  const raw = String(text || "");
  if (!raw || raw.includes("\n") || raw.includes("`")) return false;
  let norm = stripVietnameseAccentsForMatch(raw)
    .toLowerCase()
    .replace(/^[\s,.?!;:"'“”‘’]+|[\s,.?!;:"'“”‘’]+$/g, "");
  norm = norm
    .replace(
      /^(?:e\s+|ey\s+|alo\s+|chao\s+|xin\s+chao\s+)?(?:wisp\s+)?(?:oi\s+)?(?:cho\s+(?:minh|em|anh|to|tui)\s+(?:hoi|xem|biet)\s+|cho\s+(?:hoi|xem|biet)\s+|hoi\s+)?/,
      ""
    )
    .replace(/\s+(?:nhi|vay|the|nhe|nha|di|a|ha|coi|voi|ban\s+oi|wisp\s+oi|wisp)+$/, "")
    .replace(/\s+/g, " ")
    .replace(/^[\s,.?!;:]+|[\s,.?!;:]+$/g, "");
  return [
    "xoa lich su",
    "clear",
    "reset",
    "xoa chat",
    "xoa hoi thoai",
    "xoa toan bo lich su",
  ].includes(norm);
}

/* ==========================================================================
   PHẦN 3: GIỌNG NÓI TIẾNG VIỆT NEURAL (/api/tts & INLINE STREAM) & SÓNG ÂM TRỰC TIẾP
   - Nhận trực tiếp gói âm thanh MP3 inline (`type: "audio"`) từ `/api/chat/stream` để phát TỨC THÌ (0ms trễ)
   - Phân tích phổ tần số thật (AnalyserNode) kèm bộ lọc thông dải (Band-pass) khử tạp âm nền
   ========================================================================== */

let ttsMasterInput = null;

function ensureSharedAudioContext() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  if (!sharedAudioCtx || sharedAudioCtx.state === "closed") {
    sharedAudioCtx = new AudioCtx();
    ttsAnalyser = null;
    ttsMasterInput = null;
  }
  if (sharedAudioCtx.state === "suspended") {
    sharedAudioCtx.resume().catch(() => {});
  }
  return sharedAudioCtx;
}

/**
 * Chuỗi Mastering Âm thanh Giọng nói chuẩn Podcast Studio (Google NotebookLM / ChatGPT Voice)
 */
function ensureStudioVoiceMasteringGraph(ctx) {
  if (ttsMasterInput && ttsAnalyser) {
    return ttsMasterInput;
  }
  const lowWarmth = ctx.createBiquadFilter();
  lowWarmth.type = "lowshelf";
  lowWarmth.frequency.value = 175;
  lowWarmth.gain.value = 2.8;

  const deHarsh = ctx.createBiquadFilter();
  deHarsh.type = "peaking";
  deHarsh.frequency.value = 2650;
  deHarsh.Q.value = 1.35;
  deHarsh.gain.value = -1.8;

  const studioAir = ctx.createBiquadFilter();
  studioAir.type = "highshelf";
  studioAir.frequency.value = 7500;
  studioAir.gain.value = 1.5;

  const compressor = ctx.createDynamicsCompressor();
  compressor.threshold.value = -20;
  compressor.knee.value = 18;
  compressor.ratio.value = 2.6;
  compressor.attack.value = 0.006;
  compressor.release.value = 0.16;

  ttsAnalyser = ctx.createAnalyser();
  ttsAnalyser.fftSize = 128;
  ttsAnalyser.smoothingTimeConstant = 0.72;

  lowWarmth.connect(deHarsh);
  deHarsh.connect(studioAir);
  studioAir.connect(compressor);
  compressor.connect(ttsAnalyser);
  ttsAnalyser.connect(ctx.destination);

  ttsMasterInput = lowWarmth;
  return ttsMasterInput;
}

let isTTSWorkerRunning = false;
let currentScheduledSourceNodes = [];
let scheduledEndTime = 0;
let currentStreamAbortController = null;
let ttsPlaybackStartedAtMs = 0;
let handsFreeBargeInSinceMs = 0;

function stopSpeaking() {
  ttsSessionToken += 1;
  ttsQueue = [];
  isTTSWorkerRunning = false;
  activeUtteranceCount = 0;
  isSpeakingTTS = false;
  ttsPulseBoost = 0;
  scheduledEndTime = 0;
  ttsPlaybackStartedAtMs = 0;
  handsFreeBargeInSinceMs = 0;

  for (const node of currentScheduledSourceNodes) {
    try {
      node.onended = null;
      node.stop(0);
      node.disconnect();
    } catch (_e) {}
  }
  currentScheduledSourceNodes = [];

  if (ttsCurrentAudio) {
    try {
      ttsCurrentAudio.onended = null;
      ttsCurrentAudio.onerror = null;
      ttsCurrentAudio.onplay = null;
      ttsCurrentAudio.pause();
      ttsCurrentAudio.src = "";
    } catch (_e) {}
    ttsCurrentAudio = null;
  }
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  updateVoiceMicBarUI();
}

/**
 * Điều phối ngắt lời Wisp (Barge-in) tức thì:
 * - Hủy kết nối stream server đang gửi (AbortController)
 * - Dừng ngay lập tức âm thanh đang phát (0ms)
 * - Mở khóa toàn bộ giao diện & ô nhập
 * - Kích hoạt lại Microphone ngay lập tức
 */
function interruptCurrentInteraction() {
  if (currentStreamAbortController) {
    try {
      currentStreamAbortController.abort();
    } catch (_e) {}
    currentStreamAbortController = null;
  }
  stopSpeaking();
  isSending = false;
  updateVoiceMicBarUI();
  if (sendBtn) {
    sendBtn.disabled = false;
    sendBtn.style.display = "";
  }
  const stopBtn = document.getElementById("stopBtn");
  if (stopBtn) {
    stopBtn.style.display = "none";
  }
  if (msgInput) {
    msgInput.disabled = false;
    if (currentAppMode === "chat") {
      msgInput.focus();
    }
  }
  syncIdleState();
  if (currentAppMode === "voice" && !voiceMicMuted) {
    setVoiceSubtitle("Hãy nói tiếp với Wisp…", true);
    startVoiceListening();
  }
}

async function fetchVietnameseTTSBlob(rawSegmentText, voiceId, token) {
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: rawSegmentText, voice: voiceId }),
    });
    if (token !== ttsSessionToken || !res.ok || res.status === 204) return null;
    return await res.blob();
  } catch (_err) {
    return null;
  }
}

function base64ToMp3Blob(b64Data) {
  try {
    const binary = atob(b64Data);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new Blob([bytes], { type: "audio/mpeg" });
  } catch (_e) {
    return null;
  }
}

function onSingleAudioNodeEnded(sourceNode) {
  const idx = currentScheduledSourceNodes.indexOf(sourceNode);
  if (idx !== -1) {
    currentScheduledSourceNodes.splice(idx, 1);
  }
  try {
    sourceNode.disconnect();
  } catch (_) {}

  activeUtteranceCount = ttsQueue.length + currentScheduledSourceNodes.length;

  if (currentScheduledSourceNodes.length === 0 && ttsQueue.length === 0) {
    scheduledEndTime = 0;
    isSpeakingTTS = false;
    activeUtteranceCount = 0;
    ttsPulseBoost = 0;
    ttsPlaybackStartedAtMs = 0;
    handsFreeBargeInSinceMs = 0;
    updateVoiceMicBarUI();
    if (!isSending) {
      syncIdleState();
      if (currentAppMode === "voice" && !voiceMicMuted) {
        setTimeout(() => {
          if (!isSpeakingTTS && !isSending) startVoiceListening();
        }, 180);
      }
    }
  }
}

/**
 * Phát âm thanh mượt mà không khoảng lặng (Gapless Playback 0ms) qua Web Audio API:
 * Giải mã MP3 trực tiếp bằng AudioContext.decodeAudioData() và lập lịch theo sharedAudioCtx.currentTime.
 */
async function playSingleAudioBlob(blob, token) {
  if (token !== ttsSessionToken || !blob || blob.size === 0) {
    return;
  }
  const ctx = ensureSharedAudioContext();
  if (!ctx) return;

  let audioBuffer;
  try {
    const arrayBuffer = await blob.arrayBuffer();
    if (token !== ttsSessionToken) return;
    audioBuffer = await ctx.decodeAudioData(arrayBuffer);
  } catch (_decodeErr) {
    return;
  }
  if (token !== ttsSessionToken || !audioBuffer) return;

  const masterIn = ensureStudioVoiceMasteringGraph(ctx);
  const sourceNode = ctx.createBufferSource();
  sourceNode.buffer = audioBuffer;

  const liveSpeed = getPlaybackRateNumber() || 1.0;
  try {
    sourceNode.playbackRate.value = liveSpeed;
  } catch (_) {}

  sourceNode.connect(masterIn);

  const duration = audioBuffer.duration / liveSpeed;
  const nextStartTime = Math.max(ctx.currentTime, scheduledEndTime);
  try {
    sourceNode.start(nextStartTime);
  } catch (_startErr) {
    return;
  }

  scheduledEndTime = nextStartTime + duration;
  currentScheduledSourceNodes.push(sourceNode);

  if (!ttsPlaybackStartedAtMs) {
    ttsPlaybackStartedAtMs = performance.now();
  }
  isSpeakingTTS = true;
  ttsPulseBoost = 0.55;
  lastSoundDetectedMs = performance.now();
  if (isRecognitionRunning) {
    stopVoiceListening();
  }
  updateVoiceMicBarUI();
  startAudioWaveVisualizer();
  setOrbVisualState("listening", "Wisp đang nói…", 1.24);

  sourceNode.onended = () => {
    if (token !== ttsSessionToken) {
      try {
        sourceNode.disconnect();
      } catch (_) {}
      return;
    }
    onSingleAudioNodeEnded(sourceNode);
  };
}

/**
 * Worker xử lý hàng đợi TTS:
 * Giải mã và lập lịch phát liên tục các vế câu với độ trễ chuyển câu chính xác 0ms.
 */
async function runTTSQueueWorker(token) {
  if (isTTSWorkerRunning) return;
  isTTSWorkerRunning = true;

  try {
    while (ttsQueue.length > 0 && token === ttsSessionToken) {
      const nextItem = ttsQueue.shift();
      activeUtteranceCount = ttsQueue.length + currentScheduledSourceNodes.length + 1;
      isSpeakingTTS = true;

      const blob = await nextItem.blobPromise;
      if (token !== ttsSessionToken) break;
      if (!blob || blob.size === 0) continue;

      await playSingleAudioBlob(blob, token);
    }
  } finally {
    if (token === ttsSessionToken) {
      isTTSWorkerRunning = false;
      if (currentScheduledSourceNodes.length === 0) {
        isSpeakingTTS = false;
        activeUtteranceCount = 0;
        ttsPulseBoost = 0;
        updateVoiceMicBarUI();
        if (!isSending) {
          syncIdleState();
          if (currentAppMode === "voice" && !voiceMicMuted) {
            setTimeout(() => {
              if (!isSpeakingTTS && !isSending) startVoiceListening();
            }, 180);
          }
        }
      }
    }
  }
}

/**
 * Đưa trực tiếp gói âm thanh MP3 base64 từ luồng `/api/chat/stream` vào hàng đợi phát TỨC THÌ (0ms chờ).
 */
function enqueueInlineAudioBase64(b64Data) {
  if (!b64Data) return;
  const blob = base64ToMp3Blob(b64Data);
  if (!blob || blob.size === 0) return;

  const token = ttsSessionToken;
  ttsQueue.push({ text: "[inline]", blobPromise: Promise.resolve(blob) });
  activeUtteranceCount = ttsQueue.length + (ttsCurrentAudio ? 1 : 0);
  isSpeakingTTS = true;
  updateVoiceMicBarUI();

  if (!isTTSWorkerRunning) {
    runTTSQueueWorker(token);
  }
}

function enqueueSpeechChunk(rawSegmentText, forceSpeak = false) {
  if (!forceSpeak && currentAppMode !== "voice") return;
  const trimmed = String(rawSegmentText || "").trim();
  if (!trimmed) return;

  const token = ttsSessionToken;
  const voiceId = selectedVoiceId || "hoaimy";
  const blobPromise = fetchVietnameseTTSBlob(trimmed, voiceId, token);

  ttsQueue.push({ text: trimmed, blobPromise });
  activeUtteranceCount = ttsQueue.length + (ttsCurrentAudio ? 1 : 0);
  isSpeakingTTS = true;
  updateVoiceMicBarUI();

  if (!isTTSWorkerRunning) {
    runTTSQueueWorker(token);
  }
}

/**
 * Dự phòng tách câu phía client nếu gọi qua `/api/chat` thường (khi không có inline audio từ `/api/chat/stream`).
 */
function feedStreamingTextToTTS(accumulatedText, isFinalFlush = false) {
  if (currentAppMode !== "voice") return;

  const speechSafeText = String(accumulatedText || "").replace(
    /(?:^|\n+)\s*(?:🌐\s*)?Nguồn:\s*[\s\S]*$/,
    ""
  );

  if (isFinalFlush) {
    const remaining = speechSafeText.slice(ttsSpokenCharIndex);
    ttsSpokenCharIndex = speechSafeText.length;
    if (remaining.trim()) {
      enqueueSpeechChunk(remaining);
    }
    return;
  }

  if (/^(?:🌤️|🕐|📅|👋|🤖|🧹)/.test(speechSafeText.trim())) {
    return;
  }

  const safeSentenceBoundary = /^([\s\S]{20,}?(?<![0-9])[.!?。]+[\s\n]+|[\s\S]{28,}?[,;:]\s+)/;

  while (true) {
    const unspoken = speechSafeText.slice(ttsSpokenCharIndex);
    const match = unspoken.match(safeSentenceBoundary);
    if (!match || !match[1]) break;
    const chunk = match[1];
    ttsSpokenCharIndex += chunk.length;
    enqueueSpeechChunk(chunk);
  }
}

function waitForSpeechToFinish(maxWaitMs = 60000) {
  return new Promise((resolve) => {
    const start = performance.now();
    function check() {
      const speaking =
        isSpeakingTTS ||
        isTTSWorkerRunning ||
        activeUtteranceCount > 0 ||
        currentScheduledSourceNodes.length > 0 ||
        ttsCurrentAudio !== null ||
        ttsQueue.length > 0;
      if (!speaking || currentAppMode !== "voice" || performance.now() - start > maxWaitMs) {
        isSpeakingTTS = false;
        activeUtteranceCount = 0;
        ttsPulseBoost = 0;
        updateVoiceMicBarUI();
        resolve();
      } else {
        setTimeout(check, 50);
      }
    }
    check();
  });
}

const VOICE_PREVIEW_SAMPLES = {
  hoaimy: "Chào bạn! Mình là Wisp, rất vui được hỗ trợ bạn.",
  namminh: "Chào bạn! Mình là Wisp, bạn cần mình hỗ trợ gì hôm nay cứ nói nhé.",
  hoaimy_fast: "Chào bạn! Mình là Wisp, sẵn sàng hỗ trợ bạn ngay đây.",
  namminh_deep: "Chào bạn, mình là Wisp. Cần hỏi thời tiết hay viết code cứ bảo mình nhé.",
  ava_multilingual: "Chào bạn! Mình là Wisp, trợ lý AI song ngữ của bạn.",
};

/**
 * Phát câu chào mẫu tức thì (đã được pre-cache trên RAM của server) khi người dùng chọn giọng mới hoặc bấm nút Nghe thử.
 */
function previewSelectedVoice() {
  stopSpeaking();
  const vid = selectedVoiceId || "hoaimy";
  const sampleText = VOICE_PREVIEW_SAMPLES[vid] || VOICE_PREVIEW_SAMPLES.hoaimy;
  if (currentAppMode === "voice") {
    setVoiceSubtitle(sampleText, false);
  }
  enqueueSpeechChunk(sampleText, true);
}

if (voiceSelectEl) {
  if (!VOICE_PREVIEW_SAMPLES[selectedVoiceId]) {
    selectedVoiceId = "hoaimy";
    voiceSelectEl.value = "hoaimy";
  }
  voiceSelectEl.addEventListener("change", () => {
    selectedVoiceId = voiceSelectEl.value || "hoaimy";
    localStorage.setItem(VOICE_STORAGE_KEY, selectedVoiceId);
    previewSelectedVoice();
  });
}

if (voiceSpeedSelectEl) {
  voiceSpeedSelectEl.addEventListener("change", () => {
    const val = voiceSpeedSelectEl.value;
    selectedVoiceSpeed = VALID_VOICE_SPEEDS.includes(val) ? val : "1.15";
    localStorage.setItem(VOICE_SPEED_KEY, selectedVoiceSpeed);
    const liveSpeed = getPlaybackRateNumber();
    if (currentScheduledSourceNodes.length > 0) {
      for (const node of currentScheduledSourceNodes) {
        try {
          node.playbackRate.value = liveSpeed;
        } catch (_e) {}
      }
    } else if (ttsCurrentAudio) {
      ttsCurrentAudio.defaultPlaybackRate = liveSpeed;
      ttsCurrentAudio.playbackRate = liveSpeed;
    } else if (!isSending) {
      previewSelectedVoice();
    }
  });
}

if (voicePreviewBtn) {
  voicePreviewBtn.addEventListener("click", () => {
    previewSelectedVoice();
  });
}

const HANDS_FREE_BARGE_IN_GRACE_MS = 320;
const HANDS_FREE_BARGE_IN_SUSTAIN_MS = 240;
const HANDS_FREE_BARGE_IN_DELTA = 26;

/**
 * Tự động ngắt lời không cần chạm (Hands-free Acoustic Voice Barge-in):
 * Khi Wisp đang nói ở Chế độ Nói, nếu Microphone (đã bật WebRTC echoCancellation + Band-pass 140..3400Hz)
 * thu được giọng người dùng vượt ngưỡng liên tục >= 240ms (bỏ qua 320ms đầu câu và tiếng ho/click ngắn),
 * tự động gọi interruptCurrentInteraction() để dừng phát âm, hủy stream và bật Mic nghe ngay.
 */
function evaluateHandsFreeVoiceBargeIn(micVoiceAvg, micProfile, nowMs = performance.now()) {
  const ttsActive = isSpeakingTTS || currentScheduledSourceNodes.length > 0;
  if (currentAppMode !== "voice" || voiceMicMuted || !ttsActive) {
    handsFreeBargeInSinceMs = 0;
    return false;
  }
  if (ttsPlaybackStartedAtMs > 0 && nowMs - ttsPlaybackStartedAtMs < HANDS_FREE_BARGE_IN_GRACE_MS) {
    handsFreeBargeInSinceMs = 0;
    return false;
  }
  const baseGate = micProfile && typeof micProfile.noiseGate === "number" ? micProfile.noiseGate : 38;
  const bargeThreshold = baseGate + HANDS_FREE_BARGE_IN_DELTA;
  if (micVoiceAvg > bargeThreshold) {
    if (handsFreeBargeInSinceMs <= 0) {
      handsFreeBargeInSinceMs = nowMs;
      return false;
    }
    if (nowMs - handsFreeBargeInSinceMs >= HANDS_FREE_BARGE_IN_SUSTAIN_MS) {
      handsFreeBargeInSinceMs = 0;
      interruptCurrentInteraction();
      return true;
    }
    return false;
  }
  handsFreeBargeInSinceMs = 0;
  return false;
}

/**
 * Vẽ sóng âm thanh trực tiếp lên `#voiceWaveCanvas` với ngưỡng lọc tạp âm (Noise Gate) đã tinh chỉnh
 * và giám sát tự ngắt lời không cần chạm khi Wisp đang nói.
 */
function startAudioWaveVisualizer() {
  if (waveLoopRaf) return;

  const freqData = new Uint8Array(64);
  const micFreqData = new Uint8Array(64);
  let phase = 0;

  function renderWaveLoop(now) {
    if (currentAppMode !== "voice" && !isSpeakingTTS) {
      waveLoopRaf = 0;
      return;
    }

    const micProfile = getMicProfile();
    let rawLevel = 0;
    const hasActiveTtsAudio =
      isSpeakingTTS &&
      (currentScheduledSourceNodes.length > 0 || Boolean(ttsCurrentAudio && !ttsCurrentAudio.paused));

    if (hasActiveTtsAudio) {
      if (ttsAnalyser) {
        ttsAnalyser.getByteFrequencyData(freqData);
        let sum = 0;
        const count = Math.min(32, freqData.length);
        for (let i = 1; i < count; i++) {
          sum += freqData[i];
        }
        const avg = sum / Math.max(1, count - 1);
        rawLevel = Math.max(0.16, Math.min(0.88, avg / 110));
      } else {
        const synthMod = 0.36 + 0.25 * Math.sin(now * 0.016) * Math.cos(now * 0.009) + ttsPulseBoost * 0.28;
        rawLevel = Math.max(0.2, Math.min(0.85, synthMod));
      }
      ttsPulseBoost *= 0.9;
      lastSoundDetectedMs = now;

      // Giám sát giọng nói người dùng chen ngang không cần chạm (Hands-free Barge-in)
      if (micAnalyser && currentAppMode === "voice" && !voiceMicMuted) {
        micAnalyser.getByteFrequencyData(micFreqData);
        let micSum = 0;
        const startBin = 3;
        const endBin = Math.min(22, micFreqData.length);
        for (let i = startBin; i < endBin; i++) {
          micSum += micFreqData[i];
        }
        const micAvg = micSum / Math.max(1, endBin - startBin);
        if (evaluateHandsFreeVoiceBargeIn(micAvg, micProfile, now)) {
          waveLoopRaf = requestAnimationFrame(renderWaveLoop);
          return;
        }
      }
    } else if (micAnalyser && currentAppMode === "voice" && !voiceMicMuted && !isSending) {
      micAnalyser.getByteFrequencyData(freqData);
      let sum = 0;
      // Chỉ lấy dải tần cốt lõi của giọng nói con người (bins 3..22 ~ 350Hz - 2600Hz)
      const startBin = 3;
      const endBin = Math.min(22, freqData.length);
      for (let i = startBin; i < endBin; i++) {
        sum += freqData[i];
      }
      const avg = sum / Math.max(1, endBin - startBin);
      // Áp dụng Noise Gate cao (mặc định 38 ở mức "low") để triệt tiêu tạp âm quạt gió, điều hòa, gõ phím
      if (avg > micProfile.noiseGate) {
        rawLevel = Math.min(0.9, (avg - micProfile.noiseGate) / micProfile.levelScale);
        if (rawLevel > micProfile.orbTrigger) {
          lastSoundDetectedMs = now;
        }
      }
    }

    // Làm mượt biên độ âm thanh (tăng độ đằm, giảm độ giật do tạp âm ngắn)
    const smoothing = rawLevel > smoothAudioLevel ? 0.2 : 0.14;
    smoothAudioLevel += (rawLevel - smoothAudioLevel) * smoothing;
    if (smoothAudioLevel < 0.01) smoothAudioLevel = 0;

    // 2. Đồng bộ trạng thái quả cầu Thinking Orb:
    if (!isSending || isSpeakingTTS) {
      const hasActiveSound =
        isSpeakingTTS ||
        smoothAudioLevel > micProfile.orbTrigger ||
        now - lastSoundDetectedMs < 340;
      if (hasActiveSound) {
        const waveEnergy = Math.max(0.95, Math.min(1.45, 0.92 + smoothAudioLevel * 0.65));
        const caption = isSpeakingTTS ? "Wisp đang nói…" : "Đang nghe bạn nói…";
        if (centerOrb) {
          if (centerOrb.getState() !== "listening") {
            setOrbVisualState("listening", caption, waveEnergy);
          } else {
            centerOrb.setEnergy(waveEnergy);
            if (typingText && typingText.textContent !== caption) {
              typingText.textContent = caption;
            }
          }
        }
      } else if (!isSending && centerOrb && centerOrb.getState() === "listening") {
        syncIdleState();
      }
    }

    // 3. Vẽ sóng âm đa hài (Multi-harmonic Sound Wave) lên #voiceWaveCanvas
    if (voiceWaveCanvas && currentAppMode === "voice") {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const cssW = 360;
      const cssH = 64;
      if (voiceWaveCanvas.width !== Math.round(cssW * dpr)) {
        voiceWaveCanvas.width = Math.round(cssW * dpr);
        voiceWaveCanvas.height = Math.round(cssH * dpr);
      }
      const ctx = voiceWaveCanvas.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, cssW, cssH);

        const dark = isDarkMode();
        const baseRgb = dark ? "244, 244, 246" : "15, 23, 42";
        const midY = cssH / 2;
        phase += 0.068 + smoothAudioLevel * 0.13;

        if (smoothAudioLevel < 0.035) {
          ctx.beginPath();
          ctx.strokeStyle = `rgba(${baseRgb}, 0.22)`;
          ctx.lineWidth = 1.5;
          ctx.moveTo(24, midY);
          ctx.lineTo(cssW - 24, midY);
          ctx.stroke();
        } else {
          const maxAmp = (cssH * 0.38) * Math.min(1, smoothAudioLevel * 1.1);
          const waves = [
            { freq: 0.045, speed: 1.0, ampMul: 1.0, alpha: 0.88, width: 2.2 },
            { freq: 0.068, speed: -1.35, ampMul: 0.65, alpha: 0.45, width: 1.5 },
            { freq: 0.031, speed: 0.7, ampMul: 0.42, alpha: 0.26, width: 1.2 },
          ];

          for (const w of waves) {
            ctx.beginPath();
            ctx.strokeStyle = `rgba(${baseRgb}, ${w.alpha})`;
            ctx.lineWidth = w.width;
            const startX = 16;
            const endX = cssW - 16;
            const span = endX - startX;

            for (let x = startX; x <= endX; x += 2) {
              const normX = (x - startX) / span;
              const envelope = Math.sin(normX * Math.PI) ** 1.6;
              const y =
                midY +
                Math.sin(x * w.freq + phase * w.speed) *
                  Math.cos(x * 0.015 - phase * 0.4) *
                  maxAmp *
                  w.ampMul *
                  envelope;
              if (x === startX) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
            }
            ctx.stroke();
          }
        }
      }
    }

    waveLoopRaf = requestAnimationFrame(renderWaveLoop);
  }

  waveLoopRaf = requestAnimationFrame(renderWaveLoop);
}

async function startMicAudioMeter() {
  startAudioWaveVisualizer();
  if (micAnalyser || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false, // Tắt autoGainControl để trình duyệt không tự kích đại tạp âm phòng khi im lặng
      },
    });
    const ctx = ensureSharedAudioContext();
    if (!ctx) return;
    const source = ctx.createMediaStreamSource(micStream);

    // Bộ lọc thông dải (Band-pass 140Hz - 3400Hz) cắt bỏ tiếng ù quạt máy tính (<140Hz) và tiếng click chuột/phím (>3400Hz)
    const highpass = ctx.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = 140;

    const lowpass = ctx.createBiquadFilter();
    lowpass.type = "lowpass";
    lowpass.frequency.value = 3400;

    micAnalyser = ctx.createAnalyser();
    micAnalyser.fftSize = 128;
    micAnalyser.smoothingTimeConstant = 0.82;

    source.connect(highpass);
    highpass.connect(lowpass);
    lowpass.connect(micAnalyser);
  } catch (_err) {
    // Nếu không lấy được raw stream thì SpeechRecognition vẫn chạy bình thường
  }
}

function stopMicAudioMeter() {
  if (waveLoopRaf && !isSpeakingTTS) {
    cancelAnimationFrame(waveLoopRaf);
    waveLoopRaf = 0;
  }
  smoothAudioLevel = 0;
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  micAnalyser = null;
}

// Các từ nối, giới từ, đại từ hoặc động từ bỏ lửng ở cuối câu báo hiệu người dùng ĐANG NÓI DỞ, chưa hết ý
// Đã loại bỏ: không, chưa, đi, ơi (là các từ kết câu trọn vẹn trong tiếng Việt)
const TRAILING_INCOMPLETE_SPEECH_RE =
  /(?<![\p{L}\p{N}_])(ở|tại|là|với|và|nhưng|mà|thì|nếu|vì|do|bởi|để|cho|của|hay|hoặc|như|ví dụ|còn|về|trong|ngoài|trên|dưới|khi|lúc|từ|đến|tới|sang|vào|ra|mình|tôi|tao|tớ|anh|em|bạn|wisp|này|rằng|có|đang|sẽ|vừa|mới|muốn|cần|định|tính|hỏi|xem|thử|nặng|cao|tuổi|sinh|năm|làm|nghề|ăn|mặc|uống)\s*$/iu;

// Chỉ những câu lệnh ngắn độc lập hoàn chỉnh (khớp toàn bộ câu) mới chốt ở mức ~850ms
const STANDALONE_SHORT_COMMAND_RE =
  /^(?:thời tiết(?:\s+(?:hôm nay|ngày mai|sài gòn|hà nội|đà nẵng|đà lạt|cần thơ|hải phòng|nha trang|vũng tàu|huế))?|mấy giờ(?:\s+rồi)?|bây giờ\s+mấy\s+giờ|hôm nay\s+(?:thứ mấy|ngày mấy)|xóa lịch sử|help|trợ giúp)$/i;

function computeSmartSpeechWaitMs(transcriptText, hasInterim) {
  const micProfile = getMicProfile();
  const clean = String(transcriptText || "").trim();
  const wordCount = clean ? clean.split(/\s+/).length : 0;

  if (!hasInterim && STANDALONE_SHORT_COMMAND_RE.test(clean)) {
    return 850;
  }

  let baseWait = hasInterim ? micProfile.silenceInterimMs : micProfile.silenceFinalMs;

  if (hasInterim) {
    // Khi đang nói dở (interim results): người dùng có thể đang ngẫm nghĩ tiếp
    if (TRAILING_INCOMPLETE_SPEECH_RE.test(clean) || wordCount <= 3) {
      baseWait += 1100;
    } else if (wordCount >= 10) {
      baseWait += 450;
    }
  } else {
    // Khi đã có isFinal: chốt nhanh trong khoảng 950–1150ms (mặc định 1050ms)
    // Chỉ thêm buffer nếu câu rõ ràng kết thúc bằng từ nối/bỏ lửng
    if (TRAILING_INCOMPLETE_SPEECH_RE.test(clean)) {
      baseWait += 350;
    }
  }

  return baseWait;
}

function flushVoiceDraftAndSend() {
  clearTimeout(silenceSendTimer);
  const textToSend = normalizeVietnameseClientInput(currentLiveTranscript || pendingVoiceDraft);
  pendingVoiceDraft = "";
  currentLiveTranscript = "";
  if (
    textToSend &&
    !isNoiseFillerTranscript(textToSend) &&
    currentAppMode === "voice" &&
    !isSending &&
    !isSpeakingTTS
  ) {
    stopVoiceListening(true);
    sendMessage(textToSend);
  } else {
    clearVoiceDraftBubble();
  }
}

function scheduleVoiceAutoSend(waitMs) {
  clearTimeout(silenceSendTimer);
  silenceSendTimer = setTimeout(() => {
    if (currentAppMode !== "voice" || voiceMicMuted || isSending || isSpeakingTTS) {
      return;
    }
    const now = performance.now();
    const micProfile = getMicProfile();
    // Nếu người dùng vẫn đang phát ra âm thanh vào Mic hoặc vừa mới nói trong 700ms qua -> gia hạn thêm, KHÔNG ngắt lời!
    const stillSpeakingVoice =
      smoothAudioLevel > Math.max(0.06, micProfile.orbTrigger * 0.65) ||
      now - lastSoundDetectedMs < 700 ||
      now - lastSpeechActivityMs < 550;

    if (stillSpeakingVoice) {
      scheduleVoiceAutoSend(650);
      return;
    }

    flushVoiceDraftAndSend();
  }, waitMs);
}

function initSpeechRecognition() {
  if (speechRec) return speechRec;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return null;

  const rec = new SpeechRecognition();
  rec.lang = "vi-VN";
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 5;

  rec.onstart = () => {
    isRecognitionRunning = true;
    updateVoiceMicBarUI();
  };

  rec.onresult = (event) => {
    if (
      currentAppMode !== "voice" ||
      voiceMicMuted ||
      isSending ||
      isSpeakingTTS ||
      isTTSWorkerRunning ||
      !isRecognitionRunning
    ) {
      return;
    }

    let sessionFinal = "";
    let sessionInterim = "";
    for (let i = 0; i < event.results.length; i++) {
      const bestText = pickBestVietnameseAlternative(event.results[i]);
      if (!bestText) continue;
      if (event.results[i].isFinal) {
        sessionFinal = mergeSpeechSegments(sessionFinal, bestText);
      } else {
        sessionInterim = mergeSpeechSegments(sessionInterim, bestText);
      }
    }

    const combinedSession = mergeSpeechSegments(sessionFinal, sessionInterim);
    const liveDisplay = mergeSpeechSegments(pendingVoiceDraft, combinedSession);
    if (!liveDisplay) {
      return;
    }

    currentLiveTranscript = liveDisplay;
    lastSpeechActivityMs = performance.now();
    const hasInterim = Boolean(sessionInterim && sessionInterim.trim());
    if (hasInterim) {
      lastSoundDetectedMs = lastSpeechActivityMs;
      smoothAudioLevel = Math.max(smoothAudioLevel, 0.24);
    }

    setVoiceSubtitle(`“${liveDisplay}”`, false);
    ensureVoiceDraftBubble(liveDisplay);
    updateVoiceMicBarUI();
    setOrbVisualState("listening", "Đang nghe bạn nói… (Chạm để gửi ngay)", 1.18);

    const waitMs = computeSmartSpeechWaitMs(liveDisplay, hasInterim);
    scheduleVoiceAutoSend(waitMs);
  };

  rec.onerror = (event) => {
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      voiceMicMuted = true;
      updateVoiceMicBarUI();
      setOrbVisualState("breathing", "Chưa cấp quyền Microphone", 0.85);
      setVoiceSubtitle("⚠️ Trình duyệt chưa được cấp quyền Microphone. Hãy cho phép Mic trên thanh địa chỉ nhé.", true);
    }
  };

  rec.onend = () => {
    isRecognitionRunning = false;
    // Nếu phiên nhận diện của trình duyệt tự ngắt khi người dùng đang nghỉ lấy hơi mà câu nháp vẫn chưa gửi -> bảo toàn vào pendingVoiceDraft!
    if (currentLiveTranscript && !isSending && !isSpeakingTTS) {
      pendingVoiceDraft = currentLiveTranscript;
    }
    updateVoiceMicBarUI();
    if (
      currentAppMode === "voice" &&
      !voiceMicMuted &&
      !isSending &&
      !isSpeakingTTS &&
      !isTTSWorkerRunning
    ) {
      setTimeout(() => {
        startVoiceListening(true);
      }, 160);
    }
  };

  speechRec = rec;
  return rec;
}

function startVoiceListening(preserveDraft = false) {
  if (
    currentAppMode !== "voice" ||
    voiceMicMuted ||
    isSending ||
    isSpeakingTTS ||
    isTTSWorkerRunning
  ) {
    return;
  }
  const rec = initSpeechRecognition();
  if (!rec) {
    setVoiceSubtitle("⚠️ Trình duyệt này không hỗ trợ nhận diện giọng nói (hãy dùng Microsoft Edge hoặc Chrome).", true);
    return;
  }
  if (!isRecognitionRunning) {
    try {
      if (!preserveDraft && !currentLiveTranscript) {
        pendingVoiceDraft = "";
      }
      rec.start();
    } catch (_e) {}
  }
  updateVoiceMicBarUI();
}

function stopVoiceListening(keepDraftBubble = false) {
  clearTimeout(silenceSendTimer);
  pendingVoiceDraft = "";
  currentLiveTranscript = "";
  if (!keepDraftBubble) {
    clearVoiceDraftBubble();
  }
  isRecognitionRunning = false;
  if (speechRec) {
    try {
      speechRec.abort();
    } catch (_e) {}
  }
  updateVoiceMicBarUI();
}

/**
 * Chuyển đổi giữa 2 chế độ:
 * - "chat": [Quả cầu Wisp (50% Trái) | Khung Chat văn bản (50% Phải)]
 * - "voice": [Khung Chat Giọng Nói trượt ra (50% Trái) | Quả cầu Wisp trượt sang (50% Phải)]
 */
function switchAppMode(mode) {
  if (mode !== "chat" && mode !== "voice") return;
  if (isSending || isSpeakingTTS || currentScheduledSourceNodes.length > 0) {
    interruptCurrentInteraction();
  }
  currentAppMode = mode;

  if (appLayout) {
    appLayout.setAttribute("data-mode", mode);
  }
  if (modeChatBtn) {
    modeChatBtn.classList.toggle("active", mode === "chat");
  }
  if (modeVoiceBtn) {
    modeVoiceBtn.classList.toggle("active", mode === "voice");
  }

  if (mode === "voice") {
    ensureSharedAudioContext();
    voiceMicMuted = false;
    setVoiceSubtitle("Hãy nói gì đó với Wisp…", true);
    syncIdleState();
    startMicAudioMeter();
    startVoiceListening();
    scrollToBottom(voiceChatEl);
  } else {
    stopVoiceListening();
    stopMicAudioMeter();
    stopSpeaking();
    syncIdleState();
    scrollToBottom(chatEl);
    if (msgInput && !isSending) {
      setTimeout(() => msgInput.focus(), 150);
    }
  }
}

/* ==========================================================================
   PHẦN 4: LUỒNG GỬI TIN NHẮN, STREAMING TOKEN & PHÁT ÂM THANH TỨC THÌ (0MS DELAY)
   ========================================================================== */

function adaptOrbToLiveTokenStream(accumulatedText, tokensPerSec) {
  const energy = Math.max(0.95, Math.min(1.45, 0.9 + tokensPerSec / 45));
  if (currentAppMode === "voice") {
    setOrbVisualState("listening", "Wisp đang nói…", Math.max(1.12, energy));
    return;
  }

  const fenceCount = (accumulatedText.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) {
    setOrbVisualState("working", "Đang viết mã nguồn…", energy * 1.08);
    return;
  }

  const recentWindow = accumulatedText.slice(-180);
  const bulletMatches = recentWindow.match(/(?:^|\n)\s*(?:[-*•]|\d+\.)\s+/g);
  if (bulletMatches && bulletMatches.length >= 2) {
    setOrbVisualState("weaving", "Đang hệ thống hóa nội dung…", energy);
    return;
  }

  setOrbVisualState("composing", STATE_DESCRIPTIONS.composing, energy);
}

const MSG_INPUT_MIN_HEIGHT = 36;
const MSG_INPUT_MAX_HEIGHT = 120;

function autoResizeMsgInput() {
  if (!msgInput) return;
  msgInput.style.height = "auto";
  if (!msgInput.value) {
    msgInput.style.height = `${MSG_INPUT_MIN_HEIGHT}px`;
    msgInput.style.overflowY = "hidden";
    return;
  }
  const scrollH = msgInput.scrollHeight || MSG_INPUT_MIN_HEIGHT;
  const clamped = Math.min(MSG_INPUT_MAX_HEIGHT, Math.max(MSG_INPUT_MIN_HEIGHT, scrollH));
  msgInput.style.height = `${clamped}px`;
  msgInput.style.overflowY = scrollH > MSG_INPUT_MAX_HEIGHT ? "auto" : "hidden";
}

function renderBotMessageInstant(fullText, existingBotTurn = null) {
  const turn = updateUnifiedBotTurnHtml(existingBotTurn, fullText, true);
  if (currentAppMode === "voice") {
    setVoiceSubtitle(fullText, false);
  }
  return turn;
}

async function clearAllConversationHistory() {
  historyEpoch += 1;
  if (isSending || isSpeakingTTS || isTTSWorkerRunning || currentScheduledSourceNodes.length > 0) {
    interruptCurrentInteraction();
  }
  clearVoiceDraftBubble();
  pendingVoiceDraft = "";
  currentLiveTranscript = "";
  chatHistoryStore = [];
  try {
    localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
  } catch (_e) {}

  if (chatEl) {
    chatEl.innerHTML = "";
  }
  if (voiceChatEl) {
    voiceChatEl.innerHTML = "";
  }
  if (msgInput) {
    msgInput.value = "";
    autoResizeMsgInput();
  }
  prevInputLength = 0;
  consecutiveDeletes = 0;

  let serverMessage = "Đã xóa toàn bộ lịch sử hội thoại";
  try {
    const res = await fetch("/api/history", { method: "DELETE" });
    if (res.ok) {
      const data = await res.json();
      if (data && typeof data.message === "string" && data.message.trim()) {
        serverMessage = data.message.trim();
      }
    }
  } catch (_e) {}

  showAssistantMemoryToast(`🧹 ${serverMessage}`);
  if (currentAppMode === "voice") {
    setVoiceSubtitle(`${serverMessage}. Hãy nói gì đó với Wisp…`, true);
    if (!voiceMicMuted) {
      setTimeout(() => {
        startVoiceListening();
      }, 150);
    }
  } else if (msgInput) {
    msgInput.focus();
  }
  syncIdleState();
}

async function sendMessage(presetText) {
  if (isSending) return;

  const rawText = typeof presetText === "string" ? presetText : (msgInput ? msgInput.value : "");
  const message = normalizeVietnameseClientInput(rawText);
  if (!message) {
    if (currentAppMode === "chat" && msgInput) msgInput.focus();
    return;
  }

  // Nếu người dùng gõ hoặc nói lệnh xóa lịch sử -> đồng bộ xóa cả UI (#chat + #voiceChat), localStorage và Backend
  if (isClearHistoryCommand(message)) {
    await clearAllConversationHistory();
    return;
  }

  const targetMode = currentAppMode;
  const shouldSpeakReply = targetMode === "voice";
  const myHistoryEpoch = historyEpoch;
  const streamAbortController = new AbortController();

  clearTimeout(typingIdleTimer);
  clearTimeout(typingShapeTimer);
  clearTimeout(previewResetTimer);
  stopVoiceListening(true);
  stopSpeaking();
  ttsSpokenCharIndex = 0;

  const userTurn = appendUnifiedUserTurn(message, true);
  if (targetMode === "voice") {
    setVoiceSubtitle(`“${message}”`, false);
  }

  if (typeof presetText !== "string" && msgInput) {
    msgInput.value = "";
    autoResizeMsgInput();
  } else if (msgInput && normalizeVietnameseClientInput(msgInput.value) === message) {
    msgInput.value = "";
    autoResizeMsgInput();
  }
  prevInputLength = 0;
  consecutiveDeletes = 0;

  isSending = true;
  currentStreamAbortController = streamAbortController;
  updateVoiceMicBarUI();
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.style.display = "none";
  }
  const stopBtn = document.getElementById("stopBtn");
  if (stopBtn) {
    stopBtn.style.display = "inline-flex";
  }
  if (msgInput) msgInput.disabled = true;

  let botTurn = null;
  let accumulatedText = "";
  let streamedTokenCount = 0;
  let firstTokenMs = 0;
  let receivedServerInlineAudio = false;

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      signal: streamAbortController.signal,
      body: JSON.stringify({
        message,
        voice: selectedVoiceId || "hoaimy",
        speak: shouldSpeakReply,
        prefs: getAiPrefs(),
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error("Stream unavailable");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (myHistoryEpoch !== historyEpoch || streamAbortController.signal.aborted) break;
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIdx;
      while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
        if (myHistoryEpoch !== historyEpoch || streamAbortController.signal.aborted) break;
        const line = buffer.slice(0, newlineIdx).trim();
        buffer = buffer.slice(newlineIdx + 1);
        if (!line) continue;

        let evt;
        try {
          evt = JSON.parse(line);
        } catch (_e) {
          continue;
        }

        if (evt.type === "normalized" && typeof evt.text === "string" && evt.text.trim()) {
          updateUnifiedUserTurnText(userTurn, evt.text);
          if (targetMode === "voice" && streamedTokenCount === 0) {
            setVoiceSubtitle(`“${evt.text}”`, false);
          }
        } else if (evt.type === "profile_updated" && evt.profile) {
          localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(evt.profile));
          syncUserProfileUI(evt.profile);
          const learned = evt.learned || {};
          const labels = [];
          if (learned.name) labels.push(`Tên: ${learned.name}`);
          if (learned.age) labels.push(`Tuổi: ${learned.age}`);
          if (learned.height_cm) labels.push(`Cao: ${learned.height_cm}cm`);
          if (learned.weight_kg) labels.push(`Nặng: ${learned.weight_kg}kg`);
          if (learned.city) labels.push(`Nơi ở: ${learned.city}`);
          if (learned.job) labels.push(`Công việc: ${learned.job}`);
          if (learned.notes) labels.push(`Ghi chú mới`);
          if (labels.length > 0) {
            showAssistantMemoryToast(`✨ Wisp đã nhớ vào Hồ sơ: ${labels.join(" · ")}`);
          }
        } else if (evt.type === "memory_updated") {
          if (evt.counts) {
            syncMemoryCountsUI(evt.counts);
          }
          if (typeof evt.toast === "string" && evt.toast.trim()) {
            showAssistantMemoryToast(evt.toast.trim());
          }
          fetchMemoriesState();
        } else if (evt.type === "state" && evt.state) {
          if (!isSpeakingTTS) {
            setOrbVisualState(evt.state, evt.label, 1.08);
          }
        } else if (evt.type === "audio" && typeof evt.b64 === "string" && evt.b64) {
          // Nhận trực tiếp gói âm thanh MP3 đã tổng hợp sẵn từ luồng server -> Phát NGAY LẬP TỨC (0ms trễ)!
          receivedServerInlineAudio = true;
          if (currentAppMode === "voice") {
            enqueueInlineAudioBase64(evt.b64);
          }
        } else if (evt.type === "token" && typeof evt.delta === "string") {
          if (!firstTokenMs) {
            firstTokenMs = performance.now();
          }

          streamedTokenCount += 1;
          accumulatedText += evt.delta;

          botTurn = updateUnifiedBotTurnHtml(botTurn, accumulatedText, false);
          if (currentAppMode === "voice") {
            setVoiceSubtitle(accumulatedText, false);
          }
          const elapsedSec = Math.max(0.08, (performance.now() - firstTokenMs) / 1000);
          const tokPerSec = streamedTokenCount / elapsedSec;
          adaptOrbToLiveTokenStream(accumulatedText, tokPerSec);
        } else if (evt.type === "done" && typeof evt.reply === "string" && evt.reply.trim()) {
          // Bảo vệ không để sự kiện done làm giật/cắt ngắn nội dung người dùng vừa thấy hoặc vừa nghe
          if (!accumulatedText.trim() || evt.reply.length >= accumulatedText.length) {
            accumulatedText = evt.reply;
          }
          botTurn = updateUnifiedBotTurnHtml(botTurn, accumulatedText, true);
          if (currentAppMode === "voice") {
            setVoiceSubtitle(accumulatedText, false);
          }
        }
      }
    }

    if (myHistoryEpoch !== historyEpoch) {
      return;
    }
    if (streamAbortController.signal.aborted) {
      saveChatHistoryToStorage();
      return;
    }

    if (!accumulatedText.trim()) {
      botTurn = renderBotMessageInstant(FALLBACK_ERROR, botTurn);
    } else {
      saveChatHistoryToStorage();
      if (shouldSpeakReply && currentAppMode === "voice" && !receivedServerInlineAudio) {
        feedStreamingTextToTTS(accumulatedText, true);
      }
    }
  } catch (_err) {
    if (myHistoryEpoch !== historyEpoch) {
      return;
    }
    if ((_err && _err.name === "AbortError") || streamAbortController.signal.aborted) {
      // Bị người dùng ngắt lời (Barge-in)! Lưu phần đã stream (nếu có) và không gọi fallback /api/chat
      saveChatHistoryToStorage();
      return;
    }
    try {
      setOrbVisualState("connecting", STATE_DESCRIPTIONS.connecting, 1);
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: streamAbortController.signal,
        body: JSON.stringify({ message, prefs: getAiPrefs() }),
      });
      if (myHistoryEpoch !== historyEpoch || streamAbortController.signal.aborted) {
        return;
      }
      const data = res.ok ? await res.json() : null;
      if (myHistoryEpoch !== historyEpoch || streamAbortController.signal.aborted) {
        return;
      }
      if (data && data.profile) {
        localStorage.setItem(USER_PROFILE_KEY, JSON.stringify(data.profile));
        syncUserProfileUI(data.profile);
      }
      if (data && data.memory_counts) {
        syncMemoryCountsUI(data.memory_counts);
      }
      if (data && typeof data.normalized === "string" && data.normalized.trim()) {
        updateUnifiedUserTurnText(userTurn, data.normalized);
      }
      const reply = data && typeof data.reply === "string" && data.reply.trim() ? data.reply : FALLBACK_ERROR;
      const stateForReply = currentAppMode === "voice" ? "listening" : "composing";
      setOrbVisualState(stateForReply, STATE_DESCRIPTIONS.composing, 1.15);
      botTurn = renderBotMessageInstant(reply, botTurn);
      if (shouldSpeakReply && currentAppMode === "voice") {
        feedStreamingTextToTTS(reply, true);
      }
    } catch (_fallbackErr) {
      if (myHistoryEpoch !== historyEpoch || streamAbortController.signal.aborted) {
        return;
      }
      botTurn = renderBotMessageInstant(FALLBACK_ERROR, botTurn);
    }
  }

  scheduleBackgroundMemorySync(1150);

  if (currentAppMode === "voice") {
    await waitForSpeechToFinish();
  }

  if (currentStreamAbortController !== streamAbortController) {
    return;
  }

  isSending = false;
  currentStreamAbortController = null;
  updateVoiceMicBarUI();
  if (sendBtn) {
    sendBtn.disabled = false;
    sendBtn.style.display = "";
  }
  if (stopBtn) {
    stopBtn.style.display = "none";
  }
  if (msgInput) {
    msgInput.disabled = false;
    if (currentAppMode === "chat") {
      msgInput.focus();
    }
  }

  syncIdleState();
  if (currentAppMode === "voice" && !voiceMicMuted) {
    setTimeout(() => {
      startVoiceListening();
    }, 150);
  }
}

/**
 * Phản ứng thông minh theo động lực gõ phím thời gian thực của người dùng ở Chế độ Chat,
 * kèm co giãn chiều cao <textarea id="msg"> (1..5 dòng, tối đa 120px), Shift+Enter xuống dòng & bảo toàn thụt lề khi Paste code.
 */
if (msgInput) {
  msgInput.addEventListener("input", () => {
    autoResizeMsgInput();
    if (isSending || currentAppMode !== "chat") return;
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

    keystrokeTimes.push(now);
    while (keystrokeTimes.length > 0 && now - keystrokeTimes[0] > 1200) {
      keystrokeTimes.shift();
    }
    const keysPerSec = keystrokeTimes.length / 1.2;
    const waveEnergy = Math.max(0.88, Math.min(1.55, 0.85 + keysPerSec * 0.085));

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

    if (trimmedLen >= 6) {
      typingShapeTimer = setTimeout(() => {
        if (!isSending && currentAppMode === "chat" && msgInput.value.trim().length >= 6) {
          setOrbVisualState("shaping", STATE_DESCRIPTIONS.shaping, 0.92);
        }
      }, 950);
    }

    typingIdleTimer = setTimeout(() => {
      if (!isSending && currentAppMode === "chat") {
        syncIdleState();
      }
    }, 2800);
  });

  msgInput.addEventListener("paste", (event) => {
    if (event.clipboardData) {
      const pastedText = event.clipboardData.getData("text/plain");
      if (typeof pastedText === "string" && pastedText.length > 0) {
        event.preventDefault();
        const normalizedPaste = pastedText.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        const start = typeof msgInput.selectionStart === "number" ? msgInput.selectionStart : msgInput.value.length;
        const end = typeof msgInput.selectionEnd === "number" ? msgInput.selectionEnd : msgInput.value.length;
        if (typeof msgInput.setRangeText === "function") {
          msgInput.setRangeText(normalizedPaste, start, end, "end");
        } else {
          msgInput.value = msgInput.value.slice(0, start) + normalizedPaste + msgInput.value.slice(end);
          msgInput.selectionStart = msgInput.selectionEnd = start + normalizedPaste.length;
        }
        autoResizeMsgInput();
        msgInput.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
    }
    setTimeout(() => {
      autoResizeMsgInput();
    }, 0);
  });

  msgInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      if (event.isComposing || event.keyCode === 229) {
        return;
      }
      event.preventDefault();
      sendMessage();
    }
  });
}

// Sự kiện chuyển đổi giữa Chế độ Chat & Chế độ Nói
if (modeChatBtn) {
  modeChatBtn.addEventListener("click", () => switchAppMode("chat"));
}
if (modeVoiceBtn) {
  modeVoiceBtn.addEventListener("click", () => switchAppMode("voice"));
}
if (quickVoiceBtn) {
  quickVoiceBtn.addEventListener("click", () => switchAppMode("voice"));
}

function hasActiveTextSelection() {
  if (!window.getSelection) return false;
  const sel = window.getSelection();
  return Boolean(sel && !sel.isCollapsed && sel.toString().trim().length > 0);
}

// Chạm vào dải sóng, quả cầu hoặc nút Mic trong Chế độ Nói để ngắt lời Wisp, gửi ngay câu vừa nói, hoặc Bật/Tạm dừng nghe
function handleVoiceStageTap(ignoreSelection = false) {
  const bypassSelection = ignoreSelection === true;
  if (!bypassSelection && hasActiveTextSelection()) return;
  if (currentAppMode !== "voice") return;
  if (isSending || isSpeakingTTS || isTTSWorkerRunning || currentScheduledSourceNodes.length > 0) {
    interruptCurrentInteraction();
    return;
  }
  if (!isSending) {
    // Nếu đang có câu nháp vừa nói trên màn hình -> Chạm sẽ gửi ngay lập tức câu đó!
    if ((currentLiveTranscript && currentLiveTranscript.trim()) || (pendingVoiceDraft && pendingVoiceDraft.trim())) {
      flushVoiceDraftAndSend();
      return;
    }
    voiceMicMuted = !voiceMicMuted;
    if (voiceMicMuted) {
      stopVoiceListening();
      setVoiceSubtitle("Đang tạm dừng Microphone.", true);
      syncIdleState();
    } else {
      setVoiceSubtitle("Hãy nói gì đó với Wisp…", true);
      syncIdleState();
      startVoiceListening();
    }
  }
}

if (voiceWaveWrap) {
  voiceWaveWrap.addEventListener("click", () => handleVoiceStageTap(false));
}

if (voiceMicToggleBtn) {
  voiceMicToggleBtn.addEventListener("click", () => handleVoiceStageTap(true));
}

if (clearVoiceChatBtn) {
  clearVoiceChatBtn.addEventListener("click", () => {
    clearAllConversationHistory();
  });
}

if (clearChatBtn) {
  clearChatBtn.addEventListener("click", () => {
    clearAllConversationHistory();
  });
}

if (orbModeBtn) {
  orbModeBtn.addEventListener("click", () => {
    if (hasActiveTextSelection()) return;
    if (currentAppMode === "voice") {
      handleVoiceStageTap();
      return;
    }
    if (isSending || isSpeakingTTS || currentScheduledSourceNodes.length > 0) {
      interruptCurrentInteraction();
      return;
    }
    clearTimeout(previewResetTimer);
    const cur = centerOrb ? centerOrb.getState() : "breathing";
    const idx = (ALL_STATES.indexOf(cur) + 1) % ALL_STATES.length;
    const next = ALL_STATES[idx];
    setOrbVisualState(next, `${STATE_DESCRIPTIONS[next]}`, 1);
    previewResetTimer = setTimeout(() => {
      if (!isSending && currentAppMode === "chat") syncIdleState();
    }, 4000);
  });
}

// Bấm nút Dừng (Stop) trên UI để ngắt lời lập tức
const stopBtn = document.getElementById("stopBtn");
if (stopBtn) {
  stopBtn.addEventListener("click", () => {
    interruptCurrentInteraction();
  });
}

// Bắt phím tắt Escape (đóng Popover không lưu đè hoặc Barge-in) và Space (Barge-in)
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (userProfilePanel && !userProfilePanel.hasAttribute("hidden")) {
      e.preventDefault();
      closeUserProfilePanel(true);
      return;
    }
    if (aiConfigPanel && !aiConfigPanel.hasAttribute("hidden")) {
      e.preventDefault();
      aiConfigPanel.setAttribute("hidden", "");
      if (aiConfigBtn) aiConfigBtn.setAttribute("aria-expanded", "false");
      return;
    }
    if (isSending || isSpeakingTTS || currentScheduledSourceNodes.length > 0) {
      e.preventDefault();
      interruptCurrentInteraction();
      return;
    }
  }
  if (e.key === " " || e.code === "Space") {
    const activeTag = (document.activeElement && document.activeElement.tagName) || "";
    const isInputFocused = ["INPUT", "TEXTAREA", "SELECT"].includes(activeTag);
    if (!isInputFocused && (isSending || isSpeakingTTS || currentScheduledSourceNodes.length > 0)) {
      e.preventDefault();
      interruptCurrentInteraction();
      return;
    }
  }
});

// Đổi giao diện Sáng / Tối
if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const nextTheme = isDarkMode() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", nextTheme);
  });
}

async function copyTextToClipboard(rawText) {
  const text = String(rawText ?? "");
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_e) {}
  }
  try {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.top = "-9999px";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(helper);
    return Boolean(ok);
  } catch (_err) {
    return false;
  }
}

// Xử lý sự kiện click trong cả 2 khung chat (#chat và #voiceChat):
// 1. Nút Sao chép mã nguồn (.copy-btn) -> copy vào clipboard & báo "Đã sao chép!" trong 2 giây
// 2. Bôi đen text hoặc click trong khối <pre class="code-block"> -> tuyệt đối không gửi nhầm tin nhắn
// 3. Click nhanh vào thẻ `.inline-code` (khi không bôi đen text) -> gửi lệnh nhanh
async function handleChatContainerClick(e) {
  const copyBtn = e.target.closest(".copy-btn");
  if (copyBtn) {
    e.preventDefault();
    e.stopPropagation();
    const preEl = copyBtn.closest("pre, .code-block");
    const codeEl = preEl ? preEl.querySelector("code") : null;
    const codeText = codeEl ? codeEl.textContent : "";
    await copyTextToClipboard(codeText);
    clearTimeout(copyBtn._copyResetTimer);
    copyBtn.textContent = "Đã sao chép!";
    copyBtn.classList.add("copied");
    copyBtn._copyResetTimer = setTimeout(() => {
      copyBtn.textContent = "Sao chép";
      copyBtn.classList.remove("copied");
    }, 2000);
    return;
  }

  if (e.target.closest("a.chat-md-link")) {
    return;
  }

  if (e.target.closest("pre, .code-block")) {
    return;
  }

  if (hasActiveTextSelection()) {
    return;
  }

  const codeEl = e.target.closest(".inline-code");
  if (codeEl && codeEl.textContent) {
    sendMessage(codeEl.textContent.trim());
  }
}

if (chatEl) {
  chatEl.addEventListener("click", handleChatContainerClick);
}
if (voiceChatEl) {
  voiceChatEl.addEventListener("click", handleChatContainerClick);
}

if (sendBtn) {
  sendBtn.addEventListener("click", () => {
    sendMessage();
  });
}

// Khởi tạo lại mạch hội thoại từ localStorage khi tải trang / F5 & đồng bộ chiều cao ô nhập
initHistory();
autoResizeMsgInput();

window.sendMessage = sendMessage;
window.switchAppMode = switchAppMode;
window.interruptCurrentInteraction = interruptCurrentInteraction;
window.initHistory = initHistory;
window.clearAllConversationHistory = clearAllConversationHistory;
window.normalizeVietnameseClientInput = normalizeVietnameseClientInput;
window.formatSafeMessageHtml = formatSafeMessageHtml;
window.evaluateHandsFreeVoiceBargeIn = evaluateHandsFreeVoiceBargeIn;
window.switchMemoryTab = switchMemoryTab;
window.fetchMemoriesState = fetchMemoriesState;
window.__wispVoiceDebug = {
  setSpeakingState(speaking, startedAtMs = 0) {
    isSpeakingTTS = Boolean(speaking);
    ttsPlaybackStartedAtMs = Number(startedAtMs) || 0;
    handsFreeBargeInSinceMs = 0;
  },
  getState() {
    return {
      isSpeakingTTS,
      isSending,
      voiceMicMuted,
      ttsPlaybackStartedAtMs,
      handsFreeBargeInSinceMs,
    };
  },
};


