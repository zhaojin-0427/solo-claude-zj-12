/* 变量控制排演板 · 原生 JS（无框架） */
'use strict';

let state = null;            // 服务器返回的完整状态
let inquiries = [];
let activeRoundId = null;    // 当前录入的轮次
let dragFactorId = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

function setSave(msg, color) {
  const el = $('#saveState');
  el.textContent = msg;
  el.style.color = color || '#cbd5e1';
}

const KIND_LABEL = { independent: '自变量', dependent: '因变量', controlled: '控制变量', unassigned: '未归类' };

// ------------------------------------------------------------ 初始化 ----
window.addEventListener('DOMContentLoaded', async () => {
  bindStaticEvents();
  await loadInquiries();
});

async function loadInquiries() {
  inquiries = await api('GET', '/api/inquiries');
  const sel = $('#inquirySelect');
  sel.innerHTML = '';
  if (!inquiries.length) {
    sel.innerHTML = '<option>（暂无）</option>';
    $('#app').classList.add('hidden');
    return;
  }
  for (const q of inquiries) {
    const o = document.createElement('option');
    o.value = q.id;
    o.textContent = `${q.name}${q.status === 'frozen' ? ' 🔒' : ''}（v${q.generation}）`;
    sel.appendChild(o);
  }
  await selectInquiry(inquiries[0].id);
}

async function selectInquiry(id) {
  state = await api('GET', `/api/inquiries/${id}`);
  $('#app').classList.remove('hidden');
  renderAll();
}

function renderAll() {
  renderHeader();
  renderBins();
  renderResources();
  renderIssues();
  renderTimeline();
  renderResults();
  renderMeta();
}

// ------------------------------------------------------------ 头部表单 ----
function renderHeader() {
  const q = state.inquiry;
  $('#nameInput').value = q.name;
  $('#hypothesisInput').value = q.hypothesis;
  $('#repeatsInput').value = q.repeats;
  $('#seedInput').value = q.seed;
  $('#nameInput').disabled = $('#hypothesisInput').disabled =
    $('#repeatsInput').disabled = q.status === 'frozen';
}

function renderMeta() {
  const q = state.inquiry;
  $('#genInfo').textContent =
    `第 ${q.generation} 版次序 · 种子 ${q.seed} · 更新于 ${q.updated_at}`;
  $('#freezeState').textContent =
    q.status === 'frozen' ? `🔒 已于 ${q.frozen_at || ''} 冻结，只读` : '当前为可修改的工作版本';
  $('#freezeBtn').disabled = q.status === 'frozen';
  $('#scheduleBtn').disabled = $('#rescheduleBtn').disabled =
    $('#saveDesignBtn').disabled = q.status === 'frozen';

  const box = $('#snapshotList');
  box.innerHTML = '';
  for (const s of state.snapshots) {
    const div = document.createElement('div');
    div.className = 'snap';
    div.innerHTML = `<span>📌 ${s.label} <small>（${s.created_at}）</small></span>`;
    const b = document.createElement('button');
    b.className = 'btn small';
    b.textContent = '查看冻结快照';
    b.onclick = () => viewSnapshot(s.id);
    div.appendChild(b);
    box.appendChild(div);
  }
}

// ------------------------------------------------------------ 因素卡片 ----
function factorLevelsText(f) {
  if (f.kind === 'controlled') return (f.levels && f.levels[0]) || '';
  return (f.levels || []).join('，');
}

function renderBins() {
  for (const kind of ['independent', 'dependent', 'controlled', 'unassigned']) {
    const body = document.querySelector(`[data-drop="${kind}"]`);
    body.innerHTML = '';
    for (const f of state.factors.filter(x => x.kind === kind)) {
      body.appendChild(makeFactorCard(f));
    }
    bindDrop(body, kind);
  }
}

