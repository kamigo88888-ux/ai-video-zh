'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const BUSY = ['queued', 'analyzing', 'translating', 'rendering'];
const STATUS_TXT = {queued: '排隊中', analyzing: '辨識英文中', translating: 'AI 翻譯中', ready: '待確認／可輸出',
  rendering: '輸出中', done: '完成', error: '失敗'};

async function api(url, opt = {}) {
  if (opt.json !== undefined) {
    opt.body = JSON.stringify(opt.json);
    opt.headers = {'Content-Type': 'application/json'};
    delete opt.json;
  }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg, ms = 2800) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const fmtT = s => { const m = Math.floor(s / 60); return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(2).padStart(5, '0')}`; };

/* ======================== LIST VIEW ======================== */
let listTimer = null, selected = new Set();

function showList() {
  $('#editView').classList.add('hidden'); $('#listView').classList.remove('hidden');
  ED.job = null; $('#vid').pause(); history.replaceState(null, '', '#');
  refreshList();
}

async function refreshList() {
  clearTimeout(listTimer);
  try {
    const d = await api('/api/jobs');
    setAIBadge(d.ai_available);
    renderRows(d.jobs);
    if (d.jobs.some(j => BUSY.includes(j.status))) listTimer = setTimeout(refreshList, 1500);
    else listTimer = setTimeout(refreshList, 6000);
  } catch (e) { listTimer = setTimeout(refreshList, 4000); }
}

function setAIBadge(ok) {
  const b = $('#aiBadge');
  b.textContent = ok ? 'AI 已連線' : 'AI 未設定金鑰';
  b.className = 'badge ' + (ok ? 'ok' : 'no');
}

function renderRows(jobs) {
  $('#emptyHint').classList.toggle('hidden', jobs.length > 0);
  $('#jobRows').innerHTML = jobs.map(j => {
    const busy = BUSY.includes(j.status);
    const pct = Math.round((j.progress || 0) * 100);
    const info = j.info ? `${j.info.width}×${j.info.height} · ${j.info.fps.toFixed(2)}fps · ${j.info.duration.toFixed(1)}s` : '';
    return `<tr>
      <td><input type="checkbox" class="sel" data-id="${j.id}" ${selected.has(j.id) ? 'checked' : ''}></td>
      <td><div class="nm"><img src="/api/jobs/${j.id}/thumb.jpg" onerror="this.style.visibility='hidden'">
        <div><b>${esc(j.name)}</b><div class="muted small">${info}</div></div></div></td>
      <td class="st"><span class="st-${j.status}">${STATUS_TXT[j.status] || j.status}</span>
        ${busy ? `<div class="bar small"><div style="width:${pct}%"></div></div><div class="muted small">${esc(j.message || '')} ${pct}%</div>` : ''}
        ${j.status === 'error' ? `<div class="muted small">${esc(j.error || '')}</div>` : ''}</td>
      <td>${j.status === 'ready' || j.status === 'done' || j.status === 'rendering' ? j.n_captions + ' 段' : '-'}</td>
      <td><div class="acts">
        <button onclick="openEditor('${j.id}')" ${j.status === 'queued' || j.status === 'analyzing' ? 'disabled' : ''}>預覽／編輯</button>
        ${j.has_output ? `<a class="btn" href="/api/jobs/${j.id}/output?download=1">下載</a>` : ''}
        <button class="ghost" onclick="delJob('${j.id}')" ${busy && j.status !== 'queued' ? 'disabled' : ''}>刪除</button>
      </div></td></tr>`;
  }).join('');
  $$('.sel').forEach(c => c.onchange = () => { c.checked ? selected.add(c.dataset.id) : selected.delete(c.dataset.id); });
}

async function delJob(id) {
  if (!confirm('確定刪除此影片與成品？')) return;
  try { await api('/api/jobs/' + id, {method: 'DELETE'}); selected.delete(id); refreshList(); } catch (e) { toast(e.message); }
}
async function batchRender() {
  if (!selected.size) return toast('請先勾選影片');
  const d = await api('/api/batch/render', {method: 'POST', json: {ids: [...selected]}});
  toast(`已排入 ${d.queued} 支影片輸出`); refreshList();
}
function batchDownload() {
  if (!selected.size) return toast('請先勾選影片');
  location.href = '/api/batch/download?ids=' + [...selected].join(',');
}
$('#selAll').onchange = e => { $$('.sel').forEach(c => { c.checked = e.target.checked; c.onchange(); }); };

function uploadFiles(files) {
  files = [...files].filter(f => /video|\.mov$|\.mp4$|\.webm$|\.mkv$/i.test(f.type + f.name));
  if (!files.length) return toast('請選擇影片檔');
  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  fd.append('tone', $('#toneSel').value);
  fd.append('auto_render', $('#autoRender').checked ? 'true' : 'false');
  const xhr = new XMLHttpRequest();
  const box = $('#uploadProg'); box.classList.remove('hidden');
  xhr.upload.onprogress = e => {
    const p = e.total ? e.loaded / e.total * 100 : 0;
    $('.bar>div', box).style.width = p + '%';
    $('span', box).textContent = `上傳中 ${p.toFixed(0)}%（${files.length} 支）`;
  };
  xhr.onload = () => {
    box.classList.add('hidden');
    if (xhr.status === 200) { toast(`已上傳 ${files.length} 支影片，開始 AI 處理`); refreshList(); }
    else { let m = xhr.statusText; try { m = JSON.parse(xhr.responseText).detail; } catch (e) {} toast('上傳失敗：' + m, 5000); }
  };
  xhr.onerror = () => { box.classList.add('hidden'); toast('上傳失敗（網路錯誤）'); };
  xhr.open('POST', '/api/jobs'); xhr.send(fd);
}
const drop = $('#drop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => uploadFiles(e.dataTransfer.files));
$('#fileInput').onchange = e => { uploadFiles(e.target.files); e.target.value = ''; };

/* ======================== EDITOR ======================== */
const ED = {job: null, layout: [], sprites: {}, style: {}, frame: 0, pollT: null, saveT: null, pending: {}};
const vid = $('#vid'), cvs = $('#cvs'), ctx = cvs.getContext('2d');

async function openEditor(id) {
  $('#listView').classList.add('hidden'); $('#editView').classList.remove('hidden');
  history.replaceState(null, '', '#job=' + id);
  clearTimeout(listTimer);
  ED.job = null; ED.sprites = {};
  await loadJob(id, true);
}

async function loadJob(id, first = false) {
  const j = await api('/api/jobs/' + id);
  if (!first && ED.job && ED.job.id !== id) return;
  const prevStatus = ED.job && ED.job.status;
  ED.job = j; ED.style = {...j.default_style, ...j.style};
  setAIBadge(j.ai_available);
  $('#edName').textContent = j.name;
  const i = j.info;
  $('#edMeta').textContent = i ? `${i.width}×${i.height} · ${i.fps.toFixed(3)} fps · ${i.duration.toFixed(2)} 秒 · ${i.has_audio ? '含音訊' : '無音訊'}` : '';
  if (first) {
    vid.src = `/api/jobs/${id}/video`;
    cvs.width = i.width; cvs.height = i.height;
    $('#stage').style.aspectRatio = `${i.width}/${i.height}`;
    $('#stage').style.maxWidth = `calc(76vh * ${i.width / i.height})`;
    $('#aiTone').value = j.tone || 'humor';
    setPlayer('preview');
  }
  updateStatus();
  const busy = BUSY.includes(j.status);
  if (first || (prevStatus && prevStatus !== j.status && !busy)) {
    setLayout(j.layout);
    renderCaps(); fillStyleForm();
  }
  clearTimeout(ED.pollT);
  if (busy) ED.pollT = setTimeout(() => ED.job && loadJob(id), 1200);
}

function updateStatus() {
  const j = ED.job, busy = BUSY.includes(j.status);
  const pct = Math.round((j.progress || 0) * 100);
  $('#edStatus').innerHTML = `<b class="st-${j.status}">${STATUS_TXT[j.status]}</b> <span class="muted small">${esc(j.message || '')}${busy ? ' ' + pct + '%' : ''}</span>`;
  $('#edBar').style.width = (busy ? pct : (j.status === 'done' ? 100 : 0)) + '%';
  $('#btnRender').disabled = busy;
  $('#btnRender').textContent = j.has_output ? '重新輸出 MP4' : '輸出 MP4';
  const dl = $('#btnDownload');
  dl.classList.toggle('hidden', !j.has_output);
  dl.href = `/api/jobs/${j.id}/output?download=1`;
  $('#tabOut').disabled = !j.has_output;
  const w = $('#edWarn');
  const stale = j.has_output && j.rendered_version !== undefined && j.rendered_version !== j.version && !busy;
  const msgs = [];
  if (j.status === 'error') msgs.push('處理失敗：' + esc(j.error));
  if (j.warning) msgs.push(esc(j.warning));
  if (stale) msgs.push('你已修改字幕或樣式，成品尚未更新，請按「重新輸出 MP4」。');
  w.innerHTML = msgs.join('<br>'); w.classList.toggle('hidden', !msgs.length);
}

function setLayout(layout) {
  ED.layout = layout || [];
  for (const l of ED.layout) {
    const key = l.id + ':' + l.sprite_hash;
    if (!ED.sprites[key]) {
      const img = new Image();
      img.onload = () => draw();
      img.src = `/api/jobs/${ED.job.id}/sprite/${l.id}.png?h=${l.sprite_hash}`;
      ED.sprites[key] = img;
    }
  }
  draw();
}

function posAt(l, f) {
  if (f < l.start_frame || f > l.end_frame) return null;
  const k = l.keyframes;
  if (f <= k[0][0]) return k[0].slice(1);
  if (f >= k[k.length - 1][0]) return k[k.length - 1].slice(1);
  for (let i = 1; i < k.length; i++) {
    if (k[i][0] >= f) {
      const a = k[i - 1], b = k[i], u = (f - a[0]) / Math.max(1, b[0] - a[0]);
      return [1, 2, 3].map(n => a[n] + (b[n] - a[n]) * u);
    }
  }
  return null;
}

function engBoxAt(t, f) {
  if (f < t.start_frame || f > t.end_frame) return null;
  const k = t.keyframes;
  let best = k[0];
  for (const x of k) if (Math.abs(x[0] - f) < Math.abs(best[0] - f)) best = x;
  return best.slice(1, 5);
}

function curFrame(mediaTime) {
  const fps = ED.job.info.fps;
  const t = mediaTime ?? vid.currentTime;
  return Math.max(0, Math.min(ED.job.info.frame_count - 1, Math.floor(t * fps + 1e-3)));
}

function draw(mediaTime) {
  if (!ED.job || !ED.job.info) return;
  const f = curFrame(mediaTime);
  ED.frame = f;
  ctx.clearRect(0, 0, cvs.width, cvs.height);
  if ($('#showBoxes').checked) {
    ctx.lineWidth = Math.max(2, cvs.width / 400);
    for (const t of ED.job.tracks) {
      const b = engBoxAt(t, f); if (!b) continue;
      ctx.strokeStyle = t.enabled ? '#00e5ff' : '#ff3b3b';
      ctx.strokeRect(b[0], b[1], b[2], b[3]);
      ctx.fillStyle = ctx.strokeStyle; ctx.font = `${Math.round(cvs.width / 30)}px sans-serif`;
      ctx.fillText('#' + t.id, b[0], b[1] - 4);
    }
  }
  for (const l of ED.layout) {
    const p = posAt(l, f); if (!p) continue;
    const img = ED.sprites[l.id + ':' + l.sprite_hash];
    if (img && img.complete && img.naturalWidth) ctx.drawImage(img, p[0], p[1], l.sprite_w * p[2], l.sprite_h * p[2]);
  }
  $('#tcode').textContent = fmtT(vid.currentTime);
  $('#fnum').textContent = f;
  $$('.cap').forEach(c => {
    const t = ED.job.tracks.find(x => x.id == c.dataset.id);
    c.classList.toggle('active', !!t && f >= t.start_frame && f <= t.end_frame);
  });
}

if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
  const loop = (now, meta) => { draw(meta.mediaTime); vid.requestVideoFrameCallback(loop); };
  vid.requestVideoFrameCallback(loop);
} else {
  const loop = () => { if (!vid.paused) draw(); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}
['seeked', 'loadeddata', 'pause', 'timeupdate'].forEach(ev => vid.addEventListener(ev, () => draw()));

function stepFrame(d) {
  vid.pause();
  const fps = ED.job.info.fps;
  vid.currentTime = Math.max(0, (ED.frame + d + 0.5) / fps);
}
function seekFrame(f) { vid.pause(); vid.currentTime = (f + 0.5) / ED.job.info.fps; }

function setPlayer(which) {
  const out = which === 'output';
  $('#tabPrev').classList.toggle('active', !out); $('#tabOut').classList.toggle('active', out);
  $('#stage').classList.toggle('hidden', out); $('#outVid').classList.toggle('hidden', !out);
  $('.frame-info').classList.toggle('hidden', out);
  if (out) { vid.pause(); $('#outVid').src = `/api/jobs/${ED.job.id}/output?t=${Date.now()}`; }
  else { $('#outVid').pause(); }
}

function sideTab(btn) {
  $$('.side-tabs .tab').forEach(b => b.classList.toggle('active', b === btn));
  $$('.pane').forEach(p => p.classList.toggle('hidden', p.id !== btn.dataset.p));
}

function capHTML(t) {
  const fps = ED.job.info.fps, ov = t.override || {};
  return `<div class="cap ${t.enabled ? '' : 'off'}" data-id="${t.id}">
    <div class="cap-h">
      <label class="chk"><input type="checkbox" class="en-chk" ${t.enabled ? 'checked' : ''}> 顯示</label>
      <span class="time" title="跳到此段">${fmtT(t.start_frame / fps)} → ${fmtT((t.end_frame + 1) / fps)}</span>
      <span class="muted small">#${t.id}${t.motion > 0.012 || t.size_change > 1.15 ? ' · 動態' : ''}</span>
      <span class="sp"></span>
      <button class="ghost sm rt" title="AI 重翻這句">↻ 重翻</button>
      <button class="ghost sm rt-fun" title="更幽默">😆 更有梗</button>
    </div>
    <div class="en">${esc(t.text)}</div>
    <textarea class="zh" rows="2" placeholder="輸入中文翻譯（可用 Enter 換行）">${esc((t.translation || '').replace(/\\n/g, '\n'))}</textarea>
    <div class="cap-tools">
      大小 <input type="range" class="ov" data-k="size_scale" min="0.5" max="1.8" step="0.05" value="${ov.size_scale ?? ED.style.size_scale}">
      上下 <input type="range" class="ov" data-k="dy" min="-30" max="30" step="0.5" value="${ov.dy ?? 0}">
      左右 <input type="range" class="ov" data-k="dx" min="-30" max="30" step="0.5" value="${ov.dx ?? 0}">
      <select class="ov" data-k="position"><option value="">位置：跟隨全域</option><option value="below" ${ov.position === 'below' ? 'selected' : ''}>英文下方</option><option value="above" ${ov.position === 'above' ? 'selected' : ''}>英文上方</option></select>
      <button class="ghost sm reset-ov">重設</button>
    </div></div>`;
}

function renderCaps() {
  const caps = ED.job.tracks.filter(t => t.is_caption);
  const ign = ED.job.tracks.filter(t => !t.is_caption);
  $('#capList').innerHTML = caps.length ? caps.map(capHTML).join('') :
    `<p class="muted">${BUSY.includes(ED.job.status) ? '處理中，請稍候…' : '沒有偵測到後製英文字幕。'}</p>`;
  $('#ignList').innerHTML = ign.map(capHTML).join('') || '<p class="muted small">無</p>';
  $('#ignCount').textContent = ` (${ign.length})`;
  $$('.cap').forEach(bindCap);
}

function bindCap(el) {
  const id = +el.dataset.id;
  const t = () => ED.job.tracks.find(x => x.id === id);
  $('.time', el).onclick = () => seekFrame(t().start_frame);
  $('.en-chk', el).onchange = e => { t().enabled = e.target.checked; el.classList.toggle('off', !e.target.checked); queueSave(id); };
  $('.zh', el).oninput = e => { t().translation = e.target.value; queueSave(id); };
  $('.zh', el).onfocus = () => { const tr = t(); if (ED.frame < tr.start_frame || ED.frame > tr.end_frame) seekFrame(Math.round((tr.start_frame + tr.end_frame) / 2)); };
  $$('.ov', el).forEach(inp => inp.oninput = () => {
    const tr = t(); tr.override = tr.override || {};
    const v = inp.value;
    if (inp.dataset.k === 'position') { if (v) tr.override.position = v; else delete tr.override.position; }
    else tr.override[inp.dataset.k] = parseFloat(v);
    queueSave(id);
  });
  $('.reset-ov', el).onclick = () => { t().override = {}; queueSave(id); el.outerHTML = capHTML(t()); bindCap($(`.cap[data-id="${id}"]`)); };
  $('.rt', el).onclick = () => retranslateOne(id, '', el);
  $('.rt-fun', el).onclick = () => retranslateOne(id, '更幽默、更有梗、更像台灣網紅，但保持精簡', el);
}

function queueSave(id) {
  if (id !== undefined) ED.pending[id] = true;
  clearTimeout(ED.saveT);
  ED.saveT = setTimeout(saveEdits, 350);
}

async function saveEdits(withStyle = false) {
  const ids = Object.keys(ED.pending); ED.pending = {};
  const body = {tracks: ids.map(id => { const t = ED.job.tracks.find(x => x.id == id); return {id: t.id, enabled: t.enabled, translation: t.translation, override: t.override || {}}; })};
  if (withStyle || ED.styleDirty) { body.style = ED.style; ED.styleDirty = false; }
  try {
    const r = await api('/api/jobs/' + ED.job.id, {method: 'PATCH', json: body});
    ED.job.version = r.version; setLayout(r.layout); updateStatus();
  } catch (e) { toast('儲存失敗：' + e.message); }
}

async function retranslateOne(id, instruction, el) {
  const btns = $$('button', el); btns.forEach(b => b.disabled = true);
  try {
    const r = await api(`/api/jobs/${ED.job.id}/translate_one`, {method: 'POST', json: {id, instruction, tone: $('#aiTone').value}});
    const t = ED.job.tracks.find(x => x.id === id);
    t.translation = r.translation; t.enabled = true;
    $('.zh', el).value = r.translation.replace(/\\n/g, '\n'); $('.en-chk', el).checked = true; el.classList.remove('off');
    setLayout(r.layout);
  } catch (e) { toast(e.message, 5000); }
  btns.forEach(b => b.disabled = false);
}

async function retranslateAll() {
  if (!confirm('AI 將重新判斷並覆蓋所有中文翻譯，確定？')) return;
  try {
    await api(`/api/jobs/${ED.job.id}/translate`, {method: 'POST', json: {tone: $('#aiTone').value, instruction: $('#aiInstr').value}});
    toast('AI 翻譯中…'); loadJob(ED.job.id);
  } catch (e) { toast(e.message, 5000); }
}
async function reanalyze() {
  if (!confirm('重新偵測會清除目前的翻譯與編輯，確定？')) return;
  try { await api(`/api/jobs/${ED.job.id}/reanalyze`, {method: 'POST'}); loadJob(ED.job.id); } catch (e) { toast(e.message); }
}
async function renderJob() {
  clearTimeout(ED.saveT);
  if (Object.keys(ED.pending).length || ED.styleDirty) await saveEdits();
  try { await api(`/api/jobs/${ED.job.id}/render`, {method: 'POST'}); toast('開始輸出 MP4'); loadJob(ED.job.id); }
  catch (e) { toast(e.message); }
}

/* style form */
function fillStyleForm() {
  $$('[data-s]').forEach(inp => {
    const v = ED.style[inp.dataset.s];
    if (inp.type === 'checkbox') inp.checked = !!v; else inp.value = v;
    const o = inp.nextElementSibling; if (o && o.tagName === 'OUTPUT') o.textContent = v;
  });
}
$$('[data-s]').forEach(inp => {
  const h = () => {
    const k = inp.dataset.s;
    let v = inp.type === 'checkbox' ? inp.checked : inp.value;
    if (inp.type === 'range' || k === 'max_lines') v = parseFloat(v);
    ED.style[k] = v;
    const o = inp.nextElementSibling; if (o && o.tagName === 'OUTPUT') o.textContent = v;
    ED.styleDirty = true; queueSave();
  };
  inp.addEventListener(inp.type === 'range' ? 'input' : 'change', h);
});
function resetStyle() { ED.style = {...ED.job.default_style}; fillStyleForm(); ED.styleDirty = true; queueSave(); }

/* settings */
async function openSettings() {
  const s = await api('/api/settings');
  $('#setKey').value = ''; $('#keyHint').textContent = s.api_key_set ? `目前：${s.api_key_hint || '已設定'}${s.api_key_from_env ? '（環境變數）' : ''}` : '尚未設定';
  $('#setModel').value = s.model; $('#setCrf').value = s.crf; $('#setPreset').value = s.preset;
  $('#settingsModal').classList.remove('hidden');
}
function closeSettings() { $('#settingsModal').classList.add('hidden'); }
async function saveSettings() {
  const body = {model: $('#setModel').value, crf: parseInt($('#setCrf').value), preset: $('#setPreset').value};
  if ($('#setKey').value.trim()) body.anthropic_api_key = $('#setKey').value.trim();
  const s = await api('/api/settings', {method: 'POST', json: body});
  setAIBadge(s.api_key_set); closeSettings(); toast('設定已儲存');
}

/* boot */
api('/api/info').then(d => { if (d.lan_urls.length) $('#lanUrl').textContent = '同事連線：' + d.lan_urls.join('  '); }).catch(() => {});
const m = location.hash.match(/job=([\w-]+)/);
if (m) openEditor(m[1]); else refreshList();
