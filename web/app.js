let currentProjectId = null;
let currentManifest = null;
// Filled from /api/config at startup; the {} default keeps every read safe if
// that call fails, in which case the app behaves as the local desktop tool.
let deployConfig = {};

const el = (sel) => document.querySelector(sel);
const content = () => el('#content');
const WELCOME_HTML = content().innerHTML; // captured before any project replaces it

function fmtCell(v) {
  if (v === null || v === undefined || v === '') return '<span class="null-cell">—</span>';
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;');
}

// Columns that are internal/noise on most ETAP tables (audit trail, revision
// bookkeeping, per-page "checked" flags). Hidden by default on wide tables
// so the useful columns aren't buried - toggle back on via the Columns panel.
const NOISE_COLUMN_RE = /^(IID|Revision|DataRevs|Issue|RevCtrl|AlteredBy|AlteredTime|CheckedBy|CheckedTime|LibDataModified|LibDataAccessed|Reserved\d*|Checker.*)$/i;

const PAGE_SIZE_OPTIONS = [50, 100, 250, 1000];
const STAT_OPTIONS = [
  { value: 'none', label: 'Summary: none' },
  { value: 'sum', label: 'Sum' },
  { value: 'avg', label: 'Average' },
  { value: 'min', label: 'Min' },
  { value: 'max', label: 'Max' },
  { value: 'count', label: 'Count' },
];