function makeFactorCard(f) {
  const card = document.createElement('div');
  card.className = 'factor-card';
  card.draggable = true;
  card.dataset.id = f.id;
  const levelsCaption = {
    independent: '水平（用逗号分隔，如：0h，4h，8h）',
    dependent: '单位（如：mm、个、秒）',
    controlled: '固定为（每轮都保持的值）',
    unassigned: '归类后再设置',
  }[f.kind];

  card.innerHTML = `
    <div class="fc-head">
      <span class="fc-name">${escapeHtml(f.name)}</span>
      <span>
        <span class="fc-tag tag-${f.kind}">${KIND_LABEL[f.kind]}</span>
        <button class="fc-del" title="删除">✕</button>
      </span>
    </div>`;
  if (f.kind !== 'unassigned') {
    if (f.kind === 'independent') {
      card.insertAdjacentHTML('beforeend',
        `<input data-f="levels" placeholder="${levelsCaption}" value="${escapeHtml(factorLevelsText(f))}">`);
    } else if (f.kind === 'dependent') {
      card.insertAdjacentHTML('beforeend',
        `<input data-f="unit" placeholder="${levelsCaption}" value="${escapeHtml(f.unit || '')}">`);
    } else if (f.kind === 'controlled') {
      card.insertAdjacentHTML('beforeend',
        `<input data-f="fixed" placeholder="${levelsCaption}" value="${escapeHtml(factorLevelsText(f))}">`);
    }
  }
  // 名称就地编辑：用一个隐藏 input 不如直接 contenteditable —— 这里用 input 更直观
  const nameInput = document.createElement('input');
  nameInput.dataset.f = 'name';
  nameInput.value = f.name;
  nameInput.placeholder = '因素名称';
  card.insertBefore(nameInput, card.querySelector('.fc-head'));
  card.querySelector('.fc-name').style.display = 'none';

  card.addEventListener('dragstart', () => { dragFactorId = f.id; });
  card.querySelector('.fc-del').addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`删除因素「${f.name}」？已录入轮次的组合信息会保留。`)) return;
    await saveDesign({ dropFactorId: f.id });
  });
  return card;
}

function bindDrop(body, kind) {
  body.addEventListener('dragover', (e) => { e.preventDefault(); body.classList.add('drag-over'); });
  body.addEventListener('dragleave', () => body.classList.remove('drag-over'));
  body.addEventListener('drop', (e) => {
    e.preventDefault();
    body.classList.remove('drag-over');
    if (!dragFactorId) return;
    // 本地即时移动
    const f = state.factors.find(x => x.id === dragFactorId);
    if (f) { f.kind = kind; renderBins(); }
    dragFactorId = null;
  });
}

function collectFactors() {
  const out = [];
  for (const card of $$('.factor-card')) {
    const id = Number(card.dataset.id);
    const f = state.factors.find(x => x.id === id) || { id, kind: 'unassigned' };
    const kind = f.kind;
    const name = card.querySelector('[data-f="name"]').value.trim() || '未命名因素';
    const rawExtra = card.querySelector('[data-f="levels"],[data-f="fixed"],[data-f="unit"]');
    const extra = rawExtra ? rawExtra.value.trim() : '';
    let levels = [];
    let unit = '';
    if (kind === 'independent') {
      levels = extra.split(/[,，、]/).map(s => s.trim()).filter(Boolean);
    } else if (kind === 'controlled') {
      levels = extra ? [extra] : [];
    } else if (kind === 'dependent') {
      unit = extra;
    }
    out.push({ id, name, kind, unit, levels });
  }
  return out;
}

// ------------------------------------------------------------ 材料/时段 ----
function renderResources() {
  const mtb = $('#materialTable tbody');
  mtb.innerHTML = '';
  for (const m of state.materials) {
    const tr = document.createElement('tr');
    tr.dataset.id = m.id;
    tr.innerHTML = `
      <td><input data-f="name" value="${escapeHtml(m.name)}"></td>
      <td><input data-f="per_round" type="number" min="0" step="0.5" value="${m.per_round}"></td>
      <td><input data-f="stock" type="number" min="0" step="0.5" value="${m.stock}"></td>
      <td><input data-f="unit" value="${escapeHtml(m.unit || '')}" style="width:4.5rem"></td>
      <td class="del-cell"><button title="删除">✕</button></td>`;
    tr.querySelector('button').onclick = () => { tr.remove(); };
    mtb.appendChild(tr);
  }
  const stb = $('#slotTable tbody');
  stb.innerHTML = '';
  for (const s of state.slots) {
    const tr = document.createElement('tr');
    tr.dataset.id = s.id;
    tr.innerHTML = `
      <td><input data-f="label" value="${escapeHtml(s.label)}"></td>
      <td><input data-f="capacity" type="number" min="1" value="${s.capacity}" style="width:5rem"></td>
      <td class="del-cell"><button title="删除">✕</button></td>`;
    tr.querySelector('button').onclick = () => { tr.remove(); };
    stb.appendChild(tr);
  }
}

