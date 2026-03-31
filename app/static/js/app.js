/**
 * Task Reminders — UI
 * Vanilla JS, no frameworks.
 * All datetimes from the API are naive UTC ISO strings (no Z).
 * We append Z before parsing so the browser treats them as UTC.
 */

// ── Constants ──────────────────────────────────────────────────────────────
const ALL_STATUSES = ['active', 'snoozed', 'inactive', 'scheduled', 'done', 'inactive_passed'];

// ── State ──────────────────────────────────────────────────────────────────
const S = {
  reminders: [],
  config: { max_active: 5 },
  view:  lsGet('tr_view')  || 'card',
  theme: lsGet('tr_theme') || 'dark',
  editingId: null,
  filters: {
    statuses:    new Set(['active']),
    showDeleted: false,
    due_date:          { op: '', v1: '', v2: '' },
    active_start_hour: { op: '', v1: '', v2: '' },
    active_end_hour:   { op: '', v1: '', v2: '' },
  },
};

// ── localStorage helpers ───────────────────────────────────────────────────
function lsGet(k)    { return localStorage.getItem(k); }
function lsSet(k, v) { localStorage.setItem(k, v); }

// ── API helper ─────────────────────────────────────────────────────────────
async function api(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body != null) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  return res.json();
}

// ── Data fetch ─────────────────────────────────────────────────────────────
async function fetchAll() {
  try {
    const [rRes, cRes] = await Promise.all([fetch('/reminders/all'), fetch('/config')]);
    const rData = await rRes.json();
    const cData = await cRes.json();
    S.reminders = rData.data || [];
    if (cData.data) S.config = cData.data;
  } catch (e) {
    console.error('Failed to fetch data:', e);
  }
}

// ── Filtering ──────────────────────────────────────────────────────────────
function getFiltered() {
  return S.reminders.filter(r => {
    // Deleted items: only include if showDeleted is on
    if (r.deleted_at) return S.filters.showDeleted;
    // Non-deleted: must match a checked status
    if (!S.filters.statuses.has(r.status)) return false;
    // Date filters
    if (!matchDate(r.due_date,          S.filters.due_date))          return false;
    if (!matchDate(r.active_start_hour, S.filters.active_start_hour)) return false;
    if (!matchDate(r.active_end_hour,   S.filters.active_end_hour))   return false;
    return true;
  });
}

function matchDate(isoStr, f) {
  if (!f.op) return true;          // no filter applied
  if (!isoStr) return false;       // field is null — exclude when filter is set
  const val = new Date(utcStr(isoStr));
  const v1  = f.v1 ? new Date(f.v1) : null;
  const v2  = f.v2 ? new Date(f.v2) : null;
  if (f.op === 'before')  return v1 && val < v1;
  if (f.op === 'after')   return v1 && val > v1;
  if (f.op === 'between') return v1 && v2 && val >= v1 && val <= v2;
  return true;
}

// ── Sorting ────────────────────────────────────────────────────────────────
function getSorted(list) {
  return [...list].sort((a, b) => {
    // Overdue first
    const ao = a.overdue ? 1 : 0, bo = b.overdue ? 1 : 0;
    if (ao !== bo) return bo - ao;
    // Then by priority descending
    if (a.priority !== b.priority) return b.priority - a.priority;
    // Then oldest created first
    return new Date(utcStr(a.created_at)) - new Date(utcStr(b.created_at));
  });
}

// ── Date utilities ─────────────────────────────────────────────────────────
/** Ensure an ISO string is treated as UTC when parsed by new Date(). */
function utcStr(s) {
  if (!s) return s;
  return (s.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(s)) ? s : s + 'Z';
}

/** Format a UTC ISO string for display in the user's local timezone. */
function fmtDate(s) {
  if (!s) return '—';
  return new Date(utcStr(s)).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/** Format a short date (no time). */
function fmtDateShort(s) {
  if (!s) return '—';
  return new Date(utcStr(s)).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  });
}

/** Convert a UTC ISO string to a datetime-local input value (local timezone). */
function toInputVal(s) {
  if (!s) return '';
  const d = new Date(utcStr(s));
  return [
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
  ].join('-') + 'T' + [pad(d.getHours()), pad(d.getMinutes())].join(':');
}

