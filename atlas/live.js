/* =========================================================================
   ATLAS live data layer (served by atlas_api.py)
   Loaded after the main inline script. Replaces every DEMO generator with
   data from the Python backend, and adds the trading-bot pages.
   If the backend is unreachable, the original DEMO data and its DEMO
   labels stay on screen: nothing here ever invents a number.
   ========================================================================= */
(() => {
'use strict';
const L = window.ATLAS_LIVE = { on: false, quotes: null, india: null, fund: {}, bot: null, botMode: 'live', sim: null };
const $ = id => document.getElementById(id);
const E = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(p, ms = 20000) {
  const r = await fetch(p, { cache: 'no-store', signal: AbortSignal.timeout(ms) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const ago = ts => { if (!ts) return ''; const m = Math.max(0, Math.round((Date.now() - new Date(ts)) / 6e4)); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; };
const inr = v => v == null ? '—' : (v < 0 ? '−' : '') + '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v, d = 2) => v == null || !isFinite(v) ? '—' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(d) + '%';
const big = (v, cur) => { if (v == null) return '—'; const a = Math.abs(v); if (cur === '₹') { if (a >= 1e12) return '₹' + (v / 1e12).toFixed(2) + 'L Cr'; return '₹' + Math.round(v / 1e7).toLocaleString('en-IN') + ' Cr'; } return (cur || '$') + (a >= 1e12 ? (v / 1e12).toFixed(2) + 'T' : a >= 1e9 ? (v / 1e9).toFixed(1) + 'B' : (v / 1e6).toFixed(1) + 'M'); };
const curOf = a => a.m === 'IN' ? '₹' : a.m === 'US' ? '$' : (CUR[a.m] || '$');
const css = document.createElement('style');
css.textContent = `
.badge.LIVE{color:var(--up)}.badge.WARN{color:#d9a441}.badge.ERR{color:var(--dn)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.kpi .card{text-align:center;padding:26px 18px}.kpi .p{white-space:nowrap;font-size:clamp(26px,3.4vw,40px);font-weight:700;letter-spacing:-.02em;margin:0}
.kpi small{display:block;color:var(--mu);font-size:12px;letter-spacing:.12em;margin-top:6px}
.seg{display:inline-flex;gap:4px;padding:4px;border-radius:99px;border:1px solid var(--ln);background:var(--s1);align-items:center}
.seg span{font-size:13px;color:var(--mu);padding:0 10px}.seg button{padding:7px 14px;border-radius:99px;font-size:12px;font-weight:600;letter-spacing:.08em;color:var(--mu)}
.seg button.on{background:var(--up);color:#04140d}
.mdl{display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:center}.mdl .ring{width:92px;height:92px}
.mbar{height:8px;border-radius:5px;background:var(--s3);overflow:hidden;margin-top:6px}.mbar i{display:block;height:100%;background:var(--up)}
.note{color:var(--mu);font-size:13px;margin-top:10px}
.sk{color:var(--mu)}
#lslog td{white-space:nowrap;padding:8px 6px;font-size:14px}
`;
document.head.appendChild(css);

/* ---------------------------------------------------------------------
   1. QUOTES -> every card on the home page, globe, heatmap, watchlist
   --------------------------------------------------------------------- */
function applyQuotes(j) {
  j.items.forEach(i => {
    const a = BY[i.s]; if (!a) return;
    Object.assign(a, {
      p: i.p, pc: i.pc, ch: i.ch, prev: i.prev, v: i.v, hi: i.hi, lo: i.lo, op: i.op, open: i.open,
      pts: i.pts && i.pts.length > 1 ? i.pts : [i.p, i.p],
      st: i.status === 'LIVE' ? 'REALTIME' : i.status, src: i.src, ts: new Date(i.ts),
    });
  });
}
function regionCards() {
  const REGS = [['USA', 'SPX', 'Wall Street, regular session'], ['INDIA', 'NIFTY50', 'NSE · BSE'], ['EUROPE', 'DAX', 'Xetra · LSE · Euronext'], ['ASIA', 'N225', 'Tokyo · Hong Kong · Shanghai'], ['CRYPTO', 'BTC', '24/7 digital markets']];
  $('hs').innerHTML = REGS.map(([n, s, d]) => { const a = BY[s]; const live = a.src !== 'DEMO';
    return `<div class="rc"><div><div class="badge ${live ? (a.open ? 'OPEN' : 'CLOSED') : 'DEMO'}"><i></i>${live ? (a.open ? 'OPEN' : 'CLOSED') + ' · ' + E(a.st) : 'DEMO'}</div><h3>${n}</h3><div style="color:var(--mu)">${d}</div></div><div><div style="color:var(--mu);font-size:14px">${E(a.n)}</div><div class="big num">${fmt(a)}</div><div class="num ${cls(a)}">${fch(a)} · ${fpc(a)}</div>${spark(a)}</div></div>`; }).join('');
}
function movers() {
  const tbl = l => l.map(x => `<tr><td><a href="#/asset/${E(x.s)}" style="color:inherit">${E(BY[x.s]?.n || x.s)}</a></td><td>₹${x.p.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td><td class="${x.pc >= 0 ? 'up' : 'dn'}">${pct(x.pc)}</td></tr>`).join('');
  if (L.india && L.india.gainers) {
    const I = L.india;
    $('inmov').innerHTML = [['Top Gainers', I.gainers], ['Top Losers', I.losers], ['Most Active (by value)', I.active]]
      .map(([t, l]) => `<div class="panel rev in"><h4>${t} · NIFTY 50 · Yahoo · DELAYED</h4><table class="tb num">${tbl(l.slice(0, 4))}</table></div>`).join('');
  } else {
    const inS = get('Stocks', 'IN').sort((a, b) => b.pc - a.pc), lab = inS[0].src === 'DEMO' ? 'DEMO' : 'DELAYED';
    $('inmov').innerHTML = [['Top Gainers', inS.slice(0, 4)], ['Top Losers', inS.slice(-4).reverse()], ['Most Active', [...inS].sort((a, b) => (b.v || 0) * b.p - (a.v || 0) * a.p).slice(0, 4)]]
      .map(([t, l]) => `<div class="panel rev in"><h4>${t} · NSE · ${lab}</h4><table class="tb num">${rows(l)}</table></div>`).join('');
  }
}
function renderHome() {
  put('idx', get('Indices').filter(a => ['SPX', 'NDX', 'DJI', 'NIFTY50', 'SENSEX', 'UKX', 'DAX', 'CAC', 'N225', 'HSI', 'SSEC'].includes(a.s)));
  put('inidx', get('Indices', 'IN')); put('usidx', get('Indices', 'US'));
  put('fxg', get('Forex')); put('cmg', get('Commodities')); put('crg', get('Crypto'));
  $('ustb').innerHTML = '<tr><th>Stock</th><th>Price</th><th>Change</th></tr>' + rows(get('Stocks', 'US'));
  movers(); regionCards(); renderWL(); drawHM(); drawSp();
  document.querySelectorAll('#globe .gp .badge.DEMO').forEach(b => { b.className = 'badge WARN'; b.innerHTML = '<i></i>Yahoo Finance · DELAYED'; });
  document.querySelectorAll('#heat p').forEach(p => { if (/DEMO DATA/.test(p.textContent)) p.textContent = 'Prices: Yahoo Finance (delayed). Tile size: market cap from Yahoo fundamentals once loaded.'; });
}
function banner(txt, ok) { const d = document.querySelector('.demo'); d.textContent = txt; d.style.color = ok ? 'var(--up)' : '#d9a441'; }
async function loadQuotes() {
  try {
    const j = await api('/api/quotes', 45000);
    L.quotes = j; L.on = true; applyQuotes(j); renderHome();
    const fy = j.items.filter(i => i.src === 'Fyers').length;
    banner(`● ${j.status === 'CACHED' ? 'CACHED' : 'LIVE DATA'} · Yahoo Finance (delayed)${fy ? ' · Fyers live for ' + fy + ' bot stocks' : ''} · ${new Date(j.asOf).toLocaleTimeString()}`, true);
    document.querySelectorAll('footer p').forEach(p => { if (/DEMO DATA/.test(p.textContent)) p.textContent = 'Market data from Yahoo Finance (delayed) and Fyers (live, bot symbols only). Information provided by ATLAS is for informational purposes and should not be considered investment advice.'; });
    if (location.hash === '#/india' || location.hash === '#/commodities') route();
  } catch (e) {
    if (!L.on) banner('● DEMO DATA — backend unreachable (' + e.message + '). Start the server with start_today.command or atlas_server.py', false);
  }
}
async function loadIndia() { try { L.india = await api('/api/india', 45000); movers(); } catch (e) { /* keep */ } }
async function loadCaps() {
  const syms = Object.keys(CAPW.US).concat(Object.keys(CAPW.IN));
  try {
    const j = await api('/api/fundamentals?s=' + encodeURIComponent(syms.join(',')), 90000);
    Object.assign(L.fund, j.items || {});
    for (const m of ['US', 'IN']) for (const s of Object.keys(CAPW[m])) { const f = L.fund[s]; if (f && f.mc) CAPW[m][s] = m === 'IN' ? f.mc / 1e9 / (BY.USDINR?.p || 88) : f.mc / 1e9; }
    drawHM();
  } catch (e) { /* demo weights stay */ }
}

/* ---------------------------------------------------------------------
   2. ASSET PAGE (chart, stats, technicals, ML model view, fundamentals)
   --------------------------------------------------------------------- */
let A_cur = null, A_rng = '1D', A_ser = null;
function ensureAsset(s) {
  if (BY[s]) return BY[s];
  if (!/^[A-Z0-9&\-]{1,20}$/.test(s)) return null;
  // any NSE stock (e.g. from the NIFTY 50 movers or the bot's universe)
  const q = (L.india?.nifty50 || []).find(x => x.s === s);
  const a = { s, n: s, c: 'Stocks', m: 'IN', e: 'NSE', p: q?.p || 0, pc: q?.pc || 0, ch: q ? q.p - q.p / (1 + q.pc / 100) : 0, pts: [], st: 'DELAYED', src: q ? 'Yahoo Finance' : 'DEMO', ts: new Date(), extra: true };
  BY[s] = a; return a;
}
function chartSVG(pts, ts, a, label) {
  const svg = $('ch'); if (!svg) return;
  if (!pts || pts.length < 2) { svg.innerHTML = `<text x="400" y="160" text-anchor="middle" fill="var(--mu)" font-size="16">No chart data for this range</text>`; return; }
  const W = 800, H = 320, m = Math.min(...pts), M = Math.max(...pts), X = i => i / (pts.length - 1) * W, Y = v => H - 16 - (v - m) / (M - m || 1) * (H - 32);
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(''), c = pts[pts.length - 1] >= pts[0] ? 'up' : 'dn';
  svg.innerHTML = `<defs><linearGradient id="gr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--${c})" stop-opacity=".25"/><stop offset="1" stop-color="var(--${c})" stop-opacity="0"/></linearGradient></defs>
  <path d="${d}L${W},${H}L0,${H}Z" fill="url(#gr)" style="opacity:0;transition:opacity 1s .5s" id="ar"/>
  <path id="ln" d="${d}" fill="none" stroke="var(--${c})" stroke-width="2.2" vector-effect="non-scaling-stroke" stroke-linejoin="round" pathLength="1" style="stroke-dasharray:1;stroke-dashoffset:1;transition:stroke-dashoffset .9s cubic-bezier(.3,.7,.2,1)"/>
  <line id="cl" y1="0" y2="${H}" stroke="var(--mu)" stroke-width="1" vector-effect="non-scaling-stroke" opacity="0"/><circle id="cd" r="4" fill="var(--tx)" opacity="0"/>`;
  requestAnimationFrame(() => requestAnimationFrame(() => { const l = $('ln'), ar = $('ar'); if (l) l.style.strokeDashoffset = 0; if (ar) ar.style.opacity = 1; }));
  const tip = $('tip'), intra = ['1D', '5D'].includes(A_rng);
  const base = `Hover or drag to inspect · ${label}`; tip.textContent = base;
  svg.onpointermove = e => { const r = svg.getBoundingClientRect(), i = Math.max(0, Math.min(pts.length - 1, Math.round((e.clientX - r.left) / r.width * (pts.length - 1))));
    $('cl').setAttribute('x1', X(i)); $('cl').setAttribute('x2', X(i)); $('cl').setAttribute('opacity', 1); $('cd').setAttribute('cx', X(i)); $('cd').setAttribute('cy', Y(pts[i])); $('cd').setAttribute('opacity', 1);
    const t = ts && ts[i] ? new Date(ts[i]) : null;
    tip.textContent = `${t ? (intra ? t.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : t.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })) : 'point ' + (i + 1)} · ${fmt({ ...a, p: pts[i] })}`; };
  svg.onpointerleave = () => { ['cl', 'cd'].forEach(i => $(i)?.setAttribute('opacity', 0)); tip.textContent = base; };
}
async function loadChart(a) {
  const want = A_rng;
  try {
    const h = await api(`/api/history?s=${encodeURIComponent(a.s)}&r=${want}`);
    if (A_cur !== a || A_rng !== want) return;
    A_ser = h; chartSVG(h.c, h.t, a, `${h.src} · ${h.status}`);
  } catch (e) {
    if (A_cur !== a || A_rng !== want) return;
    chartSVG(series(a, want), null, a, 'DEMO series (history unavailable: ' + e.message + ')');
  }
}
const statBox = (k, v) => `<div><small>${k}</small><b class="num">${v}</b></div>`;
function ring(p, th) {
  const r = 38, C = 2 * Math.PI * r, col = p >= th ? 'var(--up)' : p >= th - 0.08 ? '#d9a441' : 'var(--mu)';
  return `<svg class="ring" viewBox="0 0 92 92"><circle cx="46" cy="46" r="${r}" fill="none" stroke="var(--s3)" stroke-width="8"/><circle cx="46" cy="46" r="${r}" fill="none" stroke="${col}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${C * p} ${C}" transform="rotate(-90 46 46)"/><text x="46" y="52" text-anchor="middle" fill="var(--tx)" font-size="19" font-weight="700">${Math.round(p * 100)}%</text></svg>`;
}
function modelPanel(m) {
  if (!m) return '';
  if (m.error) return `<div class="panel" style="margin-top:20px"><h4>ATLAS ML model</h4><p class="note">Model view unavailable: ${E(m.error)}</p></div>`;
  const p = m.p_win, th = m.threshold, buy = m.signal === 'BUY';
  return `<div class="panel" style="margin-top:20px"><h4>ATLAS ML model · swing-trade filter · as of ${E(m.asOf)} close</h4>
   <div class="mdl">${ring(p, th)}<div><div style="font-size:22px;font-weight:700" class="${buy ? 'up' : ''}">${buy ? 'BUY SETUP' : m.gate === false ? 'NO TRADE · OUTSIDE RULES' : 'NO TRADE'}</div>
   ${m.gate === false ? `<div class="note" style="margin-top:4px">The bot only trades stocks in an uptrend (200-day average rising, price no more than 10% below it), with ATR under 4% and India VIX under 30. This stock fails at least one of those today.</div>` : ''}
   <div class="note" style="margin-top:4px">Estimated chance this trade ends in profit after charges: <b class="num">${(p * 100).toFixed(1)}%</b>. The bot only buys at ≥ <b class="num">${(th * 100).toFixed(1)}%</b>.</div>
   ${buy ? `<div class="note">Plan: buy at next open ≈ ${fmt({ ...A_cur, p: m.entry_ref })}, take profit ${fmt({ ...A_cur, p: m.tp })} (${pct(m.tp_pct)}), stop ${fmt({ ...A_cur, p: m.sl })} (${pct(-m.sl_pct)}), exit by day ${m.max_hold}.</div>` : ''}
   </div></div><p class="note">Out-of-sample record at this threshold: ${m.oos ? `${m.oos.n} trades, ${(m.oos.win_rate * 100).toFixed(1)}% won after charges (${E(m.oos.period)})` : '—'}. Past results do not guarantee future results. Not investment advice.</p></div>`;
}
function techTable(t, a) {
  if (!t) return '';
  const P = t.close, rel = v => v == null ? '' : P >= v ? 'Price above' : 'Price below', F = v => v == null ? '—' : fmt({ ...a, p: v });
  const n = (v, d = 1) => v == null ? '—' : v.toFixed(d);
  const R = (k, v, r) => `<tr><td>${k}</td><td>${v}</td><td style="color:var(--mu)">${r}</td></tr>`;
  return `<div class="panel"><table class="tb num"><tr><th>Indicator</th><th>Value</th><th>Reading</th></tr>
  ${R('SMA 20', F(t.sma20), rel(t.sma20))}${R('SMA 50', F(t.sma50), rel(t.sma50))}${R('SMA 200', F(t.sma200), rel(t.sma200))}${R('EMA 20', F(t.ema20), rel(t.ema20))}
  ${R('EMA 9 / 21', F(t.ema9) + ' / ' + F(t.ema21), t.ema9 > t.ema21 ? 'Short trend up' : 'Short trend down')}
  ${R('RSI (14)', n(t.rsi), t.rsi > 70 ? 'Overbought zone' : t.rsi < 30 ? 'Oversold zone' : 'Neutral')}${R('RSI (2)', n(t.rsi2), t.rsi2 < 10 ? 'Short-term oversold' : t.rsi2 > 90 ? 'Short-term overbought' : '—')}
  ${R('MACD (12, 26, 9)', n(t.macd, 2) + ' / ' + n(t.macd_signal, 2), t.macd > t.macd_signal ? 'Above signal' : 'Below signal')}
  ${R('Bollinger (20, 2)', F(t.bb_lower) + ' – ' + F(t.bb_upper), P > t.bb_upper ? 'Above upper band' : P < t.bb_lower ? 'Below lower band' : 'Inside bands')}
  ${R('VWAP (20 d)', F(t.vwap), rel(t.vwap))}${R('Stochastic (14)', n(t.stoch), t.stoch > 80 ? 'Overbought zone' : t.stoch < 20 ? 'Oversold zone' : 'Neutral')}
  ${R('ADX (14)', n(t.adx), t.adx > 25 ? 'Trending' : 'Range-bound')}${R('CCI (20)', n(t.cci), '')}${R('Williams %R (14)', n(t.williams_r), '')}
  ${R('ATR (14)', F(t.atr) + ' · ' + n(t.atr_pct, 2) + '%', 'Typical daily range')}${R('From 52W high / low', pct(t.pct_from_52w_high) + ' / ' + pct(t.pct_from_52w_low), '')}
  </table><p class="tech">Calculated by features.py, the same code the ML model and the trading bot use. Descriptive only, not a buy or sell recommendation.</p></div>`;
}
window.openAsset = openAsset = function (s) {
  const a = ensureAsset(s);
  if (!a) { aw.innerHTML = `<button class="back" onclick="closeAsset()">← Back</button><h1>Symbol not found</h1><p class="lead">“${E(s)}” isn't in ATLAS.</p>`; asEl.classList.add('on'); document.body.style.overflow = 'hidden'; return; }
  A_cur = a; A_rng = '1D'; A_ser = null;
  const eq = a.c === 'Stocks' || a.c === 'Indices', live = a.src !== 'DEMO';
  aw.innerHTML = `<button class="back" onclick="closeAsset()">← Back to ATLAS</button>
  <div class="ah"><div><h1>${E(a.n.toUpperCase())}</h1><div class="meta">${E(a.e)} · ${E(a.s)} · ${E(a.c)}</div></div><button class="star ${WL.has(s) ? 'on' : ''}" id="stb" aria-label="Toggle watchlist" aria-pressed="${WL.has(s)}">${WL.has(s) ? '★' : '☆'}</button></div>
  <div class="px num" id="apx">${a.p ? fmt(a) : '…'}</div>
  <div class="num ${cls(a)}" style="font-size:20px" id="ach">${a.p ? fch(a) + ' &nbsp; ' + fpc(a) : ''}</div>
  <div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="badge ${eq ? (live ? (a.open ? 'OPEN' : 'CLOSED') : (a.st === 'OPEN' ? 'OPEN' : 'CLOSED')) : 'CLOSED'}"><i></i>${eq ? 'Market ' + (live ? (a.open ? 'OPEN' : 'CLOSED') : a.st) : '24H'}</span><span class="badge ${live ? (a.st === 'REALTIME' ? 'LIVE' : 'WARN') : 'DEMO'}"><i></i>${live ? E(a.src) + ' · ' + E(a.st) : 'DEMO'} · ${a.ts.toLocaleString()}</span></div>
  <div class="rg" role="tablist">${Object.keys(RN).map(r => `<button role="tab" class="${r === '1D' ? 'on' : ''}" data-r="${r}">${r}</button>`).join('')}</div>
  <svg id="ch" viewBox="0 0 800 320" role="img" aria-label="Price chart" preserveAspectRatio="none"></svg><div class="tip" id="tip">Loading chart…</div>
  <div class="stats" id="ast">${['Open', 'Previous Close', 'Day High', 'Day Low', '52W High', '52W Low'].map(k => statBox(k, '<span class="sk">…</span>')).join('')}</div>
  <div id="amodel"></div><div id="tabs"></div>
  <p class="tech">Prices from ${live ? E(a.src) : 'DEMO data'}; may be delayed. Nothing here is investment advice.</p>`;
  asEl.classList.add('on'); asEl.scrollTop = 0; document.body.style.overflow = 'hidden';
  $('stb').onclick = e => { WL.toggle(s); const on = WL.has(s); e.currentTarget.textContent = on ? '★' : '☆'; e.currentTarget.classList.toggle('on', on); e.currentTarget.setAttribute('aria-pressed', on); };
  aw.querySelector('.rg').onclick = e => { const b = e.target.closest('button'); if (!b) return; A_rng = b.dataset.r; aw.querySelectorAll('.rg button').forEach(x => x.classList.toggle('on', x === b)); $('tip').textContent = 'Loading…'; loadChart(a); };
  loadChart(a);
  const showTabs = a.c === 'Stocks';
  if (showTabs) liveTabs(a, null);
  if (a.c === 'Stocks' || a.c === 'Indices' || a.c === 'Commodities' || a.c === 'Forex' || a.c === 'Crypto') api('/api/asset?s=' + encodeURIComponent(s)).then(j => {
    if (A_cur !== a) return;
    const S = j.stats, F = v => v == null ? '—' : fmt({ ...a, p: v });
    if (a.extra && !a.p) { a.p = j.tech.close; a.ch = j.tech.close - S.prevClose; a.pc = a.ch / S.prevClose * 100; a.src = j.src; $('apx').textContent = fmt(a); $('ach').innerHTML = fch(a) + ' &nbsp; ' + fpc(a); $('ach').className = 'num ' + cls(a); }
    $('ast').innerHTML = statBox('Open', F(S.open)) + statBox('Previous Close', F(S.prevClose)) + statBox('Day High', F(S.high)) + statBox('Day Low', F(S.low)) + statBox('52W High', F(S.hi52)) + statBox('52W Low', F(S.lo52)) + (eq && S.volume ? statBox('Volume', S.volume >= 1e7 ? (S.volume / 1e7).toFixed(2) + ' Cr' : S.volume >= 1e5 ? (S.volume / 1e5).toFixed(2) + ' L' : S.volume.toLocaleString()) : '') + `<div style="grid-column:1/-1;background:none;border:0;padding:0"><small>Daily bars as of ${E(j.asOf)} · ${E(j.src)}</small></div>`;
    $('amodel').innerHTML = modelPanel(j.model);
    if (showTabs) liveTabs(a, j);
  }).catch(e => { if (A_cur === a) $('ast').innerHTML = `<p class="note" style="grid-column:1/-1">Stats unavailable (${E(e.message)}).</p>`; });
};
function liveTabs(a, assetJ) {
  const host = $('tabs'); if (!host) return;
  const T = ['Overview', 'Financials', 'Shareholding', 'Peers', 'Technicals', 'News'];
  const cur = host.dataset.t || 'Overview';
  const cs = curOf(a), money = v => v == null ? '—' : big(v, cs);
  const fundP = L.fund[a.s] ? Promise.resolve(L.fund[a.s]) : api('/api/fundamentals?s=' + encodeURIComponent(a.s), 30000).then(f => (L.fund[a.s] = f));
  const kv = l => `<div class="stats">${l.map(([k, v]) => statBox(k, v)).join('')}</div>`;
  const fail = (what, e) => `<p class="note">${what} unavailable from Yahoo Finance right now (${E(e.message || e)}).</p>`;
  const P = {
    Overview: () => fundP.then(f => kv([['Market cap', money(f.mc)], ['P/E (TTM)', f.pe ? f.pe.toFixed(1) : '—'], ['EPS (TTM)', f.eps != null ? fmt({ ...a, p: f.eps }) : '—'], ['Dividend yield', f.dy != null ? f.dy.toFixed(2) + '%' : '—'], ['Beta', f.beta != null ? f.beta.toFixed(2) : '—'], ['P/B', f.pb != null ? f.pb.toFixed(2) : '—'], ['Sector', E(f.sector || '—')], ['Industry', E(f.industry || '—')]]) + (f.summary ? `<p class="tech">${E(f.summary)}${f.summary.length >= 600 ? '…' : ''}</p>` : '') + `<p class="tech">Source: Yahoo Finance · ${E(f.status)}</p>`, e => fail('Profile', e)),
    Financials: () => fundP.then(f => { const q = f.quarters || []; if (!q.length) return '<p class="note">No quarterly statements available for this company on Yahoo Finance.</p>'; const mx = Math.max(...q.map(x => x.rev || 0)) || 1;
      return `<div class="panel"><h4>Revenue by quarter · Yahoo Finance</h4>${q.map(x => `<div class="bar" style="grid-template-columns:80px 1fr 130px"><span>${E(x.l)}</span><span class="tr"><span class="f" style="left:0;width:${(x.rev || 0) / mx * 100}%;background:var(--tx);opacity:.7"></span></span><span class="num" style="text-align:right">${money(x.rev)}</span></div>`).join('')}</div>
      <div class="panel" style="margin-top:12px"><table class="tb num"><tr><th>Quarter</th><th>Revenue</th><th>Net income</th><th>EPS</th><th>Op. margin</th></tr>${q.map(x => `<tr><td>${E(x.l)}</td><td>${money(x.rev)}</td><td>${money(x.ni)}</td><td>${x.eps != null ? fmt({ ...a, p: x.eps }) : '—'}</td><td>${x.opm != null ? x.opm.toFixed(1) + '%' : '—'}</td></tr>`).join('')}</table></div>`; }, e => fail('Financials', e)),
    Shareholding: () => fundP.then(f => { if (f.insiders == null && f.institutions == null) return '<p class="note">Holding data not available on Yahoo Finance for this company.</p>';
      const ins = (f.insiders || 0) * 100, inst = (f.institutions || 0) * 100, rest = Math.max(0, 100 - ins - inst), L2 = a.m === 'IN' ? ['Insiders / promoters', 'Institutions (FII + DII)', 'Public & other'] : ['Insiders', 'Institutions', 'Public & other'], V = [ins, inst, rest], C = ['var(--tx)', '#6e7bd8', '#8e9099'];
      return `<div class="panel"><h4>Latest holding · Yahoo Finance</h4><div style="display:flex;height:14px;border-radius:8px;overflow:hidden;margin:10px 0">${V.map((v, i) => `<span style="width:${v}%;background:${C[i]}"></span>`).join('')}</div>${V.map((v, i) => `<div class="r2"><span><span style="color:${C[i]}">●</span> ${L2[i]}</span><b class="num">${v.toFixed(1)}%</b></div>`).join('')}<p class="tech">${a.m === 'IN' ? 'Yahoo reports insiders and institutions only. The promoter / FII / DII split filed with NSE every quarter is not in this free feed.' : ''}</p></div>`; }, e => fail('Shareholding', e)),
    Peers: () => { const peers = get('Stocks', a.m).map(x => x.s); return api('/api/fundamentals?s=' + encodeURIComponent(peers.join(',')), 60000).then(j => { Object.assign(L.fund, j.items || {});
      return `<div class="panel"><table class="tb num"><tr><th>Company</th><th>Price</th><th>Change</th><th>Mkt cap</th><th>P/E</th></tr>${peers.map(s => { const x = BY[s], g = L.fund[s] || {}; return `<tr style="${s === a.s ? 'font-weight:600' : ''}"><td><a href="#/asset/${s}" style="color:inherit">${E(x.n)}</a></td><td>${fmt(x)}</td><td class="${cls(x)}">${fpc(x)}</td><td>${big(g.mc, curOf(x))}</td><td>${g.pe ? g.pe.toFixed(1) : '—'}</td></tr>`; }).join('')}</table></div><p class="tech">Peer set: every ${a.m === 'IN' ? 'Indian' : 'US'} stock tracked in ATLAS. Source: Yahoo Finance.</p>`; }, e => fail('Peer data', e)); },
    Technicals: () => assetJ ? Promise.resolve(techTable(assetJ.tech, a)) : Promise.resolve('<p class="note">Loading…</p>'),
    News: () => api('/api/news?s=' + encodeURIComponent(a.s)).then(j => j.items.length ? j.items.map(x => `<a class="card nc" style="margin-bottom:10px" href="${E(x.url)}" target="_blank" rel="noopener noreferrer"><div class="m"><span>${E(x.src)}</span><span>${ago(x.ts)}</span></div><h3>${E(x.h)}</h3></a>`).join('') : '<p class="note">No recent stories for this symbol on Yahoo Finance.</p>', e => fail('News', e)),
  };
  host.innerHTML = `<div class="chips" role="tablist" style="margin-top:34px;overflow-x:auto;flex-wrap:nowrap">${T.map(t => `<button role="tab" class="${t === cur ? 'on' : ''}" data-t="${t}">${t}</button>`).join('')}</div><div id="tp" style="animation:fi .3s"><p class="note">Loading…</p></div>`;
  const show = t => { host.dataset.t = t; host.querySelectorAll('.chips button').forEach(b => b.classList.toggle('on', b.dataset.t === t)); const el = $('tp'); el.innerHTML = '<p class="note">Loading…</p>'; P[t]().then(h => { if ($('tp') === el && host.dataset.t === t) el.innerHTML = h; }); };
  host.querySelector('.chips').onclick = e => { const b = e.target.closest('button'); if (b) show(b.dataset.t); };
  show(cur);
}

/* ---------------------------------------------------------------------
   3. INDIA / COMMODITIES / SCREENER pages
   --------------------------------------------------------------------- */
const _openIndiaDemo = openIndia;
window.openIndia = openIndia = function () {
  if (!L.on) return _openIndiaDemo();
  const I = L.india, n = BY.NIFTY50, st = n.open ? 'OPEN' : 'CLOSED';
  const ist = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' }).format(new Date()) + ' IST';
  const co = v => v >= 0 ? 'var(--up)' : 'var(--dn)', sg = v => pct(v);
  const bars = (rows, fn, lab, val) => rows.map(r => { const w = fn(r); return `<div class="bar"><span>${lab(r)}</span><span class="tr"><span class="f" style="background:${co(r.v)};${r.v >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`}"></span></span><span class="num" style="text-align:right;color:${co(r.v)}">${val(r)}</span></div>`; }).join('');
  const secs = I?.sectors ? [...I.sectors].sort((a, b) => b.pc - a.pc) : [];
  const tbl = (l, c) => l.map(x => `<tr><td><a href="#/asset/${E(x.s)}" style="color:inherit">${E(BY[x.s]?.n || x.s)}</a></td><td>₹${x.p.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td><td class="${c}">${pct(x.pc)}</td></tr>`).join('');
  const fl = I?.flows || [], fii = fl.find(x => /FII|FPI/i.test(x.cat || '')), dii = fl.find(x => /DII/i.test(x.cat || ''));
  const b = I?.breadth, tot = b ? b.adv + b.dec + b.unc : 1;
  aw.innerHTML = `<button class="back" onclick="closeAsset()">← Back to ATLAS</button>
  <h1 style="font-size:clamp(40px,8vw,96px);letter-spacing:-.04em;margin:0;line-height:1">India.</h1>
  <p class="lead" style="margin-top:12px">NIFTY 50 is <b class="${cls(n)}">${n.pc >= 0 ? 'up' : 'down'} ${Math.abs(n.pc).toFixed(2)}%</b>${secs.length ? `, with ${E(secs[0].n)} the strongest sector (${sg(secs[0].pc)}) and ${E(secs.at(-1).n)} the weakest (${sg(secs.at(-1).pc)})` : ''}.</p>
  <div style="display:flex;gap:8px;flex-wrap:wrap"><span class="badge ${st}"><i></i>NSE ${st} · ${ist}</span><span class="badge WARN"><i></i>Yahoo Finance · DELAYED</span></div>
  <div class="grid" style="margin-top:26px">${get('Indices', 'IN').map(card).join('')}</div>
  <div class="cols">
   <div class="panel"><h4>India VIX · Yahoo Finance</h4>${I?.vix ? `<div class="px num" style="font-size:44px;margin:8px 0 2px">${I.vix.p.toFixed(2)}</div><div class="num" style="color:${co(-I.vix.pc)}">${sg(I.vix.pc)}</div>` : '<p class="note">Unavailable right now.</p>'}<p class="tech" style="margin-top:10px">Expected volatility implied by NIFTY options.</p></div>
   <div class="panel"><h4>Market breadth · NIFTY 50 constituents</h4>${b ? `<div style="display:flex;height:14px;border-radius:8px;overflow:hidden;margin:14px 0"><span style="width:${b.adv / tot * 100}%;background:var(--up)"></span><span style="width:${b.unc / tot * 100}%;background:var(--mu)"></span><span style="width:${b.dec / tot * 100}%;background:var(--dn)"></span></div><div class="r2"><span>Advancing</span><b class="num up">${b.adv}</b></div><div class="r2"><span>Declining</span><b class="num dn">${b.dec}</b></div><div class="r2"><span>Unchanged (±0.05%)</span><b class="num">${b.unc}</b></div>` : '<p class="note">Unavailable right now.</p>'}</div>
   <div class="panel"><h4>FII / DII net · ₹ Cr · ${E(I?.flowsSrc || 'NSE')}</h4>${fii || dii ? [fii, dii].filter(Boolean).map(x => `<div class="r2"><span>${E(x.cat)} <small style="color:var(--mu)">${E(x.date)}</small></span><b class="num" style="color:${co(x.net)}">${x.net >= 0 ? '+' : '−'}₹${Math.abs(x.net).toLocaleString('en-IN')} Cr</b></div>`).join('') : `<p class="note">${E(I?.flowsError || 'Unavailable right now.')}</p>`}<p class="tech">GIFT Nifty is not shown: no free source publishes it reliably.</p></div>
  </div>
  <div class="cols">
   <div class="panel"><h4>Sectoral indices · today · ${E(I?.sectorsSrc || '')}</h4>${secs.length ? bars(secs.map(s => ({ n: s.n, v: s.pc })), r => Math.min(50, Math.abs(r.v) / 3 * 50), r => 'Nifty ' + E(r.n), r => sg(r.v)) : '<p class="note">Unavailable right now.</p>'}</div>
   <div class="panel"><h4>Top gainers · NIFTY 50</h4><table class="tb num">${I?.gainers ? tbl(I.gainers, 'up') : ''}</table><h4 style="margin-top:16px">Top losers · NIFTY 50</h4><table class="tb num">${I?.losers ? tbl(I.losers, 'dn') : ''}</table></div>
  </div>
  <p class="tech">Delayed data from Yahoo Finance and NSE's provisional FII/DII report. Nothing here is investment advice.</p>`;
  asEl.classList.add('on'); asEl.scrollTop = 0; document.body.style.overflow = 'hidden'; drawSp(); setTimeout(() => aw.querySelectorAll('.sp').forEach(p => p.style.strokeDashoffset = 0), 60);
  if (!I) loadIndia().then(() => { if (location.hash === '#/india') openIndia(); });
};
const _openComm = openCommodities;
window.openCommodities = openCommodities = function () { _openComm(); if (L.on) aw.querySelectorAll('.badge.DEMO').forEach(b => { b.className = 'badge WARN'; b.innerHTML = '<i></i>' + E(BY.XAU.src) + ' · ' + E(BY.XAU.st); }); };
const _openScr = openScreener, FUND0 = fund;
window.openScreener = openScreener = function () {
  _openScr();
  if (!L.on) return;
  const stocks = get('Stocks').map(a => a.s);
  const badge = aw.querySelector('.badge.DEMO'); if (badge) { badge.className = 'badge WARN'; badge.innerHTML = '<i></i>Loading live fundamentals…'; }
  api('/api/fundamentals?s=' + encodeURIComponent(stocks.join(',')), 90000).then(j => {
    Object.assign(L.fund, j.items || {});
    // patch the screener's demo fundamentals with real ones, then re-run its filter
    const real = s => L.fund[s];
    window.fund = fund = a => { const f = FUND0(a), r = real(a.s); if (!r) return f;
      return { ...f, mc: a.m === 'IN' ? (r.mc || 0) / 1e7 : (r.mc || 0) / 1e9, pe: r.pe ?? NaN, dy: r.dy ?? 0, beta: r.beta ?? NaN, eps: r.eps ?? NaN, real: true }; };
    Object.entries(L.fund).forEach(([s, r]) => { if (r.sector) SECT[s] = [r.sector, r.industry || '—']; });
    if (location.hash === '#/screener') { _openScr(); const b2 = aw.querySelector('.badge.DEMO'); if (b2) { b2.className = 'badge WARN'; b2.innerHTML = '<i></i>Yahoo Finance fundamentals · DELAYED'; } aw.querySelectorAll('p.tech').forEach(p => p.textContent = 'Market caps converted to USD at the live USD/INR rate for comparison. P/E shows — when a company has no positive trailing earnings. Source: Yahoo Finance. Nothing here is investment advice.'); const c = $('cnt'); if (c) c.textContent = c.textContent.replace('DEMO', 'Yahoo Finance'); }
  }).catch(() => { if (badge) { badge.className = 'badge DEMO'; badge.innerHTML = '<i></i>DEMO DATA · live fundamentals unavailable'; } });
};

/* ---------------------------------------------------------------------
   4. AI brief: data-grounded template from the live numbers on screen
   --------------------------------------------------------------------- */
$('brief').onclick = () => {
  const o = $('bo'), t = s => BY[s] ? `${BY[s].n} ${fpc(BY[s])}` : '';
  const src = L.on ? 'live delayed data (Yahoo Finance)' : 'DEMO DATA';
  const us = get('Stocks', 'US').sort((a, b) => b.pc - a.pc), cm = get('Commodities').sort((a, b) => b.pc - a.pc), cr = get('Crypto').sort((a, b) => b.pc - a.pc);
  const bot = L.bot && L.bot.running ? `<br><b>Trading bot</b> — ${L.bot.positions?.length || 0} open paper position(s), today's P&L ${inr(L.bot.today?.total_pnl)}.` : '';
  o.innerHTML = `<b style="color:var(--tx)">MARKET BRIEF</b> · auto-generated from ${src}, ${new Date().toLocaleTimeString()}<br><br><b>Global Markets</b> — ${t('SPX')}, ${t('N225')}, ${t('DAX')}.<br><b>India</b> — ${t('NIFTY50')}, ${t('SENSEX')}, ${t('BANKNIFTY')}; IT at ${fpc(BY.NIFTYIT)}${L.india?.breadth ? `; ${L.india.breadth.adv} of 50 NIFTY stocks advancing` : ''}.<br><b>United States</b> — ${t('NDX')}; best large cap ${us[0].n} ${fpc(us[0])}, weakest ${us.at(-1).n} ${fpc(us.at(-1))}.<br><b>Currencies</b> — USD/INR ${fmt(BY.USDINR)} (${fpc(BY.USDINR)}).<br><b>Commodities</b> — strongest ${cm[0].n} ${fpc(cm[0])}, weakest ${cm.at(-1).n} ${fpc(cm.at(-1))}.<br><b>Crypto</b> — ${cr[0].n} leads at ${fpc(cr[0])}.${bot}<br><br><span style="font-size:13px">A template filled with the numbers above. It does not explain why markets moved. Not investment advice.</span>`;
};
document.querySelectorAll('#ai .badge').forEach(b => { b.className = 'badge WARN'; b.innerHTML = '<i></i>ATLAS BRIEF · AUTO-GENERATED FROM DATA'; });

/* ---------------------------------------------------------------------
   5. TRADING BOT: home section + #/bot page (LIVE and SIM replay)
   --------------------------------------------------------------------- */
function kpis(o) {
  return `<div class="kpi">${o.map(([v, lab, col]) => `<div class="card"><div class="p num" style="color:${col}">${v}</div><small>${lab}</small></div>`).join('')}</div>`;
}
function botStatus(b) {
  if (!b || !b.running) return ['ERR', 'BOT OFFLINE'];
  if (b.token_status !== 'ok') return ['ERR', b.token_status === 'missing' ? 'FYERS NOT LOGGED IN' : 'FYERS TOKEN EXPIRED'];
  if (b.market !== 'OPEN') return ['CLOSED', 'MARKET ' + b.market];
  return ['LIVE', 'LIVE · SCANNING'];
}
function liveKPIs(b) {
  const t = b?.today || {}, a = b?.alltime || {};
  return kpis([[(t.total_pnl >= 0 ? '+' : '') + inr(t.total_pnl ?? 0), 'TOTAL P&L TODAY', (t.total_pnl ?? 0) >= 0 ? 'var(--up)' : 'var(--dn)'],
    [String(t.orders ?? 0), 'ORDERS TODAY', '#e8a33c'], [String(b?.positions?.length ?? 0), 'OPEN POSITIONS', '#5b8def'],
    [a.trades ? (a.win_rate * 100).toFixed(0) + '%' : '––', a.trades ? `WIN RATE · ${a.trades} CLOSED` : 'WIN RATE', '#a78bfa']]);
}
function simKPIs(S) {
  const s = S?.summary || {};
  return kpis([[s.totalPnl != null ? (s.totalPnl >= 0 ? '+' : '') + inr(s.totalPnl) : '––', 'REPLAY P&L', (s.totalPnl ?? 0) >= 0 ? 'var(--up)' : 'var(--dn)'],
    [String(s.n ?? 0), 'TRADES', '#e8a33c'], [s.avgRet != null ? pct(s.avgRet * 100) : '––', 'AVG TRADE (NET)', '#5b8def'],
    [s.winRate != null ? (s.winRate * 100).toFixed(1) + '%' : '––', 'WIN RATE', '#a78bfa']]);
}
function segHTML() {
  const lab = { live: 'Live from Python bot', sim: 'Real-data results & replay', livesim: 'Simulated market, trading live' }[L.botMode];
  return `<div class="seg" id="bseg"><span>${lab}</span><button data-m="live" class="${L.botMode === 'live' ? 'on' : ''}">LIVE</button><button data-m="sim" class="${L.botMode === 'sim' ? 'on' : ''}">SIM</button><button data-m="livesim" class="${L.botMode === 'livesim' ? 'on' : ''}">LIVE SIM</button></div>`;
}
function homeBot() {
  const host = $('botk'); if (!host) return;
  const b = L.bot;
  if (L.botMode === 'livesim') { lsRender(); $('bsegw').innerHTML = segHTML(); return; }
  if (L.botMode === 'sim') { host.innerHTML = simKPIs(L.sim); $('botnote').innerHTML = L.sim ? `Replay of the model's real out-of-sample trades on ${L.sim.scope === 'bot' ? 'the bot\'s focus stocks' : 'its whole universe'}, ${E(L.sim.summary.from)} → ${E(L.sim.summary.to)}, after charges. <a href="#/bot" style="color:var(--tx)">Open the replay →</a>` : 'Loading replay…'; }
  else { host.innerHTML = liveKPIs(b); const [c, t] = botStatus(b); $('botnote').innerHTML = `<span class="badge ${c}"><i></i>${t}</span> ${b && b.running ? `${E(b.mode)} trading · ${b.universe_size || 0} stocks scanned · last scan ${b.last_scan ? new Date(b.last_scan).toLocaleTimeString() : '—'}` : E(b?.reason || 'Start the bot with start_today.command')}${b && b.token_status !== 'ok' && b.running ? ' · run start_today.command (or get_token.py) — the bot picks up the new token automatically' : ''} · <a href="#/bot" style="color:var(--tx)">Open bot →</a>`; }
  $('bsegw').innerHTML = segHTML();
}
document.addEventListener('click', e => { const b = e.target.closest('#bseg button'); if (!b) return; L.botMode = b.dataset.m; if (L.botMode === 'sim' && !L.sim) loadSim(); if (L.botMode === 'livesim') lsEnsure(); homeBot(); if (location.hash === '#/bot') openBot(); });
async function loadBot() {
  try { L.bot = await api('/api/bot/state', 8000); } catch (e) { L.bot = { running: false, reason: 'Backend unreachable' }; }
  homeBot(); if (location.hash === '#/bot' && L.botMode === 'live') renderBotLive(false);
}
let SIM_SCOPE = 'all';
async function loadSim() {
  try {
    const j = await api('/api/bot/sim?scope=' + SIM_SCOPE, 30000);
    const pv = j.positionValue || 50000; let eq = 0;
    j.trades.forEach(t => { t.pnl = t.net_ret * pv; eq += t.pnl; t.eq = eq; });
    j.summary.totalPnl = eq; L.sim = j;
  } catch (e) { L.sim = { error: e.message, trades: [], summary: {} }; }
  homeBot(); if (location.hash === '#/bot' && L.botMode === 'sim') renderBotSim();
}
function posTable(b) {
  const P = b.positions || [];
  if (!P.length) return '<p class="note">No open positions. The bot buys only when the model\'s confidence clears the threshold, so days without trades are normal.</p>';
  return `<table class="tb num"><tr><th>Stock</th><th>Qty</th><th>Entry</th><th>Now</th><th>Target</th><th>Stop</th><th>P&L</th><th>Day</th></tr>${P.map(p => `<tr><td><a href="#/asset/${E(p.symbol)}" style="color:inherit">${E(p.symbol)}</a></td><td>${p.qty}</td><td>${inr(p.entry)}</td><td>${inr(p.price)}</td><td>${inr(p.tp)}</td><td>${inr(p.sl)}</td><td class="${p.pnl >= 0 ? 'up' : 'dn'}">${inr(p.pnl)} (${pct(p.pnl_pct)})</td><td>${p.days_held}/${p.max_hold}</td></tr>`).join('')}</table>`;
}
function renderBotLive(full = true) {
  const b = L.bot || { running: false };
  const [c, t] = botStatus(b), m = b.model || {};
  const sig = (b.signals || []).slice(0, 12), focus = b.focus || [];
  const html = `<div id="botbody">${liveKPIs(b)}
  <div class="cols">
   <div class="panel"><h4>Status</h4><div class="r2"><span>Bot</span><span class="badge ${c}"><i></i>${t}</span></div><div class="r2"><span>Mode</span><b>${E(b.mode || '—')}</b></div><div class="r2"><span>Fyers token</span><b>${E(b.token_msg || b.token_status || '—')}</b></div><div class="r2"><span>Universe</span><b class="num">${b.universe_size || 0} NSE stocks</b></div><div class="r2"><span>Last scan</span><b class="num">${b.last_scan ? new Date(b.last_scan).toLocaleTimeString() : '—'}</b></div><div class="r2"><span>Today's signals computed</span><b class="num">${b.signals_date || '—'}</b></div>${b.reason ? `<p class="note">${E(b.reason)}</p>` : ''}</div>
   <div class="panel"><h4>Model</h4><div class="r2"><span>Model</span><b>${E(m.name || '—')}</b></div><div class="r2"><span>Buy threshold</span><b class="num">${m.threshold ? (m.threshold * 100).toFixed(1) + '%' : '—'}</b></div><div class="r2"><span>Exit rules</span><b class="num">${m.rules ? `TP ${m.rules.tp_atr}×ATR · SL ${m.rules.sl_atr}×ATR · ${m.rules.max_hold}d` : '—'}</b></div><div class="r2"><span>Out-of-sample win rate</span><b class="num">${m.oos_win_rate ? (m.oos_win_rate * 100).toFixed(1) + '% (' + m.oos_trades + ' trades)' : '—'}</b></div><div class="r2"><span>Trained</span><b class="num">${E(m.trained_at || '—')}</b></div></div>
  </div>
  <div class="panel" style="margin-top:14px"><h4>Open positions</h4>${posTable(b)}</div>
  <div class="cols">
   <div class="panel"><h4>Today's top model candidates (from yesterday's close)</h4>${sig.length ? `<table class="tb num"><tr><th>Stock</th><th>P(win)</th><th>Action</th></tr>${sig.map(s => `<tr><td><a href="#/asset/${E(s.symbol)}" style="color:inherit">${E(s.symbol)}</a></td><td>${(s.p * 100).toFixed(1)}%</td><td class="${s.action === 'BUY' ? 'up' : ''}">${E(s.action)}</td></tr>`).join('')}</table>` : '<p class="note">Signals are computed once a day after the 9:15 open, from the previous session\'s completed bars.</p>'}</div>
   <div class="panel"><h4>Focus stocks</h4><table class="tb num"><tr><th>Stock</th><th>Price</th><th>Change</th><th>P(win)</th></tr>${focus.map(f => `<tr><td><a href="#/asset/${E(f.symbol)}" style="color:inherit">${E(f.symbol)}</a></td><td>${f.price ? inr(f.price) : '—'}</td><td class="${(f.pc || 0) >= 0 ? 'up' : 'dn'}">${f.pc != null ? pct(f.pc) : '—'}</td><td>${f.p != null ? (f.p * 100).toFixed(1) + '%' : '—'}</td></tr>`).join('')}</table></div>
  </div>
  <div class="panel" style="margin-top:14px"><h4>Order log (most recent first)</h4>${(b.orders || []).length ? `<table class="tb num"><tr><th>Time</th><th>Side</th><th>Stock</th><th>Qty</th><th>Price</th><th>P&L</th><th>Reason</th></tr>${b.orders.slice().reverse().slice(0, 40).map(o => `<tr><td>${E(o.date)} ${E(o.time)}</td><td class="${o.type === 'BUY' ? 'up' : 'dn'}">${o.type}</td><td>${E(o.symbol)}</td><td>${o.qty}</td><td>${inr(o.price)}</td><td class="${(o.pnl || 0) >= 0 ? 'up' : 'dn'}">${o.pnl != null ? inr(o.pnl) : ''}</td><td style="color:var(--mu)">${E(o.reason || '')}</td></tr>`).join('')}</table>` : '<p class="note">No orders yet.</p>'}</div>
  <p class="tech">${b.mode === 'PAPER' ? 'PAPER trading: orders are simulated and never reach the exchange.' : 'LIVE trading: orders are sent to Fyers.'} Live trading stays off until you change LIVE_TRADING in fyers_execution.py yourself. Not investment advice.</p></div>`;
  if (!full && $('botbody')) { $('botbody').outerHTML = html; return; }
  return html;
}
function eqSVG(tr) {
  if (tr.length < 2) return '';
  const W = 800, H = 240, v = [0, ...tr.map(t => t.eq)], m = Math.min(...v), M = Math.max(...v), X = i => i / (v.length - 1) * W, Y = x => H - 12 - (x - m) / (M - m || 1) * (H - 24);
  const d = v.map((x, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(x).toFixed(1)}`).join(''), c = v.at(-1) >= 0 ? 'up' : 'dn';
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:220px;display:block"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--mu)" stroke-dasharray="3 5" opacity=".5" vector-effect="non-scaling-stroke"/><path d="${d}" fill="none" stroke="var(--${c})" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
let simTimer = null;
/* LIVE SIM: simulated market + bot trading it in real time (sim_market.py, runs 24/7 on the server). */
const LS = { data: null, timer: null, speed: 1 };
async function lsPoll() {
  try { LS.data = await api('/api/bot/livesim', 8000); LS.speed = LS.data.speed || LS.speed; } catch (e) { LS.data = { ok: false, error: e.message }; }
  lsRender();
}
function lsEnsure() { if (!LS.timer) { lsPoll(); LS.timer = setInterval(lsPoll, 1000); } lsRender(); }
function lsKPIs(d) {
  d = d || LS.data || {}; const tot = d.total_pnl || 0;
  return kpis([[(tot >= 0 ? '+' : '') + inr(tot), 'TOTAL P&L', tot >= 0 ? 'var(--up)' : 'var(--dn)'], [String(d.orders || 0), 'ORDERS', '#e8a33c'],
    [String((d.positions || []).length), 'OPEN POSITIONS', '#5b8def'], [d.closed ? (d.win_rate * 100).toFixed(1) + '%' : '––', d.closed ? `WIN RATE · ${d.closed} CLOSED` : 'WIN RATE', '#a78bfa']]);
}
function miniChart(st) {
  const c = st.closes, n = c.length; if (n < 2) return '';
  const sm = st.sma50.map((v, k) => v || null), vals = c.concat(sm.filter(Boolean));
  const W = 300, H = 90, lo = Math.min(...vals), hi = Math.max(...vals), X = k => k / (n - 1) * W, Y = v => H - 6 - (v - lo) / (hi - lo || 1) * (H - 12);
  const line = c.map((v, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const sml = sm.map((v, k) => v ? `${k && sm[k - 1] ? 'L' : 'M'}${X(k).toFixed(1)},${Y(v).toFixed(1)}` : '').join('');
  const up = c[n - 1] >= c[0];
  const mk = st.markers.map(m => { const x = X(m.i), y = Y(c[m.i]); return m.t === 'B' ? `<path d="M${x},${y + 4}l-5,9h10z" fill="var(--up)"/>` : `<path d="M${x},${y - 4}l-5,-9h10z" fill="var(--dn)"/>`; }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:90px;display:block;margin-top:8px"><path d="${sml}" fill="none" stroke="#d9a441" stroke-width="1" opacity=".8" vector-effect="non-scaling-stroke"/><path d="${line}" fill="none" stroke="var(--${up ? 'up' : 'dn'})" stroke-width="1.6" vector-effect="non-scaling-stroke"/>${mk}</svg>`;
}
function lsRender() {
  if (L.botMode !== 'livesim') return;
  const d = LS.data; if (!d) return;
  if (!d.ok) { if ($('botk')) $('botk').innerHTML = `<p class="note" style="text-align:center">Simulated market unavailable: ${E(d.error || '')}. Restart the bot (restart_clean.command).</p>`; return; }
  const last = (d.log || [])[0];
  if ($('botk') && location.hash !== '#/bot') {
    $('botk').innerHTML = lsKPIs(d);
    $('botnote').innerHTML = `<span class="badge LIVE"><i></i>SIMULATED MARKET · 24/7</span> <b class="num">${E(d.clock)}</b>${last ? ` · last order: <span class="${last.type === 'BUY' ? 'up' : 'dn'}">${last.type}</span> ${E(last.symbol)} @ ${inr(last.price)} · ${E(last.why)}` : ''} · <a href="#/bot" style="color:var(--tx)">Open full view →</a>`;
  }
  if (!$('lsclock')) return;
  $('lsclock').textContent = d.clock;
  $('lskpi').innerHTML = lsKPIs(d);
  document.querySelectorAll('#lsspd button').forEach(x => x.classList.toggle('on', +x.dataset.v === d.speed));
  $('lscharts').innerHTML = d.stocks.map(st => `<div class="card" style="padding:14px"><div class="t"><span><b style="color:var(--tx)">${E(st.symbol)}</b></span><span class="badge ${st.status.startsWith('HOLD') ? 'LIVE' : 'CLOSED'}"><i></i>${E(st.status)}</span></div>
     <div class="num" style="font-size:22px;font-weight:600;margin-top:6px">${inr(st.price)} <span class="${st.pc >= 0 ? 'up' : 'dn'}" style="font-size:14px">${pct(st.pc)}</span></div>${miniChart(st)}
     <div class="ts num">Trend ${st.trend} · RSI ${st.rsi != null ? st.rsi.toFixed(0) : '–'} · RSI-2 ${st.rsi2 != null ? st.rsi2.toFixed(0) : '–'} · z ${st.z != null ? st.z.toFixed(1) : '–'}</div></div>`).join('');
  $('lspos').innerHTML = d.positions.length ? `<table class="tb num"><tr><th>Stock</th><th>Qty</th><th>Entry</th><th>Now</th><th>Target</th><th>Stop</th><th>P&L</th></tr>${d.positions.map(o => `<tr><td>${E(o.symbol)}</td><td>${o.qty}</td><td>${inr(o.entry)}</td><td>${inr(o.price)}</td><td>${inr(o.tp)}</td><td>${inr(o.sl)}</td><td class="${o.pnl >= 0 ? 'up' : 'dn'}">${inr(o.pnl)} (${pct(o.pnl_pct)})</td></tr>`).join('')}</table>` : '<p class="note">No open positions. The bot is watching the charts for a dip in an uptrend.</p>';
  $('lslog').innerHTML = d.log.length ? `<table class="tb num">${d.log.map(o => `<tr><td>${E(o.time)}</td><td class="${o.type === 'BUY' ? 'up' : 'dn'}">${o.type}</td><td>${E(o.symbol)}</td><td>${inr(o.price)}</td><td style="color:var(--mu)">${E(o.why)}</td></tr>`).join('')}</table>` : '<p class="note">No orders yet.</p>';
}
function lsPanelHTML() {
  return `<div class="panel" style="border-color:var(--up)"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><div><h4 style="margin:0">LIVE SIM · SIMULATED MARKET · 24/7</h4><div class="note" style="margin-top:4px">A simulated stock market running in real time (one 5-minute bar per second), unrelated to any past data. The bot reads each chart's indicators (SMA 50/200 trend, RSI, RSI-2, Bollinger z-score, ATR), buys sharp dips in uptrends and sells at its target, stop or time limit, ₹50,000 per trade after Fyers charges. Simulated prices, no real orders.</div></div>
   <div class="seg" id="lsspd"><span>Speed</span>${[1, 4, 10].map(v => `<button data-v="${v}" class="${LS.speed === v ? 'on' : ''}">${v}×</button>`).join('')}</div></div>
   <div style="display:flex;justify-content:space-between;align-items:baseline;margin:14px 0 6px;flex-wrap:wrap;gap:8px"><div style="font-size:26px;font-weight:700" class="num" id="lsclock">—</div><span class="badge LIVE"><i></i>SIMULATED MARKET · TRADING</span></div>
   <div id="lskpi"></div>
   <div class="grid" id="lscharts" style="margin-top:14px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))"></div>
   <p class="note">Green line: price · amber line: 50-bar average · ▲ buy · ▼ sell</p>
   <div class="cols"><div class="panel"><h4>Open positions</h4><div id="lspos"></div></div>
   <div class="panel"><h4>Order log</h4><div id="lslog" style="max-height:340px;overflow:auto"></div></div></div></div>`;
}
document.addEventListener('click', e => {
  const sp = e.target.closest('#lsspd button'); if (sp) { api('/api/bot/livesim/speed?x=' + sp.dataset.v).then(lsPoll); }
  const rs = e.target.closest('#rpspd button'); if (rs) { api('/api/bot/replay/speed?x=' + rs.dataset.v).then(rpPoll); }
});

/* SIM tab: real-time replay of the model's REAL out-of-sample trades on real NSE prices (replay.py) */
const RP = { data: null, timer: null };
async function rpPoll() { try { RP.data = await api('/api/bot/replay', 8000); } catch (e) { RP.data = { ok: false, error: e.message }; } rpRender(); }
function rpRender() {
  const d = RP.data; if (!d || !$('rpclock')) { if (!$('rpclock')) { clearInterval(RP.timer); RP.timer = null; } return; }
  if (!d.ok) { $('rpclock').textContent = 'Replay unavailable: ' + (d.error || ''); return; }
  $('rpclock').textContent = `${new Date(d.sim_date).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })} · ${d.sim_time}`;
  $('rpbadge').innerHTML = `<i></i>${d.segment === 'holdout' ? '2022-2026 HOLDOUT' : '2018-2021'} · ${E(d.progress)}`;
  $('rpkpi').innerHTML = lsKPIs(d);
  document.querySelectorAll('#rpspd button').forEach(x => x.classList.toggle('on', +x.dataset.v === d.speed));
  $('rppos').innerHTML = d.positions.length ? `<table class="tb num"><tr><th>Stock</th><th>Qty</th><th>Bought</th><th>Entry</th><th>Now</th><th>P&L</th></tr>${d.positions.map(o => `<tr><td>${E(o.symbol)}</td><td>${o.qty}</td><td>${E(o.entry_date)}</td><td>${inr(o.entry)}</td><td>${inr(o.price)}</td><td class="${o.pnl >= 0 ? 'up' : 'dn'}">${inr(o.pnl)} (${pct(o.pnl_pct)})</td></tr>`).join('')}</table>` : '<p class="note">No open positions.</p>';
  $('rplog').innerHTML = d.log.length ? `<table class="tb num">${d.log.map(o => `<tr><td>${E(o.date)} ${E(o.time)}</td><td class="${o.type === 'BUY' ? 'up' : 'dn'}">${o.type}</td><td>${E(o.symbol)}</td><td>${inr(o.price)}</td><td style="color:var(--mu)">${E(o.why)}</td></tr>`).join('')}</table>` : '<p class="note">No orders yet.</p>';
}
function rpPanelHTML() {
  return `<div class="panel" style="border-color:var(--up);margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><div><h4 style="margin:0">REAL-DATA REPLAY · the model's real out-of-sample trades, played in real time</h4><div class="note" style="margin-top:4px">Real NSE prices from past days (one trading day ≈ 45 s). Every BUY and SELL is a trade the model really made in its walk-forward test, after Fyers charges. This is the evidence for the win rate.</div></div>
   <div class="seg" id="rpspd"><span>Speed</span>${[1, 4, 15].map(v => `<button data-v="${v}">${v}×</button>`).join('')}</div></div>
   <div style="display:flex;justify-content:space-between;align-items:baseline;margin:14px 0 6px;flex-wrap:wrap;gap:8px"><div style="font-size:24px;font-weight:700" class="num" id="rpclock">—</div><span class="badge LIVE" id="rpbadge"><i></i>REPLAY</span></div>
   <div id="rpkpi"></div>
   <div class="cols"><div class="panel"><h4>Open positions (real prices)</h4><div id="rppos"></div></div>
   <div class="panel"><h4>Order log</h4><div id="rplog" style="max-height:300px;overflow:auto"></div></div></div></div>`;
}
function renderBotSim() {
  const S = L.sim; const body = $('botbody'); if (!body) return;
  if (!S) { body.innerHTML = '<p class="note">Loading replay…</p>'; return; }
  if (S.error) { body.innerHTML = `<p class="note">Replay unavailable: ${E(S.error)}. Run train_model.py to create models/oos_trades.csv.</p>`; return; }
  clearInterval(simTimer);
  const T = S.trades, r = S.rules || {}, byYear = {};
  T.forEach(t => { const y = t.entry_date.slice(0, 4); (byYear[y] ||= { n: 0, w: 0, pnl: 0 }); byYear[y].n++; byYear[y].w += t.net_ret > 0; byYear[y].pnl += t.pnl; });
  body.innerHTML = `${rpPanelHTML()}
  <h4 style="margin:8px 0">All out-of-sample results</h4>${simKPIs(S)}
  <div class="chips" id="simscope" style="margin-top:18px"><button data-s="bot" class="${S.scope === 'bot' ? 'on' : ''}">Bot's focus stocks</button><button data-s="all" class="${S.scope === 'all' ? 'on' : ''}">Whole universe (what the bot scans)</button></div>
  <div class="panel"><h4>Equity curve · ₹${(S.positionValue || 50000).toLocaleString('en-IN')} per trade · after Fyers charges · <span id="simclock"></span></h4><div id="simeq"></div><div style="display:flex;gap:8px;margin-top:8px"><button class="pill" id="simplay" style="padding:10px 18px">▶ Replay</button></div></div>
  <div class="cols">
   <div class="panel"><h4>By year</h4><table class="tb num"><tr><th>Year</th><th>Trades</th><th>Win rate</th><th>P&L</th></tr>${Object.entries(byYear).map(([y, x]) => `<tr><td>${y}</td><td>${x.n}</td><td>${(x.w / x.n * 100).toFixed(1)}%</td><td class="${x.pnl >= 0 ? 'up' : 'dn'}">${inr(x.pnl)}</td></tr>`).join('')}</table></div>
   <div class="panel"><h4>Rules being replayed</h4><div class="r2"><span>Entry</span><b>next open after P(win) ≥ ${S.threshold ? (S.threshold * 100).toFixed(1) + '%' : '—'}</b></div><div class="r2"><span>Take profit</span><b>${r.tp_atr ?? '—'} × ATR</b></div><div class="r2"><span>Stop loss</span><b>${r.sl_atr ?? '—'} × ATR</b></div><div class="r2"><span>Time stop</span><b>${r.max_hold ?? '—'} trading days</b></div><div class="r2"><span>Profit factor</span><b class="num">${S.summary.profitFactor ? S.summary.profitFactor.toFixed(2) : '—'}</b></div><p class="note">Every trade here comes from a walk-forward test: each year was predicted by a model trained only on earlier years.</p></div>
  </div>
  <div class="panel" style="margin-top:14px"><h4>Trades</h4><div style="max-height:420px;overflow:auto"><table class="tb num"><tr><th>Entry</th><th>Exit</th><th>Stock</th><th>P(win)</th><th>Exit</th><th>Net</th></tr>${T.slice().reverse().map(t => `<tr><td>${E(t.entry_date)}</td><td>${E(t.exit_date)}</td><td>${E(t.symbol)}</td><td>${(t.p * 100).toFixed(1)}%</td><td style="color:var(--mu)">${t.exit_reason}</td><td class="${t.net_ret > 0 ? 'up' : 'dn'}">${pct(t.net_ret * 100)}</td></tr>`).join('')}</table></div></div>
  <p class="tech">SIM mode replays real historical prices through the real model. Past results do not guarantee future results; the replay assumes fills at the next open and at the target/stop levels. Not investment advice.</p>`;
  $('simeq').innerHTML = eqSVG(T); $('simclock').textContent = `${E(S.summary.from)} → ${E(S.summary.to)}`;
  clearInterval(RP.timer); RP.timer = setInterval(rpPoll, 1500); rpPoll();
  $('simscope').onclick = e => { const b = e.target.closest('button'); if (!b) return; SIM_SCOPE = b.dataset.s; L.sim = null; renderBotSim(); loadSim(); };
  $('simplay').onclick = () => { clearInterval(simTimer); let k = 1; simTimer = setInterval(() => { k = Math.min(T.length, k + Math.max(1, Math.round(T.length / 120))); $('simeq').innerHTML = eqSVG(T.slice(0, k)); $('simclock').textContent = `${T[k - 1].exit_date} · ${k}/${T.length} trades · ${inr(T[k - 1].eq)}`; if (k >= T.length) clearInterval(simTimer); }, 50); };
}
window.openBot = function () {
  aw.innerHTML = `<button class="back" onclick="closeAsset()">← Back to ATLAS</button>
  <h1 style="font-size:clamp(40px,8vw,96px);letter-spacing:-.04em;margin:0;line-height:1">Trading bot.</h1>
  <p class="lead" style="margin-top:12px">ML swing-trade model on NSE stocks · ${L.botMode === 'live' ? 'live state from the Python bot' : L.botMode === 'sim' ? 'replay of real out-of-sample history' : 'bot trading a simulated market in real time, 24/7'}.</p>
  <div style="margin:0 0 24px" id="bsegw2">${segHTML()}</div>
  ${L.botMode === 'live' ? renderBotLive(true) : L.botMode === 'livesim' ? lsPanelHTML() : '<div id="botbody"></div>'}`;
  asEl.classList.add('on'); asEl.scrollTop = 0; document.body.style.overflow = 'hidden';
  if (L.botMode === 'sim') { if (!L.sim) loadSim(); renderBotSim(); }
  if (L.botMode === 'livesim') lsEnsure();
};

/* ---------------------------------------------------------------------
   boot
   --------------------------------------------------------------------- */
function goLiveSim() {
  if (!/^#\/?livesim$/.test(location.hash)) return;
  L.botMode = 'livesim'; lsEnsure(); homeBot();
  setTimeout(() => { document.querySelectorAll('#bot .rev').forEach(e => e.classList.add('in')); const el = $('bot'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 700);
}
addEventListener('hashchange', goLiveSim); goLiveSim();
// When NSE is closed (nights, weekends), open the bot section in LIVE SIM demo trading by default
(() => { const p = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', weekday: 'short', hour: 'numeric', minute: 'numeric', hour12: false }).formatToParts(new Date());
  const g = t => p.find(x => x.type === t).value, m = (+g('hour') % 24) * 60 + +g('minute'), wd = g('weekday');
  if (L.botMode === 'live' && (wd === 'Sat' || wd === 'Sun' || m < 555 || m >= 930)) { L.botMode = 'livesim'; lsEnsure(); setTimeout(homeBot, 50); } })();
loadQuotes(); setInterval(loadQuotes, 60000);
loadIndia(); setInterval(loadIndia, 300000);
setTimeout(loadCaps, 4000);
loadBot(); setInterval(loadBot, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { loadQuotes(); loadBot(); } });
if (location.hash === '#/bot' || /^#\/asset\//.test(location.hash)) route();
})();