function collectTable(tbodySel) {
  const rows = [];
  for (const tr of $$(`${tbodySel} tr`)) {
    const obj = { id: Number(tr.dataset.id) || undefined };
    for (const inp of tr.querySelectorAll('[data-f]')) {
      obj[inp.dataset.f] = inp.type === 'number' ? Number(inp.value) : inp.value.trim();
    }
    rows.push(obj);
  }
  return rows;
}

// ------------------------------------------------------------ 保存设计 ----
async function saveDesign(opts = {}) {
  try {
    let factors = collectFactors();
    if (opts.dropFactorId) factors = factors.filter(f => f.id !== opts.dropFactorId);
    const payload = {
      name: $('#nameInput').value,
      hypothesis: $('#hypothesisInput').value,
      repeats: Number($('#repeatsInput').value) || 1,
      factors,
      materials: collectTable('#materialTable tbody').filter(m => m.name),
      slots: collectTable('#slotTable tbody').filter(s => s.label),
    };
    state = await api('PUT', `/api/inquiries/${state.inquiry.id}`, payload);
    setSave('已保存并检查 ✓', '#86efac');
    renderAll();
  } catch (e) {
    alert(e.message);
  }
}

// ------------------------------------------------------------ 检查列表 ----
function renderIssues() {
  const box = $('#issueList');
  box.innerHTML = '';
  const designIssues = state.issues;
  if (!designIssues.length) {
    box.innerHTML = '<div class="issue ok">✓ 暂未发现问题，可以排演；执行后这里还会提示数据问题。</div>';
    return;
  }
  for (const is of designIssues) {
    const div = document.createElement('div');
    div.className = `issue ${is.level}`;
    div.innerHTML = `<span>${is.level === 'error' ? '⛔' : '⚠️'}</span>
      <span>${escapeHtml(is.msg)}</span> <span class="icode">${is.code}</span>`;
    if (is.round_seq) div.title = `点击定位到第 ${is.round_seq} 轮`;
    div.onclick = () => {
      if (is.round_seq) {
        const r = state.rounds.find(x => x.seq === is.round_seq);
        if (r) {
          openEntry(r.id);
          $$('.round-chip').forEach(c => c.classList.toggle('active', Number(c.dataset.id) === r.id));
          document.getElementById('entryCard').scrollIntoView({ behavior: 'smooth' });
        }
      } else if (is.factor_id) {
        const card = $(`.factor-card[data-id="${is.factor_id}"]`);
        if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.outline = '3px solid #f59e0b';
          setTimeout(() => (card.style.outline = ''), 1800); }
      }
    };
    box.appendChild(div);
  }
}

// ------------------------------------------------------------ 时间轴 ----
function slotMap() {
  return Object.fromEntries(state.slots.map(s => [s.id, s]));
}

function renderTimeline() {
  const tl = $('#timeline');
  tl.innerHTML = '';
  const sm = slotMap();
  const flagged = new Set(state.issues.filter(i => i.round_seq).map(i => i.round_seq));
  for (const r of state.rounds) {
    const div = document.createElement('div');
    div.className = 'round-chip' +
      (r.locked ? ' locked' : '') + (flagged.has(r.seq) ? ' flagged' : '');
    div.dataset.id = r.id;
    const chips = Object.entries(r.combo)
      .map(([k, v]) => `<span class="chip-level">${escapeHtml(k)}：${escapeHtml(String(v))}</span>`)
      .join('');
    div.innerHTML = `
      <div><span class="seq">第 ${r.seq} 轮</span>
        ${r.locked ? '<span class="lock-mark">🔒</span>' : ''}
        ${r.block_no ? `<span class="slot">区组 ${r.block_no}</span>` : ''}</div>
      <div class="combo">${chips}</div>
      <div class="slot">📅 ${r.slot_id ? escapeHtml(sm[r.slot_id]?.label || '?') : '⚠️ 无可用时段'}</div>
      <div class="rc-actions">
        <button class="btn">📝 录入</button>
        <button class="btn">${r.locked ? '解锁' : '锁定'}</button>
      </div>`;
    div.querySelector('.btn').onclick = () => openEntry(r.id);
    div.querySelectorAll('.btn')[1].onclick = async (e) => {
      e.stopPropagation();
      await api('POST', `/api/inquiries/${state.inquiry.id}/rounds/${r.id}/lock`,
        { locked: !r.locked });
      state = await api('GET', `/api/inquiries/${state.inquiry.id}`);
      renderAll();
    };
    tl.appendChild(div);
  }
  $('#timelineCard').classList.toggle('hidden', !state.rounds.length);
}

