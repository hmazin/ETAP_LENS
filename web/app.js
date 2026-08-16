let currentProjectId = null;
let currentManifest = null;
// Filled from /api/config at startup; the {} default keeps every read safe if
// that call fails, in which case the app behaves as the local desktop tool.
let deployConfig = {};

const el = (sel) => document.querySelector(sel);
// Views replace this wholesale. The breadcrumb lives in a sibling so it
// survives every navigation without each view having to redraw it.
const content = () => el('#view');
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
      <button class="export-toggle-btn" id="${wrapId}_exportbtn">&#11015; Export to Excel / CSV &#9662;</button>
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
    // The caret says whether pressing it opens or closes, so a first click
    // that "did nothing visible" is not a plausible reading of it.
    const open = !exportPanel.classList.contains('hidden');
    exportBtn.innerHTML = `&#11015; Export to Excel / CSV ${open ? '&#9652;' : '&#9662;'}`;
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

// ---------- Breadcrumb ----------
// The site is three levels deep - board, study, view - and until now only the
// way in existed. Every page below the board states the whole path and makes
// each step above it clickable, so leaving a study never means unloading it.

/** `leaf` is the page you are on; the levels above it are worked out from
 *  what is currently open. Pass nothing on the board itself, which is the
 *  top and needs no trail. */
function setCrumbs(leaf) {
  const bar = el('#crumbs');
  if (!bar) return;
  if (!leaf) {
    bar.classList.add('hidden');
    bar.innerHTML = '';
    return;
  }

  const trail = [];
  // The board this study came from, named after its folder - but only when it
  // did come from there. Opening something from Recents while another folder
  // is on the board would otherwise print a trail claiming it lives in a
  // folder it has nothing to do with. The step still exists either way; going
  // back is what it is for.
  const fromThisBoard = currentBoard && currentProjectId
    && currentBoard.modules.some(m => m.files.some(f => f.project_id === currentProjectId));
  trail.push({
    label: fromThisBoard ? currentBoard.name : 'All modules',
    go: () => (currentBoard ? showBoard(currentBoard) : showEmptyBoard()),
  });
  if (currentManifest) {
    trail.push({
      label: currentManifest.display_name || currentManifest.db_name || 'Study',
      go: () => showOverview(),
    });
  }

  bar.innerHTML = trail.map((c, i) =>
    `<button class="crumb" data-i="${i}">${esc(c.label)}</button>`
    + '<span class="crumb-sep">/</span>').join('')
    + `<span class="crumb crumb-here" aria-current="page">${esc(leaf)}</span>`;

  bar.querySelectorAll('.crumb[data-i]').forEach(b => {
    b.addEventListener('click', () => trail[Number(b.dataset.i)].go());
  });
  bar.classList.remove('hidden');
}

// ---------- Views ----------