function formatStatNumber(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function computeColumnStat(values, statType) {
  const nums = values.filter(v => typeof v === 'number' && !Number.isNaN(v));
  if (statType === 'count') return values.filter(v => v !== null && v !== undefined && v !== '').length;
  if (nums.length === 0) return null;
  switch (statType) {
    case 'sum': return nums.reduce((a, b) => a + b, 0);
    case 'avg': return nums.reduce((a, b) => a + b, 0) / nums.length;
    case 'min': return Math.min(...nums);
    case 'max': return Math.max(...nums);
    default: return null;
  }
}
// Long enough to survive normal typing pauses between letters (so filtering
// doesn't fire - and re-render - after nearly every keystroke), short enough
// to still feel immediate once you actually stop. Press Enter to skip the
// wait and filter right away.
const SEARCH_DEBOUNCE_MS = 400;

function csvEscape(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function buildCsv(columns, rows) {
  const lines = [columns.map(csvEscape).join(',')];
  rows.forEach(r => lines.push(columns.map(c => csvEscape(r[c])).join(',')));
  return lines.join('\r\n');
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadCsv(columns, rows, filename) {
  downloadBlob(new Blob([buildCsv(columns, rows)], { type: 'text/csv;charset=utf-8' }), filename);
}

async function downloadXlsx(columns, rows, filename, sheetName) {
  const res = await fetch(apiUrl('/api/export/xlsx'), {
    method: 'POST',
    headers: { ...sessionHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ columns, rows, filename, sheet_name: sheetName || filename }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || res.statusText);
  }
  downloadBlob(await res.blob(), filename);
}

function renderDataTable(columns, rows, exportName) {
  const wrapId = 'tbl_' + Math.random().toString(36).slice(2);
  const showColumnControls = columns.length > 8;
  const hideNoiseByDefault = columns.length > 15;
  const hiddenCols = new Set(hideNoiseByDefault ? columns.filter(c => NOISE_COLUMN_RE.test(c)) : []);
  if (hiddenCols.size >= columns.length) hiddenCols.clear(); // never hide every column
  const baseName = (exportName || 'export').replace(/[^\w.\- ]+/g, '_');

  const container = document.createElement('div');
  container.innerHTML = `
    <div class="table-toolbar">
      <select id="${wrapId}_searchcol" class="search-col-select">
        <option value="">All columns</option>
        ${columns.map(c => `<option value="${c}">${c}</option>`).join('')}
      </select>
      <input type="search" placeholder="Search... (Enter to search now)" id="${wrapId}_search">
      <select id="${wrapId}_pagesize">
        ${PAGE_SIZE_OPTIONS.map(n => `<option value="${n}" ${n === 100 ? 'selected' : ''}>${n} / page</option>`).join('')}
        <option value="0">Show all</option>
      </select>
      ${showColumnControls ? `<button class="col-toggle-btn" id="${wrapId}_colsbtn">Columns</button>` : ''}
      <select id="${wrapId}_stat">
        ${STAT_OPTIONS.map(o => `<option value="${o.value}">${o.label}</option>`).join('')}
      </select>
      <button class="export-toggle-btn" id="${wrapId}_exportbtn">&#11015; Export</button>
      <span class="row-count" id="${wrapId}_count"></span>
    </div>
    <div class="col-panel hidden" id="${wrapId}_colpanel"></div>
    <div class="export-panel hidden" id="${wrapId}_exportpanel"></div>
    <div class="table-scroll">
      <table class="data-table" id="${wrapId}">
        <thead><tr></tr></thead>
        <tbody></tbody>
        <tfoot class="hidden"><tr></tr></tfoot>
      </table>
    </div>
    <div class="pager" id="${wrapId}_pager"></div>`;

  let sortCol = null, sortDir = 1;
  let filtered = rows;
  let page = 1;
  let pageSize = 100;
  let searchTimer = null;
  let searchColumn = '';   // '' = search all columns
  let lastQuery = '';
  let statType = 'none';

  const visibleColumns = () => columns.filter(c => !hiddenCols.has(c));

  function updateColsBtnLabel() {
    const btn = container.querySelector(`#${wrapId}_colsbtn`);
    if (btn) btn.textContent = `Columns (${columns.length - hiddenCols.size}/${columns.length})`;
  }

  function renderHead() {
    const tr = container.querySelector(`#${wrapId} thead tr`);
    tr.innerHTML = visibleColumns().map(c => {
      const arrow = sortCol === c ? (sortDir === 1 ? ' &#9650;' : ' &#9660;') : '';
      return `<th data-col="${c}">${c}${arrow}</th>`;
    }).join('');
    tr.querySelectorAll('th').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.dataset.col;
        sortDir = (sortCol === col) ? -sortDir : 1;
        sortCol = col;
        resort();
        renderHead();
        renderBody();
      });
    });
  }

  function resort() {
    if (!sortCol) return;
    filtered = [...filtered].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol];
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sortDir;
      return String(av).localeCompare(String(bv)) * sortDir;
    });
  }

  function applyFilter(q) {
    lastQuery = q;
    const searchCols = searchColumn ? [searchColumn] : columns;
    filtered = !q ? rows : rows.filter(r => searchCols.some(c => String(r[c] ?? '').toLowerCase().includes(q)));
    resort();
    page = 1;
  }

  function renderBody() {
    const cols = visibleColumns();
    const total = filtered.length;
    const size = pageSize || Math.max(total, 1);
    const totalPages = Math.max(1, Math.ceil(total / size));
    if (page > totalPages) page = totalPages;
    const start = pageSize ? (page - 1) * size : 0;
    const pageRows = pageSize ? filtered.slice(start, start + size) : filtered;

    const tbody = container.querySelector(`#${wrapId} tbody`);
    tbody.innerHTML = pageRows.map(r =>
      `<tr>${cols.map(c => `<td>${fmtCell(r[c])}</td>`).join('')}</tr>`
    ).join('');
    container.querySelector(`#${wrapId}_count`).textContent =
      total === rows.length ? `${total} rows` : `${total} of ${rows.length} rows`;

    renderFooter();

    const pager = container.querySelector(`#${wrapId}_pager`);
    if (pageSize && totalPages > 1) {
      pager.innerHTML = `
        <button class="pager-btn" data-act="first"${page === 1 ? ' disabled' : ''}>&laquo;</button>
        <button class="pager-btn" data-act="prev"${page === 1 ? ' disabled' : ''}>&lsaquo; Prev</button>
        <span class="pager-info">Page ${page} of ${totalPages}</span>
        <button class="pager-btn" data-act="next"${page === totalPages ? ' disabled' : ''}>Next &rsaquo;</button>
        <button class="pager-btn" data-act="last"${page === totalPages ? ' disabled' : ''}>&raquo;</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b => b.addEventListener('click', () => {
        if (b.dataset.act === 'first') page = 1;
        if (b.dataset.act === 'prev') page = Math.max(1, page - 1);
        if (b.dataset.act === 'next') page = Math.min(totalPages, page + 1);
        if (b.dataset.act === 'last') page = totalPages;
        renderBody();
      }));
    } else {
      pager.innerHTML = '';
    }
  }

  function renderFooter() {
    const tfoot = container.querySelector(`#${wrapId} tfoot`);
    if (statType === 'none') {
      tfoot.classList.add('hidden');
      return;
    }
    tfoot.classList.remove('hidden');
    const cols = visibleColumns();
    const label = STAT_OPTIONS.find(o => o.value === statType).label;
    tfoot.innerHTML = '';
    const tr = document.createElement('tr');
    cols.forEach((c, i) => {
      const td = document.createElement('td');
      if (i === 0) {
        td.textContent = label;
        td.className = 'stat-label';
      } else {
        const value = computeColumnStat(filtered.map(r => r[c]), statType);
        td.textContent = value === null ? '' : formatStatNumber(value);
      }
      tr.appendChild(td);
    });
    tfoot.appendChild(tr);
  }

  function runFilter(q) {
    applyFilter(q);
    renderBody();
  }

  const searchBox = container.querySelector(`#${wrapId}_search`);
  searchBox.addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.toLowerCase();
    searchTimer = setTimeout(() => runFilter(q), SEARCH_DEBOUNCE_MS);
  });
  searchBox.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      clearTimeout(searchTimer);
      runFilter(e.target.value.toLowerCase());
    }
  });

  container.querySelector(`#${wrapId}_searchcol`).addEventListener('change', (e) => {
    searchColumn = e.target.value;
    searchBox.placeholder = searchColumn
      ? `Search ${searchColumn}... (Enter to search now)`
      : 'Search... (Enter to search now)';
    if (searchColumn && hiddenCols.has(searchColumn)) {
      hiddenCols.delete(searchColumn); // so you can actually see what you're filtering on
      updateColsBtnLabel();
      renderHead();
    }
    clearTimeout(searchTimer);
    runFilter(lastQuery);
  });

  container.querySelector(`#${wrapId}_pagesize`).addEventListener('change', (e) => {
    pageSize = Number(e.target.value);
    page = 1;
    renderBody();
  });

  container.querySelector(`#${wrapId}_stat`).addEventListener('change', (e) => {
    statType = e.target.value;
    renderFooter();
  });

  const colsBtn = container.querySelector(`#${wrapId}_colsbtn`);
  if (colsBtn) {
    const panel = container.querySelector(`#${wrapId}_colpanel`);
    function renderColPanel() {
      panel.innerHTML = `
        <div class="col-panel-actions">
          <button type="button" data-act="all">Show all</button>
          <button type="button" data-act="essential">Essential only</button>
        </div>
        <div class="col-panel-list">
          ${columns.map(c => `<label><input type="checkbox" data-col="${c}"${hiddenCols.has(c) ? '' : ' checked'}> ${c}</label>`).join('')}
        </div>`;
      panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener('change', () => {
          if (cb.checked) hiddenCols.delete(cb.dataset.col); else hiddenCols.add(cb.dataset.col);
          updateColsBtnLabel();
          renderHead();
          renderBody();
        });
      });
      panel.querySelector('[data-act="all"]').addEventListener('click', () => {
        hiddenCols.clear();
        renderColPanel();
        updateColsBtnLabel();
        renderHead();
        renderBody();
      });
      panel.querySelector('[data-act="essential"]').addEventListener('click', () => {
        columns.forEach(c => { if (NOISE_COLUMN_RE.test(c)) hiddenCols.add(c); else hiddenCols.delete(c); });
        renderColPanel();
        updateColsBtnLabel();
        renderHead();
        renderBody();
      });
    }
    renderColPanel();
    colsBtn.addEventListener('click', () => panel.classList.toggle('hidden'));
    updateColsBtnLabel();
  }

  const exportBtn = container.querySelector(`#${wrapId}_exportbtn`);
  const exportPanel = container.querySelector(`#${wrapId}_exportpanel`);
  function renderExportPanel() {
    exportPanel.innerHTML = `
      <div class="export-note">Exports the ${filtered.length} currently filtered/visible row(s) and column(s) - not just this page.</div>
      <div class="export-panel-actions">
        <button type="button" data-act="csv">Download CSV</button>
        <button type="button" data-act="xlsx">Download Excel (.xlsx)</button>
      </div>
      <div class="export-status" id="${wrapId}_exportstatus"></div>`;
    exportPanel.querySelector('[data-act="csv"]').addEventListener('click', () => {
      downloadCsv(visibleColumns(), filtered, `${baseName}.csv`);
    });
    exportPanel.querySelector('[data-act="xlsx"]').addEventListener('click', async () => {
      const status = exportPanel.querySelector(`#${wrapId}_exportstatus`);
      status.textContent = 'Generating...';
      try {
        await downloadXlsx(visibleColumns(), filtered, `${baseName}.xlsx`, baseName);
        status.textContent = '';
      } catch (e) {
        status.textContent = 'Error: ' + e.message;
      }
    });
  }
  renderExportPanel();
  exportBtn.addEventListener('click', () => {
    renderExportPanel(); // refresh row/column counts to match current filter state
    exportPanel.classList.toggle('hidden');
  });

  applyFilter('');
  renderHead();
  renderBody();
  return container;
}

