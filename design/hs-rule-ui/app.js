/* DOM-based HS-RULE UI. Server text is never interpreted as HTML. */
(() => {
  'use strict';
  const API = window.HSRuleAPI, I = window.HSRuleI18n;
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const t = (key, values) => I.t(key, values), page = document.body.dataset.page;
  let sequence = 0, toastState = null, toastTimer, refreshPage = () => {};
  const text = value => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';
  function node(tag, className = '', content) { const element = document.createElement(tag); if (className) element.className = className; if (content !== undefined) element.textContent = text(content); return element; }
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', `icons.svg#${name}`); svg.append(use); return svg;
  }
  function button(label, className, callback, id = '') { const element = node('button', className, label); element.type = 'button'; element.setAttribute('data-od-id', id || `action-${++sequence}`); if (callback) element.addEventListener('click', callback); return element; }
  function safeURL(value) { if (typeof value !== 'string' || !value.trim()) return ''; try { const url = new URL(value, location.href); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; } catch { return ''; } }
  const storage = { get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }, set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } } };
  function route(name) { const files = /\.html$/.test(location.pathname) || location.protocol === 'file:'; return (files ? { chat: 'index.html', library: 'index.html#library', login: 'login.html', upload: 'upload.html' } : { chat: '/', library: '/#library', login: '/login-page', upload: '/upload' })[name] || '/'; }
  function errorKey(error, scope = '') {
    const code = error?.code || '';
    const keys = {
      rag_not_connected: scope === 'conflict' ? 'errorAnalysisNotConnected' : 'errorRag', analysis_not_connected: 'errorAnalysisNotConnected', conflict_not_connected: 'errorAnalysisNotConnected',
      admin_not_configured: 'errorAdminUnconfigured', admin_unconfigured: 'errorAdminUnconfigured', admin_auth_not_configured: 'errorAdminUnconfigured',
      invalid_credentials: 'errorCredentials', session_expired: 'errorSession', token_expired: 'errorSession', invalid_token: 'errorSession', unauthorized: 'errorSession', authentication_required: 'errorSession', forbidden: 'errorForbidden',
      duplicate_pdf: 'errorDuplicate', duplicate_document: 'errorDuplicate', document_exists: 'errorDuplicate', file_too_large: 'errorOversize', payload_too_large: 'errorOversize',
      invalid_pdf: 'errorInvalidPdf', invalid_file: 'errorInvalidPdf', unsupported_file_type: 'errorInvalidPdf', invalid_file_type: 'errorInvalidPdf', invalid_document_id: 'errorDocumentId',
      request_timeout: 'errorTimeout', connection_error: 'errorConnection', invalid_response: 'errorResponse', question_required: 'queryRequired', question_too_long: 'questionTooLong'
    };
    if (keys[code]) return keys[code];
    if (error?.status === 401) return scope === 'login' ? 'errorCredentials' : 'errorSession';
    if (error?.status === 403) return 'errorForbidden';
    if (error?.status === 409 && scope === 'upload') return 'errorDuplicate';
    if (error?.status === 413) return 'errorOversize';
    if ([415, 422].includes(error?.status) && ['upload', 'conflict'].includes(scope)) return 'errorInvalidPdf';
    if (error?.status === 404 && scope === 'original') return 'errorFileMissing';
    return { query: 'errorQueryUnavailable', upload: 'errorUpload', delete: 'errorDelete', conflict: 'errorConflict', export: 'errorExport', login: 'errorLogin', original: 'errorOriginal' }[scope] || 'errorConnection';
  }
  function errorText(error, scope) { return `${t(errorKey(error, scope))}${error?.requestId ? ` ${t('requestId', { id: error.requestId })}` : ''}`; }
  function showToast(key, values = {}, error = null, scope = '') { toastState = { key, values, error, scope }; const target = $('#toast'); target.textContent = error ? errorText(error, scope) : t(key, values); target.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { target.hidden = true; toastState = null; }, 6000); }
  function stateBlock(title, description = '', { pending = false, error = false } = {}) {
    const block = node('div', `inline-state${error ? ' error' : ''}`); if (pending) { const spinner = node('span', 'spinner'); spinner.setAttribute('aria-hidden', 'true'); block.append(spinner); block.setAttribute('role', 'status'); } else block.append(icon(error ? 'alert' : 'file'));
    const copy = node('div'); copy.append(node('strong', '', title)); if (description) copy.append(node('p', '', description)); block.append(copy); return block;
  }
  function errorBlock(error, scope, retry) {
    const key = errorKey(error, scope), block = stateBlock(t(key), key === 'errorRag' ? t('errorRagBody') : '', { error: true });
    if (error?.requestId) $('div', block).append(node('p', '', t('requestId', { id: error.requestId })));
    if (retry) $('div', block).append(button(t('retry'), 'button', retry)); return block;
  }
  function pageLabel(source) { return source.page_start ? t(source.page_end > source.page_start ? 'pageRange' : 'pageOne', { start: source.page_start, end: source.page_end }) : t('pageMissing'); }
  function normalizeSource(source) {
    const start = Number.isInteger(source.page_start) && source.page_start > 0 ? source.page_start : null;
    const end = source.page_end === undefined || source.page_end === null ? start : Number.isInteger(source.page_end) && source.page_end >= start ? source.page_end : null;
    const result = { document_id: API.validId(source.document_id) ? source.document_id : '', title: text(source.title), url: safeURL(source.url), page_start: start && end ? start : null, page_end: start && end ? end : null };
    if (typeof source.chunk_id === 'string') result.chunk_id = source.chunk_id;
    if (typeof source.score === 'number' && Number.isFinite(source.score)) result.score = source.score;
    return result;
  }
  function normalizeAnswer(data) {
    if (!data || typeof data.answer !== 'string' || typeof data.found !== 'boolean' || !Array.isArray(data.sources) || (data.found && !data.answer.trim())) throw new API.ServiceError('invalid_response', 502);
    return { answer: data.answer, found: data.found, sources: data.sources.filter(item => item && typeof item === 'object').map(normalizeSource), followups: Array.isArray(data.followups) ? data.followups.filter(item => typeof item === 'string' && item.trim()).slice(0, 4) : [] };
  }
  const validEvidence = source => Boolean(source.page_start && (source.document_id || source.url));
  function normalizeDocument(item, chapter = '') {
    return { id: API.validId(item.id) ? item.id : '', title: text(item.title) || text(item.name), filename: text(item.filename), chapter, page_count: Number.isInteger(item.page_count) && item.page_count > 0 ? item.page_count : null, visibility: ['public', 'private'].includes(item.visibility) ? item.visibility : '', status: text(item.status), index_status: text(item.index_status), warnings: Array.isArray(item.warnings) ? item.warnings.filter(warning => typeof warning === 'string') : typeof item.warnings === 'string' ? [item.warnings] : [], url: safeURL(item.url) };
  }
  function downloadBlob(blob, filename) { const url = URL.createObjectURL(blob), link = node('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); }
  async function openOriginal(record, control, startPage = null) {
    let id = record.id || record.document_id;
    if (!id && record.url) { const url = new URL(record.url); const match = url.origin === location.origin && url.pathname.match(/^\/documents\/([a-zA-Z0-9_-]{32})\/file$/); if (match) id = match[1]; }
    if (!API.authenticated() && record.visibility === 'private') { location.href = `${route('login')}?next=library`; return; }
    if (!id) { const url = safeURL(record.url); if (url && record.visibility !== 'private') window.open(startPage ? `${url.split('#')[0]}#page=${startPage}` : url, '_blank', 'noopener,noreferrer'); else showToast('', {}, new API.ServiceError('invalid_document_id'), 'original'); return; }
    const popup = window.open('about:blank', '_blank'); if (popup) popup.opener = null;
    control.disabled = true; const admin = record.visibility === 'public' ? false : API.authenticated();
    try { const blob = await API.documentFile(id, admin); if (popup) { const url = URL.createObjectURL(blob); popup.location.href = `${url}${startPage ? `#page=${startPage}` : ''}`; setTimeout(() => URL.revokeObjectURL(url), 60000); } else { downloadBlob(blob, record.filename || 'HS-RULE.pdf'); showToast('exported'); } }
    catch (error) { if (popup) popup.close(); if (admin && [401, 403].includes(error.status)) { API.clearToken(); updateAccount(); window.dispatchEvent(new CustomEvent('hsrule:auth-failure', { detail: error })); } showToast('', {}, error, 'original'); }
    finally { control.disabled = false; }
  }
  function originalButton(record, className = 'button', label = 'openPdf', pageStart = null) {
    const control = button(t(record.visibility === 'private' && !API.authenticated() ? 'privateLogin' : label), className, () => openOriginal(record, control, pageStart));
    control.append(icon('external')); control.disabled = !record.id && !record.document_id && (!record.url || record.visibility === 'private');
    if (control.disabled) control.title = t('originalUnavailable'); return control;
  }
  function documentTable(documents, admin = false, onDelete) {
    const table = node('table', `document-table${admin ? ' admin-table' : ''}`); table.append(node('caption', 'sr-only', t(admin ? 'storedDocuments' : 'pdfDocuments')));
    const head = node('thead'), heading = node('tr'); const columns = ['titleColumn', 'visibility', 'pagesColumn', 'statusColumn', admin ? 'manageColumn' : 'actionsColumn'];
    columns.forEach(key => { const th = node('th', '', t(key)); th.scope = 'col'; heading.append(th); }); head.append(heading); table.append(head);
    const body = node('tbody'); documents.forEach((item, index) => {
      const row = node('tr'); row.setAttribute('data-od-id', `${admin ? 'admin' : 'library'}-document-${item.id || index}`);
      const name = node('td'), title = node('div', 'document-title'); title.append(icon('file'), node('span', '', item.title || item.filename || t('documentUntitled'))); name.append(title);
      if (item.filename && item.title && item.filename !== item.title) name.append(node('p', 'document-filename', item.filename));
      if (item.chapter) name.append(node('p', 'document-filename', item.chapter));
      item.warnings.forEach(warning => name.append(node('p', 'document-warnings', ['rag_not_connected', 'index_not_connected', 'not_connected'].includes(warning) ? t('searchNotConnected') : warning)));
      const visibility = node('td', '', t(item.visibility || 'unknownVisibility')); visibility.setAttribute('data-label', t('visibility'));
      const pages = node('td', 'page-count', item.page_count ? item.page_count.toLocaleString(I.language) : t('unknown')); pages.setAttribute('data-label', t('pagesColumn'));
      const status = node('td'); const statusText = node('div', 'document-status', t(item.status === 'stored' ? 'stored' : 'unknownStatus')); statusText.append(node('span', '', t(item.index_status === 'not_connected' ? 'searchNotConnected' : 'unknownSearchStatus'))); status.append(statusText);
      const actions = node('td'), controls = node('div', 'row-actions'); controls.append(originalButton(item));
      if (admin) { const remove = button('', 'icon-button', () => onDelete(item), `delete-${item.id || index}`); remove.setAttribute('aria-label', t('deleteNamed', { filename: item.filename || item.title })); remove.append(icon('trash')); remove.disabled = !item.id || !item.filename; if (remove.disabled) remove.title = t('errorDocumentId'); controls.append(remove); }
      actions.append(controls); row.append(name, visibility, pages, status, actions); body.append(row);
    }); table.append(body); return table;
  }
  function renderRichText(target, content) {
    let list = null;
    const inline = (parent, value) => value.split(/(\*\*[^*]+\*\*)/g).forEach(part => parent.append(part.startsWith('**') && part.endsWith('**') ? node('strong', '', part.slice(2, -2)) : document.createTextNode(part)));
    content.split('\n').forEach(line => { const item = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.+)/); if (item) { if (!list) { list = node('ul'); target.append(list); } const li = node('li'); inline(li, item[1]); list.append(li); return; } list = null; if (!line.trim()) return; const heading = line.match(/^#{1,4}\s+(.+)/), part = node(heading ? 'h3' : 'p'); inline(part, heading ? heading[1] : line); target.append(part); });
  }
  function updateAccount() {
    $$('[data-account]').forEach(link => { $('span', link).textContent = t(API.authenticated() ? page === 'upload' ? 'logout' : 'account' : 'login'); link.href = route(API.authenticated() && page !== 'upload' ? 'upload' : 'login'); });
  }
  function setupShell() {
    $$('[data-route]').forEach(link => { link.href = route(link.dataset.route) + (link.dataset.next ? `?next=${encodeURIComponent(link.dataset.next)}` : ''); });
    $$('[data-close-dialog]').forEach(control => control.addEventListener('click', () => control.closest('dialog').close()));
    const settings = $('#settings-dialog');
    $$('[data-open-settings]').forEach(control => control.addEventListener('click', () => { settings.showModal(); control.setAttribute('aria-expanded', 'true'); }));
    settings.addEventListener('close', () => $$('[data-open-settings]').forEach(control => control.setAttribute('aria-expanded', 'false')));
    $$('[data-open-help]').forEach(control => control.addEventListener('click', () => { if (settings.open) settings.close(); $('#help-dialog').showModal(); }));
    window.addEventListener('hsrule:settings', () => { updateAccount(); refreshPage(); if (toastState) $('#toast').textContent = toastState.error ? errorText(toastState.error, toastState.scope) : t(toastState.key, toastState.values); }); updateAccount();
  }
  function initWorkspace() {
    const form = $('#question-form'), input = $('#question-input'), submit = $('#question-submit');
    let history = storage.get('hsu_conversations_v2', []); if (!Array.isArray(history)) history = [];
    history = history.filter(item => item && typeof item.id === 'string' && Array.isArray(item.turns)).slice(0, 20);
    let current = history.find(item => item.id === storage.get('hsu_active_v2', '')) || null;
    const restoredTurns = items => items.filter(item => item && typeof item.question === 'string').slice(-20).flatMap(item => { try { return [{ question: item.question, status: 'answer', response: normalizeAnswer(item.response) }]; } catch { return []; } });
    let turns = current ? restoredTurns(current.turns) : [], busy = false, questionError = '';
    let libraryState = 'idle', libraryError = null, documents = [], chapters = [], selectedChapter = 'all';
    let placeholderTimer, placeholderExamples = [], placeholderIndex = 0, placeholderLength = 0, placeholderDeleting = false, placeholderFocused = false;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    function stopPlaceholder() { clearTimeout(placeholderTimer); }
    function animatePlaceholder() {
      if (placeholderFocused || input.value || busy || document.hidden || reducedMotion.matches) { stopPlaceholder(); return; }
      const example = placeholderExamples[placeholderIndex];
      placeholderLength += placeholderDeleting ? -1 : 1;
      input.placeholder = example.slice(0, placeholderLength);
      let delay = placeholderDeleting ? 28 : 65;
      if (placeholderLength === example.length) { placeholderDeleting = true; delay = 1400; }
      else if (placeholderLength === 0) { placeholderDeleting = false; placeholderIndex = (placeholderIndex + 1) % placeholderExamples.length; delay = 350; }
      placeholderTimer = setTimeout(animatePlaceholder, delay);
    }
    function restartPlaceholder() {
      stopPlaceholder();
      if (!placeholderExamples.length) return;
      placeholderIndex = 0; placeholderLength = 0; placeholderDeleting = false;
      input.placeholder = placeholderExamples[0];
      if (!placeholderFocused && !input.value && !busy && !document.hidden && !reducedMotion.matches && !$('#chat-view').hidden) animatePlaceholder();
    }
    input.addEventListener('focus', () => { placeholderFocused = true; stopPlaceholder(); input.placeholder = placeholderExamples[0] || ''; });
    input.addEventListener('blur', () => { placeholderFocused = false; restartPlaceholder(); });
    reducedMotion.addEventListener?.('change', restartPlaceholder);
    document.addEventListener('visibilitychange', restartPlaceholder);

    function persist() {
      const saved = turns.filter(turn => turn.status === 'answer').map(turn => ({ question: turn.question, response: turn.response })); if (!saved.length) return;
      current = { id: current?.id || crypto.randomUUID(), title: saved[0].question.slice(0, 90), turns: saved, updated: Date.now() };
      history = [current, ...history.filter(item => item.id !== current.id)].slice(0, 20); if (!storage.set('hsu_conversations_v2', history)) showToast('storageUnavailable'); storage.set('hsu_active_v2', current.id); renderHistory();
    }
    function renderHistory() {
      const target = $('#history-list'); target.replaceChildren();
      if (!history.length) { target.append(node('p', 'history-empty', t('historyEmpty'))); return; }
      history.forEach(item => { const entry = button(text(item.title), 'history-entry', () => { if (busy) { showToast('historyBusy'); return; } current = item; turns = restoredTurns(item.turns); storage.set('hsu_active_v2', item.id); input.value = ''; updateQuestion(); location.hash = ''; showView(); renderChat(); $('#history-dialog').close(); }, `history-${item.id}`); if (Number.isFinite(item.updated)) entry.append(node('time', '', new Date(item.updated).toLocaleDateString(I.language === 'ko' ? 'ko-KR' : 'en-US', { month: 'short', day: 'numeric' }))); target.append(entry); });
    }
    function renderExamples() {
      const student = I.language === 'ko' ? [['학적', '휴학 신청에 관한 규정은 어디에서 확인하나요?'], ['장학', '장학금 관련 규정을 찾고 싶습니다.'], ['졸업', '졸업 요건을 확인할 수 있는 문서를 알려 주세요.'], ['수업', '수강 신청과 관련된 규정을 찾고 싶습니다.']] : [['Enrollment', 'Where can I find the regulations on taking a leave of absence?'], ['Scholarships', 'Help me find the regulations on scholarships.'], ['Graduation', 'Which documents describe the graduation requirements?'], ['Courses', 'Where can I find regulations on course registration?']];
      const staff = I.language === 'ko' ? [['복무', '교직원 복무 관련 규정을 찾고 싶습니다.'], ['문서', '문서 관리 절차가 담긴 규정은 어디에 있나요?'], ['출장', '출장 신청 전에 확인할 규정을 알려 주세요.'], ['위원회', '위원회 운영 관련 규정을 찾고 싶습니다.']] : [['Employment', 'Help me find the regulations on staff duties.'], ['Documents', 'Where can I find the document management procedures?'], ['Travel', 'Which regulations should I review before requesting business travel?'], ['Committees', 'Help me find regulations on committee operations.']];
      const list = $('#example-list'); list.replaceChildren(); (I.audience === 'staff' ? staff : student).forEach(([category, question], index) => { const control = button('', 'example-row', () => fillQuestion(question), `example-question-${index + 1}`); control.append(node('span', 'example-category', category), node('span', 'example-question', question), icon('arrow-up-right')); list.append(control); });
      placeholderExamples = (I.audience === 'staff' ? staff : student).map(([, question]) => question); restartPlaceholder();
    }
    function updateQuestion() { $('#question-counter').textContent = `${input.value.length.toLocaleString(I.language)} / 2,000`; $('#question-error').hidden = !questionError; $('#question-error').textContent = questionError ? t(questionError) : ''; }
    function fillQuestion(value) { if (busy) return; input.value = value.slice(0, 2000); questionError = ''; updateQuestion(); input.focus({ preventScroll: true }); }
    function newQuestion() { if (busy) { showToast('historyBusy'); return; } turns = []; current = null; storage.set('hsu_active_v2', ''); input.value = ''; questionError = ''; location.hash = ''; showView(); updateQuestion(); renderChat(); input.focus({ preventScroll: true }); }
    function renderChat() {
      const target = $('#conversation'); target.replaceChildren(); $('#examples').hidden = turns.length > 0;
      turns.forEach((turn, index) => {
        const question = node('div', 'question-turn'); question.setAttribute('data-od-id', `question-${index + 1}`); question.append(node('span', 'turn-label', t('yourQuestion')), node('p', '', turn.question)); target.append(question);
        const answer = node('article', 'answer'); answer.setAttribute('data-od-id', `answer-${index + 1}`); const heading = node('div', 'answer-heading'); heading.append(node('h2', '', t('responseTitle'))); answer.append(heading);
        if (turn.status === 'pending') { answer.setAttribute('aria-busy', 'true'); answer.append(stateBlock(t('pendingQuery'), t('pendingDescription'), { pending: true })); }
        else if (turn.status === 'error') answer.append(errorBlock(turn.error, 'query', () => sendQuestion(turn.question, index)));
        else {
          const data = turn.response, evidence = data.found && data.sources.some(validEvidence); heading.append(node('span', `status-label${evidence ? '' : ' warning'}`, t(evidence ? 'foundReferences' : 'noEvidence', { count: data.sources.filter(validEvidence).length })));
          const body = node('div', 'answer-text'); renderRichText(body, data.answer || t('noEvidenceBody')); answer.append(body); if (!evidence) answer.append(node('p', 'answer-note', t('noEvidenceNote')));
          if (data.sources.length) { const citations = node('div', 'answer-citations'); data.sources.forEach((source, number) => { const control = originalButton(source, 'citation-button', 'openPdf', source.page_start); control.replaceChildren(document.createTextNode(`[${number + 1}] ${source.title || t('documentUntitled')} · ${pageLabel(source)}`), icon('external')); citations.append(control); }); answer.append(citations); }
          const actions = node('div', 'answer-actions'); actions.append(node('span', '', t('exportLabel')));
          [['pdf', 'exportPdf'], ['docx', 'exportWord']].forEach(([format, key]) => { const control = button(t(key), 'button button-quiet', () => exportTurn(control, format, turn), `export-${format}-${index + 1}`); control.prepend(icon('download')); control.disabled = !evidence; if (!evidence) control.title = t('exportDisabled'); actions.append(control); }); answer.append(actions);
          if (data.followups.length) { const block = node('section', 'followups'); block.append(node('h3', '', t('followups'))); data.followups.forEach((value, number) => { const control = button(value, 'followup-button', () => fillQuestion(value), `followup-${index}-${number}`); control.append(icon('arrow-up-right')); block.append(control); }); answer.append(block); }
        } target.append(answer);
      });
      const last = turns.at(-1), hasAnswer = last?.status === 'answer'; $('#references').hidden = !hasAnswer; $('#ask-info').hidden = hasAnswer; $('#ask-grid').classList.toggle('with-sources', hasAnswer); if (hasAnswer) renderReferences(last.response);
    }
    function renderReferences(data) {
      const target = $('#references-content'); target.replaceChildren(); $('#references-count').textContent = t('referencesCount', { count: data.sources.length });
      if (!data.sources.length) target.append(node('p', 'reference-note', t('noSourceDetails')));
      data.sources.forEach((source, index) => { const reference = node('article', 'reference'); reference.setAttribute('data-od-id', `reference-${index + 1}`); reference.append(node('span', 'reference-number', `[${String(index + 1).padStart(2, '0')}]`), node('h3', '', source.title || t('documentUntitled')), node('p', '', pageLabel(source)), originalButton(source, 'button button-quiet', 'openPdf', source.page_start)); target.append(reference); });
      if (data.sources.length) target.append(node('p', 'reference-note', t('evidenceNote')));
    }
    async function sendQuestion(value, retryIndex = -1) {
      if (busy) return; const question = value.trim(); if (!question || question.length > 2000) { questionError = question ? 'questionTooLong' : 'queryRequired'; updateQuestion(); input.focus(); return; }
      const messages = turns.slice(0, retryIndex >= 0 ? retryIndex : turns.length).filter(turn => turn.status === 'answer').slice(-6).flatMap(turn => [{ role: 'user', content: turn.question }, { role: 'assistant', content: turn.response.answer }]);
      const turn = { question, status: 'pending' }, language = I.language; if (retryIndex >= 0) turns.splice(retryIndex, 1, turn); else turns.push(turn);
      busy = true; submit.disabled = true; input.disabled = true; form.setAttribute('aria-busy', 'true'); questionError = ''; updateQuestion(); renderChat();
      try { turn.response = normalizeAnswer(await API.query(question, messages, language)); turn.status = 'answer'; input.value = ''; persist(); }
      catch (error) { turn.status = 'error'; turn.error = error; }
      finally { busy = false; submit.disabled = false; input.disabled = false; form.removeAttribute('aria-busy'); updateQuestion(); renderChat(); restartPlaceholder(); }
    }
    async function exportTurn(control, format, turn) {
      control.disabled = true; control.textContent = t('exporting');
      try { const blob = await API.exportAnswer(format, { title: t('exportTitle'), content: turn.response.answer, sources: turn.response.sources.filter(validEvidence), question: turn.question }); downloadBlob(blob, `HS-RULE.${format}`); showToast('exported'); }
      catch (error) { showToast('', {}, error, 'export'); } finally { control.disabled = false; control.replaceChildren(icon('download'), document.createTextNode(t(format === 'pdf' ? 'exportPdf' : 'exportWord'))); }
    }
    function renderLibrary() {
      const target = $('#rules-list'), count = $('#library-count'); target.replaceChildren(); const filter = $('#chapter-filter'); filter.replaceChildren(); const all = node('option', '', t('allChapters')); all.value = 'all'; filter.append(all); chapters.forEach(chapter => { const option = node('option', '', chapter.name || t('unknown')); option.value = chapter.id; filter.append(option); }); filter.value = selectedChapter; filter.hidden = chapters.length < 2;
      if (libraryState === 'pending') { count.textContent = t('loadingList'); const block = node('div', 'table-state'); block.append(stateBlock(t('loadingList'), '', { pending: true })); target.append(block); return; }
      if (libraryState === 'error') { count.textContent = t('notLoaded'); const block = node('div', 'table-state'); block.append(errorBlock(libraryError, 'library', loadLibrary)); target.append(block); return; }
      if (libraryState !== 'loaded') { count.textContent = t('notLoaded'); return; }
      const term = $('#library-search').value.trim().toLocaleLowerCase(); const filtered = documents.filter(item => (selectedChapter === 'all' || item.chapterId === selectedChapter) && `${item.title} ${item.filename}`.toLocaleLowerCase().includes(term)); count.textContent = t('countShowing', { shown: filtered.length, total: documents.length });
      if (filtered.length) target.append(documentTable(filtered)); else { const block = node('div', 'table-state'); block.append(stateBlock(t(documents.length ? 'noMatches' : 'libraryEmpty'), t(documents.length ? 'noMatchesBody' : 'libraryEmptyBody'))); target.append(block); }
    }
    async function loadLibrary() {
      if (libraryState === 'pending') return; libraryState = 'pending'; $('#refresh-library').disabled = true; renderLibrary();
      try { const data = await API.rules(); if (!data || !Array.isArray(data.chapters)) throw new API.ServiceError('invalid_response', 502); chapters = []; documents = []; data.chapters.forEach((chapter, index) => { if (!chapter || !Array.isArray(chapter.rules)) return; const id = String(index), name = text(chapter.chapter); chapters.push({ id, name }); chapter.rules.filter(item => item && typeof item === 'object').forEach(item => { const doc = normalizeDocument(item, name); if (doc.title || doc.filename) documents.push({ ...doc, chapterId: id }); }); }); if (!chapters.some(chapter => chapter.id === selectedChapter)) selectedChapter = 'all'; libraryState = 'loaded'; }
      catch (error) { libraryError = error; libraryState = 'error'; }
      finally { $('#refresh-library').disabled = false; renderLibrary(); }
    }
    function showView() { const library = location.hash === '#library'; $('#chat-view').hidden = library; $('#library-view').hidden = !library; $$('[data-nav]').forEach(link => { if (link.dataset.nav === (library ? 'library' : 'chat')) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); }); document.title = `${t(library ? 'libraryTitle' : 'askTitle')} · HS-RULE`; if (library) stopPlaceholder(); else restartPlaceholder(); if (library && libraryState === 'idle') loadLibrary(); }
    form.addEventListener('submit', event => { event.preventDefault(); sendQuestion(input.value); }); input.addEventListener('input', () => { questionError = ''; updateQuestion(); restartPlaceholder(); });
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229 && (event.ctrlKey || event.metaKey || !matchMedia('(pointer: coarse)').matches)) { event.preventDefault(); if (!busy) form.requestSubmit(); } });
    $('#new-question').addEventListener('click', newQuestion); $('#open-history').addEventListener('click', () => { renderHistory(); $('#history-dialog').showModal(); });
    document.addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !$$('dialog').some(dialog => dialog.open)) { event.preventDefault(); newQuestion(); } });
    $('#library-search').addEventListener('input', renderLibrary); $('#chapter-filter').addEventListener('change', event => { selectedChapter = event.target.value; renderLibrary(); $('#chapter-filter').focus({ preventScroll: true }); }); $('#refresh-library').addEventListener('click', loadLibrary); window.addEventListener('hashchange', showView);
    refreshPage = () => { renderExamples(); updateQuestion(); renderHistory(); renderChat(); renderLibrary(); showView(); }; refreshPage();
  }
  function initLogin() {
    const username = $('#username'), password = $('#password'), form = $('#login-form'), submit = $('#login-submit'); let busy = false, validationKey = '', failure = null;
    function render() { document.title = `${t('loginTitle')} · HS-RULE`; $('#toggle-password').setAttribute('aria-label', t(password.type === 'password' ? 'showPassword' : 'hidePassword')); $('span', submit).textContent = t(busy ? 'loggingIn' : 'login'); $('#login-error').hidden = !validationKey && !failure; $('#login-error').textContent = validationKey ? t(validationKey) : failure ? errorText(failure, 'login') : ''; }
    function fail(key, field) { validationKey = key; failure = null; if (field) { field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', 'login-error'); field.focus(); } render(); }
    $('#toggle-password').addEventListener('click', () => { const visible = password.type === 'password'; password.type = visible ? 'text' : 'password'; $('#toggle-password').setAttribute('aria-pressed', String(visible)); render(); });
    [username, password].forEach(field => field.addEventListener('input', () => { validationKey = ''; failure = null; field.removeAttribute('aria-invalid'); render(); }));
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy) return; if (!username.value.trim()) { fail('usernameRequired', username); return; } if (!password.value) { fail('passwordRequired', password); return; }
      busy = true; validationKey = ''; failure = null; submit.disabled = true; form.setAttribute('aria-busy', 'true'); render();
      try { const response = await API.login(username.value.trim(), password.value); if (response?.success !== true || typeof response.token !== 'string' || !response.token) throw new API.ServiceError('invalid_credentials', 401); if (!API.saveToken(response.token)) { fail('tokenStorageError'); return; } password.value = ''; const next = new URLSearchParams(location.search).get('next'); location.href = route(['chat', 'library', 'upload'].includes(next) ? next : 'upload'); }
      catch (error) { failure = error; } finally { busy = false; submit.disabled = false; form.removeAttribute('aria-busy'); render(); }
    }); refreshPage = render; render();
  }
  function initManagement() {
    const fileInput = $('#upload-files'), startUpload = $('#start-upload'), deleteDialog = $('#delete-dialog');
    let verified = false, documentState = API.authenticated() ? 'idle' : 'locked', documentError = null, documents = [], queue = [], selecting = false, uploading = false, uploadError = null, uploadErrorFilename = '', conflictFile = null, conflictBusy = false, conflictState = 'idle', conflictError = null, conflictData = null, deleting = false, deleteItem = null, deleteError = null;
    const busy = () => uploading || selecting || conflictBusy || deleting;
    function renderAuth() {
      const hasToken = API.authenticated(); $('#auth-bar').hidden = verified; $('#auth-status').textContent = t(verified ? 'sessionVerified' : documentState === 'pending' ? 'sessionChecking' : hasToken ? 'managementUnavailable' : 'loginRequired');
      $('#auth-message').textContent = documentError ? errorText(documentError, 'management') : t(hasToken ? 'managementChecking' : 'adminRequired'); $('a', $('#auth-bar')).hidden = hasToken;
      $('#upload-controls').disabled = !verified || uploading || selecting; $('#conflict-controls').disabled = !verified || conflictBusy;
      $('#refresh-uploaded').disabled = !hasToken || documentState === 'pending'; startUpload.disabled = !verified || uploading || selecting || !queue.some(item => ['queued', 'failed'].includes(item.state)); $('#start-conflict').disabled = !verified || conflictBusy || !conflictFile;
      $('#upload-title').disabled = queue.length > 1 || !verified || uploading || selecting; $('#upload-title-hint').textContent = t(queue.length > 1 ? 'multipleTitleHint' : 'visibilityHint');
      $('span', startUpload).textContent = t(uploading ? 'storing' : 'storePdfs'); $('#conflict-file-name').textContent = conflictFile ? conflictFile.name : t('noFileSelected'); updateAccount();
    }
    function handleAuthError(error) { if (![401, 403].includes(error?.status)) return false; API.clearToken(); verified = false; documentError = error; documentState = 'locked'; documents = []; renderAuth(); renderDocuments(); return true; }
    function renderDocuments() {
      const target = $('#uploaded-list'); target.replaceChildren(); const count = $('#uploaded-count');
      if (documentState === 'locked') { count.textContent = '—'; const block = node('div', 'table-state'); block.append(stateBlock(t('documentsLocked'))); target.append(block); return; }
      if (documentState === 'pending' || documentState === 'idle') { count.textContent = '—'; const block = node('div', 'table-state'); block.append(stateBlock(t('loadingList'), '', { pending: true })); target.append(block); return; }
      if (documentState === 'error') { count.textContent = '—'; const block = node('div', 'table-state'); block.append(errorBlock(documentError, 'management')); target.append(block); return; }
      const term = $('#document-search').value.trim().toLocaleLowerCase(), visibility = $('#visibility-filter').value;
      const filtered = documents.filter(item => `${item.title} ${item.filename}`.toLocaleLowerCase().includes(term) && (visibility === 'all' || item.visibility === visibility)); count.textContent = t('countShowing', { shown: filtered.length, total: documents.length });
      if (filtered.length) target.append(documentTable(filtered, true, openDelete)); else { const block = node('div', 'table-state'); block.append(stateBlock(t(documents.length ? 'noMatches' : 'storedEmpty'), t(documents.length ? 'noMatchesBody' : 'storedEmptyBody'))); target.append(block); }
    }
    async function loadDocuments() {
      if (!API.authenticated() || documentState === 'pending') return; documentState = 'pending'; documentError = null; renderAuth(); renderDocuments();
      try { const data = await API.uploaded(); if (!data || !Array.isArray(data.rules)) throw new API.ServiceError('invalid_response', 502); documents = data.rules.filter(item => item && typeof item === 'object').map(item => normalizeDocument(item)).filter(item => item.title || item.filename); verified = true; documentState = 'loaded'; }
      catch (error) { if (!handleAuthError(error)) { documentError = error; documentState = 'error'; } }
      finally { renderAuth(); renderDocuments(); }
    }
    function renderQueue() {
      const target = $('#upload-queue'); target.replaceChildren(); $('#queue-count').textContent = queue.length ? t('filesSelected', { count: queue.length }) : t('noFilesSelected');
      queue.forEach((item, index) => { const row = node('div', 'queue-row'); row.setAttribute('data-od-id', `upload-file-${index + 1}`); const heading = node('div', 'queue-heading'); heading.append(icon('file'), node('strong', '', item.file.name)); if (!uploading && !selecting) { const remove = button('', 'icon-button', () => { queue.splice(index, 1); renderQueue(); renderAuth(); }); remove.setAttribute('aria-label', t('removeSelection', { filename: item.file.name })); remove.append(icon('x')); heading.append(remove); }
        const meta = node('div', 'queue-meta'); meta.append(node('span', '', `${(item.file.size / 1048576).toLocaleString(I.language, { maximumFractionDigits: 2 })} MiB`));
        const label = item.state === 'uploading' ? item.progress === null ? t('fileTransferring') : item.progress >= 100 ? t('fileProcessing') : t('fileTransfer', { percent: item.progress }) : item.state === 'failed' ? errorText(item.error, 'upload') : t(item.state === 'stored' ? 'fileStored' : 'fileQueued'); meta.append(node('span', item.state === 'failed' ? 'error' : '', label)); row.append(heading, meta);
        if (item.state === 'uploading') { const progress = node('progress', 'queue-progress'); progress.max = 100; if (item.progress !== null) progress.value = item.progress; progress.setAttribute('aria-label', t('fileProgress', { filename: item.file.name })); row.append(progress); } target.append(row);
      }); $('#upload-error').hidden = !uploadError; $('#upload-error').textContent = uploadError ? `${uploadErrorFilename ? `${uploadErrorFilename}: ` : ''}${errorText(uploadError, 'upload')}` : '';
    }
    async function addFiles(files) {
      if (!verified || uploading || selecting) return; selecting = true; uploadError = null; renderAuth();
      for (const file of files) { try { await API.validatePDF(file); if (!verified) break; if (queue.some(item => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) { showToast('fileDuplicateSelection'); continue; } queue.push({ file, state: 'queued', progress: null }); } catch (error) { uploadError = error; uploadErrorFilename = file.name; } }
      fileInput.value = ''; selecting = false; renderQueue(); renderAuth();
    }
    $('#choose-upload-files').addEventListener('click', () => fileInput.click()); fileInput.addEventListener('change', () => addFiles([...fileInput.files]));
    const dropzone = $('#dropzone'); ['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); if (verified && !uploading && !selecting) dropzone.classList.add('is-dragging'); })); ['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('is-dragging'); if (name === 'drop') addFiles([...event.dataTransfer.files]); }));
    startUpload.addEventListener('click', async () => {
      if (!verified || uploading || selecting) return; const pending = queue.filter(item => ['queued', 'failed'].includes(item.state)); if (!pending.length) return;
      const visibility = $('#upload-visibility').value === 'public' ? 'public' : 'private', title = queue.length === 1 ? $('#upload-title').value.trim() : ''; uploading = true; uploadError = null; renderAuth(); renderQueue(); let stored = 0;
      for (const item of pending) { item.state = 'uploading'; item.progress = null; renderQueue(); try { await API.upload(item.file, { title, visibility, onProgress: percent => { item.progress = percent; renderQueue(); } }); item.state = 'stored'; stored++; } catch (error) { item.state = 'failed'; item.error = error; if (handleAuthError(error)) break; } renderQueue(); }
      uploading = false; renderQueue(); renderAuth(); if (stored) { showToast('storedToast', { count: stored }); await loadDocuments(); }
    });
    function openDelete(item) { if (!verified || busy()) { showToast('finishTask'); return; } if (!item.id || !item.filename) { showToast('', {}, new API.ServiceError('invalid_document_id'), 'delete'); return; } deleteItem = item; deleteError = null; $('#delete-filename').textContent = item.filename; renderDelete(); deleteDialog.showModal(); $('#cancel-delete').focus(); }
    function renderDelete() { $('#delete-error').hidden = !deleteError; $('#delete-error').textContent = deleteError ? errorText(deleteError, 'delete') : ''; $('#confirm-delete').textContent = t(deleting ? 'deleting' : 'deleteDocument'); $('#confirm-delete').disabled = deleting; $('#cancel-delete').disabled = deleting; }
    $('#cancel-delete').addEventListener('click', () => { if (!deleting) deleteDialog.close(); }); deleteDialog.addEventListener('cancel', event => { if (deleting) event.preventDefault(); });
    $('#confirm-delete').addEventListener('click', async () => { if (deleting || !deleteItem) return; deleting = true; deleteError = null; renderDelete(); try { await API.removeUploaded(deleteItem.id); deleteDialog.close(); showToast('deleted'); await loadDocuments(); } catch (error) { if (handleAuthError(error)) deleteDialog.close(); else deleteError = error; } finally { deleting = false; renderDelete(); } });
    $('#choose-conflict-file').addEventListener('click', () => $('#conflict-file').click());
    $('#conflict-file').addEventListener('change', async event => { const file = event.target.files[0]; if (!file || !verified || conflictBusy) return; try { await API.validatePDF(file); if (!verified) return; conflictFile = file; conflictState = 'idle'; conflictData = null; conflictError = null; } catch (error) { showToast('', {}, error, 'conflict'); event.target.value = ''; } renderAuth(); renderConflict(); });
    function renderConflict() {
      const target = $('#conflict-results'); target.replaceChildren(); target.hidden = conflictState === 'idle'; target.setAttribute('aria-busy', String(conflictBusy)); if (conflictState === 'idle') return;
      if (conflictState === 'pending') { target.append(stateBlock(t('conflictPending'), t('conflictPendingBody'), { pending: true })); return; }
      if (conflictState === 'error') { target.append(errorBlock(conflictError, 'conflict')); return; }
      target.append(node('h2', '', t('conflictResult')), node('p', 'reference-note', `${conflictFile?.name || ''} · ${t('conflictReviewNote')}`), node('p', 'conflict-summary', conflictData.summary));
      if (!conflictData.conflicts.length) target.append(node('p', 'reference-note', t('noConflicts')));
      conflictData.conflicts.filter(item => item && typeof item === 'object').forEach((item, index) => { const block = node('article', 'conflict-item'); block.setAttribute('data-od-id', `conflict-result-${index + 1}`); const heading = node('h3', '', [text(item.regulation), text(item.article)].filter(Boolean).join(' · ') || t('reviewItem')); const severity = text(item.severity).toLowerCase(), level = ['high', 'critical', '높음'].includes(severity) ? 'priorityHigh' : ['medium', '보통'].includes(severity) ? 'priorityMedium' : ['low', '낮음'].includes(severity) ? 'priorityLow' : 'priorityUnknown'; heading.append(node('span', `status-label${level === 'priorityHigh' ? ' error' : ' warning'}`, t('priority', { level: t(level) }))); block.append(heading, node('p', '', text(item.issue))); target.append(block); });
      const recommendations = Array.isArray(conflictData.recommendations) ? conflictData.recommendations.filter(value => typeof value === 'string') : typeof conflictData.recommendations === 'string' ? [conflictData.recommendations] : []; if (recommendations.length) { target.append(node('h3', '', t('recommendations'))); const list = node('ul'); recommendations.forEach(value => list.append(node('li', '', value))); target.append(list); }
    }
    $('#start-conflict').addEventListener('click', async () => { if (!verified || conflictBusy || !conflictFile) return; conflictBusy = true; conflictState = 'pending'; conflictError = null; renderAuth(); renderConflict(); try { const data = await API.conflict(conflictFile, I.language); if (!data || typeof data.summary !== 'string' || !Array.isArray(data.conflicts)) throw new API.ServiceError('invalid_response', 502); conflictData = data; conflictState = 'loaded'; } catch (error) { handleAuthError(error); conflictError = error; conflictState = 'error'; } finally { conflictBusy = false; renderAuth(); renderConflict(); } });
    $$('[data-account]').forEach(link => link.addEventListener('click', event => { if (!API.authenticated()) return; event.preventDefault(); if (busy()) { showToast('finishTask'); return; } API.clearToken(); verified = false; documentState = 'locked'; documentError = null; documents = []; queue = []; conflictFile = null; conflictState = 'idle'; conflictData = null; uploadError = null; $('#upload-title').value = ''; $('#upload-visibility').value = 'private'; $('#conflict-file').value = ''; renderAll(); showToast('loggedOut'); }));
    $('#document-search').addEventListener('input', renderDocuments); $('#visibility-filter').addEventListener('change', renderDocuments); $('#refresh-uploaded').addEventListener('click', loadDocuments); window.addEventListener('hsrule:auth-failure', event => handleAuthError(event.detail));
    function renderAll() { document.title = `${t('documentsTitle')} · HS-RULE`; renderAuth(); renderDocuments(); renderQueue(); renderConflict(); renderDelete(); }
    refreshPage = renderAll; renderAll(); if (API.authenticated()) loadDocuments();
  }
  setupShell(); if (page === 'workspace') initWorkspace(); if (page === 'login') initLogin(); if (page === 'upload') initManagement();
})();