const CATEGORY_SET_LABELS = {
  model: 'ETAP Project Model',
  sc_duty: 'Short Circuit Study - ANSI Duty',
  sc_fault: 'Short Circuit Study - Fault Currents',
  load_flow: 'Load Flow Study - Balanced',
  load_flow_unbalanced: 'Load Flow Study - Unbalanced (3-Phase)',
  time_domain: 'Time-Domain Load Flow Study (TDLF)',
  harmonics: 'Harmonic Analysis Study',
  ground_grid: 'Ground Grid Study',
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

/** A harmonic run's curves are in .fspdb/.hfpdb files beside the .HA1S, not
 *  inside it. Uploading the .HA1S on its own leaves them behind, which is a
 *  different thing from ETAP having saved no plots - and an engineer who is
 *  looking for a frequency-scan curve needs to know which. */
function noPlotsNotice() {
  return `<div class="truncation-note">No plot curves came with this study. ETAP keeps them in
    <code>.fspdb</code> (frequency scan) and <code>.hfpdb</code> (waveform and spectrum) files
    named after the study case, alongside the <code>.HA1S</code> - so this run was most likely
    saved without plots. They are picked up automatically when the whole project folder is
    opened; opening a single <code>.HA1S</code> on its own leaves them behind.</div>`;
}

function truncationNotice(rowCount, shown) {
  return `<div class="truncation-note">Showing the first ${shown.toLocaleString()}
    of ${rowCount.toLocaleString()} rows. Search, sort and export apply to these
    rows only - use the summary tables for whole-run figures.</div>`;
}

// ---------- Overview info cards ----------
// An info table is one of three different things and each needs its own
// shape. Rendering them all as the first row's key/value pairs - which is
// what this used to do - showed 4 rows out of the 83 these tables carry on a
// time-domain study, while giving the whole page over to the one table that
// really is a single record: ~100 raw study-case column names.

const INFO_NOISE_COLUMNS = ['IID', 'Revision', 'DataRevs', 'Issue'];

function infoPairs(row) {
  return Object.entries(row)
    .filter(([k, v]) => v !== null && v !== '' && !INFO_NOISE_COLUMNS.includes(k));
}

/** Thousands separators for readability, without inventing or dropping
 *  precision. Small magnitudes are left exactly as they arrived: ETAP stores
 *  results as single precision and a loss of 0.000003 MW is the real value,
 *  not a rounding artifact to tidy away. */
function fmtInfoValue(v) {
  if (typeof v === 'number' && Number.isFinite(v)) {
    if (Number.isInteger(v)) return v.toLocaleString();
    const abs = Math.abs(v);
    if (abs >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    if (abs >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }
  return fmtCell(v);
}

function kvGridHtml(pairs) {
  return `<div class="kv-grid">${pairs.map(([k, v]) =>
    `<div class="kv"><div class="k">${esc(k)}</div><div class="v">${fmtInfoValue(v)}</div></div>`).join('')}</div>`;
}

/** Metric/Unit/Value tables are written to be read like a report, and ETAP's
 *  own conventions come through in the rows: a blank Metric is a spacer, and
 *  "-- ... --" is a section heading. Honour both rather than rendering them
 *  as empty table rows. */
function isMetricTable(rows) {
  const cols = Object.keys(rows[0] || {});
  return cols.length === 3 && ['Metric', 'Unit', 'Value'].every(c => cols.includes(c));
}

function metricListHtml(rows) {
  return `<div class="metric-list">${rows.map(r => {
    const name = String(r.Metric ?? '').trim();
    if (!name) return '<div class="metric-gap"></div>';
    const section = name.match(/^--\s*(.*?)\s*--$/);
    if (section) return `<div class="metric-section">${esc(section[1])}</div>`;
    const unit = String(r.Unit ?? '').trim();
    return `<div class="metric-row"><span class="metric-name">${esc(name)}</span>`
      + `<span class="metric-val">${fmtInfoValue(r.Value)}`
      + (unit ? ` <span class="metric-unit">${esc(unit)}</span>` : '') + '</span></div>';
  }).join('')}</div>`;
}

function miniTableHtml(rows) {
  const cols = Object.keys(rows[0]).filter(c => !INFO_NOISE_COLUMNS.includes(c));
  return `<div class="mini-scroll"><table class="mini-table">
    <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r =>
      `<tr>${cols.map(c => `<td>${fmtInfoValue(r[c])}</td>`).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

/** Where this project came from, said in terms that mean something to whoever
 *  loaded it.
 *
 *  An uploaded file has no path worth showing - the server's copy of it lives
 *  in container scratch space under a directory named after the session, which
 *  is a bearer token. A local file does have one, and it is the answer to
 *  "which of these am I looking at". "Resolved database" only earns its row
 *  when it differs from what was opened, which is the .oti case: a pointer
 *  that resolved to the .MDF beside it.
 */
function sourceRows(m) {
  const rows = [];
  if (m.uploaded) {
    rows.push(['Uploaded file', m.display_name || m.input_path]);
  } else {
    if (m.input_path) rows.push(['Opened', m.input_path]);
    if (m.db_path && m.db_path !== m.input_path) rows.push(['Resolved database', m.db_path]);
  }
  if (m.note) rows.push(['Note', m.note]);
  return rows;
}

/** Returns {html, collapsed} so the caller can push reference cards below the
 *  results instead of leaving them wherever the table order put them. */
function infoCard(t) {
  const rows = t.rows || [];
  if (!rows.length) return null;

  // A table whose every column is bookkeeping has nothing to show. ProjProps
  // is one column wide and that column is IID, so filtering left it with no
  // columns at all and it drew an empty card with a row count in the corner.
  const pairs = infoPairs(rows[0]);
  const columns = Object.keys(rows[0]).filter(c => !INFO_NOISE_COLUMNS.includes(c));
  if (!pairs.length && !columns.length) return null;

  // Shape follows the data, whether or not the card is collapsed - collapsing
  // used to force the single-record rendering, which would have shown the
  // first row of a multi-row table and silently dropped the rest.
  const body = rows.length === 1 ? kvGridHtml(pairs)
    : isMetricTable(rows) ? metricListHtml(rows)
    : miniTableHtml(rows);
  const count = rows.length === 1 ? `${pairs.length} fields` : `${rows.length} rows`;

  // Only tables describing configuration collapse, and the server says which
  // those are. Collapsing by width instead would fold away System Totals -
  // 41 columns, and the headline result of a load flow.
  if (t.reference) {
    return {
      collapsed: true,
      html: `<div class="card"><details class="info-details">
               <summary><h3>${esc(t.title)}</h3>
                 <span class="detail-count">${esc(count)}</span></summary>
               ${body}</details></div>`,
    };
  }
  return {
    collapsed: false,
    html: `<div class="card"><h3>${esc(t.title)}
             <span class="detail-count">${esc(count)}</span></h3>${body}</div>`,
  };
}

async function showOverview() {
  content().innerHTML = '<div class="loading">Loading overview...</div>';
  const data = await api(`/api/project/${currentProjectId}/overview`);
  const m = currentManifest = data.manifest;
  const categorySet = m.category_set || 'model';

  const cards = data.info_tables.map(infoCard).filter(Boolean);
  const infoCards = cards.filter(c => !c.collapsed).map(c => c.html).join('')
                  + cards.filter(c => c.collapsed).map(c => c.html).join('');

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
      ${kvGridHtml(sourceRows(m))}
    </div>
  `;

  setCrumbs('Overview');
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

  setCrumbs(data.label);
  if (nonEmpty.length === 0) {
    // Under a lite cache these categories are empty by design, not because the
    // study had nothing in them - say which it is.
    el('#sub-content').innerHTML = currentManifest?.stats?.lite_cache
      ? truncationNotice_lite()
      : key === 'plot_curves' ? noPlotsNotice()
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
    <div class="table-toolbar">
      <input type="search" placeholder="Filter tables..." id="table-filter">
      <span class="row-count" id="table-count"></span>
      <button class="export-toggle-btn" id="index-export">&#11015; Export list to Excel / CSV &#9662;</button>
    </div>
    <div class="export-panel hidden" id="index-export-panel"></div>
    <div class="tables-list" id="tables-list"></div>
  `;
  setCrumbs('All Tables');
  const list = el('#tables-list');

  // What is on this page is an index of the database - every table, its
  // category and how many rows it holds. That is worth taking away on a
  // model with 873 of them, and it is the one table view that had no export
  // because it is not built on renderDataTable.
  let shown = tables;
  const INDEX_COLUMNS = ['Table', 'Category', 'Rows'];
  const indexRows = () => shown.map(t => ({
    Table: t.table, Category: t.category || '', Rows: t.rows,
  }));

  function draw(filter) {
    const q = filter.toLowerCase();
    shown = tables.filter(t => !q || t.table.toLowerCase().includes(q));
    el('#table-count').textContent = `${shown.length} of ${tables.length}`;
    list.innerHTML = shown.map(t => `
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

  const panel = el('#index-export-panel');
  const btn = el('#index-export');
  const stem = `${currentManifest?.db_name || 'project'}_table_index`;
  const nonEmpty = () => tables.filter(t => t.rows > 0);
  const totalRows = () => nonEmpty().reduce((a, t) => a + t.rows, 0);

  function renderPanel() {
    panel.innerHTML = `
      <div class="export-note"><strong>The whole database.</strong> One sheet per table with its
        contents - ${nonEmpty().length} non-empty table(s), ${totalRows().toLocaleString()} rows.
        Tables over 50,000 rows are written truncated and the index sheet says which.</div>
      <div class="export-panel-actions">
        <button type="button" data-act="all">Download all table data (.xlsx)</button>
      </div>
      <div class="export-note" style="margin-top:12px">Or just the list of what is here -
        ${shown.length} table(s) currently shown, with names, categories and row counts.</div>
      <div class="export-panel-actions">
        <button type="button" data-act="csv">Index as CSV</button>
        <button type="button" data-act="xlsx">Index as Excel (.xlsx)</button>
      </div>
      <div class="export-status" id="index-export-status"></div>`;
    panel.querySelector('[data-act="all"]').addEventListener('click', async () => {
      const status = el('#index-export-status');
      status.textContent = `Building workbook (${nonEmpty().length} sheets)... this can take a minute.`;
      try {
        const res = await fetch(apiUrl(`/api/project/${currentProjectId}/export_all`),
                                { headers: sessionHeaders() });
        if (!res.ok) {
          const body = await res.json().catch(() => ({ error: res.statusText }));
          throw new Error(body.error || res.statusText);
        }
        downloadBlob(await res.blob(), `${stem.replace('_table_index', '')}_all_tables.xlsx`);
        status.textContent = '';
      } catch (e) {
        status.textContent = e.message;
      }
    });
    panel.querySelector('[data-act="csv"]').addEventListener('click',
      () => downloadCsv(INDEX_COLUMNS, indexRows(), `${stem}.csv`));
    panel.querySelector('[data-act="xlsx"]').addEventListener('click', async () => {
      const status = el('#index-export-status');
      status.textContent = 'Building workbook...';
      try {
        await downloadXlsx(INDEX_COLUMNS, indexRows(), `${stem}.xlsx`, 'Table Index');
        status.textContent = '';
      } catch (e) {
        status.textContent = e.message;
      }
    });
  }
  renderPanel();
  btn.addEventListener('click', () => {
    renderPanel();  // the filter may have moved since it was last opened
    panel.classList.toggle('hidden');
    const open = !panel.classList.contains('hidden');
    btn.innerHTML = `&#11015; Export list to Excel / CSV ${open ? '&#9652;' : '&#9662;'}`;
  });
}

async function showRawTable(tableName) {
  content().innerHTML = '<div class="loading">Loading table...</div>';
  const data = await api(`/api/project/${currentProjectId}/table/${encodeURIComponent(tableName)}`);
  content().innerHTML = `
    <div class="page-title">${tableName}</div>
    <div class="page-desc"><a href="#" id="back-to-tables">&larr; back to All Tables</a></div>
    <div id="raw-table-content"></div>
  `;
  setCrumbs(tableName);
  el('#back-to-tables').addEventListener('click', (e) => { e.preventDefault(); showAllTables(); });
  if (data.truncated) {
    el('#raw-table-content').innerHTML = truncationNotice(data.row_count, data.rows.length);
  }
  el('#raw-table-content').appendChild(renderDataTable(data.columns, data.rows, tableName));
}

async function showSingleLine() {
  setActiveMenu('single-line');
  setCrumbs('Single Line');
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

// ---------- Module board ----------
// What a project folder contains, grouped the way an engineer thinks about it
// rather than by file extension. Building it is cheap by design - a directory
// listing, no study file opened - so pointing at a folder costs nothing and
// you only pay for the module you actually open.

let currentBoard = null;
// filename -> File. Only used on a hosted instance, where the browser holds
// the sole handle to the file and the server has never seen the folder.
let boardFiles = new Map();

const BOARD_STATE_COPY = {
  no_results: ['No results', 'Run this study in ETAP'],
  unsupported: ['Not supported yet', ''],
  unavailable: ['Desktop app only', 'Reading a project model needs SQL Server'],
  too_large: ['Too large to upload', 'Use the desktop app for this one'],
};

function fmtBytes(n) {
  if (n === null || n === undefined) return '';
  return n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(n / 1e6)) + ' MB';
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

/** Dates on a tile are for telling repeat runs of one study apart, so the
 *  useful precision is the day - and the time too when it was today. */
function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function boardTileHtml(m, isEmpty) {
  const [big, sub] = BOARD_STATE_COPY[m.state] || ['', ''];
  let body;

  if (isEmpty) {
    body = '<div class="tile-big">&mdash;</div>';
  } else if (m.files.length) {
    // ETAP names a result file after the study case, so the stem is what
    // distinguishes one run from another - fourteen unbalanced load flows
    // all carry the same variant label and the same extension. Lead with the
    // name, and keep the date, because picking among repeat runs of one study
    // is usually a question of which was run last.
    const oneVariant = new Set(m.files.map(f => f.variant)).size === 1;
    body = '<div class="tile-files">' + m.files.map(f => {
      const action = f.error ? 'unresolved'
        : f.too_large ? 'too large'
        : f.analyzed ? 'Open'
        : fmtBytes(f.size);
      const blocked = f.too_large || f.error;
      const sub = [
        oneVariant ? '' : f.variant,
        f.subfolder ? esc(f.subfolder) + '/' : '',
        fmtWhen(f.mtime),
      ].filter(Boolean).join(' &middot; ');
      return `<button class="tile-file${f.analyzed ? ' is-analyzed' : ''}"
                data-module="${esc(m.key)}" data-file="${esc(boardFileKey(f))}" ${blocked ? 'disabled' : ''}
                title="${esc(f.rel || f.filename)}">
                <span class="tf-line">
                  <span class="tf-name">${esc(f.name || f.filename)}</span>
                  <span class="tf-meta">${esc(action)}</span>
                </span>
                ${sub ? `<span class="tf-sub">${sub}</span>` : ''}
              </button>`;
    }).join('') + '</div>';
  } else {
    body = `<div class="tile-big">${esc(big)}</div>` + (sub ? `<div class="tile-sub">${esc(sub)}</div>` : '');
  }

  const dim = isEmpty || !m.files.length;
  // Model is the project database rather than one of ETAP's study modules,
  // so it has no tag - and with nothing to put in the footer, drawing one
  // leaves a divider rule with empty space under it.
  const foot = m.files.length && !isEmpty
    ? `${m.files.length} file${m.files.length > 1 ? 's' : ''}`
    : (m.etap_tag ? `ETAP ${esc(m.etap_tag)}` : '');

  return `<div class="tile${dim ? ' dim' : ''}" data-state="${esc(m.state)}">
            <div class="tile-mod">${esc(m.label)}</div>
            ${body}
            ${foot ? `<div class="tile-foot">${foot}</div>` : ''}
          </div>`;
}

/** hasFolder distinguishes "nothing opened yet" from "opened a folder that
 *  holds no ETAP files" - both have zero files but they need opposite copy. */
function showBoard(board, hasFolder = null) {
  currentBoard = board;
  currentProjectId = null;
  el('#menu').classList.add('hidden');
  setActiveMenu(null);
  setCrumbs(null);   // the board is the top of the trail

  const opened = hasFolder === null ? !!board.folder : hasFolder;
  const isEmpty = !opened && board.total_files === 0;
  const sub = isEmpty
    ? 'Open a project folder to see what studies it contains.'
    : board.total_files === 0
      ? 'No ETAP files directly inside that folder - try the folder holding the project itself.'
      : `${board.total_files} loadable file${board.total_files === 1 ? '' : 's'}`
        + (board.folder ? ` &middot; ${esc(board.folder)}` : '');

  content().innerHTML = `
    <div class="board-head">
      <div>
        <div class="page-title">${isEmpty ? 'ETAP Lens' : esc(board.name)}</div>
        <div class="page-desc">${sub}</div>
      </div>
      <button id="board-change-btn" class="board-change">
        ${isEmpty ? 'Open project folder' : 'Open another folder'}</button>
    </div>
    <div class="board">${board.modules.map(m => boardTileHtml(m, isEmpty)).join('')}</div>
    ${isEmpty ? '' : `<div class="board-note">Nothing is read until you open a study.
       Opening one analyzes that file only.</div>`}
  `;

  el('#board-change-btn').addEventListener('click', openProjectFolder);
  content().querySelectorAll('.tile-file').forEach(btn => {
    btn.addEventListener('click', () => openBoardFile(btn.dataset.module, btn.dataset.file, btn));
  });
}

/** What identifies one file on the board. The relative path where we have it,
 *  because a recursive folder pick can hold the same filename twice. */
function boardFileKey(f) {
  return f.rel || f.path || f.filename;
}

/** Open a module's file: activate it if already analyzed, otherwise analyze it. */
async function openBoardFile(moduleKey, key, btn) {
  const mod = currentBoard?.modules.find(m => m.key === moduleKey);
  const f = mod?.files.find(x => boardFileKey(x) === key);
  if (!f) return;

  if (f.project_id) {
    await activateProject(f.project_id);
    return;
  }

  btn.classList.add('is-working');
  btn.querySelector('.tf-meta').textContent = 'Analyzing...';
  if (f.path) {
    // Local: the server can reach the file, so nothing is transferred.
    await loadPathDirectly(f.path);
  } else {
    const file = boardFiles.get(f.rel || f.filename);
    if (!file) {
      btn.querySelector('.tf-meta').textContent = 'not found';
      return;
    }
    await uploadAndLoad(file);
  }
}

async function openBoardForPath(path) {
  el('#load-status').textContent = 'Reading folder...';
  el('#load-status').className = '';
  try {
    const board = await api(`/api/board?path=${encodeURIComponent(path)}`);
    el('#load-status').textContent = '';
    boardFiles.clear();
    showBoard(board);
  } catch (e) {
    el('#load-status').textContent = 'Error: ' + e.message;
    el('#load-status').className = 'error';
  }
}

/** Hosted path: the browser enumerated the folder, so only names and sizes
 *  are sent. No file content leaves the machine until a tile is opened. */
async function openBoardForFileList(fileList) {
  const picked = [...fileList];
  if (!picked.length) return;

  // webkitRelativePath is "<folder>/<...>/<name>"; its first segment is the
  // only name we have for the folder the user picked. Read it before
  // filtering, so a folder with no ETAP files in it still has a name.
  const folderName = (picked[0].webkitRelativePath || '').split('/')[0] || 'Project';

  // Only ETAP files are named to the server. A project folder holds drawings,
  // reports and correspondence too, and their filenames carry client and
  // project identifiers that have no business leaving this machine to answer
  // "which studies are here". The list comes from /api/config, so the server
  // stays the one place that decides what is loadable.
  // Companions are kept alongside the loadable files - a harmonic run's
  // curves live in .fspdb/.hfpdb beside the .HA1S, and if they are dropped
  // here the study uploads without them and the plots are simply gone.
  // They get no tile of their own: the server's board ignores them.
  const allowed = (deployConfig.accepted_extensions || []).map(e => e.toLowerCase());
  const companionExts = (deployConfig.companion_extensions || []).map(e => e.toLowerCase());
  const keep = allowed.concat(companionExts);
  const files = keep.length
    ? picked.filter(f => keep.some(ext => f.name.toLowerCase().endsWith(ext)))
    : picked;

  // Keyed by relative path, not name: the picker is recursive, so two
  // subfolders can each hold a file called SC_Max.SA2S and keying by name
  // would open whichever was stored last.
  boardFiles.clear();
  files.forEach(f => boardFiles.set(f.webkitRelativePath || f.name, f));

  el('#load-status').textContent = 'Reading folder...';
  try {
    const board = await api('/api/board/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: folderName,
        files: files.map(f => ({
          name: f.name, size: f.size, mtime: f.lastModified,
          rel: f.webkitRelativePath || '',
        })),
      }),
    });
    el('#load-status').textContent = '';
    showBoard(board, true);
  } catch (e) {
    el('#load-status').textContent = 'Error: ' + e.message;
    el('#load-status').className = 'error';
  }
}

/** One button, two mechanisms. Locally the server can read a real path, so we
 *  use the in-app browser that yields one. Hosted, only the browser can see
 *  the folder, so we use the directory picker. */
function openProjectFolder() {
  if (deployConfig.local_filesystem === false) {
    el('#dir-input').click();
  } else {
    openFolderBrowser(el('#path-input')?.value.trim());
  }
}

/** First run: the board with every module dimmed, so the tool shows what it
 *  can do before anything is loaded. */
async function showEmptyBoard() {
  try {
    const board = await api('/api/board/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: [] }),
    });
    showBoard(board, false);
  } catch {
    content().innerHTML = WELCOME_HTML;  // no API - keep the static welcome
  }
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
  // Home is the board now. Go back to the folder we were looking at if there
  // is one, so unloading a study leaves you where you opened it from.
  if (currentBoard) showBoard(currentBoard);
  else content().innerHTML = WELCOME_HTML;
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

// #path-candidates goes with #load-btn: absent in hosted mode. Reaching for it
// there throws, and this runs on the success path - between "Loaded." and the
// call that actually renders the project - so a throw here leaves the sidebar
// claiming success with nothing on screen.
function clearCandidateBoxes() {
  ['#browse-candidates', '#path-candidates'].forEach(sel => {
    const node = el(sel);
    if (node) node.innerHTML = '';
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
      clearCandidateBoxes();
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
      // A folder is a project, not a pick-one list - show its board.
      el('#load-status').textContent = '';
      setLoadersDisabled(false);
      await openBoardForPath(data.folder);
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
  tu1s: 'Time-Domain Load Flow (TDLF)',
};

// Out of whatever the user picked in the file dialog (they can multi-select,
// e.g. ctrl-click both the .oti and the .mdf), find the actual loadable
// file(s): prefer .mdf (live data) over .bak (backup) over study results.
function pickDbCandidates(files) {
  const dbFiles = files.filter(f => /\.(mdf|bak|sa1s|sa2s|lf1s|ul1s|tu1s)$/i.test(f.name));
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
async function putToSignedUrl(file, upload) {
  const { url, headers } = upload;
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
}

async function uploadViaSignedUrl(file, companions = []) {
  const status = el('#load-status');

  status.textContent = 'Preparing upload...';
  // One grant, one Turnstile challenge, covering the study and its plot
  // files. Asking again per companion would need a fresh token each time.
  const grant = await api('/api/upload/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      companions: companions.map(c => c.name),
      turnstile_token: currentTurnstileToken(),
    }),
  });

  // Companions before the study: uploading the study is what triggers the
  // import, and the importer looks for them at that moment.
  for (const g of grant.companions || []) {
    const c = companions.find(x => x.name === g.filename);
    if (!c) continue;
    status.textContent = `Uploading ${c.name}...`;
    try {
      await putToSignedUrl(c, g.upload);
      await api('/api/upload/companion/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: g.key, primary: file.name }),
      });
    } catch (e) {
      // The study is still worth having without its plots.
      console.warn(`Companion ${c.name} did not upload: ${e.message}`);
    }
  }

  status.textContent = `Uploading ${file.name} (${(file.size / 1e6).toFixed(0)} MB)...`;
  await putToSignedUrl(file, grant.upload);

  status.textContent = 'Reading file...';
  const { job_id } = await api('/api/upload/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: grant.key }),
  });
  return job_id;
}

async function uploadDirect(file, companions = []) {
  // Companions first - see uploadViaSignedUrl. A failure here is logged and
  // stepped over: losing the plots is better than losing the study.
  for (const c of companions) {
    el('#load-status').textContent = `Uploading ${c.name}...`;
    const cfd = new FormData();
    cfd.append('file', c);
    cfd.append('primary', file.name);
    try {
      const cres = await fetch(apiUrl('/api/upload/companion'), {
        method: 'POST', headers: sessionHeaders(), body: cfd,
      });
      if (!cres.ok) throw new Error((await cres.json().catch(() => ({}))).error || cres.statusText);
    } catch (e) {
      console.warn(`Companion ${c.name} did not upload: ${e.message}`);
    }
  }

  el('#load-status').textContent = `Uploading ${file.name}...`;
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

/** The plot files sitting beside a study in the picked folder, matched the
 *  way ETAP names them: same stem, companion extension, same subfolder. */
function companionsOf(file) {
  const exts = (deployConfig.companion_extensions || []).map(e => e.toLowerCase());
  if (!exts.length) return [];
  const rel = file.webkitRelativePath || file.name;
  const dir = rel.slice(0, rel.length - (file.name.length));
  const stem = file.name.replace(/\.[^.]*$/, '').toLowerCase();

  return [...boardFiles.entries()]
    .filter(([key, f]) => {
      if (f === file) return false;
      // Same folder: a project can hold the same case name under two
      // revision folders, and pairing across them would attach the wrong run.
      if (key.slice(0, key.length - f.name.length) !== dir) return false;
      const name = f.name.toLowerCase();
      return name.replace(/\.[^.]*$/, '') === stem && exts.some(e => name.endsWith(e));
    })
    .map(([, f]) => f);
}

async function uploadAndLoad(file) {
  setLoadersDisabled(true);
  el('#load-status').textContent = `Uploading ${file.name}...`;
  el('#load-status').className = '';
  try {
    const companions = companionsOf(file);
    const jobId = deployConfig.require_session
      ? await uploadViaSignedUrl(file, companions)
      : await uploadDirect(file, companions);
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

el('#fs-modal-close').addEventListener('click', closeFolderBrowser);
el('#fs-select-folder-btn').addEventListener('click', () => {
  if (!fsCurrentPath) return;
  el('#path-input').value = fsCurrentPath;
  closeFolderBrowser();
  openBoardForPath(fsCurrentPath);
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
      // Tokens expire after ~5 minutes. Someone who opens a project folder,
      // reads the board and then picks a study is easily past that, and
      // without these the first thing they'd see is the upload refused for
      // "timeout-or-duplicate" - a verification failure they did not cause
      // and cannot act on. Re-issue quietly instead.
      'expired-callback': () => resetTurnstile(),
      'timeout-callback': () => resetTurnstile(),
      'error-callback': () => resetTurnstile(),
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

  // Chrome's own folder-picker dialog says "Upload N files to this site?",
  // which is its wording for granting access, not a description of what
  // happens. Saying so before the dialog appears is the difference between a
  // reasonable prompt and an alarming one - these are client project folders.
  const hint = el('.browse-hint');
  if (hint) {
    hint.innerHTML = 'Your browser will ask to &ldquo;upload&rdquo; the folder - it only reads the list of '
      + 'file names. Nothing is sent until you open a study, and then only that one file '
      + `(up to ${cfg.max_upload_mb} MB), read in a throwaway copy. Your originals are never modified.`;
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

el('#open-folder-btn').addEventListener('click', openProjectFolder);

// The way out of a study, in the sidebar as well as the breadcrumb. Leaving
// one used to mean unloading it, which is a destructive answer to "show me
// the other modules again".
el('#back-to-board').addEventListener('click', () => {
  if (currentBoard) showBoard(currentBoard);
  else showEmptyBoard();
});

// Picking a directory does not upload it: the browser hands us the file list
// locally and only the file behind an opened tile is ever sent.
el('#dir-input').addEventListener('change', (e) => {
  openBoardForFileList(e.target.files);
  e.target.value = '';
});

// Config first: it decides which controls exist, and the project list is
// scoped by the session header that every call now carries.
applyDeployMode().then(refreshRecentProjects).then(showEmptyBoard);