// Set by config.js. Empty = same origin (local desktop app); an absolute URL
// when the frontend is served separately from the backend.
const API_BASE = (window.ETAP_API_BASE || '').replace(/\/$/, '');

/** Absolute URL for an API path. Every call to the backend goes through this
 *  so a split deployment only has to change config.js. */
function apiUrl(path) {
  return API_BASE + path;
}

// Anonymous identity for the hosted app, so one visitor's uploads stay
// separate from another's. 128 bits from the browser's CSPRNG, kept in
// localStorage and sent as a header - not a cookie, because the frontend and
// API can be on different origins and a header sidesteps SameSite entirely.
const SESSION_KEY = 'etaplens.session';

function sessionId() {
  let id = null;
  try {
    id = localStorage.getItem(SESSION_KEY);
  } catch { /* private mode - fall through to a per-page-load id */ }
  if (!id || !/^[a-f0-9]{32,64}$/.test(id)) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    id = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    try { localStorage.setItem(SESSION_KEY, id); } catch { /* ignore */ }
  }
  return id;
}

function sessionHeaders() {
  return { 'X-Session-Id': sessionId() };
}

async function api(path, opts = {}) {
  const res = await fetch(apiUrl(path), {
    ...opts,
    headers: { ...sessionHeaders(), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || res.statusText);
  }
  return res.json();
}

function setActiveMenu(view) {
  document.querySelectorAll('.menu-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
}

// ---------- Views ----------

const CATEGORY_SET_LABELS = {
  model: 'ETAP Project Model',
  sc_duty: 'Short Circuit Study - ANSI Duty',
  sc_fault: 'Short Circuit Study - Fault Currents',
  load_flow: 'Load Flow Study - Balanced',
  load_flow_unbalanced: 'Load Flow Study - Unbalanced (3-Phase)',
  time_domain: 'Time-Domain Load Flow Study (TDLF)',
};

// Shown above a table whose payload the server capped. Filtering and sorting
// in the toolbar only see the rows that were sent, so say so plainly rather
// than letting a search silently miss most of the year.
/** Shown where a lite cache has deliberately left nothing to display. */
function truncationNotice_lite() {
  return `<div class="truncation-note">This copy keeps only the summary tables. The per-step
    detail behind them - one row per branch per time step - was discarded after the summaries
    were built, which is why there is nothing here. Load the file in the desktop app to page
    through the raw series.</div>`;
}

function truncationNotice(rowCount, shown) {
  return `<div class="truncation-note">Showing the first ${shown.toLocaleString()}
    of ${rowCount.toLocaleString()} rows. Search, sort and export apply to these
    rows only - use the summary tables for whole-run figures.</div>`;
}

async function showOverview() {
  content().innerHTML = '<div class="loading">Loading overview...</div>';
  const data = await api(`/api/project/${currentProjectId}/overview`);
  const m = currentManifest = data.manifest;
  const categorySet = m.category_set || 'model';

  const kv = (obj) => Object.entries(obj)
    .filter(([k, v]) => v !== null && v !== '' && !['IID', 'Revision', 'DataRevs', 'Issue'].includes(k))
    .map(([k, v]) => `<div class="kv"><div class="k">${k}</div><div class="v">${fmtCell(v)}</div></div>`)
    .join('');

  const infoCards = data.info_tables.map(t => {
    const row = t.rows[0] || {};
    return Object.keys(row).length
      ? `<div class="card"><h3>${t.title}</h3><div class="kv-grid">${kv(row)}</div></div>` : '';
  }).join('');

  content().innerHTML = `
    <div class="page-title-row">
      <div>
        <div class="page-title">${CATEGORY_SET_LABELS[categorySet] || 'Overview'}</div>
        <div class="page-desc">${m.db_name} &middot; loaded from ${m.db_kind.toUpperCase()} &middot; ${m.stats.tables} tables, ${m.stats.rows_total.toLocaleString()} total rows</div>
      </div>
      ${categorySet !== 'model' ? `
        <div class="violations-report-box">
          <button id="violations-report-btn" class="violations-report-btn">&#128202; Violations Report (.xlsx)</button>
          <div class="export-status" id="violations-report-status"></div>
        </div>` : ''}
    </div>
    ${m.stats.lite_cache ? `<div class="truncation-note">Summary tables only. The per-step
      detail ETAP produced (one row per branch per time step) was discarded after the
      summaries were built, so the raw result tables and the branch time series are not
      available here - load this file in the desktop app to page through them.</div>` : ''}

    <div class="card">
      <h3>${categorySet === 'model' ? 'Equipment Summary' : 'Results Summary'}</h3>
      <div class="stat-grid" id="stat-grid"></div>
    </div>

    ${infoCards}

    <div class="card">
      <h3>Source</h3>
      <div class="kv-grid">
        <div class="kv"><div class="k">Input path</div><div class="v">${fmtCell(m.input_path)}</div></div>
        <div class="kv"><div class="k">Resolved database</div><div class="v">${fmtCell(m.db_path)}</div></div>
        <div class="kv"><div class="k">Note</div><div class="v">${fmtCell(m.note)}</div></div>
      </div>
    </div>
  `;

  const grid = el('#stat-grid');
  data.equipment_counts.forEach(c => {
    const card = document.createElement('div');
    card.className = 'stat-card';
    card.innerHTML = `<div class="num">${c.row_count}</div><div class="lbl">${c.label}</div>`;
    card.addEventListener('click', () => showCategory(c.key));
    grid.appendChild(card);
  });

  const reportBtn = el('#violations-report-btn');
  if (reportBtn) {
    reportBtn.addEventListener('click', async () => {
      const status = el('#violations-report-status');
      reportBtn.disabled = true;
      status.textContent = 'Generating...';
      try {
        const res = await fetch(apiUrl(`/api/project/${currentProjectId}/violations_report`),
                                { headers: sessionHeaders() });
        if (!res.ok) {
          const body = await res.json().catch(() => ({ error: res.statusText }));
          throw new Error(body.error || res.statusText);
        }
        downloadBlob(await res.blob(), `${m.db_name}_violations_report.xlsx`);
        status.textContent = '';
      } catch (e) {
        status.textContent = e.message;
      } finally {
        reportBtn.disabled = false;
      }
    });
  }
}

async function showCategory(key) {
  setActiveMenu(key);
  content().innerHTML = '<div class="loading">Loading...</div>';
  const data = await api(`/api/project/${currentProjectId}/category/${key}`);
  const nonEmpty = data.subtables.filter(s => s.row_count > 0);
  const empty = data.subtables.filter(s => s.row_count === 0);

  content().innerHTML = `
    <div class="page-title">${data.label}</div>
    <div class="page-desc">${data.description}${empty.length ? ` &middot; (${empty.map(s => s.table).join(', ')} present but empty)` : ''}</div>
    <div class="subtable-tabs" id="sub-tabs"></div>
    <div id="sub-content"></div>
  `;

  if (nonEmpty.length === 0) {
    // Under a lite cache these categories are empty by design, not because the
    // study had nothing in them - say which it is.
    el('#sub-content').innerHTML = currentManifest?.stats?.lite_cache
      ? truncationNotice_lite()
      : `<div class="loading">No data found for this category in this project.</div>`;
    return;
  }

  const tabs = el('#sub-tabs');
  nonEmpty.forEach((s, i) => {
    const tab = document.createElement('button');
    tab.className = 'subtable-tab' + (i === 0 ? ' active' : '');
    tab.textContent = `${s.table} (${s.row_count})`;
    tab.addEventListener('click', () => {
      tabs.querySelectorAll('.subtable-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderSub(s);
    });
    tabs.appendChild(tab);
  });
  renderSub(nonEmpty[0]);

  function renderSub(s) {
    const wrap = el('#sub-content');
    wrap.innerHTML = s.truncated ? truncationNotice(s.row_count, s.rows.length) : '';
    wrap.appendChild(renderDataTable(s.columns, s.rows, s.table));
  }
}

async function showAllTables() {
  setActiveMenu('all-tables');
  content().innerHTML = '<div class="loading">Loading table list...</div>';
  const tables = await api(`/api/project/${currentProjectId}/tables`);
  content().innerHTML = `
    <div class="page-title">All Tables (raw)</div>
    <div class="page-desc">Every table in the underlying ETAP database, including revision-history (H1-H8) and rating (_R) tables. ${tables.length} tables total.</div>
    <div class="table-toolbar"><input type="search" placeholder="Filter tables..." id="table-filter"></div>
    <div class="tables-list" id="tables-list"></div>
  `;
  const list = el('#tables-list');
  function draw(filter) {
    const q = filter.toLowerCase();
    list.innerHTML = tables.filter(t => !q || t.table.toLowerCase().includes(q)).map(t => `
      <div class="t-row" data-table="${t.table}">
        <span>${t.table} ${t.category ? `<span class="cat-tag">${t.category}</span>` : ''}</span>
        <span>${t.rows}</span>
      </div>`).join('');
    list.querySelectorAll('.t-row').forEach(row => {
      row.addEventListener('click', () => showRawTable(row.dataset.table));
    });
  }
  draw('');
  el('#table-filter').addEventListener('input', e => draw(e.target.value));
}

async function showRawTable(tableName) {
  content().innerHTML = '<div class="loading">Loading table...</div>';
  const data = await api(`/api/project/${currentProjectId}/table/${encodeURIComponent(tableName)}`);
  content().innerHTML = `
    <div class="page-title">${tableName}</div>
    <div class="page-desc"><a href="#" id="back-to-tables">&larr; back to All Tables</a></div>
    <div id="raw-table-content"></div>
  `;
  el('#back-to-tables').addEventListener('click', (e) => { e.preventDefault(); showAllTables(); });
  if (data.truncated) {
    el('#raw-table-content').innerHTML = truncationNotice(data.row_count, data.rows.length);
  }
  el('#raw-table-content').appendChild(renderDataTable(data.columns, data.rows, tableName));
}

async function showSingleLine() {
  setActiveMenu('single-line');
  content().innerHTML = '<div class="loading">Loading buses...</div>';
  const buses = await api(`/api/project/${currentProjectId}/buses`);
  content().innerHTML = `
    <div class="page-title">Single Line &middot; Bus Explorer</div>
    <div class="page-desc">Pick a bus to see every piece of equipment connected to it - a tabular view of the
      one-line diagram topology around that bus (this is not a graphical SLD renderer).</div>
    <div class="bus-picker">
      <select id="bus-select">
        <option value="">Select a bus (${buses.length} total)...</option>
        ${buses.map(b => `<option value="${b.id}">${b.id} ${b.kv ? `(${b.kv} kV)` : ''}</option>`).join('')}
      </select>
    </div>
    <div id="bus-detail"></div>
  `;
  el('#bus-select').addEventListener('change', (e) => {
    if (e.target.value) showBusConnectivity(e.target.value);
    else el('#bus-detail').innerHTML = '';
  });
}

async function showBusConnectivity(busId) {
  el('#bus-detail').innerHTML = '<div class="loading">Loading connectivity...</div>';
  const data = await api(`/api/project/${currentProjectId}/bus/${encodeURIComponent(busId)}/connectivity`);
  const bus = data.bus;
  const groups = {};
  data.connections.forEach(c => {
    groups[c.table] = groups[c.table] || [];
    groups[c.table].push(c);
  });

  let busInfoHtml = '';
  if (bus) {
    const keep = ['ID', 'NominalkV', 'InService', 'VMag', 'VAng', 'Area', 'Zone', 'Description'];
    busInfoHtml = `<div class="card"><h3>Bus Properties</h3><div class="kv-grid">${
      keep.filter(k => bus[k] !== null && bus[k] !== undefined && bus[k] !== '').map(k =>
        `<div class="kv"><div class="k">${k}</div><div class="v">${fmtCell(bus[k])}</div></div>`).join('')
    }</div></div>`;
  }

  const groupsHtml = Object.entries(groups).map(([table, items]) => `
    <div class="conn-group">
      <h4>${table} <span class="badge">${items.length}</span></h4>
      <div class="conn-list">
        ${items.map(c => `
          <div class="conn-row">
            <span class="id">${fmtCell(c.id)}</span>
            <span class="other">${c.other_end.filter(Boolean).map(fmtCell).join(' &rarr; ') || '&nbsp;'}
              ${c.in_service !== null && c.in_service !== undefined ? ` &middot; InService: ${fmtCell(c.in_service)}` : ''}</span>
          </div>`).join('')}
      </div>
    </div>
  `).join('');

  el('#bus-detail').innerHTML = busInfoHtml + (
    data.connections.length
      ? `<div class="card"><h3>Connected Equipment (${data.connections.length})</h3>${groupsHtml}</div>`
      : `<div class="loading">No connected equipment found referencing this bus by name.</div>`
  );
}

// ---------- Shell / loading ----------

function buildCategoryMenu(categories) {
  const menu = el('#category-menu');
  menu.innerHTML = '';
  categories.forEach(c => {
    const btn = document.createElement('button');
    btn.className = 'menu-item';
    btn.dataset.view = c.key;
    btn.innerHTML = `<span>${c.label}</span><span class="count">${c.row_count}</span>`;
    btn.addEventListener('click', () => showCategory(c.key));
    menu.appendChild(btn);
  });
}

async function activateProject(projectId) {
  currentProjectId = projectId;
  el('#menu').classList.remove('hidden');
  const { category_set, categories } = await api(`/api/project/${projectId}/categories`);
  buildCategoryMenu(categories);
  el('[data-view="single-line"]').classList.toggle('hidden', category_set !== 'model');
  await showOverview();
  setActiveMenu('overview');
}

function resetToWelcome() {
  currentProjectId = null;
  currentManifest = null;
  el('#menu').classList.add('hidden');
  content().innerHTML = WELCOME_HTML;
}

async function unloadProject(projectId) {
  await api(`/api/project/${projectId}/unload`, { method: 'DELETE' });
  if (currentProjectId === projectId) resetToWelcome();
  await refreshRecentProjects();
}

async function refreshRecentProjects() {
  const projects = await api('/api/projects');
  const box = el('#recent-projects');
  box.innerHTML = '';
  projects.forEach(p => {
    const row = document.createElement('div');
    row.className = 'recent-item-row';
    row.innerHTML = `
      <span class="recent-item" title="${p.input_path}">${p.db_name}</span>
      <button class="recent-remove" title="Unload this project">&times;</button>`;
    row.querySelector('.recent-item').addEventListener('click', () => activateProject(p.project_id));
    row.querySelector('.recent-remove').addEventListener('click', (e) => {
      e.stopPropagation();
      unloadProject(p.project_id);
    });
    box.appendChild(row);
  });
  el('#clear-all-btn').classList.toggle('hidden', projects.length === 0);
}

function setLoadersDisabled(disabled) {
  // #load-btn is absent in hosted mode, where loading by path is disabled.
  ['#load-btn', '#browse-btn'].forEach(sel => {
    const node = el(sel);
    if (node) node.disabled = disabled;
  });
}

async function pollJob(jobId) {
  const statusBox = el('#load-status');
  while (true) {
    const job = await api(`/api/load/status/${jobId}`);
    if (job.error) {
      statusBox.textContent = 'Error: ' + job.error;
      statusBox.className = 'error';
      setLoadersDisabled(false);
      return;
    }
    if (job.done) {
      statusBox.textContent = 'Loaded.';
      statusBox.className = 'ok';
      setLoadersDisabled(false);
      el('#browse-candidates').innerHTML = '';
      el('#path-candidates').innerHTML = '';
      await refreshRecentProjects();
      await activateProject(job.project_id);
      return;
    }
    const stageLabel = {
      starting: 'Starting...', copying: 'Copying database file...',
      starting_instance: 'Starting SQL Server LocalDB...', attaching: 'Attaching database...',
      dumping: `Reading tables (${job.current}/${job.total})...`, detaching: 'Cleaning up...',
      indexing: 'Indexing tables...',
    }[job.stage] || job.stage;
    statusBox.textContent = stageLabel;
    statusBox.className = '';
    await new Promise(r => setTimeout(r, 600));
  }
}

function renderPathCandidates(candidates) {
  const box = el('#path-candidates');
  if (candidates.length === 0) {
    box.innerHTML = `<div class="browse-error">No .oti/.mdf/.bak/study-result files found directly inside that folder.</div>`;
    return;
  }
  box.innerHTML = `<div class="candidates-label">Found ${candidates.length} loadable file(s) - click to load:</div>` +
    candidates.map((c, i) => `
      <div class="candidate-item" data-idx="${i}">
        <span>${c.filename}${c.error ? '' : `<span class="cand-type">${c.label}</span>`}</span>
        <span class="sz">${c.error ? 'unresolved' : (c.size / 1e6).toFixed(1) + ' MB'}</span>
      </div>`).join('');
  box.querySelectorAll('.candidate-item').forEach((item, i) => {
    item.addEventListener('click', () => loadPathDirectly(candidates[i].path));
  });
}

async function loadPathDirectly(path) {
  setLoadersDisabled(true);
  el('#load-status').textContent = 'Starting...';
  el('#load-status').className = '';
  try {
    const data = await api('/api/load', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    el('#path-candidates').innerHTML = '';
    pollJob(data.job_id);
  } catch (e) {
    el('#load-status').textContent = 'Error: ' + e.message;
    el('#load-status').className = 'error';
    setLoadersDisabled(false);
  }
}

async function loadOrScanPath(path) {
  setLoadersDisabled(true);
  el('#load-status').textContent = 'Checking path...';
  el('#load-status').className = '';
  el('#path-candidates').innerHTML = '';
  try {
    const data = await api('/api/load', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (data.is_folder) {
      el('#load-status').textContent = '';
      renderPathCandidates(data.candidates);
      setLoadersDisabled(false);
    } else {
      pollJob(data.job_id);
    }
  } catch (e) {
    el('#load-status').textContent = 'Error: ' + e.message;
    el('#load-status').className = 'error';
    setLoadersDisabled(false);
  }
}

el('#load-btn').addEventListener('click', () => {
  const path = el('#path-input').value.trim();
  if (!path) {
    el('#load-status').textContent = 'Type or paste a file or folder path first.';
    el('#load-status').className = 'error';
    el('#path-input').focus();
    return;
  }
  loadOrScanPath(path);
});
el('#path-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') el('#load-btn').click(); });

const EXT_LABELS = {
  mdf: 'Project Database (.MDF)', bak: 'Project Database Backup (.BAK)',
  sa1s: 'Short Circuit - ANSI Duty', sa2s: 'Short Circuit - Fault Currents',
  lf1s: 'Load Flow - Balanced', ul1s: 'Load Flow - Unbalanced (3-Phase)',
};

// Out of whatever the user picked in the file dialog (they can multi-select,
// e.g. ctrl-click both the .oti and the .mdf), find the actual loadable
// file(s): prefer .mdf (live data) over .bak (backup) over study results.
function pickDbCandidates(files) {
  const dbFiles = files.filter(f => /\.(mdf|bak|sa1s|sa2s|lf1s|ul1s)$/i.test(f.name));
  const otiFiles = files.filter(f => /\.oti$/i.test(f.name));
  const rank = (name) => {
    if (/\.mdf$/i.test(name)) return 0;
    if (/\.bak$/i.test(name)) return 1;
    return 2; // study result files
  };
  dbFiles.sort((a, b) => (rank(a.name) - rank(b.name)) || (b.size - a.size));
  return { dbFiles, otiFiles };
}

el('#browse-btn').addEventListener('click', () => el('#folder-input').click());

el('#folder-input').addEventListener('change', (e) => {
  const files = Array.from(e.target.files);
  const { dbFiles, otiFiles } = pickDbCandidates(files);
  const box = el('#browse-candidates');

  if (dbFiles.length === 0) {
    box.innerHTML = `<div class="browse-error">${
      otiFiles.length
        ? `You selected ${otiFiles.map(f => f.name).join(', ')}, which only stores a connection pointer - it has no engineering data. Click "Browse for single file..." again and, in the same folder, hold Ctrl and also click the matching .MDF (or .BAK) file, or paste its path below.`
        : `No .oti/.mdf/.bak/study-result file was selected.`
    }</div>`;
    return;
  }

  box.innerHTML = `<div class="candidates-label">Click to load:</div>` +
    dbFiles.map((f, i) => {
      const ext = f.name.split('.').pop().toLowerCase();
      return `<div class="candidate-item" data-idx="${i}">
        <span>${f.name}<span class="cand-type">${EXT_LABELS[ext] || ''}</span></span>
        <span class="sz">${(f.size / 1e6).toFixed(1)} MB</span>
      </div>`;
    }).join('');
  box.querySelectorAll('.candidate-item').forEach((item, i) => {
    item.addEventListener('click', () => uploadAndLoad(dbFiles[i]));
  });
});

/** Ask for an upload URL, PUT the file to it, then tell the API it landed.
 *  Used when the API is hosted: a study result is routinely bigger than the
 *  32 MB a Cloud Run request body allows, so the bytes go straight to object
 *  storage and never pass through the service. */
async function uploadViaSignedUrl(file) {
  const status = el('#load-status');

  status.textContent = 'Preparing upload...';
  const grant = await api('/api/upload/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      turnstile_token: currentTurnstileToken(),
    }),
  });

  status.textContent = `Uploading ${file.name} (${(file.size / 1e6).toFixed(0)} MB)...`;
  const { url, headers } = grant.upload;
  const absolute = url.startsWith('http');
  const put = await fetch(absolute ? url : apiUrl(url), {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/octet-stream',
      ...(headers || {}),
      // Only for the local-storage fallback, which PUTs back to this API and
      // needs to know whose upload this is. A GCS signed URL must be sent
      // exactly as signed - an extra header invalidates the signature.
      ...(absolute ? {} : sessionHeaders()),
    },
    body: file,
  });
  if (!put.ok) throw new Error(`Upload failed (${put.status})`);

  status.textContent = 'Reading file...';
  const { job_id } = await api('/api/upload/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: grant.key }),
  });
  return job_id;
}

