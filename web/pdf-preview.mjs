import {getDocument, GlobalWorkerOptions} from './vendor/pdfjs/pdf.min.mjs';

GlobalWorkerOptions.workerSrc = new URL('./vendor/pdfjs/pdf.worker.min.mjs', import.meta.url).href;

// Render the actual PDF bytes in every browser, including those with no native
// PDF plugin. No document scripts or template calculations execute here.
export function mountPdfPreview(host, url, onReady, onError) {
  host.innerHTML = `<div class="pdf-controls" aria-label="PDF controls">
    <button type="button" class="report-secondary" data-pdf-prev aria-label="Previous PDF page">←</button>
    <label>Page <input type="number" data-pdf-page value="1" min="1" aria-label="PDF page number"></label>
    <span data-pdf-total></span>
    <button type="button" class="report-secondary" data-pdf-next aria-label="Next PDF page">→</button>
    <label>Zoom <select data-pdf-zoom><option value="fit">Fit width</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label>
  </div><form class="pdf-search"><label>Find text <input type="search" data-pdf-query maxlength="200"></label><button class="report-secondary" type="submit">Find next</button></form>
  <p class="report-meta pdf-status" data-pdf-status role="status" aria-live="polite">Loading PDF…</p>
  <div class="pdf-page-scroll"><canvas role="img" aria-label="Generated ETAP report page"></canvas></div>
  <details class="pdf-text"><summary>Page text</summary><pre></pre></details>`;
  const $ = selector => host.querySelector(selector);
  let document, pageNumber = 1, renderTask, destroyed = false, sequence = 0, searching = false;
  let previousQuery = '', previousMatch = 0;
  const loading = getDocument({url, isEvalSupported: false,
    standardFontDataUrl: new URL('./vendor/pdfjs/standard_fonts/', import.meta.url).href,
    wasmUrl: new URL('./vendor/pdfjs/wasm/', import.meta.url).href});
  const setStatus = text => { if (!destroyed) $('[data-pdf-status]').textContent = text; };

  async function render() {
    if (!document || destroyed) return;
    const current = ++sequence;
    if (renderTask) {
      renderTask.cancel();
      await renderTask.promise.catch(() => {});
      renderTask = null;
    }
    const page = await document.getPage(pageNumber);
    if (destroyed || current !== sequence) return;
    const natural = page.getViewport({scale: 1});
    const zoom = $('[data-pdf-zoom]').value;
    const scale = zoom === 'fit' ? Math.max(.2, ($('.pdf-page-scroll').clientWidth - 24) / natural.width) : Number(zoom);
    const viewport = page.getViewport({scale});
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = $('canvas');
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    canvas.setAttribute('aria-label', `Generated ETAP report, page ${pageNumber} of ${document.numPages}. Full text is available below.`);
    $('[data-pdf-page]').value = pageNumber;
    $('[data-pdf-page]').max = document.numPages;
    $('[data-pdf-total]').textContent = `of ${document.numPages}`;
    $('[data-pdf-prev]').disabled = pageNumber === 1;
    $('[data-pdf-next]').disabled = pageNumber === document.numPages;
    renderTask = page.render({canvasContext: canvas.getContext('2d'), viewport,
                             transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0]});
    await renderTask.promise;
    const text = await page.getTextContent();
    if (destroyed || current !== sequence) return;
    $('.pdf-text pre').textContent = text.items.map(item => (item.str || '') + (item.hasEOL ? '\n' : ' ')).join('');
    setStatus(`Page ${pageNumber} of ${document.numPages}`);
  }

  const safelyRender = () => render().catch(error => {
    if (!destroyed && error.name !== 'RenderingCancelledException') onError(error);
  });
  $('[data-pdf-prev]').addEventListener('click', () => { if (pageNumber > 1) { pageNumber--; safelyRender(); } });
  $('[data-pdf-next]').addEventListener('click', () => { if (document && pageNumber < document.numPages) { pageNumber++; safelyRender(); } });
  $('[data-pdf-page]').addEventListener('change', e => {
    if (!document) return;
    pageNumber = Math.max(1, Math.min(document.numPages, Math.floor(Number(e.target.value) || 1)));
    safelyRender();
  });
  $('[data-pdf-zoom]').addEventListener('change', safelyRender);
  $('.pdf-search').addEventListener('submit', async event => {
    event.preventDefault();
    const query = $('[data-pdf-query]').value.trim().toLowerCase();
    if (!document || destroyed || !query || searching) return;
    searching = true;
    const start = query === previousQuery ? previousMatch % document.numPages + 1 : pageNumber;
    previousQuery = query;
    try {
      for (let offset = 0; offset < document.numPages && !destroyed; offset++) {
        const index = (start - 1 + offset) % document.numPages + 1;
        setStatus(`Searching page ${index} of ${document.numPages}…`);
        const page = await document.getPage(index);
        const text = await page.getTextContent();
        if (text.items.map(item => item.str || '').join(' ').toLowerCase().includes(query)) {
          previousMatch = pageNumber = index;
          await render();
          setStatus(`Text found on page ${index}. Open Page text to read it.`);
          return;
        }
      }
      setStatus('No matching text found in this PDF.');
    } catch (error) { if (!destroyed) onError(error); }
    finally { searching = false; }
  });
  let lastWidth = 0;
  const observer = new ResizeObserver(entries => {
    const width = Math.round(entries[0].contentRect.width);
    if (width !== lastWidth) { lastWidth = width; if ($('[data-pdf-zoom]').value === 'fit') safelyRender(); }
  });
  observer.observe($('.pdf-page-scroll'));
  loading.promise.then(async pdf => {
    if (destroyed) return;
    document = pdf;
    await render();
    if (!destroyed) onReady();
  }).catch(error => { if (!destroyed) onError(error); });
  return () => { destroyed = true; sequence++; observer.disconnect(); renderTask?.cancel(); loading.destroy(); };
}