// ------------------------------------------------------------ 测量录入 ----
function openEntry(roundId) {
  activeRoundId = roundId;
  const r = state.rounds.find(x => x.id === roundId);
  if (!r) return;
  $('#entryCard').classList.remove('hidden');
  $('#entrySeq').textContent = r.seq;
  $('#entryCombo').innerHTML = Object.entries(r.combo)
    .map(([k, v]) => `<span class="chip-level">${escapeHtml(k)}：${escapeHtml(String(v))}</span>`)
    .join('');
  $('#entryDone').checked = r.locked;

  const box = $('#entryRows');
  box.innerHTML = '';
  const dvs = state.factors.filter(f => f.kind === 'dependent');
  if (!dvs.length) {
    box.innerHTML = '<p class="hint">还没有因变量，先在第②步归类并设置单位。</p>';
  }
  for (const dv of dvs) {
    const m = state.measurements.find(x => x.round_id === roundId && x.dv_id === dv.id) || {};
    const row = document.createElement('div');
    row.className = 'entry-row';
    row.dataset.dv = dv.id;
    row.innerHTML = `
      <div class="er-title">${escapeHtml(dv.name)}</div>
      <div class="er-grid">
        <label>测量值（留空=缺测）
          <input data-f="value" type="number" step="any" value="${m.value ?? ''}"></label>
        <label>单位
          <input data-f="unit" type="text" value="${escapeHtml(m.unit || dv.unit || '')}"></label>
        <label>异常原因
          <input data-f="anomaly" type="text" placeholder="如：洒出、读数错误…"
            value="${escapeHtml(m.anomaly || '')}"></label>
        <label class="checkline">
          <input data-f="excluded" type="checkbox" ${m.excluded ? 'checked' : ''}>
          剔除该异常值</label>
      </div>`;
    box.appendChild(row);
  }
  $$('.round-chip').forEach(c => c.classList.toggle('active', Number(c.dataset.id) === roundId));
}

async function saveEntry() {
  if (activeRoundId == null) return;
  const round = state.rounds.find(r => r.id === activeRoundId);
  try {
    for (const row of $$('#entryRows .entry-row')) {
      const dv = state.factors.find(f => f.id === Number(row.dataset.dv));
      const value = row.querySelector('[data-f="value"]').value;
      const payload = {
        dv_id: dv.id,
        dv_name: dv.name,
        value: value === '' ? null : Number(value),
        unit: row.querySelector('[data-f="unit"]').value.trim(),
        anomaly: row.querySelector('[data-f="anomaly"]').value.trim(),
        excluded: row.querySelector('[data-f="excluded"]').checked,
      };
      await api('PUT',
        `/api/inquiries/${state.inquiry.id}/rounds/${activeRoundId}/measurement`, payload);
    }
    const wantLock = $('#entryDone').checked;
    if (wantLock !== round.locked) {
      await api('POST',
        `/api/inquiries/${state.inquiry.id}/rounds/${activeRoundId}/lock`,
        { locked: wantLock });
    }
    state = await api('GET', `/api/inquiries/${state.inquiry.id}`);
    setSave('测量已保存 ✓', '#86efac');
    renderAll();
    openEntry(activeRoundId);
  } catch (e) {
    alert(e.message);
  }
}