async function uploadDirect(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(apiUrl('/api/upload'), {
    method: 'POST', headers: sessionHeaders(), body: fd,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || res.statusText);
  }
  return (await res.json()).job_id;
}

async function uploadAndLoad(file) {
  setLoadersDisabled(true);
  el('#load-status').textContent = `Uploading ${file.name}...`;
  el('#load-status').className = '';
  try {
    const jobId = deployConfig.require_session
      ? await uploadViaSignedUrl(file)
      : await uploadDirect(file);
    pollJob(jobId);
  } catch (e) {
    el('#load-status').textContent = 'Error: ' + e.message;
    el('#load-status').className = 'error';
    setLoadersDisabled(false);
    resetTurnstile();
  }
}

document.querySelectorAll('#menu > button.menu-item').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.view === 'overview') showOverview();
    if (btn.dataset.view === 'single-line') showSingleLine();
    if (btn.dataset.view === 'all-tables') showAllTables();
    setActiveMenu(btn.dataset.view);
  });
});

el('#clear-all-btn').addEventListener('click', async () => {
  if (!confirm('Unload all loaded projects and studies? You can reload any of them again from a path.')) return;
  await api('/api/projects/clear', { method: 'POST' });
  resetToWelcome();
  await refreshRecentProjects();
});

