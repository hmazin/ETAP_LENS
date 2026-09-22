/* Report generation uses original study files; table exports remain in app.js. */
window.ETAPReports = (() => {
  let dispose = () => {};
  const drafts = new Map();
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
    document.body.classList.add('reports-active');
    root.innerHTML = '<div class="loading" role="status">Loading reports…</div>';
    let timer, observer, uploadObserver, previewUrl, catalog, jobs = [], mode = 'single', busy = false, destroyPdf = () => {};
    let chosen = projectId, templateId = '', selected = new Set(), kinds = new Set(['summary']);
    let includeTimestamp = false, pendingRequest = null, previewRequest = 0, refreshing = false;
    let headers = {}, advanced = false, editing = false, awaiting = [], autoPreview = null;
    const visibleValues = new Map();
    const originalHeaders = new Map(), headerRequests = new Set();
    const active = () => root.isConnected;
    const $ = selector => root.querySelector(selector);
    const cleanup = () => {
      clearInterval(timer);
      observer?.disconnect();
      uploadObserver?.disconnect();
      destroyPdf();
      if (previewUrl?.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
      previewRequest++;
      if (!document.querySelector('#reports-workspace')) document.body.classList.remove('reports-active');
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
      box.textContent = catalog.online ? 'Ready to generate' : 'Service offline';
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
      button.textContent = busy ? 'Starting…' : mode === 'batch' ? `Generate ${pairs.length} PDFs` : editing ? 'Generate updated PDF' : 'Generate PDF';
      $('#report-skipped').innerHTML = (pairs.length > 24 ? '<p>Select at most 24 reports per batch.</p>' : '') +
        (skipped.length ? `<details><summary>${skipped.length} selection${skipped.length === 1 ? '' : 's'} unavailable</summary><ul>${skipped.map(s => `<li>${esc(s)}</li>`).join('')}</ul></details>` : '');
    }

    const draftKey = () => mode === 'batch' ? 'batch' : chosen;
    const originals = () => {
      const study = catalog.studies.find(s => s.project_id === chosen);
      return mode === 'batch' ? undefined : originalHeaders.get(`${chosen}:${study?.sha256}`);
    };

    function saveHeaderDraft() {
      drafts.set(draftKey(), {...headers}); pendingRequest = null;
      const count = Object.keys(headers).length;
      const label = $('#report-header-summary');
      if (label) label.textContent = count ? `${count} field${count === 1 ? '' : 's'} changed` : 'Original values';
      $('#report-header-reset')?.toggleAttribute('disabled', !count);
    }

    function headerFields(ids) {
      return ids.map(id => catalog.header_fields.find(f => f.id === id)).filter(Boolean).map(field => {
        const hidden = headers[field.id] === null;
        const value = Object.hasOwn(headers, field.id) ? headers[field.id] : originals()?.[field.id];
        return `<div class="report-header-field${hidden ? ' is-hidden' : ''}">
          <div class="report-field-label"><label for="report-header-${esc(field.id)}">${esc(field.label)}</label>
            ${field.id === 'sn' ? '' : `<label class="report-hide"><input type="checkbox" data-header-hide="${esc(field.id)}" aria-label="Hide ${esc(field.label)}" ${hidden ? 'checked' : ''}> Hide</label>`}</div>
          <input id="report-header-${esc(field.id)}" type="text" data-header-value="${esc(field.id)}" maxlength="${field.max_length}" value="${esc(value ?? '')}" placeholder="${hidden ? 'Hidden in PDF' : mode === 'batch' ? 'Keep each study’s value' : 'Loading…'}" ${hidden ? 'disabled' : ''}>
        </div>`;
      }).join('');
    }

    async function loadHeaderValues() {
      const study = catalog.studies.find(s => s.project_id === chosen);
      const key = study ? `${study.project_id}:${study.sha256}` : '';
      const show = (failed = false) => {
        if (mode !== 'single') return;
        root.querySelectorAll('[data-header-value]').forEach(input => {
          const id = input.dataset.headerValue;
          if (Object.hasOwn(headers, id)) return;
          input.value = originalHeaders.get(key)?.[id] ?? '';
          input.placeholder = originalHeaders.has(key) ? 'Not set' : failed ? 'Keep original' : 'Loading…';
        });
        const hint = $('#report-header-hint');
        if (hint) hint.textContent = failed ? 'Original values could not be loaded. Untouched fields will still use the study’s values.' :
          originalHeaders.has(key) ? 'Edit the text below, or check Hide to remove a field from the PDF.' : 'Loading details from your study…';
      };
      show();
      if (mode !== 'single' || !study?.ready || originalHeaders.has(key) || headerRequests.has(key)) return;
      headerRequests.add(key);
      try {
        const result = await api(`/api/reports/studies/${study.project_id}/headers`);
        originalHeaders.set(`${study.project_id}:${result.source_sha256}`, result.values);
        if (active() && chosen === study.project_id) show();
      } catch (error) {
        if (active() && chosen === study.project_id) show(true);
      } finally { headerRequests.delete(key); }
    }

    function builder() {
      const study = catalog.studies.find(s => s.project_id === chosen);
      const compatible = catalog.templates.filter(t => t.study_types.includes(study?.study_type));
      if (!compatible.some(t => t.id === templateId)) templateId = compatible[0]?.id || '';
      const uniqueKinds = [...new Map(catalog.templates.map(t => [t.kind, t.name]))];
      $('#report-builder').innerHTML = `
        <div class="report-editor-title"><div><h2>${editing ? 'Edit report details' : 'Create a PDF'}</h2>
          <p>${editing ? 'Your changes will be saved in a new PDF.' : 'Choose a study, check the details, then generate.'}</p></div>
          <div class="report-mode-switch" role="group" aria-label="Number of reports"><button data-build-mode="single" aria-pressed="${mode === 'single'}">One report</button><button data-build-mode="batch" aria-pressed="${mode === 'batch'}">Multiple reports</button></div></div>
        <fieldset id="report-form" ${busy ? 'disabled' : ''}>
          <div class="report-source card">
            ${!catalog.studies.length ? '<div class="report-empty"><h2>Add a study to get started</h2><p>Open an SA1S or SA2S short-circuit result file.</p><button class="report-primary" id="report-open-file">Add study</button></div>'
              : mode === 'single' ? `<div class="report-source-grid"><div><label for="report-study">Study file</label><select id="report-study">${catalog.studies.map(s => `<option value="${esc(s.project_id)}" ${s.project_id === chosen ? 'selected' : ''}>${esc(s.filename)}</option>`).join('')}</select></div>
                <div><label for="report-template">Report type</label><select id="report-template" ${!compatible.length ? 'disabled' : ''}>${compatible.length ? compatible.map(t => `<option value="${esc(t.id)}" ${t.id === templateId ? 'selected' : ''}>${esc(t.name)}</option>`).join('') : '<option>No compatible templates</option>'}</select></div></div>
                ${study?.ready ? '' : `<p class="report-notice">${esc(study?.reason || '')}</p>`}`
              : `<div class="report-source-grid"><div><h3>Study files</h3><div class="report-checks">${catalog.studies.map(s => `<label><input type="checkbox" data-study="${esc(s.project_id)}" ${selected.has(s.project_id) ? 'checked' : ''} ${!s.ready ? 'disabled' : ''}><span>${esc(s.filename)}${s.ready ? '' : `<small>${esc(s.reason)}</small>`}</span></label>`).join('')}</div></div>
                <div><h3>Report types</h3><div class="report-checks">${uniqueKinds.map(([kind, name]) => `<label><input type="checkbox" data-kind="${esc(kind)}" ${kinds.has(kind) ? 'checked' : ''}><span>${esc(name)}</span></label>`).join('')}</div></div></div>`}
          </div>
          ${catalog.header_fields?.length && catalog.studies.length ? `<div class="card report-details">
            <div class="report-details-heading"><h3>Details printed on the report</h3><button type="button" id="report-header-reset" class="report-text-button">Restore originals</button></div>
            <p id="report-header-hint" class="report-notice">${mode === 'batch' ? 'Only your changes apply to every selected study. Leave a field empty to keep each study’s value.' : 'Edit the text below, or check Hide to remove a field from the PDF.'}</p>
            <div class="report-header-fields">${headerFields(['project', 'location', 'contract', 'engineer', 'date', 'revision', 'filename'])}</div>
            <label class="report-sn-option"><input type="checkbox" data-header-hide="sn" ${headers.sn === null ? 'checked' : ''}><span>Hide SN <small>Remove the serial number and its label</small></span></label>
            <details id="report-more-details" ${advanced ? 'open' : ''}><summary>More details <span>Titles, study case, configuration &amp; serial number</span></summary>
              <div class="report-header-fields">${headerFields(['title_1', 'title_2', 'study_case', 'configuration', 'sn'])}</div>
              <label class="report-check"><input type="checkbox" id="report-timestamp" ${includeTimestamp ? 'checked' : ''}> Add a timestamp to the downloaded filename</label>
              <p class="report-meta">Filename above is printed inside the report. Download names are created automatically.</p>
            </details>
          </div>` : ''}
          <div class="report-action-bar"><div><strong id="report-count"></strong><span id="report-header-summary"></span></div><button class="report-primary" id="report-generate">Generate PDF</button></div>
          <div id="report-skipped" class="report-notice"></div>
        </fieldset>`;
      root.querySelectorAll('[data-build-mode]').forEach(b => b.addEventListener('click', () => {
        if (mode === b.dataset.buildMode) return;
        mode = b.dataset.buildMode; headers = {...drafts.get(draftKey())}; visibleValues.clear(); editing = false; pendingRequest = null; builder();
      }));
      $('#report-study')?.addEventListener('change', e => { chosen = e.target.value; headers = {...drafts.get(draftKey())}; visibleValues.clear(); editing = false; pendingRequest = null; builder(); });
      $('#report-template')?.addEventListener('change', e => { templateId = e.target.value; pendingRequest = null; summary(); });
      root.querySelectorAll('[data-study]').forEach(input => input.addEventListener('change', () => { input.checked ? selected.add(input.dataset.study) : selected.delete(input.dataset.study); pendingRequest = null; summary(); }));
      root.querySelectorAll('[data-kind]').forEach(input => input.addEventListener('change', () => { input.checked ? kinds.add(input.dataset.kind) : kinds.delete(input.dataset.kind); pendingRequest = null; summary(); }));
      $('#report-timestamp')?.addEventListener('change', e => { includeTimestamp = e.target.checked; pendingRequest = null; });
      $('#report-open-file')?.addEventListener('click', () => $('#report-file-input').click());
      $('#report-generate').addEventListener('click', generate);
      $('#report-more-details')?.addEventListener('toggle', e => { advanced = e.target.open; });
      root.querySelectorAll('[data-header-hide]').forEach(toggle => toggle.addEventListener('change', () => {
        const id = toggle.dataset.headerHide, input = root.querySelector(`[data-header-value="${id}"]`);
        if (toggle.checked) { visibleValues.set(id, headers[id]); headers[id] = null; }
        else {
          if (visibleValues.has(id) && visibleValues.get(id) !== undefined) headers[id] = visibleValues.get(id);
          else delete headers[id];
        }
        if (input) {
          input.disabled = toggle.checked;
          input.value = toggle.checked ? '' : headers[id] ?? originals()?.[id] ?? '';
          input.placeholder = toggle.checked ? 'Hidden in PDF' : mode === 'batch' ? 'Keep each study’s value' : 'Not set';
          input.closest('.report-header-field').classList.toggle('is-hidden', toggle.checked);
        }
        saveHeaderDraft();
      }));
      root.querySelectorAll('[data-header-value]').forEach(input => input.addEventListener('input', () => {
        const id = input.dataset.headerValue;
        if (mode === 'batch' ? input.value === '' : originals() && input.value === (originals()[id] ?? '')) delete headers[id];
        else headers[id] = input.value;
        saveHeaderDraft();
      }));
      $('#report-header-reset')?.addEventListener('click', () => { headers = {}; visibleValues.clear(); saveHeaderDraft(); builder(); });
      saveHeaderDraft(); loadHeaderValues(); summary();
    }

    async function generate() {
      const {pairs} = choices();
      if (busy || !pairs.length || pairs.length > 24) return;
      busy = true; summary();
      $('#report-form').disabled = true;
      root.querySelectorAll('[data-mode], [data-build-mode]').forEach(b => { b.disabled = true; });
      message('');
      // Reuse this ID after a lost response; a retry must not duplicate a batch.
      pendingRequest ||= {request_id: requestId(), jobs: pairs, timestamp: includeTimestamp, headers: {...headers}};
      try {
        const result = await post('/api/reports/jobs', pendingRequest);
        pendingRequest = null;
        if (!active()) return;
        jobs = [...result.jobs.reverse(), ...jobs.filter(j => !result.jobs.some(n => n.id === j.id))];
        awaiting = result.jobs.map(j => j.id);
        autoPreview = awaiting.length === 1 ? awaiting[0] : null;
        busy = false; mode = 'history'; renderMode(); jumpToTop();
        progress();
      } catch (error) { message(error.message, true); }
      finally {
        busy = false;
        if (active()) {
          $('#report-form').disabled = false;
          root.querySelectorAll('[data-mode], [data-build-mode]').forEach(b => { b.disabled = false; });
          if (mode === 'single' || mode === 'batch') summary();
        }
      }
    }

    async function pdfLink(job, download = false) {
      const grant = await api(`/api/reports/jobs/${job.id}/link${download ? '?download=1' : ''}`);
      if (grant.url) return grant.url;
      const response = await fetch(apiUrl(`/api/reports/jobs/${job.id}/pdf`), {headers: sessionHeaders()});
      if (!response.ok) { const value = await response.json(); throw new Error(value.error || 'PDF download failed.'); }
      return URL.createObjectURL(await response.blob());
    }

    async function preview(job) {
      autoPreview = null;
      clearPreview();
      mode = 'preview'; renderMode(); jumpToTop();
      $('#report-preview-body').innerHTML = '<div class="report-empty" role="status">Loading PDF…</div>';
      $('#report-preview-name').textContent = `${job.filename} · ${job.template_name}`;
      $('#report-preview-download').onclick = () => download(job);
      $('#report-preview-edit').onclick = () => edit(job);
      const generation = ++previewRequest;
      try {
        const url = await pdfLink(job);
        if (!active() || generation !== previewRequest) { if (url.startsWith('blob:')) URL.revokeObjectURL(url); return; }
        if (previewUrl?.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
        previewUrl = url;
        const {mountPdfPreview} = await import('./pdf-preview.mjs');
        if (!active() || generation !== previewRequest) return;
        destroyPdf();
        destroyPdf = mountPdfPreview($('#report-preview-body'), url,
          () => message(''),
          error => message('PDF preview could not be loaded. You can still download it. ' + error.message, true));
        $('#report-preview-name').textContent = `${job.filename} · ${job.template_name}`;
        $('#report-preview-download').hidden = false;
        $('#report-preview-download').onclick = () => download(job);
      } catch (error) { if (active()) message(error.message, true); }
    }

    function clearPreview() {
      previewRequest++;
      destroyPdf(); destroyPdf = () => {};
      if (previewUrl?.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }

    function jumpToTop() {
      root.scrollIntoView({block: 'start'});
    }

    function edit(job) {
      chosen = job.project_id; templateId = job.template_id; includeTimestamp = job.timestamp;
      headers = {...(job.headers || {})}; visibleValues.clear(); editing = true; autoPreview = null;
      mode = 'single'; saveHeaderDraft(); renderMode(); jumpToTop();
    }

    function progress() {
      const group = jobs.filter(j => awaiting.includes(j.id));
      const pending = group.filter(j => ['queued', 'generating'].includes(j.status));
      const failed = group.filter(j => j.status === 'failed');
      const ready = group.filter(j => j.status === 'ready');
      const box = $('#report-progress');
      box.hidden = !group.length;
      box.classList.toggle('error', !!failed.length);
      box.textContent = pending.length ? `Generating ${group.length === 1 ? 'your PDF' : `PDFs · ${ready.length} of ${group.length} ready`}… ${autoPreview ? 'It will open here when ready.' : 'You can continue working.'}` :
        failed.length ? `${failed.length} report${failed.length === 1 ? '' : 's'} failed. See the details below to retry.` : `${ready.length} PDF${ready.length === 1 ? ' is' : 's are'} ready.`;
      const next = jobs.find(j => j.id === autoPreview);
      if (next?.status === 'ready' && mode === 'history') preview(next);
      if (next?.status === 'failed') autoPreview = null;
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
          ${job.status === 'failed' ? `<p class="report-job-message">${esc(job.message)}</p>` : ''}
          <div class="report-job-actions">${job.status === 'ready' ? `<button class="report-secondary" data-preview="${job.id}">Open PDF</button><button class="report-secondary" data-download="${job.id}">Download PDF</button><button class="report-secondary" data-edit-headers="${job.id}">Edit details</button>` : job.status === 'failed' ? `<button class="report-secondary" data-retry="${job.id}">Retry report</button>` : ''}</div>
          ${Object.keys(job.headers || {}).length ? `<details class="report-audit"><summary>Header changes (${Object.keys(job.headers).length})</summary><dl>${Object.entries(job.headers).map(([key, value]) => `<dt>${esc(catalog.header_fields?.find(f => f.id === key)?.label || key)}</dt><dd>${value === null ? 'Hidden (label and value)' : esc(value || '(blank)')}</dd>`).join('')}</dl></details>` : ''}
          <details class="report-audit"><summary>File information</summary><dl><dt>Report ID</dt><dd>${esc(job.id)}</dd><dt>Source SHA-256</dt><dd>${esc(job.source_sha256)}</dd><dt>Template SHA-256</dt><dd>${esc(job.template_sha256)}</dd>${job.pdf_sha256 ? `<dt>PDF SHA-256</dt><dd>${esc(job.pdf_sha256)}</dd>` : ''}</dl></details>
        </article>`).join('') : '<div class="report-empty"><h2>No reports yet</h2><p>Select a study and template to generate your first PDF.</p></div>';
      root.querySelectorAll('[data-preview]').forEach(b => b.addEventListener('click', () => preview(jobs.find(j => j.id === b.dataset.preview))));
      root.querySelectorAll('[data-download]').forEach(b => b.addEventListener('click', () => download(jobs.find(j => j.id === b.dataset.download))));
      root.querySelectorAll('[data-edit-headers]').forEach(b => b.addEventListener('click', () => edit(jobs.find(j => j.id === b.dataset.editHeaders))));
      root.querySelectorAll('[data-retry]').forEach(b => b.addEventListener('click', async () => {
        const job = jobs.find(j => j.id === b.dataset.retry);
        b.disabled = true;
        try {
          const result = await post('/api/reports/jobs', {request_id: b.dataset.requestId ||= requestId(), timestamp: job.timestamp, headers: job.headers || {},
            jobs: [{project_id: job.project_id, template_id: job.template_id, source_sha256: job.source_sha256}]});
          if (!active()) return;
          jobs.unshift(...result.jobs); awaiting = result.jobs.map(j => j.id); autoPreview = awaiting[0]; history(); progress();
        } catch (error) { message(error.message, true); b.disabled = false; }
      }));
      const ready = jobs.filter(j => j.status === 'ready');
      $('#report-zip').disabled = !ready.length;
      $('#report-zip').textContent = `Download latest ${Math.min(ready.length, 24)} PDFs as ZIP`;
      $('#report-history-count').textContent = jobs.length ? ` (${jobs.length})` : '';
    }

    function renderMode() {
      const creating = mode === 'single' || mode === 'batch';
      root.querySelectorAll('[data-mode]').forEach(b => {
        const on = b.dataset.mode === (creating ? 'single' : 'history');
        b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1;
      });
      $('#report-builder').hidden = !creating;
      $('#report-history').hidden = mode !== 'history';
      $('#report-preview').hidden = mode !== 'preview';
      if (mode !== 'preview') clearPreview();
      message('');
      if (creating) builder(); else if (mode === 'history') { history(); progress(); }
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
        if (changed) { history(); progress(); }
        const next = await api('/api/reports/catalog');
        if (!active()) return;
        catalog = next; status(); if (mode === 'single' || mode === 'batch') summary();
      } catch (error) { if (active()) message('Could not refresh reports. ' + error.message, true); }
      finally { refreshing = false; }
    }

    try {
      [catalog, {jobs}] = await Promise.all([api('/api/reports/catalog'), api('/api/reports/jobs')]);
      if (!active()) return;
      chosen = catalog.studies.some(s => s.project_id === chosen) ? chosen : catalog.studies[0]?.project_id;
      headers = {...drafts.get(chosen)};
      selected = new Set(catalog.studies.filter(s => s.ready).map(s => s.project_id));
      root.innerHTML = `
        <input type="file" id="report-file-input" accept=".sa1s,.sa2s,.ul1s" hidden>
        <div class="page-title-row"><div><h1 class="page-title">Reports</h1><p class="page-desc">Create, customize and download ETAP reports.</p></div><div class="report-page-actions"><span id="report-service-status" role="status"></span><button id="report-add-study" class="report-secondary">+ Add study</button></div></div>
        <p id="report-offline" class="report-notice"></p>
        <div class="report-tabs" role="tablist" aria-label="Reports">
          <button id="report-tab-single" role="tab" aria-controls="report-builder" data-mode="single">Create PDF</button>
          <button id="report-tab-history" role="tab" aria-controls="report-history report-preview" data-mode="history">My PDFs<span id="report-history-count"></span></button>
        </div>
        <div id="report-feedback" role="status" aria-live="polite"></div>
        <section id="report-builder" role="tabpanel" aria-labelledby="report-tab-single"></section>
        <section id="report-history" role="tabpanel" aria-labelledby="report-tab-history" hidden>
          <div class="report-history-head"><div><h2>My PDFs</h2><p class="report-meta">Available for 7 days in this browser.</p></div><button class="report-secondary" id="report-zip">Download all as ZIP</button></div>
          <div id="report-progress" class="report-progress" role="status" aria-live="polite" hidden></div><div id="report-history-list"></div>
        </section>
        <section id="report-preview" role="tabpanel" aria-labelledby="report-tab-history" hidden>
          <button id="report-preview-back" class="report-text-button">← Back to my PDFs</button>
          <div class="report-preview-head"><div><h2>Your PDF is ready</h2><p id="report-preview-name" class="report-meta"></p></div><div class="report-job-actions"><button class="report-secondary" id="report-preview-edit">Edit details</button><button class="report-primary" id="report-preview-download">Download PDF</button></div></div>
          <div class="report-preview" id="report-preview-body"></div>
        </section>`;
      $('#report-add-study').addEventListener('click', () => $('#report-file-input').click());
      $('#report-file-input').addEventListener('change', async event => {
        const file = event.target.files[0];
        if (!file) return;
        $('#report-add-study').disabled = true;
        event.target.disabled = true;
        const loadStatus = el('#load-status');
        uploadObserver = new MutationObserver(() => { if (active()) message(loadStatus.textContent, loadStatus.classList.contains('error')); });
        uploadObserver.observe(loadStatus, {childList: true, characterData: true, subtree: true, attributes: true});
        await uploadAndLoad(file, async id => { if (active()) await open(id); });
        uploadObserver.disconnect();
        if (active()) { $('#report-add-study').disabled = false; event.target.disabled = false; event.target.value = ''; }
      });
      $('#report-preview-back').addEventListener('click', () => { mode = 'history'; renderMode(); jumpToTop(); });
      root.querySelectorAll('[data-mode]').forEach(b => {
        b.addEventListener('click', () => {
          autoPreview = null;
          if (b.dataset.mode === 'single' && mode !== 'single' && mode !== 'batch') { mode = 'single'; headers = {...drafts.get(chosen)}; visibleValues.clear(); }
          else if (b.dataset.mode === 'history') mode = 'history';
          pendingRequest = null; renderMode();
        });
        b.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const tabs = [...root.querySelectorAll('[data-mode]')];
          const i = tabs.indexOf(b), target = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (i + 1) % tabs.length;
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