// ------------------------------------------------------------ 结果展示 ----
function renderResults() {
  const wrap = $('#resultPanels');
  const charts = $('#charts');
  wrap.innerHTML = '';
  charts.innerHTML = '';
  const hasData = state.measurements.some(m => m.value !== null);
  $('#resultsCard').classList.toggle('hidden', !hasData && !state.rounds.length);

  for (const s of state.stats) {
    const div = document.createElement('div');
    div.className = 'result-panel';
    let html = `<h3>${escapeHtml(s.dv_name)}（单位：${escapeHtml(s.unit || '?')}）</h3>
      <table><thead><tr><th>水平</th><th>原始数据</th><th>均值</th><th>极差</th><th>份数 n</th></tr></thead><tbody>`;
    for (const lv of s.levels) {
      html += `<tr><td><b>${escapeHtml(lv.level)}</b></td>
        <td>${lv.values.map(v => escapeHtml(String(v))).join('，') || '—'}</td>
        <td>${lv.mean ?? '—'}</td>
        <td>${lv.range ?? '—'}</td>
        <td>${lv.n}</td></tr>`;
    }
    html += '</tbody></table>';
    div.innerHTML = html;
    wrap.appendChild(div);

    const cw = document.createElement('div');
    cw.className = 'chart-wrap';
    const cv = document.createElement('canvas');
    cw.appendChild(cv);
    charts.appendChild(cw);
    drawChart(cv, s);
  }
  // 数据级问题（单位混用/缺测/剔除）在结果表下方点明轮次
  const dataIssues = state.issues.filter(i =>
    ['W_UNIT_MIX', 'W_MISSING', 'W_EXCLUDED'].includes(i.code) && i.round_seq);
  if (dataIssues.length) {
    const h = document.createElement('h3');
    h.textContent = '数据问题（已定位到轮次）';
    wrap.appendChild(h);
    for (const is of dataIssues) {
      const d = document.createElement('div');
      d.className = `issue ${is.level}`;
      d.innerHTML = `<span>${is.level === 'error' ? '⛔' : '⚠️'}</span><span>${escapeHtml(is.msg)}</span>`;
      d.style.cursor = 'pointer';
      d.onclick = () => {
        const r = state.rounds.find(x => x.seq === is.round_seq);
        if (r) openEntry(r.id);
      };
      wrap.appendChild(d);
    }
  }
}

function drawChart(canvas, stat) {
  const levels = stat.levels.filter(l => l.mean !== null);
  const W = 140 + levels.length * 110, H = 320;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1f2937';
  ctx.font = 'bold 13px sans-serif';
  ctx.fillText(`${stat.dv_name}（${stat.unit || ''}）：均值＋极差`, 12, 20);
  if (!levels.length) {
    ctx.fillStyle = '#9ca3af';
    ctx.fillText('暂无可绘制的数据', 12, 60);
    return;
  }
  const padL = 52, padB = 46, padT = 44;
  const allVals = levels.flatMap(l => [l.min, l.max]);
  let minV = Math.min(...allVals, 0), maxV = Math.max(...allVals);
  if (minV === maxV) { minV -= 1; maxV += 1; }
  const span = maxV - minV;
  minV -= span * 0.1; maxV += span * 0.1;
  const y = (v) => padT + (1 - (v - minV) / (maxV - minV)) * (H - padT - padB);
  const baseY = y(Math.max(minV, 0));

  // 坐标轴
  ctx.strokeStyle = '#9ca3af'; ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB);
  ctx.lineTo(W - 12, H - padB); ctx.stroke();
  ctx.fillStyle = '#6b7280'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {
    const v = minV + (maxV - minV) * i / 4;
    ctx.fillText(v.toFixed(2), 6, y(v) + 3);
  }
  // 每根柱
  const bw = 56, gap = (W - padL - 12 - bw * levels.length) / (levels.length + 1);
  levels.forEach((l, i) => {
    const x = padL + gap + i * (bw + gap);
    const top = y(l.max), bot = baseY;
    ctx.fillStyle = 'rgba(37,99,235,.75)';
    ctx.fillRect(x, y(l.mean), bw, Math.max(1, bot - y(l.mean)));
    // 极差线
    ctx.strokeStyle = '#b45309'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x + bw / 2, top); ctx.lineTo(x + bw / 2, y(l.min)); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + bw / 2 - 8, top); ctx.lineTo(x + bw / 2 + 8, top);
    ctx.moveTo(x + bw / 2 - 8, y(l.min)); ctx.lineTo(x + bw / 2 + 8, y(l.min));
    ctx.stroke(); ctx.lineWidth = 1;
    // 原始数据灰点
    ctx.fillStyle = 'rgba(31,41,55,.55)';
    l.values.forEach(v => {
      ctx.beginPath();
      ctx.arc(x + bw / 2 + (Math.random() - .5) * 14, y(v), 3.2, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.fillStyle = '#374151';
    ctx.fillText(l.level, x + 4, H - padB + 16);
    ctx.fillStyle = '#2563eb';
    ctx.fillText(`均值 ${l.mean}`, x, y(l.mean) - 5);
  });
}