// ---------- In-app folder browser modal ----------
// Native OS file/folder pickers can't give us real paths (browser security),
// and a native "select folder" dialog doesn't show files while browsing at
// all. Since this app already runs locally with real filesystem access, we
// browse it ourselves instead - a drill-down list, right inside the page.

let fsQuickLocationsCache = null;

async function getQuickLocations() {
  if (!fsQuickLocationsCache) {
    fsQuickLocationsCache = await api('/api/browse/quick');
  }
  return fsQuickLocationsCache;
}

function renderQuickLocations(locations, currentPath) {
  const box = el('#fs-quick');
  box.innerHTML = locations.map(loc => {
    const isActive = loc.path.toLowerCase() === (currentPath || '').toLowerCase();
    return `<button class="fs-quick-btn${isActive ? ' active' : ''}" data-path="${loc.path}">${loc.name}</button>`;
  }).join('');
  box.querySelectorAll('.fs-quick-btn').forEach(b => b.addEventListener('click', () => fsNavigate(b.dataset.path)));
}

let fsCurrentPath = '';

function openFolderBrowser(startPath) {
  el('#fs-modal').classList.remove('hidden');
  fsNavigate(startPath || '');
}

function closeFolderBrowser() {
  el('#fs-modal').classList.add('hidden');
}

