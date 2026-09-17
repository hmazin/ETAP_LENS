/* Report generation uses original study files; table exports remain in app.js. */
window.ETAPReports = (() => {
  let dispose = () => {};
  let headerDraft = {}, headerExpanded = false;
  const post = (path, body) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const requestId = () => crypto.randomUUID().replaceAll('-', '');
  const date = value => new Date(value * 1000).toLocaleString();

  async function open(projectId = null) {
    dispose();
    setActiveMenu('reports');
    setCrumbs('Reports');
    const root = document.createElement('section');
    root.id = 'reports-workspace';
    content().replaceChildren(root);
    root.innerHTML = '<div class="loading" role="status">Loading reports…</div>';
    let timer, observer, previewUrl, catalog, jobs = [], mode = 'single', busy = false, destroyPdf = () => {};
    let chosen = projectId, templateId = '', selected = new Set(), kinds = new Set(['summary']);
    let includeTimestamp = false, pendingRequest = null, previewRequest = 0, refreshing = false;
    let headers = {...headerDraft};
    const originalHeaders = new Map(), headerRequests = new Set();
    const active = () => root.isConnected;
    const $ = selector => root.querySelector(selector);
    const cleanup = () => {
      clearInterval(timer);
      observer?.disconnect();
      destroyPdf();
      if (previewUrl?.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
      previewRequest++;
    };
    dispose = cleanup;
    observer = new MutationObserver(() => { if (!active()) cleanup(); });
    observer.observe(content(), {childList: true});

    function message(text, error = false) {
      const box = $('#report-feedback');
      if (!box) return;
      box.textContent = text;
      box.classList.toggle('error', error);
      box.setAttribute('role', error ? 'alert' : 'status');
    }

    function status() {
      const box = $('#report-service-status');
      if (!box) return;
      box.textContent = catalog.online ? 'Reporting service online' : 'Reporting service offline';
      box.classList.toggle('offline', !catalog.online);
      $('#report-offline').hidden = catalog.online;
      $('#report-offline').textContent = catalog.message || 'The reporting service is offline. You can still browse studies and download completed reports.';
    }

    function choices() {
      const targets = mode === 'batch' ? catalog.studies.filter(s => selected.has(s.project_id))
        : catalog.studies.filter(s => s.project_id === chosen);
      const pairs = [], skipped = [];
      for (const study of targets) {
        if (!study.ready) { skipped.push(`${study.filename}: ${study.reason}`); continue; }
        const templates = mode === 'batch'
          ? [...kinds].map(kind => catalog.templates.find(t => t.kind === kind && t.study_types.includes(study.study_type)))
          : [catalog.templates.find(t => t.id === templateId && t.study_types.includes(study.study_type))];
        for (const [index, template] of templates.entries()) {
          if (!template) {
            const kind = [...kinds][index];
            const name = catalog.templates.find(t => t.kind === kind)?.name || 'Selected report';
            skipped.push(`${study.filename}: ${name} is incompatible.`);
          } else if (!template.available) skipped.push(`${study.filename}: ${template.name} is currently unavailable.`);
          else pairs.push({project_id: study.project_id, template_id: template.id, source_sha256: study.sha256});
        }
      }
      return {pairs, skipped};
    }

    function summary() {
      const {pairs, skipped} = choices();
      const count = $('#report-count');
      if (!count) return;
      count.textContent = `${pairs.length} report${pairs.length === 1 ? '' : 's'} selected`;
      const button = $('#report-generate');
      button.disabled = busy || !catalog.online || !pairs.length || pairs.length > 24;
      button.textContent = busy ? 'Adding reports…' : mode === 'batch' ? `Generate ${pairs.length} reports` : 'Generate PDF';
      $('#report-skipped').innerHTML = (pairs.length > 24 ? '<p>Select at most 24 reports per batch.</p>' : '') +
        (skipped.length ? `<details><summary>${skipped.length} selection${skipped.length === 1 ? '' : 's'} unavailable</summary><ul>${skipped.map(s => `<li>${esc(s)}</li>`).join('')}</ul></details>` : '');
    }

    function saveHeaderDraft() {
      headerDraft = {...headers}; pendingRequest = null;
      const count = Object.keys(headers).length;
      const label = $('#report-header-summary');
      if (label) label.textContent = `Customize header fields${count ? ` · ${count} changed` : ''}`;
    }

    function headerFields() {
      return (catalog.header_fields || []).map(field => {
        const changed = Object.hasOwn(headers, field.id), value = headers[field.id];
        const setting = !changed ? 'original' : value === null ? 'hide' : 'custom';
        return `<div class="report-header-field" data-header-field="${esc(field.id)}">
          <label for="report-header-${esc(field.id)}-mode">${esc(field.label)}</label>
          <select id="report-header-${esc(field.id)}-mode" data-header-mode="${esc(field.id)}" aria-label="${esc(field.label)} setting">
            <option value="original" ${setting === 'original' ? 'selected' : ''}>Keep original</option>
            <option value="custom" ${setting === 'custom' ? 'selected' : ''}>Custom text</option>
            <option value="hide" ${setting === 'hide' ? 'selected' : ''}>Hide label &amp; value</option>
          </select>
          <input type="text" data-header-value="${esc(field.id)}" aria-label="${esc(field.label)} custom text" maxlength="${field.max_length}" value="${esc(value ?? '')}" placeholder="Enter ${esc(field.label.toLowerCase())}" ${setting !== 'custom' ? 'hidden disabled' : ''}>
          <p class="report-header-original" data-header-original="${esc(field.id)}"></p>
        </div>`;
      }).join('');
    }

    async function loadHeaderValues() {
      const study = catalog.studies.find(s => s.project_id === chosen);
      const key = study ? `${study.project_id}:${study.sha256}` : '';
      const show = () => root.querySelectorAll('[data-header-original]').forEach(item => {
        const id = item.dataset.headerOriginal;
        item.textContent = mode === 'batch' ? 'Original: from each selected study' :
          originalHeaders.has(key) ? `Original: ${originalHeaders.get(key)[id] || '(blank)'}` :
          study?.ready ? 'Loading original value…' : 'Load a study to see its original value.';
      });
      show();
      if (mode === 'batch' || !headerExpanded || !study?.ready || originalHeaders.has(key) || headerRequests.has(key)) return;
      headerRequests.add(key);
      try {
        const result = await api(`/api/reports/studies/${study.project_id}/headers`);
        originalHeaders.set(`${study.project_id}:${result.source_sha256}`, result.values);
        if (active() && chosen === study.project_id && mode !== 'history') show();
      } catch (error) {
        if (active() && chosen === study.project_id) root.querySelectorAll('[data-header-original]').forEach(item => { item.textContent = 'Original value unavailable. Keep original still preserves it.'; });
      } finally { headerRequests.delete(key); }
    }

    function builder() {
      const study = catalog.studies.find(s => s.project_id === chosen);
      const compatible = catalog.templates.filter(t => t.study_types.includes(study?.study_type));
      if (!compatible.some(t => t.id === templateId)) templateId = compatible[0]?.id || '';
      const selectedTemplate = compatible.find(t => t.id === templateId);
      const uniqueKinds = [...new Map(catalog.templates.map(t => [t.kind, t.name]))];
      $('#report-builder').innerHTML = `
        <div class="report-settings">
          <div class="card report-step"><h2><span>1</span> ${mode === 'batch' ? 'Study results' : 'Study result'}</h2>
            ${!catalog.studies.length ? '<p>No short-circuit studies are loaded.</p><button class="report-secondary" id="report-open-file">Open study file</button>'
              : mode === 'single' ? `<label for="report-study">Selected file</label><select id="report-study">${catalog.studies.map(s => `<option value="${esc(s.project_id)}" ${s.project_id === chosen ? 'selected' : ''}>${esc(s.filename)}</option>`).join('')}</select>
                <p class="report-study-name">${esc(study?.study_name || '')}</p>
                ${study?.ready ? `<div class="report-meta">Valid SQLite · ${study.table_count} tables · Study type ${study.study_type}</div>` : `<p class="report-notice">${esc(study?.reason || '')}</p>`}`
              : `<div class="report-checks">${catalog.studies.map(s => `<label><input type="checkbox" data-study="${esc(s.project_id)}" ${selected.has(s.project_id) ? 'checked' : ''} ${!s.ready ? 'disabled' : ''}><span>${esc(s.filename)}<small>${esc(s.ready ? s.study_name : s.reason)}</small></span></label>`).join('')}</div>`}
          </div>
          <div class="card report-step"><h2><span>2</span> Report template</h2>
            ${mode === 'single' ? `<label for="report-template">Compatible templates</label><select id="report-template" ${!compatible.length ? 'disabled' : ''}>${compatible.length ? compatible.map(t => `<option value="${esc(t.id)}" ${t.id === templateId ? 'selected' : ''}>${esc(t.name)}</option>`).join('') : '<option>No compatible templates</option>'}</select>
              <div class="report-meta">${esc(selectedTemplate?.family || 'Select a supported study')} ${selectedTemplate ? '· ETAP 24' : ''}</div>`
              : `<div class="report-checks">${uniqueKinds.map(([kind, name]) => `<label><input type="checkbox" data-kind="${esc(kind)}" ${kinds.has(kind) ? 'checked' : ''}><span>${esc(name)}</span></label>`).join('')}</div><p class="report-meta">The matching template family is selected for each study.</p>`}
          </div>
          ${catalog.header_fields?.length ? `<div class="card report-step"><h2><span>3</span> Report header</h2>
            <p class="report-notice">Keep original values, replace text, or hide a label and value.${mode === 'batch' ? ' These settings apply to every report in this batch.' : ''}</p>
            <details id="report-header-options" ${headerExpanded ? 'open' : ''}><summary id="report-header-summary">Customize header fields</summary>
              <div class="report-header-fields">${headerFields()}</div>
              <p class="report-meta">Keep text short to fit the template. Filename changes the printed header; the download name is generated separately.</p>
              <button type="button" id="report-header-reset" class="report-secondary">Reset all to original</button>
            </details>
          </div>` : ''}
          <div class="card report-step"><h2><span>${catalog.header_fields?.length ? '4' : '3'}</span> Generate &amp; download</h2>
            <p class="report-meta">PDF · original ETAP template formatting</p>
            <label class="report-check"><input type="checkbox" id="report-timestamp" ${includeTimestamp ? 'checked' : ''}> Add UTC timestamp to filename</label>
            <div id="report-count" class="report-meta" aria-live="polite"></div>
            <button class="report-primary" id="report-generate">Generate PDF</button>
            <div id="report-skipped" class="report-notice"></div>
          </div>
        </div>`;
      $('#report-study')?.addEventListener('change', e => { chosen = e.target.value; pendingRequest = null; builder(); });
      $('#report-template')?.addEventListener('change', e => { templateId = e.target.value; pendingRequest = null; builder(); });
      root.querySelectorAll('[data-study]').forEach(input => input.addEventListener('change', () => { input.checked ? selected.add(input.dataset.study) : selected.delete(input.dataset.study); pendingRequest = null; summary(); }));
      root.querySelectorAll('[data-kind]').forEach(input => input.addEventListener('change', () => { input.checked ? kinds.add(input.dataset.kind) : kinds.delete(input.dataset.kind); pendingRequest = null; summary(); }));
      $('#report-timestamp').addEventListener('change', e => { includeTimestamp = e.target.checked; pendingRequest = null; });
      $('#report-open-file')?.addEventListener('click', () => el('#folder-input').click());
      $('#report-generate').addEventListener('click', generate);
      $('#report-header-options')?.addEventListener('toggle', event => { headerExpanded = event.target.open; if (headerExpanded) loadHeaderValues(); });
      root.querySelectorAll('[data-header-mode]').forEach(select => select.addEventListener('change', () => {
        const id = select.dataset.headerMode;
        const input = root.querySelector(`[data-header-value="${id}"]`);
        if (select.value === 'original') delete headers[id];
        else if (select.value === 'hide') headers[id] = null;
        else {
          const study = catalog.studies.find(s => s.project_id === chosen);
          const original = originalHeaders.get(`${chosen}:${study?.sha256}`)?.[id] || '';
          input.value = input.value || original;
          headers[id] = input.value;
        }
        input.hidden = input.disabled = select.value !== 'custom';
        saveHeaderDraft();
      }));
      root.querySelectorAll('[data-header-value]').forEach(input => input.addEventListener('input', () => { headers[input.dataset.headerValue] = input.value; saveHeaderDraft(); }));
      $('#report-header-reset')?.addEventListener('click', () => { headers = {}; saveHeaderDraft(); builder(); });
      saveHeaderDraft(); loadHeaderValues();
      summary();
    }

    async function generate() {
      const {pairs} = choices();
      if (busy || !pairs.length || pairs.length > 24) return;
      busy = true; summary();
      // Reuse this ID after a lost response; a retry must not duplicate a batch.
      pendingRequest ||= {request_id: requestId(), jobs: pairs, timestamp: includeTimestamp, headers: {...headers}};
      try {
        const result = await post('/api/reports/jobs', pendingRequest);
        pendingRequest = null;
        if (!active()) return;
        jobs = [...result.jobs.reverse(), ...jobs.filter(j => !result.jobs.some(n => n.id === j.id))];
        mode = 'history'; renderMode();
        message(`${result.jobs.length} report${result.jobs.length === 1 ? '' : 's'} queued. You can continue browsing while they generate.`);
      } catch (error) { message(error.message, true); }
      finally { busy = false; if (active() && mode !== 'history') summary(); }
    }

    async function pdfLink(job, download = false) {
      const grant = await api(`/api/reports/jobs/${job.id}/link${download ? '?download=1' : ''}`);
      if (grant.url) return grant.url;
      const response = await fetch(apiUrl(`/api/reports/jobs/${job.id}/pdf`), {headers: sessionHeaders()});
      if (!response.ok) { const value = await response.json(); throw new Error(value.error || 'PDF download failed.'); }
      return URL.createObjectURL(await response.blob());
    }

    async function preview(job) {
      const generation = ++previewRequest;
      message('Loading PDF preview…');
      try {
        const url = await pdfLink(job);
        if (!active() || generation !== previewRequest) { if (url.startsWith('blob:')) URL.revokeObjectURL(url); return; }
        if (previewUrl?.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
        previewUrl = url;
        const {mountPdfPreview} = await import('./pdf-preview.mjs');
        if (!active() || generation !== previewRequest) return;
        destroyPdf();
        destroyPdf = mountPdfPreview($('#report-preview-body'), url,
          () => message('The preview shows the generated PDF. Download opens the same report.'),
          error => message('PDF preview could not be loaded. You can still download it. ' + error.message, true));
        $('#report-preview-name').textContent = `${job.filename} · ${job.template_name}`;
        $('#report-preview-download').hidden = false;
        $('#report-preview-download').onclick = () => download(job);
      } catch (error) { if (active()) message(error.message, true); }
    }

    async function download(job) {
      try {
        const url = await pdfLink(job, true);
        const a = document.createElement('a');
        a.href = url; a.download = job.output_name; a.rel = 'noreferrer';
        if (!url.startsWith('blob:')) a.target = '_blank';
        document.body.appendChild(a); a.click(); a.remove();
        if (url.startsWith('blob:')) setTimeout(() => URL.revokeObjectURL(url), 60000);
      } catch (error) { message(error.message, true); }
    }

    function history() {
      $('#report-history-list').innerHTML = jobs.length ? jobs.map(job => `
        <article class="report-job"><div class="report-job-heading"><strong>${esc(job.template_name)}</strong><span class="report-state report-state-${esc(job.status)}">${esc(job.status === 'ready' ? 'Ready' : job.status === 'generating' ? 'Generating' : job.status === 'queued' ? 'Queued' : 'Failed')}</span></div>
          <div class="report-file">${esc(job.filename)}</div><div class="report-meta">${esc(job.study_name)} · ${esc(date(job.created_at))}</div>
          <p class="report-job-message">${esc(job.message)}</p>
          <div class="report-job-actions">${job.status === 'ready' ? `<button class="report-secondary" data-preview="${job.id}">Preview</button><button class="report-secondary" data-download="${job.id}">Download PDF</button><button class="report-secondary" data-edit-headers="${job.id}">Edit headers</button>` : job.status === 'failed' ? `<button class="report-secondary" data-retry="${job.id}">Retry report</button>` : ''}</div>
          ${Object.keys(job.headers || {}).length ? `<details class="report-audit"><summary>Header changes (${Object.keys(job.headers).length})</summary><dl>${Object.entries(job.headers).map(([key, value]) => `<dt>${esc(catalog.header_fields?.find(f => f.id === key)?.label || key)}</dt><dd>${value === null ? 'Hidden (label and value)' : esc(value || '(blank)')}</dd>`).join('')}</dl></details>` : ''}
          <details class="report-audit"><summary>Report details</summary><dl><dt>Report ID</dt><dd>${esc(job.id)}</dd><dt>Source SHA-256</dt><dd>${esc(job.source_sha256)}</dd><dt>Template SHA-256</dt><dd>${esc(job.template_sha256)}</dd>${job.pdf_sha256 ? `<dt>PDF SHA-256</dt><dd>${esc(job.pdf_sha256)}</dd>` : ''}</dl></details>
        </article>`).join('') : '<div class="report-empty"><h2>No reports yet</h2><p>Select a study and template to generate your first PDF.</p></div>';
      root.querySelectorAll('[data-preview]').forEach(b => b.addEventListener('click', () => preview(jobs.find(j => j.id === b.dataset.preview))));
      root.querySelectorAll('[data-download]').forEach(b => b.addEventListener('click', () => download(jobs.find(j => j.id === b.dataset.download))));
      root.querySelectorAll('[data-edit-headers]').forEach(b => b.addEventListener('click', () => {
        const job = jobs.find(j => j.id === b.dataset.editHeaders);
        chosen = job.project_id; templateId = job.template_id; includeTimestamp = job.timestamp;
        headers = {...(job.headers || {})}; headerExpanded = true; saveHeaderDraft();
        mode = 'single'; renderMode(); message('Edit the header settings, then generate a new PDF.');
      }));
      root.querySelectorAll('[data-retry]').forEach(b => b.addEventListener('click', async () => {
        const job = jobs.find(j => j.id === b.dataset.retry);
        b.disabled = true;
        try {
          const result = await post('/api/reports/jobs', {request_id: b.dataset.requestId ||= requestId(), timestamp: job.timestamp, headers: job.headers || {},
            jobs: [{project_id: job.project_id, template_id: job.template_id, source_sha256: job.source_sha256}]});
          if (!active()) return;
          jobs.unshift(...result.jobs); history(); message('Report queued again.');
        } catch (error) { message(error.message, true); b.disabled = false; }
      }));
      const ready = jobs.filter(j => j.status === 'ready');
      $('#report-zip').disabled = !ready.length;
      $('#report-zip').textContent = `Download latest ${Math.min(ready.length, 24)} PDFs as ZIP`;
      $('#report-history-count').textContent = jobs.length ? ` (${jobs.length})` : '';
    }

    function renderMode() {
      root.querySelectorAll('[data-mode]').forEach(b => {
        const on = b.dataset.mode === mode;
        b.setAttribute('aria-selected', String(on));
      });
      $('#report-builder').hidden = mode === 'history';
      $('#report-history').hidden = mode !== 'history';
      $('#report-builder').setAttribute('aria-labelledby', mode === 'batch' ? 'report-tab-batch' : 'report-tab-single');
      if (mode === 'history') history(); else builder();
    }

    async function refresh() {
      if (!active()) { cleanup(); return; }
      if (refreshing) return;
      refreshing = true;
      try {
        const data = await api('/api/reports/jobs');
        if (!active()) return;
        const changed = JSON.stringify(data.jobs) !== JSON.stringify(jobs);
        jobs = data.jobs;
        if (changed) history();
        const next = await api('/api/reports/catalog');
        if (!active()) return;
        catalog = next; status(); if (mode !== 'history') summary();
      } catch (error) { if (active()) message('Could not refresh reports. ' + error.message, true); }
      finally { refreshing = false; }
    }

    try {
      [catalog, {jobs}] = await Promise.all([api('/api/reports/catalog'), api('/api/reports/jobs')]);
      if (!active()) return;
      chosen = catalog.studies.some(s => s.project_id === chosen) ? chosen : catalog.studies[0]?.project_id;
      selected = new Set(catalog.studies.filter(s => s.ready).map(s => s.project_id));
      root.innerHTML = `
        <div class="page-title-row"><div><h1 class="page-title">Reports</h1><p class="page-desc">Generate PDFs from your ETAP short-circuit study results.</p></div><span id="report-service-status" role="status"></span></div>
        <p id="report-offline" class="report-notice"></p>
        <div class="report-tabs" role="tablist" aria-label="Report workflow">
          <button id="report-tab-single" role="tab" aria-controls="report-builder" data-mode="single">Single report</button>
          <button id="report-tab-batch" role="tab" aria-controls="report-builder" data-mode="batch">Batch reports</button>
          <button id="report-tab-history" role="tab" aria-controls="report-history" data-mode="history">Report history<span id="report-history-count"></span></button>
        </div>
        <div id="report-feedback" role="status" aria-live="polite"></div>
        <div class="report-layout"><div class="report-left">
          <section id="report-builder" role="tabpanel"></section>
          <section id="report-history" role="tabpanel" aria-labelledby="report-tab-history" hidden><div class="report-history-head"><h2>Report jobs</h2><button class="report-secondary" id="report-zip">Download completed PDFs as ZIP</button></div><p class="report-meta">Reports are kept for seven days in this browser's session. Clearing site data loses access.</p><div id="report-history-list"></div></section>
        </div><aside class="report-preview" aria-label="Generated PDF preview"><div class="report-preview-head"><h2>Report preview</h2><button class="report-secondary" id="report-preview-download" hidden>Download PDF</button></div><p id="report-preview-name" class="report-meta"></p><div id="report-preview-body"><div class="report-empty"><h2>Your generated PDF will appear here</h2><p>Choose Preview on a completed report to view its original ETAP formatting.</p></div></div></aside></div>`;
      root.querySelectorAll('[data-mode]').forEach(b => {
        b.addEventListener('click', () => { mode = b.dataset.mode; pendingRequest = null; renderMode(); });
        b.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const tabs = [...root.querySelectorAll('[data-mode]')];
          const i = tabs.indexOf(b), target = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (i + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
          tabs[target].focus(); tabs[target].click();
        });
      });
      $('#report-zip').addEventListener('click', async () => {
        const button = $('#report-zip'); button.disabled = true; message('Preparing ZIP download…');
        try {
          const response = await fetch(apiUrl('/api/reports/download-zip'), {method: 'POST', headers: {...sessionHeaders(), 'Content-Type': 'application/json'}, body: JSON.stringify({ids: jobs.filter(j => j.status === 'ready').slice(0, 24).map(j => j.id)})});
          if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'ZIP download failed.'); }
          downloadBlob(await response.blob(), 'ETAP_Reports.zip'); message('ZIP downloaded.');
        } catch (error) { message(error.message, true); }
        finally { if (active()) button.disabled = false; }
      });
      status(); renderMode(); history();
      timer = setInterval(refresh, 8000);
    } catch (error) {
      if (active()) root.innerHTML = `<h1 class="page-title">Reports</h1><div class="card"><h2>Reporting is not available yet</h2><p>The report service has not been connected to this website, or could not be reached.</p><p class="report-meta">${esc(error.message)}</p><button id="report-reconnect" class="report-secondary">Try again</button></div>`;
      $('#report-reconnect')?.addEventListener('click', () => open(projectId));
    }
  }
  return {open};
})();
