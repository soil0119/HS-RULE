/* Interface copy only. Document titles, source text and conversation content stay original. */
(() => {
  'use strict';
  const copy = {
    skip: ['본문으로 건너뛰기', 'Skip to main content'], mainNavigation: ['주요 탐색', 'Main navigation'],
    navAsk: ['질문', 'Ask'], navLibrary: ['규정', 'Regulations'], navDocuments: ['문서 관리', 'Documents'],
    audience: ['질문 예시 대상', 'Question examples for'], student: ['학생', 'Student'], staff: ['교직원', 'Staff'],
    interfaceLanguage: ['화면 언어', 'Interface language'], language: ['언어', 'Language'], settings: ['언어 및 계정 설정', 'Language & account'], close: ['닫기', 'Close'],
    login: ['로그인', 'Sign in'], logout: ['로그아웃', 'Sign out'], account: ['문서 관리', 'Documents'],
    help: ['이용 안내', 'Help'], helpTitle: ['HS-RULE 이용 안내', 'Using HS-RULE'],
    askTitle: ['규정 질문', 'Ask about regulations'], askDescription: ['질문과 PDF 근거를 한곳에서 확인합니다.', 'Ask a question and check the supporting PDF pages.'],
    history: ['최근 질문', 'History'], newQuestion: ['새 질문', 'New question'], questionLabel: ['질문 내용', 'Your question'],
    questionPlaceholder: ['예: 휴학 신청에 관한 규정은 어디에서 확인하나요?', 'E.g. Where can I find the regulations on taking a leave of absence?'],
    staffPlaceholder: ['예: 교직원 복무에 관한 규정을 찾고 싶습니다.', 'E.g. Where can I find regulations on staff duties?'],
    keyboardHint: ['Enter 전송 · Shift + Enter 줄바꿈', 'Enter to send · Shift + Enter for a new line'], sendQuestion: ['질문하기', 'Send question'],
    questionPrivacy: ['학번·연락처 등 개인정보는 입력하지 마세요.', 'Do not include student IDs, contact details or other personal information.'],
    examplesTitle: ['질문 예시', 'Example questions'], examplesHint: ['선택하면 입력창에 추가됩니다', 'Select to add to the question field'],
    guideTitle: ['질문·근거 확인', 'Questions & evidence'],
    guideQuestionTitle: ['상황을 구체적으로 질문하세요', 'Be specific about your question'], guideQuestionBody: ['규정명이나 확인하고 싶은 절차를 함께 적어 주세요.', 'Include a regulation name or the procedure you want to check.'],
    guideSourceTitle: ['PDF의 해당 페이지를 확인하세요', 'Check the cited PDF pages'], guideSourceBody: ['답변에 연결된 PDF와 페이지를 확인하세요. 근거가 없으면 답변만으로 판단하지 마세요.', 'Open the cited PDF and pages. If no evidence is available, do not rely on the answer alone.'],
    guideSourceShort: ['PDF 페이지로 근거 확인', 'Check evidence on the page'], guideSourceShortBody: ['답변에 제시된 문서와 페이지를 원문에서 확인하세요.', 'Verify the document and page references in the original PDF.'],
    guideServiceTitle: ['연결 상태에 따라 답변 제공', 'Answers depend on service availability'], guideServiceBody: ['답변 서비스가 연결 전이어도 공개 PDF는 열람할 수 있습니다.', 'Public PDFs remain available even when the answer service is not connected.'],
    guideDocumentTitle: ['원본 저장과 답변 검색은 별개입니다', 'Storage and answer search are separate'], guideDocumentBody: ['PDF가 저장되어 있어도 답변 검색은 연결 전일 수 있습니다. 문서 관리는 관리자 로그인이 필요합니다.', 'A stored PDF may not yet be connected to answer search. Document management requires an administrator account.'],
    historyPrivacy: ['질문 이력은 현재 브라우저에 저장됩니다. 공용 기기에서 개인정보를 입력하지 마세요.', 'Question history is saved in this browser. Avoid personal information on shared devices.'],
    historyEmpty: ['저장된 질문이 없습니다.', 'No saved questions yet.'], historyBusy: ['현재 요청이 끝난 뒤 다른 질문을 열어 주세요.', 'Wait for the current request before opening another question.'],
    storageUnavailable: ['질문 이력을 이 브라우저에 저장할 수 없습니다.', 'Question history could not be saved in this browser.'],
    browseLibrary: ['규정 문서 열람', 'Browse regulations'], referencesTitle: ['PDF 근거', 'PDF references'],
    referenceDisclaimer: ['안내는 참고용입니다. 최종 판단은 규정 원문과 담당 부서에 확인하세요.', 'Guidance is for reference. Verify the original regulation and consult the responsible office.'], university: ['한성대학교 · HS-RULE', 'Hansung University · HS-RULE'],
    libraryTitle: ['규정 문서', 'Regulation documents'], libraryDescription: ['제공된 PDF 목록을 검색하고 원본을 열람합니다.', 'Search the available PDFs and open the original documents.'],
    librarySearch: ['제목 또는 파일명 검색', 'Search titles or filenames'], librarySearchPlaceholder: ['제목 또는 파일명으로 검색', 'Search by title or filename'], chapterFilter: ['장별 필터', 'Filter by chapter'], allChapters: ['전체 분류', 'All chapters'],
    refresh: ['새로고침', 'Refresh'], pdfDocuments: ['PDF 문서', 'PDF documents'], notLoaded: ['확인 전', 'Not loaded'],
    libraryNote: ['제공된 목록 기준입니다. 원본 저장 여부가 답변 검색 연결이나 전체 규정 수록을 의미하지는 않습니다.', 'Based on the available list. Storage does not confirm answer-search availability or a complete regulation collection.'],
    titleColumn: ['문서 제목', 'Document'], visibility: ['공개 범위', 'Visibility'], pagesColumn: ['페이지', 'Pages'], statusColumn: ['상태', 'Status'], actionsColumn: ['원본', 'Original'],
    private: ['비공개', 'Private'], public: ['공개', 'Public'], unknownVisibility: ['확인 필요', 'Not provided'], unknown: ['미제공', 'Not provided'],
    stored: ['원본 저장됨', 'Original stored'], searchNotConnected: ['답변 검색 연결 전', 'Answer search not connected'], unknownStatus: ['상태 확인 필요', 'Status not provided'], unknownSearchStatus: ['답변 검색 상태 미제공', 'Answer search status not provided'], manageColumn: ['관리', 'Actions'],
    openPdf: ['PDF 열기', 'Open PDF'], downloadPdf: ['PDF 다운로드', 'Download PDF'], originalUnavailable: ['원본 정보 없음', 'Original unavailable'], privateLogin: ['로그인 후 열람', 'Sign in to open'],
    pageCount: ['{count}쪽', '{count} pages'], pageOne: ['{start}쪽', 'p. {start}'], pageRange: ['{start}–{end}쪽', 'pp. {start}–{end}'], pageMissing: ['페이지 미제공', 'Page not provided'],
    documentUntitled: ['제목 미제공', 'Untitled document'], foundReferences: ['근거 {count}건', '{count} references'], referencesCount: ['{count}건', '{count} sources'],
    responseTitle: ['답변', 'Answer'], yourQuestion: ['질문', 'Question'], noEvidence: ['확인된 근거 없음', 'No supporting evidence'],
    noEvidenceBody: ['질문에 답할 수 있는 PDF 근거를 찾지 못했습니다. 규정명이나 상황을 더 구체적으로 적어 주세요.', 'No supporting PDF evidence was found. Try a more specific regulation name or context.'],
    noEvidenceNote: ['확인된 규정 안내로 간주하지 마세요. 원문이나 담당 부서의 확인이 필요합니다.', 'Do not treat this as verified regulation guidance. Check the original document or responsible office.'],
    noSourceDetails: ['확인 가능한 PDF 페이지 정보가 제공되지 않았습니다.', 'No verifiable PDF page information was provided.'],
    pendingQuery: ['질문에 연결된 PDF 근거를 확인하고 있습니다.', 'Looking for PDF evidence related to your question.'],
    pendingDescription: ['결과가 제공되면 답변과 출처가 표시됩니다.', 'The answer and sources will appear when available.'],
    retry: ['다시 시도', 'Try again'], exportLabel: ['답변과 출처 저장', 'Save answer and sources'], exportPdf: ['PDF', 'PDF'], exportWord: ['Word', 'Word'],
    exporting: ['저장 중', 'Saving'], exported: ['다운로드를 시작했습니다.', 'Download started.'], exportTitle: ['HS-RULE 규정 안내', 'HS-RULE regulation guidance'], exportDisabled: ['실제 PDF 근거와 답변이 있을 때 사용할 수 있습니다.', 'Available for answers with actual PDF evidence.'],
    followups: ['이어서 확인하기', 'Related questions'], evidenceNote: ['제시된 페이지를 원본 PDF에서 확인하세요.', 'Check the cited pages in the original PDF.'],
    queryRequired: ['질문을 입력해 주세요.', 'Enter a question.'], questionTooLong: ['질문은 2,000자 이내로 입력해 주세요.', 'Keep your question within 2,000 characters.'],
    countDocuments: ['{count}개 문서', '{count} documents'], countShowing: ['{shown} / {total}개 표시', 'Showing {shown} of {total}'], loadingList: ['문서 목록을 불러오는 중입니다.', 'Loading the document list.'],
    libraryEmpty: ['제공된 PDF 문서가 없습니다.', 'No PDF documents are available.'], libraryEmptyBody: ['문서가 제공되면 이 목록에 표시됩니다.', 'Available documents will appear here.'],
    noMatches: ['검색 조건에 맞는 문서가 없습니다.', 'No documents match your search.'], noMatchesBody: ['검색어 또는 필터를 변경하세요.', 'Change the search term or filters.'],
    libraryFailed: ['문서 목록을 불러올 수 없습니다.', 'The document list could not be loaded.'], libraryFailedBody: ['잠시 후 목록을 새로고침하세요.', 'Refresh the list and try again.'],
    loginTitle: ['관리자 로그인', 'Administrator sign in'], loginDescription: ['문서 등록·공개 범위 관리·삭제에 사용하는 관리자 계정으로 로그인하세요.', 'Sign in with an administrator account to store and delete documents and choose their visibility.'],
    username: ['아이디', 'Username'], password: ['비밀번호', 'Password'], usernamePlaceholder: ['아이디 입력', 'Enter your username'], passwordPlaceholder: ['비밀번호 입력', 'Enter your password'],
    showPassword: ['비밀번호 표시', 'Show password'], hidePassword: ['비밀번호 숨기기', 'Hide password'], loggingIn: ['로그인 중', 'Signing in'],
    usernameRequired: ['아이디를 입력해 주세요.', 'Enter your username.'], passwordRequired: ['비밀번호를 입력해 주세요.', 'Enter your password.'],
    loginNote: ['질문과 공개 규정 열람은 로그인 없이 이용할 수 있습니다.', 'You can ask questions and browse public regulations without signing in.'], backToAsk: ['질문으로 돌아가기', 'Back to questions'],
    tokenStorageError: ['로그인 정보를 저장할 수 없습니다. 브라우저의 저장 허용 여부를 확인하세요.', 'Your session could not be saved. Check browser storage permissions.'],
    documentsTitle: ['문서 관리', 'Document management'], documentsDescription: ['PDF 원본을 저장하고 공개 범위를 관리합니다.', 'Store original PDFs and manage document visibility.'],
    loginRequired: ['로그인 필요', 'Sign in required'], adminRequired: ['문서 관리에는 관리자 로그인이 필요합니다.', 'Sign in as an administrator to manage documents.'], adminLogin: ['관리자 로그인', 'Administrator sign in'],
    sessionChecking: ['로그인 확인 중', 'Checking session'], sessionVerified: ['관리자 로그인됨', 'Administrator signed in'], managementUnavailable: ['문서 관리 연결 불가', 'Document service unavailable'],
    managementChecking: ['관리자 세션과 문서 목록을 확인하고 있습니다.', 'Checking your administrator session and documents.'],
    uploadTitle: ['PDF 등록', 'Store PDFs'], uploadDescription: ['PDF만 등록할 수 있습니다. 파일당 최대 20 MiB.', 'PDF files only. Maximum 20 MiB per file.'],
    choosePdfs: ['PDF를 선택하거나 여기에 놓으세요', 'Choose PDFs or drop them here'], multipleFiles: ['여러 파일 선택 가능 · 원본 저장', 'Select multiple files · original storage'], chooseFiles: ['파일 선택', 'Choose files'], chooseFile: ['파일 선택', 'Choose file'],
    optionalTitle: ['제목 (선택)', 'Title (optional)'], titlePlaceholder: ['생략하면 파일명으로 구분합니다', 'Leave blank to use the filename'],
    visibilityHint: ['기본값은 비공개입니다. 공개 문서는 로그인 없이 열람할 수 있습니다.', 'Private by default. Public documents can be opened without signing in.'],
    multipleTitleHint: ['여러 파일을 선택하면 별도 제목 입력을 생략합니다.', 'A custom title is omitted when multiple files are selected.'],
    noFilesSelected: ['선택한 파일 없음', 'No files selected'], noFileSelected: ['선택한 파일 없음', 'No file selected'], storePdfs: ['PDF 저장', 'Store PDFs'], storing: ['저장 중', 'Storing'],
    filesSelected: ['{count}개 파일 선택', '{count} files selected'], fileQueued: ['대기', 'Queued'], fileTransferring: ['전송 중', 'Transferring'], fileTransfer: ['전송 중 {percent}%', 'Transferring {percent}%'], fileProcessing: ['전송 완료 · 저장 확인 중', 'Transferred · confirming storage'], fileStored: ['원본 저장됨', 'Original stored'],
    removeSelection: ['{filename} 선택 취소', 'Remove {filename}'], fileProgress: ['{filename} 전송 진행률', 'Transfer progress for {filename}'], fileDuplicateSelection: ['이미 선택한 파일입니다.', 'This file is already selected.'],
    storedToast: ['{count}개 PDF 원본이 저장되었습니다.', '{count} original PDFs stored.'],
    conflictTitle: ['문서 충돌 검토', 'Document conflict review'], conflictDescription: ['선택한 PDF와 기존 규정의 충돌 가능성을 검토합니다.', 'Review potential conflicts between a PDF and existing regulations.'], chooseConflictPdf: ['검토할 PDF 선택', 'Choose a PDF to review'],
    conflictNote: ['검토는 참고용이며 담당자의 확인이 필요합니다. 원본 저장 상태와 분석 가능 여부는 별개입니다.', 'Review results need human verification. Original storage and analysis availability are separate.'], startConflict: ['충돌 검토', 'Review conflicts'],
    conflictPending: ['문서 충돌 가능성을 검토하고 있습니다.', 'Reviewing potential document conflicts.'], conflictPendingBody: ['분석 결과가 제공되면 아래에 표시됩니다.', 'Analysis results will appear below when available.'], conflictResult: ['검토 결과', 'Review results'], conflictReviewNote: ['담당자 확인 필요', 'Human verification required'],
    noConflicts: ['충돌 항목이 반환되지 않았습니다. 충돌이 없음을 보장하는 결과는 아닙니다.', 'No conflict items were returned. This does not confirm the absence of conflicts.'], recommendations: ['추가 확인 사항', 'Recommended checks'],
    priorityHigh: ['높음', 'High'], priorityMedium: ['보통', 'Medium'], priorityLow: ['낮음', 'Low'], priorityUnknown: ['미제공', 'Not provided'], priority: ['검토 우선도 {level}', 'Review priority: {level}'], reviewItem: ['검토 항목', 'Review item'],
    storedDocuments: ['저장된 문서', 'Stored documents'], allVisibility: ['모든 공개 범위', 'All visibility'], storedNote: ['저장 완료는 PDF 원본의 보관을 뜻합니다. 답변 검색이나 분석 연결 상태는 별도로 표시합니다.', 'Stored means the original PDF is retained. Answer search and analysis connection status are shown separately.'],
    documentsLocked: ['로그인 후 문서 목록을 확인할 수 있습니다.', 'Sign in to view stored documents.'], storedEmpty: ['저장된 PDF가 없습니다.', 'No PDFs have been stored.'], storedEmptyBody: ['PDF를 선택해 등록하세요.', 'Choose a PDF to store.'],
    deleteTitle: ['PDF를 삭제할까요?', 'Delete this PDF?'], deleteWarning: ['원본과 문서 등록 정보가 삭제됩니다. 파일명을 확인하세요.', 'The original and its document record will be deleted. Check the filename before continuing.'], cancel: ['취소', 'Cancel'], deleteDocument: ['문서 삭제', 'Delete document'], deleteNamed: ['{filename} 삭제', 'Delete {filename}'], deleting: ['삭제 중', 'Deleting'], deleted: ['문서가 삭제되었습니다.', 'Document deleted.'],
    finishTask: ['진행 중인 작업을 마친 뒤 시도하세요.', 'Wait for the current operation to finish.'], loggedOut: ['로그아웃했습니다.', 'Signed out.'],
    errorRag: ['답변 서비스가 아직 연결되지 않았습니다.', 'The answer service is not connected yet.'], errorRagBody: ['규정 목록과 공개 PDF 원본은 계속 이용할 수 있습니다.', 'The regulation library and public PDFs remain available.'],
    errorAnalysisNotConnected: ['문서 분석 서비스가 아직 연결되지 않았습니다.', 'The document analysis service is not connected yet.'],
    errorAdminUnconfigured: ['관리자 로그인이 아직 설정되지 않았습니다.', 'Administrator sign-in has not been configured yet.'],
    errorCredentials: ['아이디 또는 비밀번호를 확인하세요.', 'Check your username and password.'], errorSession: ['로그인이 만료되었거나 확인되지 않았습니다. 다시 로그인하세요.', 'Your session has expired or could not be verified. Sign in again.'],
    errorForbidden: ['이 계정에는 문서 관리 권한이 없습니다.', 'This account does not have document management permission.'],
    errorDuplicate: ['이미 저장된 PDF입니다. 기존 문서 목록을 확인하세요.', 'This PDF is already stored. Check the existing document list.'],
    errorOversize: ['파일은 20 MiB 이하여야 합니다.', 'The file must be 20 MiB or smaller.'], errorInvalidPdf: ['유효한 PDF 파일을 선택하세요.', 'Choose a valid PDF file.'],
    errorDocumentId: ['유효한 문서 식별자가 제공되지 않았습니다.', 'A valid document ID was not provided.'], errorFileMissing: ['PDF 원본을 찾을 수 없습니다.', 'The original PDF could not be found.'],
    errorTimeout: ['요청 시간이 초과되었습니다. 다시 시도하세요.', 'The request timed out. Try again.'], errorConnection: ['서비스에 연결할 수 없습니다. 잠시 후 다시 시도하세요.', 'Could not connect to the service. Try again shortly.'],
    errorResponse: ['서비스 응답을 확인할 수 없습니다.', 'The service response could not be verified.'], errorQueryUnavailable: ['지금은 답변을 불러올 수 없습니다.', 'The answer could not be loaded right now.'],
    errorUpload: ['PDF를 저장하지 못했습니다.', 'The PDF could not be stored.'], errorDelete: ['문서를 삭제하지 못했습니다. 다시 시도하세요.', 'The document could not be deleted. Try again.'], errorConflict: ['충돌 검토를 완료하지 못했습니다.', 'The conflict review could not be completed.'], errorExport: ['파일을 내보내지 못했습니다.', 'The file could not be exported.'], errorLogin: ['지금은 로그인할 수 없습니다.', 'Sign-in is unavailable right now.'], errorOriginal: ['PDF 원본을 열 수 없습니다.', 'The original PDF could not be opened.'],
    requestId: ['문의 번호: {id}', 'Request ID: {id}']
  };
  function read(key, fallback) { try { const value = localStorage.getItem(key); return value === null ? fallback : value.replace(/^"|"$/g, ''); } catch { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, value); } catch { /* The interface also works without persistence. */ } }
  let language = read('hsu_language', 'ko') === 'en' ? 'en' : 'ko';
  let audience = read('hsu_audience', read('hsu_preference', 'student')) === 'staff' ? 'staff' : 'student';
  const t = (key, values = {}) => {
    const template = copy[key]?.[language === 'ko' ? 0 : 1] || key;
    return template.replace(/\{(\w+)\}/g, (match, name) => values[name] === undefined ? match : String(values[name]));
  };
  function apply(root = document) {
    document.documentElement.lang = language;
    root.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = t(element.dataset.i18n); });
    [['data-i18n-placeholder', 'placeholder'], ['data-i18n-label', 'aria-label'], ['data-i18n-title', 'title']].forEach(([source, target]) => { root.querySelectorAll(`[${source}]`).forEach(element => element.setAttribute(target, t(element.getAttribute(source)))); });
    document.querySelectorAll('[data-language]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.language === language)));
    document.querySelectorAll('[data-audience]').forEach(select => { select.value = audience; });
  }
  function changed(kind) { apply(); window.dispatchEvent(new CustomEvent('hsrule:settings', { detail: { kind, language, audience } })); }
  function setLanguage(value) { if (!['ko', 'en'].includes(value)) return; language = value; save('hsu_language', language); changed('language'); }
  function setAudience(value) { audience = value === 'staff' ? 'staff' : 'student'; save('hsu_audience', audience); changed('audience'); }
  document.querySelectorAll('[data-language]').forEach(button => button.addEventListener('click', () => setLanguage(button.dataset.language)));
  document.querySelectorAll('[data-audience]').forEach(select => select.addEventListener('change', () => setAudience(select.value)));
  window.HSRuleI18n = Object.freeze({ t, apply, setLanguage, setAudience, get language() { return language; }, get audience() { return audience; }, keys: Object.keys(copy) });
  apply();
  if (document.fonts) {
    document.documentElement.dataset.fontState = 'loading';
    document.fonts.load('400 16px "SUIT Variable"', '한성대학교 HS-RULE').then(fonts => { document.documentElement.dataset.fontState = fonts.length && document.fonts.check('400 16px "SUIT Variable"', '한성대학교 HS-RULE') ? 'loaded' : 'unavailable'; }).catch(() => { document.documentElement.dataset.fontState = 'unavailable'; });
  }
})();