async function fsNavigate(path) {
  el('#fs-list').innerHTML = '<div class="loading">Loading...</div>';
  try {
    const [data, locations] = await Promise.all([
      api(`/api/browse?path=${encodeURIComponent(path)}`),
      getQuickLocations(),
    ]);
    fsCurrentPath = data.path;
    el('#fs-select-folder-btn').disabled = !data.path; // nothing to select at "This PC"
    renderQuickLocations(locations, data.path);
    renderBreadcrumb(data);
    renderFsList(data);
  } catch (e) {
    el('#fs-list').innerHTML = `<div class="browse-error">${e.message}</div>`;
  }
}

function renderBreadcrumb(data) {
  const bc = el('#fs-breadcrumb');
  const crumbs = [`<span class="fs-crumb${data.path ? '' : ' active'}" data-path="">This PC</span>`];
  if (data.path) {
    const parts = data.path.split(/[\\/]/).filter(Boolean);
    let accum = '';
    parts.forEach((p, i) => {
      accum = i === 0 ? p + '\\' : accum + p + '\\';
      const isLast = i === parts.length - 1;
      crumbs.push(`<span class="fs-crumb${isLast ? ' active' : ''}" data-path="${accum}">${p}</span>`);
    });
  }
  bc.innerHTML = crumbs.join('<span class="fs-crumb-sep">/</span>');
  bc.querySelectorAll('.fs-crumb:not(.active)').forEach(c => c.addEventListener('click', () => fsNavigate(c.dataset.path)));
}