function pad(n) { return String(n).padStart(2, '0'); }

// ── HTML escaping ──────────────────────────────────────────────────────────
function esc(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str || ''));
  return d.innerHTML;
}

// ── Active count display ───────────────────────────────────────────────────
function updateActiveCount() {
  const count = S.reminders.filter(r => !r.deleted_at && ['active', 'snoozed'].includes(r.status)).length;
  const max   = parseInt(S.config.max_active) || 5;
  const el    = document.getElementById('active-count');
  if (!el) return;
  el.textContent = `${count} / ${max} active`;
  el.className   = 'active-count' + (count >= max ? ' at-limit' : '');
}

// ── Render ─────────────────────────────────────────────────────────────────
function render() {
  updateActiveCount();
  const items   = getSorted(getFiltered());
  const box     = document.getElementById('reminders-container');
  const empty   = document.getElementById('empty-state');
  closeAllMenus();

  if (items.length === 0) {
    box.innerHTML    = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';

  if (S.view === 'card') renderCards(items, box);
  else                   renderTable(items, box);
}

// ── Card rendering ─────────────────────────────────────────────────────────
function renderCards(items, box) {
  box.className = 'cards-grid';
  box.innerHTML = items.map(cardHTML).join('');
}

function cardHTML(r) {
  const overdueCls  = r.overdue    ? ' overdue' : '';
  const deletedCls  = r.deleted_at ? ' deleted' : '';
  const dueLine     = r.due_date
    ? `<span class="meta-item${r.overdue ? ' meta-overdue' : ''}" title="Due: ${fmtDate(r.due_date)}">Due: ${fmtDate(r.due_date)}</span>`
    : '';
  const remindLine  = r.remind_at
    ? `<span class="meta-item" title="Remind at: ${fmtDate(r.remind_at)}">🔔 ${fmtDate(r.remind_at)}</span>`
    : '';

  return `
<div class="reminder-card${overdueCls}${deletedCls}" data-id="${r.id}">
  <div class="card-click-area">
    <div class="card-top">
      <div class="card-title" title="${esc(r.title)}">${esc(r.title)}</div>
      <div class="card-badges">
        ${r.overdue ? '<span class="badge badge-overdue">Overdue</span>' : ''}
        <span class="badge badge-${r.status}">${r.status}</span>
        ${r.deleted_at ? '<span class="badge badge-deleted">Deleted</span>' : ''}
      </div>
    </div>
    <div class="card-meta">
      <span class="meta-item" title="Priority">P${r.priority}</span>
      ${dueLine}
      ${remindLine}
    </div>
  </div>
  <div class="card-actions">
    <button class="btn-more" data-id="${r.id}">⋯</button>
    <div class="overflow-menu" id="menu-${r.id}" style="right:0;left:auto">
      ${menuItemsHTML(r)}
    </div>
  </div>
</div>`;
}

// ── Table rendering ────────────────────────────────────────────────────────
function renderTable(items, box) {
  box.className = 'table-wrap';
  box.innerHTML = `
<table class="reminders-table">
  <thead>
    <tr>
      <th>Title</th>
      <th>P</th>
      <th>Status</th>
      <th>Due</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    ${items.map(rowHTML).join('')}
  </tbody>
</table>`;
}

function rowHTML(r) {
  const overdueCls = r.overdue    ? ' row-overdue' : '';
  const deletedCls = r.deleted_at ? ' row-deleted' : '';
  const dueCls     = r.overdue    ? ' col-overdue' : '';

  return `
<tr class="${overdueCls}${deletedCls}" data-id="${r.id}">
  <td class="col-title">
    <span class="row-title" title="${esc(r.title)}">${esc(r.title)}</span>
    ${r.overdue ? ' <span class="badge badge-overdue badge-xs">!</span>' : ''}
  </td>
  <td class="col-priority">${r.priority}</td>
  <td class="col-status">
    <span class="badge badge-${r.status}">${r.status}</span>
    ${r.deleted_at ? '<span class="badge badge-deleted badge-xs">del</span>' : ''}
  </td>
  <td class="col-due${dueCls}">${fmtDate(r.due_date)}</td>
  <td class="col-actions">
    <button class="btn-more" data-id="${r.id}">⋯</button>
    <div class="overflow-menu" id="menu-${r.id}" style="right:0;left:auto">
      ${menuItemsHTML(r)}
    </div>
  </td>
</tr>`;
}

// ── Overflow menu HTML ─────────────────────────────────────────────────────
function menuItemsHTML(r) {
  if (r.deleted_at) {
    return `<button class="menu-item" data-action="edit" data-id="${r.id}">✎ View</button>`;
  }
  const items = [];
  if (r.status !== 'done') {
    items.push(`<button class="menu-item menu-done"   data-action="done"   data-id="${r.id}">✓ Done</button>`);
  }
  if (['active', 'snoozed'].includes(r.status)) {
    items.push(`<button class="menu-item menu-snooze" data-action="snooze" data-id="${r.id}">⏰ Snooze</button>`);
  }
  items.push(`<button class="menu-item menu-delete" data-action="delete" data-id="${r.id}">🗑 Delete</button>`);
  return items.join('');
}

// ── Overflow menu toggle ───────────────────────────────────────────────────
function toggleMenu(id, btn) {
  const menu   = document.getElementById(`menu-${id}`);
  const isOpen = menu.classList.contains('open');
  closeAllMenus();
  if (!isOpen) menu.classList.add('open');
}

function closeAllMenus() {
  document.querySelectorAll('.overflow-menu.open').forEach(m => m.classList.remove('open'));
}

// ── Action dispatcher ──────────────────────────────────────────────────────
async function handleAction(action, id) {
  closeAllMenus();
  if      (action === 'done'   ) await markDone(id);
  else if (action === 'snooze' ) await snoozeReminder(id);
  else if (action === 'delete' ) await deleteReminder(id);
  else if (action === 'edit'   ) openEdit(id);
}

// ── Actions ────────────────────────────────────────────────────────────────
async function markDone(id) {
  const r = S.reminders.find(x => x.id === id);
  if (!confirm(`Mark "${r?.title}" as done?`)) return;
  await api('POST', `/reminders/${id}/done`);
  await fetchAll();
  render();
}

async function snoozeReminder(id) {
  const r     = S.reminders.find(x => x.id === id);
  const input = prompt(
    `Snooze "${r?.title}" for how many hours?\nDecimals are OK — e.g. 4.5 = 4h 30m`,
    '1'
  );
  if (input === null) return; // cancelled
  const hours = parseFloat(input);
  if (isNaN(hours) || hours <= 0) {
    alert('Please enter a positive number of hours.');
    return;
  }
  await api('POST', `/reminders/${id}/snooze?hours=${hours}`);
  await fetchAll();
  render();
}

async function deleteReminder(id) {
  const r = S.reminders.find(x => x.id === id);
  if (!confirm(`Delete "${r?.title}"?\nIt will be soft-deleted and hidden from the default view.`)) return;
  await api('DELETE', `/reminders/${id}`);
  await fetchAll();
  render();
}

// ── Modal ──────────────────────────────────────────────────────────────────
function openAdd() {
  S.editingId = null;
  document.getElementById('modal-title').textContent = 'New Reminder';
  document.getElementById('reminder-form').reset();
  document.getElementById('f-priority').value = '3';
  setEditOnlyFields(false);
  openModal();
}

function openEdit(id) {
  const r = S.reminders.find(x => x.id === id);
  if (!r) return;
  S.editingId = id;
  document.getElementById('modal-title').textContent = 'Edit Reminder';

  document.getElementById('f-title').value    = r.title          || '';
  document.getElementById('f-priority').value = String(r.priority || 1);
  document.getElementById('f-start').value    = toInputVal(r.active_start_hour);
  document.getElementById('f-end').value      = toInputVal(r.active_end_hour);
  document.getElementById('f-due').value      = toInputVal(r.due_date);
  document.getElementById('f-remind').value   = toInputVal(r.remind_at);
  document.getElementById('f-notes').value    = r.notes_details   || '';
  document.getElementById('f-status').value   = r.status          || 'active';
  document.getElementById('f-snooze').value   = toInputVal(r.snooze_until);
  document.getElementById('f-source').value   = r.source          || '';
  document.getElementById('f-created').textContent = fmtDate(r.created_at);

  setEditOnlyFields(true);
  openModal();
}

function setEditOnlyFields(show) {
  document.querySelectorAll('.edit-only').forEach(el => {
    el.style.display = show ? '' : 'none';
  });
}

function openModal() {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  setTimeout(() => document.getElementById('f-title').focus(), 50);
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  document.body.style.overflow = '';
  S.editingId = null;
}

async function submitForm(e) {
  e.preventDefault();
  const title = document.getElementById('f-title').value.trim();
  if (!title) { alert('Title is required.'); return; }

  const data = {
    title,
    priority:      parseInt(document.getElementById('f-priority').value),
    notes_details: document.getElementById('f-notes').value.trim() || null,
  };

  const fStart  = document.getElementById('f-start').value;
  const fEnd    = document.getElementById('f-end').value;
  const fDue    = document.getElementById('f-due').value;
  const fRemind = document.getElementById('f-remind').value;

  if (fStart)  data.active_start_hour = fStart;
  if (fEnd)    data.active_end_hour   = fEnd;
  if (fDue)    data.due_date          = fDue;
  data.remind_at = fRemind || null;

  if (S.editingId) {
    data.status = document.getElementById('f-status').value;
    const fSnooze = document.getElementById('f-snooze').value;
    data.snooze_until = fSnooze || null;
    await api('PATCH', `/reminders/${S.editingId}`, data);
  } else {
    await api('POST', '/reminders', data);
  }

  closeModal();
  await fetchAll();
  render();
}

// ── Filter panel ───────────────────────────────────────────────────────────
function initFilters() {
  // Build status checkboxes
  const cbBox  = document.getElementById('status-checkboxes');
  cbBox.innerHTML = ALL_STATUSES.map(s => `
    <label class="cb-label">
      <input type="checkbox" class="status-cb" value="${s}" ${s === 'active' ? 'checked' : ''}>
      <span class="badge badge-${s}">${s}</span>
    </label>
  `).join('') + `
    <label class="cb-label">
      <input type="checkbox" id="cb-deleted">
      <span class="badge badge-deleted">deleted</span>
    </label>
  `;

  cbBox.addEventListener('change', () => {
    S.filters.statuses    = new Set([...cbBox.querySelectorAll('.status-cb:checked')].map(c => c.value));
    S.filters.showDeleted = document.getElementById('cb-deleted').checked;
    render();
  });

  // Date filter show/hide logic + state update
  document.querySelectorAll('.date-filter').forEach(df => {
    const field   = df.dataset.field;
    const opSel   = df.querySelector('.filter-op');
    const d1      = df.querySelector('.filter-date1');
    const andSpan = df.querySelector('.filter-and');
    const d2      = df.querySelector('.filter-date2');

    opSel.addEventListener('change', () => {
      const op       = opSel.value;
      const needsOne = ['before', 'after', 'between'].includes(op);
      const needsTwo = op === 'between';
      d1.style.display       = needsOne ? '' : 'none';
      andSpan.style.display  = needsTwo ? '' : 'none';
      d2.style.display       = needsTwo ? '' : 'none';
      if (!needsOne) { d1.value = ''; d2.value = ''; }
      updateDateFilter(field, op, d1.value, d2.value);
    });

    [d1, d2].forEach(inp => inp.addEventListener('change', () => {
      updateDateFilter(field, opSel.value, d1.value, d2.value);
    }));
  });

  document.getElementById('btn-filter-toggle').addEventListener('click', () => {
    const panel  = document.getElementById('filter-panel');
    const btn    = document.getElementById('btn-filter-toggle');
    const open   = panel.classList.toggle('collapsed') === false;
    btn.classList.toggle('active', open);
    document.getElementById('filter-toggle-label').textContent = open ? '▲ Filters' : '▼ Filters';
  });

  document.getElementById('btn-filter-clear').addEventListener('click', clearFilters);
}

function updateDateFilter(field, op, v1, v2) {
  S.filters[field] = { op, v1, v2 };
  render();
}

function clearFilters() {
  S.filters.statuses    = new Set(['active']);
  S.filters.showDeleted = false;
  S.filters.due_date          = { op: '', v1: '', v2: '' };
  S.filters.active_start_hour = { op: '', v1: '', v2: '' };
  S.filters.active_end_hour   = { op: '', v1: '', v2: '' };

  // Reset checkboxes
  document.querySelectorAll('.status-cb').forEach(cb => { cb.checked = cb.value === 'active'; });
  const delCb = document.getElementById('cb-deleted');
  if (delCb) delCb.checked = false;

  // Reset date filters
  document.querySelectorAll('.date-filter').forEach(df => {
    df.querySelector('.filter-op').value       = '';
    df.querySelector('.filter-date1').style.display  = 'none';
    df.querySelector('.filter-date1').value    = '';
    const andEl = df.querySelector('.filter-and');
    if (andEl) andEl.style.display = 'none';
    df.querySelector('.filter-date2').style.display  = 'none';
    df.querySelector('.filter-date2').value    = '';
  });

  render();
}

// ── Settings ───────────────────────────────────────────────────────────────
async function saveSettings() {
  const val = parseInt(document.getElementById('max-active-input').value);
  if (isNaN(val) || val < 1) { alert('Max active must be a positive number.'); return; }
  const res = await api('PUT', '/config', { max_active: val });
  if (res.success) {
    S.config.max_active = String(val);
    updateActiveCount();
  }
}

// ── Theme & view toggles ───────────────────────────────────────────────────
function applyTheme(t) {
  if (t) { S.theme = t; lsSet('tr_theme', t); }
  document.documentElement.setAttribute('data-theme', S.theme);
  document.getElementById('theme-icon').textContent = S.theme === 'dark' ? '☾' : '☀';
}

function applyView(v) {
  if (v) { S.view = v; lsSet('tr_view', v); }
  document.getElementById('view-icon').textContent = S.view === 'card' ? '☰' : '⊞';
}

// ── Event wiring ───────────────────────────────────────────────────────────
function setupEvents() {
  // Header controls
  document.getElementById('btn-add').addEventListener('click', openAdd);
  document.getElementById('btn-theme').addEventListener('click', () => {
    applyTheme(S.theme === 'dark' ? 'light' : 'dark');
  });
  document.getElementById('btn-view').addEventListener('click', () => {
    applyView(S.view === 'card' ? 'list' : 'card');
    render();
  });

  // Modal close
  document.getElementById('btn-modal-close').addEventListener('click', closeModal);
  document.getElementById('btn-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-overlay')) closeModal();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  // Form submit
  document.getElementById('reminder-form').addEventListener('submit', submitForm);

  // Empty state add button
  document.getElementById('btn-empty-add').addEventListener('click', openAdd);

  // Settings save
  document.getElementById('btn-save-settings').addEventListener('click', saveSettings);

  // Reminder container: event delegation for clicks on cards/rows and menu items
  const container = document.getElementById('reminders-container');
  container.addEventListener('click', e => {
    // ··· button
    const moreBtn = e.target.closest('.btn-more');
    if (moreBtn) {
      e.stopPropagation();
      toggleMenu(parseInt(moreBtn.dataset.id), moreBtn);
      return;
    }

    // Menu action item
    const actionItem = e.target.closest('[data-action]');
    if (actionItem) {
      e.stopPropagation();
      handleAction(actionItem.dataset.action, parseInt(actionItem.dataset.id));
      return;
    }

    // Card or row click → open edit (but not when clicking card-actions area)
    const card = e.target.closest('[data-id]');
    if (card && !e.target.closest('.card-actions') && !e.target.closest('.col-actions')) {
      openEdit(parseInt(card.dataset.id));
    }
  });

  // Close menus on any outside click
  document.addEventListener('click', e => {
    if (!e.target.closest('.overflow-menu') && !e.target.closest('.btn-more')) {
      closeAllMenus();
    }
  });
}

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  applyTheme();
  applyView();
  initFilters();
  setupEvents();
  await fetchAll();
  document.getElementById('max-active-input').value = S.config.max_active || 5;
  render();
}

document.addEventListener('DOMContentLoaded', init);
