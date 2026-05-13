// Tiny vanilla dashboard. Polls the FastAPI server every 5s.
const fmt = (n, d = 2) => (n == null || isNaN(n) ? "--" : Number(n).toFixed(d));
const fmtUSD = (n) => (n == null ? "--" : (n >= 0 ? "+" : "-") + "$" + Math.abs(n).toFixed(2));
const short = (s, n = 10) => (s && s.length > n ? s.slice(0, n) + "…" : s);
const when = (ts) => {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

function renderPnL(d) {
  const el = document.getElementById("pnl");
  const total = d.total || 0;
  el.textContent = fmtUSD(total);
  el.className = "bigpnl " + (total >= 0 ? "pos" : "neg");
  const bs = document.getElementById("pnl-bs");
  bs.innerHTML = "";
  for (const [k, v] of Object.entries(d.by_strategy || {})) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${k}</td><td class="num ${v >= 0 ? "pos" : "neg"}">${fmtUSD(v)}</td>`;
    bs.appendChild(tr);
  }
}

function renderPositions(rows) {
  const tb = document.querySelector("#pos tbody");
  tb.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="mono">${short(r.token_id, 12)}</td>
      <td>${r.strategy}</td>
      <td class="num">${fmt(r.net_shares, 1)}</td>
      <td class="num ${r.net_usd >= 0 ? "pos" : "neg"}">${fmtUSD(r.net_usd)}</td>`;
    tb.appendChild(tr);
  }
  if (!rows.length) tb.innerHTML = `<tr><td colspan="4" class="mono">no open positions</td></tr>`;
}

function renderFills(rows) {
  const tb = document.querySelector("#fills tbody");
  tb.innerHTML = "";
  for (const r of rows) {
    const side = (r.side || "").toLowerCase();
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${when(r.ts)}</td>
      <td><span class="pill ${side}">${r.side}</span></td>
      <td>${r.strategy}</td>
      <td class="num">${fmt(r.price, 3)}</td>
      <td class="num">${fmt(r.size_shares, 1)}</td>
      <td>${r.dry_run ? '<span class="pill dry">DRY</span>' : '<span class="pill">LIVE</span>'}</td>`;
    tb.appendChild(tr);
  }
  if (!rows.length) tb.innerHTML = `<tr><td colspan="6" class="mono">no fills yet</td></tr>`;
}

function renderSignals(rows) {
  const tb = document.querySelector("#sigs tbody");
  tb.innerHTML = "";
  for (const r of rows) {
    const side = (r.side || "").toLowerCase();
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${when(r.ts)}</td>
      <td>${r.strategy}</td>
      <td><span class="pill ${side}">${r.side}</span></td>
      <td class="num">${fmt(r.intended_price, 3)}</td>
      <td class="num">${fmt(r.edge, 3)}</td>`;
    tb.appendChild(tr);
  }
  if (!rows.length) tb.innerHTML = `<tr><td colspan="5" class="mono">no signals yet</td></tr>`;
}

function renderMarkets(rows) {
  const tb = document.querySelector("#mkts tbody");
  tb.innerHTML = "";
  for (const m of rows) {
    const tr = document.createElement("tr");
    const url = m.slug ? `https://polymarket.com/event/${m.slug}` : "#";
    tr.innerHTML = `<td><a href="${url}" target="_blank" rel="noopener">${m.question || m.id}</a></td>
      <td class="num">${fmt(m.lastTradePrice, 3)}</td>
      <td class="num">${fmt(m.volume24hr, 0)}</td>
      <td class="num">${fmt(m.liquidity, 0)}</td>`;
    tb.appendChild(tr);
  }
}

async function tick() {
  try {
    const [pnl, pos, fills, sigs, mkts] = await Promise.all([
      getJSON("/api/pnl"),
      getJSON("/api/positions"),
      getJSON("/api/recent-fills"),
      getJSON("/api/recent-signals"),
      getJSON("/api/markets"),
    ]);
    renderPnL(pnl);
    renderPositions(pos.positions || []);
    renderFills(fills.fills || []);
    renderSignals(sigs.signals || []);
    renderMarkets(mkts.markets || []);
  } catch (e) {
    console.error(e);
  }
}

tick();
setInterval(tick, 5000);