// ------------------------------------------------------------ 排演 ----
async function doSchedule(rescheduleAll) {
  try {
    state = await api('POST', `/api/inquiries/${state.inquiry.id}/schedule`,
      { reschedule_all: rescheduleAll, advance_seed: true });
    setSave(`已排出 ${state.rounds.length} 轮 ✓`, '#86efac');
    renderAll();
  } catch (e) {
    alert(e.message);
  }
}

// ------------------------------------------------------------ 冻结/回放 ----
async function freezeVersion() {
  const label = prompt('给冻结版本起个名字：',
    `${state.inquiry.name} @ ${new Date().toLocaleString('zh-CN')}`);
  if (label === null) return;
  try {
    state = await api('POST', `/api/inquiries/${state.inquiry.id}/freeze`, { label });
    await loadInquiries();
    setSave('版本已冻结 🔒', '#fca5a5');
  } catch (e) { alert(e.message); }
}

async function viewSnapshot(sid) {
  const snap = await api('GET', `/api/inquiries/${state.inquiry.id}/snapshots/${sid}`);
  const w = window.open('', '_blank');
  w.document.write(`<pre style="font:13px monospace;padding:1rem">
    快照：${escapeHtml(snap.label)}（${snap.created_at}）\n\n` +
    escapeHtml(JSON.stringify(snap.state, null, 2)) + '</pre>');
}

async function replayLog() {
  const box = $('#replayBox');
  box.classList.remove('hidden');
  try {
    const r = await api('GET', `/api/inquiries/${state.inquiry.id}/replay`);
    const ok = r.ok;
    box.innerHTML = `
      <div class="${ok ? 'rp-ok' : 'rp-bad'}">
        ${ok ? '✓ 随机依据回放一致：用同一颗种子重放 Fisher–Yates 洗牌，每一次随机选择都吻合。'
             : '✗ 回放不一致，排演记录可能被改动过。'}
      </div>
      <div class="hint">种子 ${r.seed} · 记录于 ${r.created_at}</div>
      <details open><summary>逐次洗牌核对（${r.details.length} 次）</summary>
        <div id="rpDetails"></div></details>
      <details><summary>原始 RNG 事件 JSON</summary><pre>${escapeHtml(JSON.stringify(r.stored_events, null, 2))}</pre></details>`;
    const d = $('#rpDetails');
    r.details.forEach((x, i) => {
      const p = document.createElement('div');
      p.innerHTML = `${i + 1}. ${x.n} 项「${escapeHtml(x.label)}」：` +
        `<b class="${x.match ? 'rp-ok' : 'rp-bad'}">${x.match ? '一致' : '不一致'}</b>` +
        ` → <small>${(x.result_order || []).map(escapeHtml).join(' → ')}</small>`;
      d.appendChild(p);
    });
  } catch (e) {
    box.innerHTML = `<div class="rp-bad">${escapeHtml(e.message)}</div>`;
  }
}

// ------------------------------------------------------------ 打印实验单 ----
function printSheet() {
  const q = state.inquiry;
  $('#psTitle').textContent = `实验单：${q.name}`;
  $('#psHyp').textContent = q.hypothesis || '（未填写）';
  $('#psMat').textContent = state.materials.length
    ? state.materials.map(m =>
        `${m.name} ${m.per_round * state.rounds.length}${m.unit || ''}/共（库存 ${m.stock}${m.unit || ''}）`)
        .join('；')
    : '（未填写）';
  $('#psCV').textContent = state.factors.filter(f => f.kind === 'controlled')
    .map(f => `${f.name}=${(f.levels || ['?'])[0]}`).join('；') || '（未设置）';

  const steps = $('#psSteps');
  steps.innerHTML = '';
  state.factors.filter(f => f.kind === 'controlled')
    .forEach(f => steps.appendChild(liEl(`每轮都把「${f.name}」保持为 ${(f.levels || ['?'])[0]}。`)));
  steps.appendChild(liEl('按下面的次序逐轮实验，只改变该轮规定的自变量水平，其余条件不动。'));
  steps.appendChild(liEl('每轮完成后立即测量并记录，写清单位；如有异常，在备注注明原因。'));

  const ivs = state.factors.filter(f => f.kind === 'independent');
  const dvs = state.factors.filter(f => f.kind === 'dependent');
  const sm = slotMap();
  let html = '<tr><th>轮次</th><th>时段</th>';
  ivs.forEach(v => html += `<th>${v.name}</th>`);
  dvs.forEach(v => html += `<th>${v.name}（${v.unit || '单位'}）</th>`);
  html += '<th>异常备注</th></tr>';
  for (const r of state.rounds) {
    html += `<tr><td>${r.seq}${r.locked ? '🔒' : ''}</td>
      <td>${r.slot_id ? escapeHtml(sm[r.slot_id]?.label || '') : ''}</td>`;
    ivs.forEach(v => html += `<td>${escapeHtml(String(r.combo[v.name] ?? ''))}</td>`);
    dvs.forEach(() => html += '<td></td>');
    html += '<td></td></tr>';
  }
  $('#psTable').innerHTML = html;
  window.print();
}

