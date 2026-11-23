/* Same-origin FastAPI boundary. Answer generation and indexing are external services. */
(() => {
  'use strict';
  const PDF_LIMIT = 20 * 1024 * 1024;
  class ServiceError extends Error {
    constructor(code = 'connection_error', status = 0, requestId = '') { super(code); this.name = 'ServiceError'; this.code = code; this.status = status; this.requestId = typeof requestId === 'string' ? requestId : ''; }
  }
  const token = () => { try { return localStorage.getItem('hsu_token') || ''; } catch { return ''; } };
  const authenticated = () => Boolean(token());
  const saveToken = value => { try { localStorage.setItem('hsu_token', value); return token() === value; } catch { return false; } };
  const clearToken = () => { try { localStorage.removeItem('hsu_token'); } catch { /* Storage may be unavailable. */ } };
  const validId = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{32}$/.test(value);
  function documentPath(id) { if (!validId(id)) throw new ServiceError('invalid_document_id'); return `/documents/${encodeURIComponent(id)}/file`; }
  function errorFrom(data, status, fallback = 'service_error', headerId = '') {
    return new ServiceError(typeof data?.code === 'string' ? data.code : typeof data?.detail?.code === 'string' ? data.detail.code : fallback, status, data?.request_id || data?.detail?.request_id || headerId);
  }
  async function validatePDF(file) {
    if (!file || typeof file.name !== 'string' || !/\.pdf$/i.test(file.name) || !file.size) throw new ServiceError('invalid_pdf');
    if (file.size > PDF_LIMIT) throw new ServiceError('file_too_large', 413);
    if (file.type && !['application/pdf', 'application/octet-stream'].includes(file.type)) throw new ServiceError('invalid_pdf');
    try { if (!(await file.slice(0, 1024).text()).includes('%PDF-')) throw new Error(); }
    catch { throw new ServiceError('invalid_pdf'); }
    return file;
  }
  async function validatedBinary(response, format) {
    const type = (response.headers.get('content-type') || '').toLowerCase();
    if (/json|text\//.test(type)) throw new ServiceError('invalid_response', 502);
    const blob = await response.blob();
    const signature = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
    const valid = format === 'pdf' ? String.fromCharCode(...signature) === '%PDF-' : signature[0] === 80 && signature[1] === 75;
    if (!blob.size || !valid) throw new ServiceError('invalid_response', 502);
    return blob;
  }
  async function request(path, { method = 'GET', json, body, admin = false, binary = '', timeout = 45000 } = {}) {
    const headers = { Accept: binary === 'pdf' ? 'application/pdf' : binary === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : 'application/json' };
    if (json !== undefined) headers['Content-Type'] = 'application/json';
    if (admin) { if (!token()) throw new ServiceError('session_expired', 401); headers.Authorization = `Bearer ${token()}`; }
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, { method, headers, body: json !== undefined ? JSON.stringify(json) : body, credentials: 'same-origin', redirect: 'error', signal: controller.signal });
      if (!response.ok) {
        let data; try { data = await response.json(); } catch { data = {}; }
        throw errorFrom(data, response.status, 'service_error', response.headers.get('x-request-id'));
      }
      if (response.status === 204 && method === 'DELETE') return null;
      if (binary) return await validatedBinary(response, binary);
      if (!/application\/(?:[\w.-]+\+)?json/i.test(response.headers.get('content-type') || '')) throw new ServiceError('invalid_response', 502);
      let result; try { result = await response.json(); } catch { throw new ServiceError('invalid_response', 502); }
      if (result?.success === false) throw errorFrom(result, response.status, 'invalid_response');
      return result;
    } catch (error) {
      if (error instanceof ServiceError) throw error;
      throw new ServiceError(error.name === 'AbortError' ? 'request_timeout' : 'connection_error');
    } finally { clearTimeout(timer); }
  }
  async function upload(file, { title = '', visibility = 'private', onProgress = () => {} } = {}) {
    await validatePDF(file);
    if (!token()) throw new ServiceError('session_expired', 401);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest(); xhr.open('POST', '/upload-regulation'); xhr.timeout = 120000;
      xhr.setRequestHeader('Authorization', `Bearer ${token()}`); xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = event => { if (event.lengthComputable && event.total > 0) onProgress(Math.min(100, Math.round(event.loaded / event.total * 100))); };
      xhr.onload = () => {
        let data; try { data = JSON.parse(xhr.responseText); } catch { reject(new ServiceError('invalid_response', xhr.status || 502)); return; }
        if (xhr.status < 200 || xhr.status >= 300) { reject(errorFrom(data, xhr.status)); return; }
        if (!/application\/(?:[\w.-]+\+)?json/i.test(xhr.getResponseHeader('content-type') || '') || data?.success !== true || !validId(data.document?.id) || data.document?.status !== 'stored') { reject(new ServiceError('invalid_response', 502)); return; }
        resolve(data);
      };
      xhr.onerror = () => reject(new ServiceError('connection_error')); xhr.ontimeout = () => reject(new ServiceError('request_timeout')); xhr.onabort = () => reject(new ServiceError('request_cancelled'));
      const form = new FormData(); form.append('file', file); if (title.trim()) form.append('title', title.trim()); form.append('visibility', visibility === 'public' ? 'public' : 'private'); xhr.send(form);
    });
  }
  async function query(question, messages = [], language = 'ko') {
    if (typeof question !== 'string' || !question.trim()) throw new ServiceError('question_required');
    if (question.trim().length > 2000) throw new ServiceError('question_too_long');
    const history = Array.isArray(messages) ? messages.filter(message => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string').slice(-12).map(({ role, content }) => ({ role, content })) : [];
    return request('/query', { method: 'POST', json: { question: question.trim(), messages: history, language: language === 'en' ? 'en' : 'ko' } });
  }
  async function conflict(file, language = 'ko') { await validatePDF(file); const body = new FormData(); body.append('file', file); body.append('language', language === 'en' ? 'en' : 'ko'); return request('/conflict/', { method: 'POST', body, admin: true, timeout: 120000 }); }
  async function removeUploaded(id) { if (!validId(id)) throw new ServiceError('invalid_document_id'); return request(`/uploaded-rules/${encodeURIComponent(id)}`, { method: 'DELETE', admin: true }); }
  async function exportAnswer(format, payload) {
    if (!['pdf', 'docx'].includes(format) || !payload || typeof payload.content !== 'string' || !payload.content.trim() || !Array.isArray(payload.sources) || !payload.sources.length) throw new ServiceError('invalid_response');
    return request(`/export/${format}`, { method: 'POST', json: payload, binary: format });
  }
  window.HSRuleAPI = Object.freeze({ ServiceError, PDF_LIMIT, validId, validatePDF, authenticated, saveToken, clearToken, query, upload, conflict, removeUploaded, exportAnswer,
    rules: () => request('/rules'), uploaded: () => request('/uploaded-rules', { admin: true }),
    login: (username, password) => request('/login', { method: 'POST', json: { username, password } }),
    documentFile: (id, admin = false) => request(documentPath(id), { admin, binary: 'pdf' })
  });
})();
