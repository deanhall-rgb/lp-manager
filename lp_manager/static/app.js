const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let state = null;

const money = (n) => new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP',maximumFractionDigits:2}).format(Number(n||0));
const pct = (n,d=1) => `${Number(n||0).toFixed(d)}%`;
const when = (ts) => ts ? new Date(ts*1000).toLocaleString('en-GB',{dateStyle:'short',timeStyle:'short'}) : '—';
const esc = (s) => String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

async function api(path, opts={}) {
  const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  const data = await res.json().catch(()=>({}));
  if (!res.ok) throw new Error(data.detail || data.reason || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const el = $('#toast'); el.textContent = msg; el.classList.remove('hidden');
  setTimeout(()=>el.classList.add('hidden'), 3500);
}

function statusBadge(r) {
  if (!r) return '<span class="badge neutral">UNKNOWN</span>';
  if (r.range_state?.startsWith('OUT_') || r.band === 'INTERVENE') return '<span class="badge action">ACTION</span>';
  if (r.band === 'WATCH') return '<span class="badge watch">WATCH</span>';
  return '<span class="badge good">HEALTHY</span>';
}

function positionCard(p) {
  const r = p.range || {};
  const e = p.economics || {};
  const progress = Math.max(0,Math.min(100,Number(r.progress_pct||0)));
  return `<div class="position-card">
    <div class="position-top"><div><div class="pair">${esc(p.pair)}</div><div class="meta">${esc(p.protocol)} · ${esc(p.chain)} · ${p.token_id ? 'NFT '+esc(p.token_id) : esc(p.source)}${p.live_snapshot?.block_number?' · LIVE block '+esc(p.live_snapshot.block_number):''}</div><div class="meta"><span class="badge neutral">${esc((p.strategy_sleeve||'TACTICAL_CAMPAIGN').replaceAll('_',' '))}</span> · ${esc(p.monitoring_class||'ACTIVE')} · ${esc(p.directional_bias||'NEUTRAL')}</div></div>${statusBadge({...r, band: r.range_state==='IN_RANGE' ? (Number(r.nearest_edge_pct)<=5?'WATCH':'COMFORTABLE') : 'INTERVENE'})}</div>
    <div class="range-wrap"><div class="range-labels"><span>${esc(p.lower_price)}</span><span>current ${esc(p.current_price)}</span><span>${esc(p.upper_price)}</span></div><div class="range-track"><div class="range-fill"></div><div class="range-marker" style="left:${progress}%"></div></div></div>
    <div class="position-metrics">
      <div class="metric"><span>Lower edge</span><strong>${r.distance_lower_pct==null?'—':pct(r.distance_lower_pct)}</strong></div>
      <div class="metric"><span>Upper edge</span><strong>${r.distance_upper_pct==null?'—':pct(r.distance_upper_pct)}</strong></div>
      <div class="metric"><span>Unclaimed fees</span><strong>${money(p.unclaimed_fees)}</strong></div>
      <div class="metric"><span>Current APR</span><strong>${pct(p.apr_current)}</strong></div>
      <div class="metric"><span>Net P/L</span><strong>${money(e.net_profit)}</strong></div>
    </div>
    <div class="position-actions">
      <button class="btn small plan-btn" data-id="${esc(p.id)}">Generate plan</button>
      <button class="btn secondary small collect-btn" data-id="${esc(p.id)}">Prepare collect</button>
      <button class="btn danger small close-btn" data-id="${esc(p.id)}">Prepare close</button>
      <button class="btn secondary small detail-btn" data-id="${esc(p.id)}">Details</button>
    </div>
  </div>`;
}


function renderLiveStatus(live) {
  const banner=$('#live-banner'); if(!banner) return;
  const configured=(live?.chains||[]).filter(c=>c.rpc_configured).length;
  const good=live?.live_mode_ready;
  banner.innerHTML=`<div class="panel-head"><div><h2>${good?'Live data ready':'Demo / configuration mode'}</h2><p>${good?`Wallet ${esc(live.wallet_display)} · ${configured} RPC chain${configured===1?'':'s'} configured`:'Add WALLET_ADDRESS and at least one RPC URL to .env, then refresh.'}</p></div><div class="top-actions"><span class="badge ${good?'good':'watch'}">${good?'LIVE READ-ONLY':'NOT LIVE'}</span><button id="live-refresh-now" class="btn small" ${good?'':'disabled'}>Refresh wallet</button></div></div><div class="live-grid"><div class="live-chip"><strong>Wallet</strong><span>${live?.wallet_configured?esc(live.wallet_display):'not configured'}</span></div><div class="live-chip"><strong>Market data</strong><span>${live?.market_data?.geckoterminal?'GeckoTerminal ready':'disabled'}</span></div><div class="live-chip"><strong>Last chain refresh</strong><span>${live?.last_refresh?.read_at?when(live.last_refresh.read_at):'never'}</span></div><div class="live-chip"><strong>Safety</strong><span>signing OFF · broadcast OFF</span></div></div>`;
  const btn=$('#live-refresh-now'); if(btn) btn.onclick=()=>refreshLiveWallet();
}

function renderSystemLive(live){
 const t=$('#live-status'); if(!t) return;
 t.innerHTML=`<div class="live-grid"><div class="live-chip"><strong>Wallet</strong><span class="${live?.wallet_valid?'provider-ok':'provider-off'}">${live?.wallet_valid?esc(live.wallet_display):'missing / invalid'}</span></div>${(live?.chains||[]).map(c=>`<div class="live-chip"><strong>${esc(c.name)}</strong><span class="${c.rpc_configured?'provider-ok':'provider-off'}">${c.rpc_configured?'RPC configured':'RPC missing'}</span><div class="meta">chain ${esc(c.chain_id)} · ${esc(c.rpc_env)}</div></div>`).join('')}</div>`;
}

async function refreshLiveWallet(){
  toast('Reading live LP positions…');
  try{const r=await api('/api/live/refresh',{method:'POST',body:JSON.stringify({})}); const ok=(r.chains||[]).filter(x=>x.ok); const found=ok.reduce((n,x)=>n+Number(x.positions||0),0); toast(`Live refresh complete: ${found} owned position${found===1?'':'s'} across ${ok.length} chain${ok.length===1?'':'s'}`); await refresh();}
  catch(e){toast(`Live refresh failed: ${e.message}`)}
}

function livePoolCard(p){
 const e=p.evaluation||{}; const pc=p.price_change_percentage||{};
 return `<div class="pool-card"><div class="position-top"><div><div class="pair">${esc(p.pair)}</div><div class="meta">${esc(p.chain)} · ${esc(p.dex_id||p.protocol)} · ${esc((p.pool_address||'').slice(0,10))}…</div></div><span class="badge ${e.preferred_sleeve==='CORE_INCOME'?'good':e.preferred_sleeve==='TACTICAL_CAMPAIGN'?'watch':'neutral'}">${esc((e.preferred_sleeve||'PRELIM').replaceAll('_',' '))}</span></div><div class="pool-grid"><div class="metric"><span>TVL</span><strong>${money(p.tvl_usd)}</strong></div><div class="metric"><span>Volume 24h</span><strong>${money(p.volume_24h_usd)}</strong></div><div class="metric"><span>Price 24h</span><strong>${pct(pc.h24||0)}</strong></div><div class="metric"><span>Core pre-score</span><strong>${Number(e.core_pre_score||0).toFixed(0)}</strong></div><div class="metric"><span>Tactical pre-score</span><strong>${Number(e.tactical_pre_score||0).toFixed(0)}</strong></div></div><div class="position-actions"><button class="btn small live-pool-detail" data-chain="${esc(p.chain)}" data-address="${esc(p.pool_address)}">Open pool</button><button class="btn secondary small live-pool-replay" data-chain="${esc(p.chain)}" data-address="${esc(p.pool_address)}" data-sleeve="${esc(e.preferred_sleeve||'CORE_INCOME')}">Replay 30d</button></div></div>`;
}

async function loadLivePools(){
 const chain=$('#live-scout-chain').value; const target=$('#live-pool-list'); target.innerHTML='<div class="empty-state">Loading live pools…</div>';
 try{const r=await api(`/api/scout/live/${encodeURIComponent(chain)}?limit=20`); target.innerHTML=r.pools?.length?r.pools.map(livePoolCard).join(''):'<div class="empty-state">No Uniswap V3 pools returned for this chain/provider page.</div>'; bindLivePoolButtons();}
 catch(e){target.innerHTML=`<div class="empty-state">${esc(e.message)}</div>`}
}
function bindLivePoolButtons(){
 $$('.live-pool-detail').forEach(b=>b.onclick=()=>showLivePool(b.dataset.chain,b.dataset.address).catch(e=>toast(e.message)));
 $$('.live-pool-replay').forEach(b=>b.onclick=()=>replayLivePool(b.dataset.chain,b.dataset.address,b.dataset.sleeve,30).catch(e=>toast(e.message)));
}
async function showLivePool(chain,address){
 const p=await api(`/api/scout/pools/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`); const e=p.evaluation||{}; const candles=p.ohlcv_7d||[]; const first=candles[0]?.close, last=candles[candles.length-1]?.close;
 modal(`<h2>${esc(p.pair)} · ${esc(chain)}</h2><p>${esc(p.dex_id||p.protocol)} · <span class="mono">${esc(address)}</span></p><div class="pool-grid"><div class="metric"><span>TVL</span><strong>${money(p.tvl_usd)}</strong></div><div class="metric"><span>Volume 24h</span><strong>${money(p.volume_24h_usd)}</strong></div><div class="metric"><span>Base USD</span><strong>${money(p.base_token_price_usd)}</strong></div><div class="metric"><span>7d candles</span><strong>${candles.length}</strong></div><div class="metric"><span>7d move</span><strong>${first&&last?pct((last/first-1)*100):'—'}</strong></div><div class="metric"><span>Core / Tactical</span><strong>${Number(e.core_pre_score||0).toFixed(0)} / ${Number(e.tactical_pre_score||0).toFixed(0)}</strong></div></div><p>${esc(e.note||'')}</p><div class="modal-actions"><button class="btn" id="pool-replay30">Replay 30d</button><button class="btn secondary" id="pool-replay90">Replay 90d</button></div><details><summary>Raw live pool data</summary><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`);
 $('#pool-replay30').onclick=()=>replayLivePool(chain,address,e.preferred_sleeve||'CORE_INCOME',30).catch(x=>toast(x.message)); $('#pool-replay90').onclick=()=>replayLivePool(chain,address,e.preferred_sleeve||'CORE_INCOME',90).catch(x=>toast(x.message));
}
async function replayLivePool(chain,address,sleeve,days){
 toast(`Running ${days}d live-history replay…`); const data=await api(`/api/scout/pools/${encodeURIComponent(chain)}/${encodeURIComponent(address)}/replay`,{method:'POST',body:JSON.stringify({days,sleeve,initial_capital:1000})}); const r=data.result,s=r.summary||{}; modal(`<h2>${esc(data.pool.pair)} · ${days}d replay</h2><p>${pct(s.active_time_pct||0,1)} active · ${Number(s.strategy_reviews||0)} strategy reviews · ${Number(s.ai_wakes||0)} AI wakes</p><p><strong>No-lookahead:</strong> ${r.no_lookahead?'verified':'not verified'} · <strong>Economics:</strong> ${esc(r.economics?.mode||'')}</p><pre>${esc(JSON.stringify({range:r.range_selection?.chosen,summary:s,economics:r.economics},null,2))}</pre>`); await refresh();
}

function renderSummary(s) {
  const cards = [
    ['Capital deployed', money(s.capital_deployed), `${s.open_positions} open position${s.open_positions===1?'':'s'}`],
    ['Fees today', money(s.fees_today), `7d ${money(s.fees_7d)}`],
    ['Fees 30d', money(s.fees_30d), `unclaimed ${money(s.unclaimed_fees)}`],
    ['Weighted APR', pct(s.current_weighted_apr), 'current portfolio rate'],
    ['Capital earning', pct(s.earning_pct,0), `${s.out_of_range_count} out of range`],
    ['Net P/L', money(s.net_profit_all_time), 'principal + fees + IL − gas'],
  ];
  $('#summary-grid').innerHTML = cards.map(([l,v,h])=>`<div class="stat"><div class="label">${l}</div><div class="value">${v}</div><div class="hint">${h}</div></div>`).join('');
}

function renderAttention(rows) {
  $('#attention-list').innerHTML = rows.length ? rows.slice(0,8).map(x=>`<div class="attention-item"><div class="attention-row"><div><strong>${esc(x.position.pair)}</strong><div class="meta">${esc(x.risk.band)} · ${esc(x.risk.range_state)}</div></div><div class="risk-score">${Number(x.risk.score).toFixed(0)}</div></div><p>${esc(x.risk.reason)}</p></div>`).join('') : '<div class="empty-state">No open positions require attention.</div>';
}

function renderTimeline(target, rows, kind) {
  $(target).innerHTML = rows?.length ? rows.map(r => {
    if (kind === 'decision') return `<div class="timeline-item"><div class="timeline-title"><span>${esc(r.summary)}</span><span class="badge ${r.severity==='ACTION'?'action':r.severity==='WATCH'?'watch':'neutral'}">${esc(r.severity)}</span></div><div class="timeline-meta">${when(r.created_at)} · ${esc(r.action)} · confidence ${pct(Number(r.confidence)*100,0)}<br>${esc(r.rationale)}</div></div>`;
    return `<div class="timeline-item"><div class="timeline-title"><span>${esc(r.action_type)}</span><span class="badge ${r.status==='BLOCKED'?'action':'neutral'}">${esc(r.status)}</span></div><div class="timeline-meta">${when(r.created_at)} · ${esc(r.position_id || 'new position')} · ${esc(r.mode)}</div></div>`;
  }).join('') : '<div class="empty-state">Nothing recorded yet.</div>';
}

function renderTable(rows) {
  $('#all-positions-table').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Pair</th><th>Sleeve</th><th>Status</th><th>Range</th><th>Current</th><th>Fees today</th><th>Unclaimed</th><th>APR</th><th>Net P/L</th><th>Source</th></tr></thead><tbody>${rows.map(p=>`<tr><td><strong>${esc(p.pair)}</strong></td><td>${esc((p.strategy_sleeve||'TACTICAL_CAMPAIGN').replaceAll('_',' '))}</td><td>${esc(p.status)}</td><td>${esc(p.lower_price)} → ${esc(p.upper_price)}</td><td>${esc(p.current_price)}</td><td>${money(p.fees_today)}</td><td>${money(p.unclaimed_fees)}</td><td>${pct(p.apr_current)}</td><td>${money(p.economics?.net_profit)}</td><td>${esc(p.source)}</td></tr>`).join('')}</tbody></table></div>`;
}

function renderSleeves(profiles, portfolio) {
  const target = $('#strategy-sleeves'); if (!target) return;
  const names = ['CORE_INCOME','TACTICAL_CAMPAIGN'];
  target.innerHTML = names.map(name=>{
    const p=profiles?.[name]||{}; const b=portfolio?.[name]||{};
    const targetText = name==='CORE_INCOME' ? `${p.target_monthly_net_min_pct||8}–${p.target_monthly_net_max_pct||20}% monthly target band` : `${p.target_campaign_net_pct||5}% prototype campaign target`;
    return `<div class="position-card"><div class="position-top"><div><div class="pair">${esc(name.replaceAll('_',' '))}</div><div class="meta">${esc(p.capital_role||'')} · preferred ${Number(p.preferred_hold_days||0)} days</div></div><span class="badge ${name==='CORE_INCOME'?'good':'watch'}">${Number(b.positions||0)} LIVE</span></div><p>${esc(p.objective||'')}</p><div class="position-metrics"><div class="metric"><span>Capital</span><strong>${money(b.capital_deployed||0)}</strong></div><div class="metric"><span>Share</span><strong>${pct(b.capital_share_pct||0,0)}</strong></div><div class="metric"><span>Fees 30d</span><strong>${money(b.fees_30d||0)}</strong></div><div class="metric"><span>Review</span><strong>${Math.round(Number(p.strategy_review_seconds||0)/60)}m</strong></div></div><div class="meta">${esc(targetText)} · ${esc(p.range_style||'')}</div></div>`;
  }).join('');
}

function renderScoutUniverse(rows) {
  const target=$('#scout-universe'); if(!target) return;
  target.innerHTML=(rows||[]).map(r=>`<div class="attention-item"><div class="attention-row"><div><strong>${esc(r.name)}</strong><div class="meta">chain ${esc(r.chain_id)} · ${esc((r.protocol_adapters||[]).join(', '))}</div></div><span class="badge neutral">${esc(r.cost_class)}</span></div><p>Core ${r.core_enabled?'enabled':'off'} · Tactical ${r.tactical_enabled?'enabled':'off'}${r.notes?' · '+esc(r.notes):''}</p></div>`).join('');
}


function renderOpportunityBook(rows) {
  const target=$('#opportunity-book'); if(!target) return;
  target.innerHTML=(rows||[]).length ? (rows||[]).slice(0,12).map(r=>{
    const c=r.candidate||{}; const e=r.evaluation||{};
    const actions = r.status==='APPROVED' || r.status==='REJECTED' ? '' : `<div class="position-actions"><button class="btn small opp-status" data-id="${esc(r.id)}" data-status="APPROVED">Approve</button><button class="btn secondary small opp-status" data-id="${esc(r.id)}" data-status="WATCH">Watch</button><button class="btn danger small opp-status" data-id="${esc(r.id)}" data-status="REJECTED">Reject</button></div>`;
    return `<div class="attention-item"><div class="attention-row"><div><strong>${esc(r.pair||c.pair)}</strong><div class="meta">${esc(r.chain||c.chain)} · ${esc(r.protocol||c.protocol)} · ${esc(r.status)}</div></div><div class="risk-score">${Number(r.preferred_score||0).toFixed(0)}</div></div><p>${esc((r.preferred_sleeve||'REJECTED').replaceAll('_',' '))} · Core ${Number(e.core?.score||0).toFixed(0)} · Tactical ${Number(e.tactical?.score||0).toFixed(0)}</p>${actions}</div>`;
  }).join('') : '<div class="empty-state">No scout candidates stored yet.</div>';
  $$('.opp-status').forEach(b=>b.onclick=async()=>{try{await api(`/api/scout/opportunities/${encodeURIComponent(b.dataset.id)}/status`,{method:'POST',body:JSON.stringify({status:b.dataset.status})});toast(`Opportunity ${b.dataset.status.toLowerCase()}`);await refresh();}catch(e){toast(e.message)}});
}


function renderReplayRuns(rows) {
  const target=$('#replay-runs'); if(!target) return;
  target.innerHTML=(rows||[]).length ? (rows||[]).map(r=>{
    const x=r.summary||{};
    return `<div class="attention-item"><div class="attention-row"><div><strong>${esc(r.scenario)}</strong><div class="meta">${esc(r.pair)} · ${esc(r.sleeve).replaceAll('_',' ')} · ${when(r.created_at)}</div></div><span class="badge neutral">${pct(x.active_time_pct||0,0)} active</span></div><p>${Number(x.strategy_reviews||0)} strategy reviews · ${Number(x.ai_wakes||0)} AI wakes · ${Number(x.material_events||0)} material events</p><div class="position-actions"><button class="btn secondary small replay-detail" data-id="${esc(r.id)}">Open replay</button></div></div>`;
  }).join('') : '<div class="empty-state">No replay runs yet. Run one of the deterministic demo scenarios.</div>';
  $$('.replay-detail').forEach(b=>b.onclick=()=>showReplay(b.dataset.id).catch(e=>toast(e.message)));
}

function renderOutcomeAudits(rows) {
  const target=$('#outcome-audits'); if(!target) return;
  target.innerHTML=(rows||[]).length ? (rows||[]).slice(0,12).map(r=>`<div class="attention-item"><div class="attention-row"><div><strong>${esc(r.verdict)}</strong><div class="meta">${esc(r.subject_type)} · ${esc(r.horizon)}</div></div><span class="badge ${r.verdict==='SUPPORTED'?'good':r.verdict==='QUESTIONABLE'||r.verdict==='EARLY_OR_UNNECESSARY'?'watch':'neutral'}">${when(r.created_at)}</span></div></div>`).join('') : '<div class="empty-state">No audited outcomes yet.</div>';
}

async function runReplayScenario(scenario) {
  const data=await api('/api/replay/run',{method:'POST',body:JSON.stringify({scenario})});
  const r=data.result; const s=r.summary||{};
  modal(`<h2>${esc(scenario.replaceAll('_',' '))} replay complete</h2><p>${pct(s.active_time_pct||0,1)} active time · ${Number(s.strategy_reviews||0)} strategy reviews · ${Number(s.ai_wakes||0)} AI wakes.</p><p><strong>Economics:</strong> ${esc(r.economics?.mode||'')}</p><pre>${esc(JSON.stringify({range:r.range_selection?.chosen,summary:r.summary,economics:r.economics},null,2))}</pre>`);
  await refresh();
}

async function showReplay(id) {
  const r=await api(`/api/replay/runs/${encodeURIComponent(id)}`);
  const decisions=(r.decisions||[]).slice(0,30);
  modal(`<h2>${esc(r.pair)} replay</h2><p>${esc(r.sleeve).replaceAll('_',' ')} · no-lookahead ${r.no_lookahead?'verified':'unknown'} · ${pct(r.summary?.active_time_pct||0,1)} active time</p><pre>${esc(JSON.stringify({range_selection:r.range_selection,summary:r.summary,economics:r.economics,decisions},null,2))}</pre>`);
}

async function buildSupportBundle() {
  const r=await api('/api/support/bundle',{method:'POST'});
  if(r.download){ window.location.href=r.download; toast('Support bundle built'); }
}

async function calculateTargets() {
  if(!state) return;
  const s=state.summary||{}; const rate=Number($('#target-rate')?.value||0);
  const data=await api('/api/targets/calculate',{method:'POST',body:JSON.stringify({capital:Number(s.capital_deployed||0),target_monthly_pct:rate,actual_today:Number(s.fees_today||0),actual_7d:Number(s.fees_7d||0),actual_30d:Number(s.fees_30d||0)})});
  const rows=[['Today',data.today],['7 days',data.week],['30 days',data.month]];
  $('#target-result').innerHTML=rows.map(([name,r])=>`<div class="stat"><div class="label">${name}</div><div class="value">${money(r.actual)} / ${money(r.target)}</div><div class="hint">${pct(r.attainment_pct,0)} of reference · ${esc(r.status)}</div></div>`).join('');
}

async function refresh() {
  try {
    state = await api('/api/overview');
    renderLiveStatus(state.live);
    renderSystemLive(state.live);
    renderSummary(state.summary);
    renderSleeves(state.strategy_profiles, state.portfolio_by_sleeve);
    renderScoutUniverse(state.scout_universe);
    renderOpportunityBook(state.opportunities);
    renderReplayRuns(state.replay_runs || []);
    renderOutcomeAudits(state.outcome_audits || []);
    const open = state.positions.filter(p=>p.status==='OPEN');
    $('#positions-list').innerHTML = open.length ? open.map(positionCard).join('') : '<div class="empty-state">No open positions yet.</div>';
    renderAttention(state.attention || []);
    renderTimeline('#decisions-list', state.decisions || [], 'decision');
    renderTimeline('#actions-list', state.actions || [], 'action');
    renderTimeline('#decision-journal', state.decisions || [], 'decision');
    renderTable(state.positions || []);
    $('#execution-json').textContent = JSON.stringify(state.execution,null,2);
    $('#legacy-json').textContent = JSON.stringify(state.legacy_sources,null,2);
    bindPositionButtons();
    await calculateTargets();
  } catch (e) { toast(`Refresh failed: ${e.message}`); }
}

function modal(html) { $('#modal-content').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); }

async function generatePlan(id) {
  const data = await api(`/api/positions/${encodeURIComponent(id)}/plan`,{method:'POST'});
  modal(`<h2>${esc(data.decision.summary)}</h2><p>${esc(data.decision.rationale)}</p><pre>${esc(JSON.stringify(data,null,2))}</pre><div class="modal-actions"><button class="btn" onclick="document.querySelector('#modal-close').click()">Done</button></div>`);
  await refresh();
}
async function prepareAction(id, type) {
  const label = type==='close' ? 'close/remove liquidity + collect' : 'collect fees';
  if (type==='close' && !confirm(`Prepare a build-only ${label} plan? Nothing will be signed or broadcast.`)) return;
  const data = await api(`/api/positions/${encodeURIComponent(id)}/prepare-${type}`,{method:'POST'});
  modal(`<h2>Prepared ${esc(type)} plan</h2><p>This is an inspectable build-only result. It has not moved funds.</p><pre>${esc(JSON.stringify(data,null,2))}</pre>`);
  await refresh();
}
async function showDetail(id) {
  const p = await api(`/api/positions/${encodeURIComponent(id)}`);
  modal(`<h2>${esc(p.pair)}</h2><p>Full position state, range metrics and deterministic risk.</p><pre>${esc(JSON.stringify(p,null,2))}</pre>`);
}

function bindPositionButtons() {
  $$('.plan-btn').forEach(b=>b.onclick=()=>generatePlan(b.dataset.id).catch(e=>toast(e.message)));
  $$('.collect-btn').forEach(b=>b.onclick=()=>prepareAction(b.dataset.id,'collect').catch(e=>toast(e.message)));
  $$('.close-btn').forEach(b=>b.onclick=()=>prepareAction(b.dataset.id,'close').catch(e=>toast(e.message)));
  $$('.detail-btn').forEach(b=>b.onclick=()=>showDetail(b.dataset.id).catch(e=>toast(e.message)));
}

function openPositionModal() {
  modal(`<h2>Prepare a new LP position</h2><p>V0.3 prep records the full strategy intent and keeps execution build-only.</p>
  <div class="form-grid">
    <div class="field"><label>Pair</label><input id="op-pair" value="DELTA/WETH"></div>
    <div class="field"><label>Protocol</label><select id="op-protocol"><option>UNISWAP_V3</option></select></div>
    <div class="field"><label>Chain</label><select id="op-chain"><option>BASE</option><option>ETHEREUM</option><option>ARBITRUM</option><option>OPTIMISM</option><option>POLYGON</option><option>ROBINHOOD_CHAIN</option></select></div>
    <div class="field"><label>Strategy sleeve</label><select id="op-sleeve"><option value="CORE_INCOME">Core income</option><option value="TACTICAL_CAMPAIGN" selected>Tactical campaign</option></select></div>
    <div class="field"><label>Directional bias</label><select id="op-bias"><option>NEUTRAL</option><option>BULLISH</option><option>BEARISH</option></select></div>
    <div class="field"><label>Inventory intent</label><select id="op-inventory"><option>BALANCED</option><option>ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE</option><option>ACCUMULATE_RISK_ASSET</option><option>DISTRIBUTE_RISK_ASSET</option></select></div>
    <div class="field"><label>Lower range</label><input id="op-low" type="number" step="any" placeholder="14.75"></div>
    <div class="field"><label>Upper range</label><input id="op-high" type="number" step="any" placeholder="21.5"></div>
    <div class="field"><label>Capital value</label><input id="op-cap" type="number" step="any" placeholder="500"></div>
    <div class="field"><label>Target hold days</label><input id="op-hold" type="number" step="any" placeholder="3"></div>
    <div class="field"><label>Fee tier (optional)</label><input id="op-fee" type="number" placeholder="3000"></div>
  </div><div class="modal-actions"><button class="btn secondary" id="op-cancel">Cancel</button><button class="btn" id="op-submit">Prepare open</button></div><div id="op-result" class="result-box"></div>`);
  $('#op-cancel').onclick=closeModal;
  $('#op-submit').onclick=async()=>{
    const body={pair:$('#op-pair').value,protocol:$('#op-protocol').value,chain:$('#op-chain').value,strategy_sleeve:$('#op-sleeve').value,directional_bias:$('#op-bias').value,inventory_intent:$('#op-inventory').value,target_hold_days:$('#op-hold').value?Number($('#op-hold').value):null,lower_price:Number($('#op-low').value),upper_price:Number($('#op-high').value),capital_value:Number($('#op-cap').value),fee_tier:$('#op-fee').value?Number($('#op-fee').value):null};
    try{const data=await api('/api/open/prepare',{method:'POST',body:JSON.stringify(body)});$('#op-result').innerHTML=`<pre>${esc(JSON.stringify(data,null,2))}</pre>`;await refresh();}catch(e){toast(e.message)}
  };
}

$$('.nav').forEach(b=>b.onclick=()=>{
  $$('.nav').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  $$('.section').forEach(x=>x.classList.remove('active'));$(`#${b.dataset.section}-section`).classList.add('active');
  const titles={overview:['Portfolio Overview','Fees, range risk, decisions and prepared actions in one place.'],positions:['Positions','Open and closed LPs with direct range visibility.'],opportunities:['Opportunity Scout','Durable opportunity discovery and range modelling.'],replay:['Replay Lab','Walk-forward strategy testing with no future leakage.'],decisions:['Decision Journal','Every material plan, trigger and outcome.'],automation:['Automation','Progressive authority with explicit safety boundaries.'],system:['System','Execution capability and legacy integration status.']};
  $('#page-title').textContent=titles[b.dataset.section][0];$('#page-subtitle').textContent=titles[b.dataset.section][1];
});
$('#refresh-btn').onclick=refresh;
$('#target-calc-btn').onclick=()=>calculateTargets().catch(e=>toast(e.message));
$('#import-btn').onclick=async()=>{try{const r=await api('/api/import/legacy',{method:'POST'});toast(r.ok?`Imported ${r.imported} legacy positions (${r.skipped} skipped).`:r.reason);await refresh()}catch(e){toast(e.message)}};
$('#new-position-btn').onclick=openPositionModal; $('#new-position-btn-2').onclick=openPositionModal;
$('#replay-core-btn').onclick=()=>runReplayScenario('CORE_TREND').catch(e=>toast(e.message));
$('#replay-tactical-btn').onclick=()=>runReplayScenario('TACTICAL_BREAKOUT').catch(e=>toast(e.message));
$('#support-bundle-btn').onclick=()=>buildSupportBundle().catch(e=>toast(e.message));
$('#live-scout-btn').onclick=()=>loadLivePools();
$('#live-refresh-btn-system').onclick=()=>refreshLiveWallet();
$('#modal-close').onclick=closeModal; $('#modal').onclick=(e)=>{if(e.target.id==='modal')closeModal()};
refresh();