function liEl(t) { const li = document.createElement('li'); li.textContent = t; return li; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ------------------------------------------------------------ 事件绑定 ----
function bindStaticEvents() {
  $('#inquirySelect').addEventListener('change', (e) => selectInquiry(Number(e.target.value)));
  $('#newInquiryBtn').addEventListener('click', async () => {
    const name = prompt('新探究的名称：', '我的新探究');
    if (!name) return;
    const created = await api('POST', '/api/inquiries', { name });
    await loadInquiries();
    $('#inquirySelect').value = created.inquiry.id;
    await selectInquiry(created.inquiry.id);
  });
  $('#addFactorBtn').addEventListener('click', async () => {
    const name = $('#newFactorName').value.trim();
    if (!name) { alert('先填写因素名称'); return; }
    // 先保存现有编辑，再以未归类加入新卡片（由后端给 id）
    await saveDesign();
    const payload = buildCurrentPayload();
    payload.factors.push({ name, kind: 'unassigned', unit: '', levels: [] });
    state = await api('PUT', `/api/inquiries/${state.inquiry.id}`, payload);
    $('#newFactorName').value = '';
    renderAll();
  });
  $('#addMaterialBtn').addEventListener('click', () => {
    const tbody = $('#materialTable tbody');
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><input data-f="name" placeholder="如：绿豆"></td>
      <td><input data-f="per_round" type="number" min="0" step="0.5" value="1"></td>
      <td><input data-f="stock" type="number" min="0" step="0.5" value="0"></td>
      <td><input data-f="unit" style="width:4.5rem" placeholder="粒"></td>
      <td class="del-cell"><button>✕</button></td>`;
    tr.querySelector('button').onclick = () => tr.remove();
    tbody.appendChild(tr);
  });
  $('#addSlotBtn').addEventListener('click', () => {
    const tbody = $('#slotTable tbody');
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><input data-f="label" placeholder="如：周三下午"></td>
      <td><input data-f="capacity" type="number" min="1" value="1" style="width:5rem"></td>
      <td class="del-cell"><button>✕</button></td>`;
    tr.querySelector('button').onclick = () => tr.remove();
    tbody.appendChild(tr);
  });
  $('#saveDesignBtn').addEventListener('click', () => saveDesign());
  $('#scheduleBtn').addEventListener('click', () => doSchedule(false));
  $('#rescheduleBtn').addEventListener('click', () => {
    if (confirm('全部重排会忽略已锁定轮次，确定吗？\n（通常条件变化时直接点「排出实验次序」，只重排未完成部分。）')) {
      doSchedule(true);
    }
  });
  $('#saveEntryBtn').addEventListener('click', saveEntry);
  $('#closeEntryBtn').addEventListener('click', () => $('#entryCard').classList.add('hidden'));
  $('#freezeBtn').addEventListener('click', freezeVersion);
  $('#replayBtn').addEventListener('click', replayLog);
  $('#printBtn').addEventListener('click', printSheet);
}

function buildCurrentPayload() {
  return {
    name: $('#nameInput').value,
    hypothesis: $('#hypothesisInput').value,
    repeats: Number($('#repeatsInput').value) || 1,
    factors: collectFactors(),
    materials: collectTable('#materialTable tbody').filter(m => m.name),
    slots: collectTable('#slotTable tbody').filter(s => s.label),
  };
}