function renderFsList(data) {
  const list = el('#fs-list');
  const rows = [];
  if (data.parent !== null) {
    rows.push(`<div class="fs-row fs-up" data-path="${data.parent}">&#8593; .. (up one level)</div>`);
  }
  data.folders.forEach(f => {
    rows.push(`<div class="fs-row fs-folder" data-path="${f.path}">&#128193; ${f.name}</div>`);
  });
  data.files.forEach(f => {
    rows.push(`<div class="fs-row fs-file" data-path="${f.path}">
      &#128196; ${f.name}<span class="cand-type">${f.label}</span>
      <span class="sz">${(f.size / 1e6).toFixed(1)} MB</span>
    </div>`);
  });
  if (rows.length === 0) {
    list.innerHTML = `<div class="fs-empty">Nothing here - no subfolders, and no .oti/.mdf/.bak/study-result files.</div>`;
    return;
  }
  list.innerHTML = rows.join('');
  list.querySelectorAll('.fs-up, .fs-folder').forEach(r => r.addEventListener('click', () => fsNavigate(r.dataset.path)));
  list.querySelectorAll('.fs-file').forEach(r => r.addEventListener('click', () => {
    closeFolderBrowser();
    loadPathDirectly(r.dataset.path);
  }));
}

el('#fs-browse-btn').addEventListener('click', () => openFolderBrowser(el('#path-input').value.trim()));
el('#fs-modal-close').addEventListener('click', closeFolderBrowser);
el('#fs-select-folder-btn').addEventListener('click', () => {
  if (!fsCurrentPath) return;
  el('#path-input').value = fsCurrentPath;
  closeFolderBrowser();
  loadOrScanPath(fsCurrentPath);
});
el('#fs-modal').addEventListener('click', (e) => { if (e.target.id === 'fs-modal') closeFolderBrowser(); });
document.addEventListener('keydown', (e) => {
  // The modal is absent entirely on a hosted instance.
  const modal = el('#fs-modal');
  if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) closeFolderBrowser();
});

// ---------- Startup ----------
// Browsing the server's filesystem and loading by absolute path only make
// sense when the server *is* your machine. A hosted instance refuses both, so
// hide those controls rather than offering buttons that 403.

// Cloudflare Turnstile, rendered only when the backend says it is configured.
// It guards the upload-URL endpoint - the one thing here that costs money to
// abuse - rather than page loads, which are cheap.
let turnstileWidgetId = null;

function currentTurnstileToken() {
  if (turnstileWidgetId === null || !window.turnstile) return '';
  return window.turnstile.getResponse(turnstileWidgetId) || '';
}

function resetTurnstile() {
  if (turnstileWidgetId !== null && window.turnstile) {
    window.turnstile.reset(turnstileWidgetId);
  }
}

function mountTurnstile(siteKey) {
  const host = document.createElement('div');
  host.id = 'turnstile-host';
  el('#loader-box').appendChild(host);

  const s = document.createElement('script');
  s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  s.async = true;
  s.defer = true;
  s.onload = () => {
    turnstileWidgetId = window.turnstile.render(host, {
      sitekey: siteKey, size: 'flexible', appearance: 'interaction-only',
    });
  };
  document.head.appendChild(s);
}

async function applyDeployMode() {
  let cfg;
  try {
    cfg = await api('/api/config');
  } catch {
    // No answer - assume the local desktop app, which is the only way this
    // page is served without an API alongside it.
    document.body.classList.remove('config-pending');
    return;
  }
  deployConfig = cfg;
  // Mode is known: either reveal the local controls or leave them hidden and
  // strip them below.
  document.body.classList.toggle('hosted', !cfg.local_filesystem);
  document.body.classList.remove('config-pending');
  if (cfg.accepted_extensions?.length) {
    const picker = el('#folder-input');
    if (picker) picker.accept = cfg.accepted_extensions.join(',');
  }
  if (cfg.turnstile_site_key) mountTurnstile(cfg.turnstile_site_key);
  if (cfg.local_filesystem) return;

  // CSS has already hidden these; remove them so no stray handler can reach
  // them and no screen reader announces controls that cannot work.
  document.querySelectorAll('.local-only').forEach(n => n.remove());

  const hint = el('.browse-hint');
  if (hint) {
    hint.textContent = `Pick an ETAP study result file to upload (up to ${cfg.max_upload_mb} MB). `
      + 'It is read in a throwaway copy - your original is never modified.';
  }
  const welcome = el('#welcome');
  if (welcome) {
    welcome.querySelectorAll('p').forEach(p => p.remove());
    welcome.insertAdjacentHTML('beforeend',
      '<p>Upload an ETAP study result file to explore everything inside it - bus voltages, '
      + 'branch loading, losses, and alerts - without needing ETAP installed.</p>'
      + '<p class="hint">Study result files work here: <code>.SA1S</code> and <code>.SA2S</code> '
      + '(short circuit), <code>.LF1S</code> and <code>.UL1S</code> (load flow), and '
      + '<code>.TU1S</code> (time-domain load flow, which also gets an annual AC loss report).</p>'
      + '<p class="hint">Project models (<code>.oti</code>/<code>.mdf</code>/<code>.bak</code>) are '
      + 'SQL Server databases and need the desktop version - they cannot be read here.</p>');
  }
}

// Config first: it decides which controls exist, and the project list is
// scoped by the session header that every call now carries.
applyDeployMode().then(refreshRecentProjects);
