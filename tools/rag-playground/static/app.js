"use strict";
const $ = (id) => document.getElementById(id);
const pretty = (value) => JSON.stringify(value, null, 2);
let config;
let latest;
let busy = false;

function status(message, state = "") {
  $("status").textContent = message;
  $("status").dataset.state = state;
}

function history() {
  const messages = JSON.parse($("history").value);
  if (!Array.isArray(messages) || messages.length > 12 || messages.some((m) =>
    !m || !["user", "assistant"].includes(m.role) || typeof m.content !== "string" ||
    !m.content.trim() || [...m.content].length > 4000 || Object.keys(m).sort().join() !== "content,role")) {
    throw new Error("대화 이력은 user/assistant 메시지 최대 12개, 각 1~4,000자여야 합니다.");
  }
  return messages;
}

function countHistory() {
  try { $("history-count").textContent = `이력 ${history().length} / 12`; }
  catch { $("history-count").textContent = "이력 형식 확인 필요"; }
}

function setBusy(value) {
  busy = value;
  for (const node of document.querySelectorAll("#query-form button, #query-form textarea, #language, #reset, [data-question], #followups button")) node.disabled = value;
  $("submit").textContent = value ? "응답 기다리는 중…" : "API에 질문하기 ↗";
}

async function api(path, data) {
  const response = await fetch(path, data ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `테스트 도구 오류: HTTP ${response.status}`);
  return result;
}

function sourceLink(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    // API sources use root-relative /documents paths, including behind /api.
    const url = value.startsWith("/") && !value.startsWith("//")
      ? new URL(config.browser_api_url + value)
      : new URL(value, config.browser_api_url + "/");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function renderSources(sources) {
  $("sources").replaceChildren();
  const heading = document.createElement("h3");
  heading.className = "sources-title";
  heading.textContent = `근거 ${sources.length}개`;
  $("sources").append(heading);
  for (const [index, source] of sources.entries()) {
    if (!source || typeof source !== "object") continue;
    const article = document.createElement("article");
    article.className = "source";
    const title = document.createElement("h3");
    title.textContent = `${source.label || index + 1} · ${source.title || source.regulation_id || "원문 근거"}`;
    const meta = document.createElement("p");
    meta.textContent = [source.article_id, source.page_start ? `p.${source.page_start}–${source.page_end || source.page_start}` : null,
      source.revision_id ? `개정 ${source.revision_id}` : null, source.score != null ? `score ${source.score}` : null].filter(Boolean).join(" · ");
    article.append(title, meta);
    if (typeof source.text === "string" || typeof source.excerpt === "string") {
      const excerpt = document.createElement("p");
      excerpt.textContent = source.text || source.excerpt;
      article.append(excerpt);
    }
    const href = sourceLink(source.official_url || source.url);
    if (href) {
      const link = document.createElement("a");
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "원문 열기 ↗";
      article.append(link);
    }
    $("sources").append(article);
  }
}

async function checkHealth() {
  $("check-health").disabled = true;
  $("live-status").textContent = "확인 중…";
  $("ready-status").textContent = "확인 중…";
  try {
    const result = await api("/api/health");
    $("health-json").textContent = pretty(result);
    for (const [name, id] of [["live", "live-status"], ["ready", "ready-status"]]) {
      const entry = result[name];
      const ok = entry.status === 200 && entry.body?.ok === true;
      $(id).textContent = ok ? (name === "live" ? "실행 중 · HTTP 200" : "준비 완료 · HTTP 200") :
        `${entry.status ? `HTTP ${entry.status}` : "연결 안 됨"} · ${entry.body?.code || "응답 형식 확인"}`;
    }
  } catch (error) {
    $("live-status").textContent = "확인 실패";
    $("ready-status").textContent = "확인 실패";
    $("health-json").textContent = error.message;
  } finally { $("check-health").disabled = false; }
}

$("query-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !config) return;
  let request;
  try {
    request = {question: $("question").value.trim(), messages: history(), language: $("language").value};
    if (!request.question || [...request.question].length > 2000) throw new Error("질문을 1~2,000자로 입력하세요.");
  } catch (error) { status(error.message, "error"); return; }
  setBusy(true);
  $("download").disabled = true;
  $("request-json").textContent = pretty(request);
  $("response-json").textContent = "응답을 기다리고 있습니다…";
  $("answer").textContent = "";
  $("sources").replaceChildren();
  $("followups").replaceChildren();
  $("metrics").hidden = true;
  status("실제 API에 요청했습니다. 모델 응답을 기다립니다.");
  try {
    const result = await api("/api/query", request);
    latest = {timestamp: new Date().toISOString(), api_url: config.api_url, request, response: result};
    $("download").disabled = false;
    $("response-json").textContent = pretty(result);
    $("metrics").hidden = false;
    $("http-status").textContent = result.status ?? "연결 실패";
    $("elapsed").textContent = `${(result.elapsed_ms / 1000).toFixed(2)}초`;
    $("request-id").textContent = result.request_id || result.body?.request_id || "없음";
    $("found").textContent = typeof result.body?.found === "boolean" ? (result.body.found ? "예" : "아니요") : "—";
    const body = result.body;
    if (result.status !== 200 || !body || typeof body.answer !== "string" || !body.answer.trim() || typeof body.found !== "boolean" || !Array.isArray(body.sources)) {
      status(`요청 실패 · ${body?.code || "응답 형식 확인 필요"}. 아래 JSON을 확인하세요.`, "error");
      $("answer").textContent = typeof body?.detail === "string" ? body.detail : "정상 답변을 받지 못했습니다. 실패한 질문은 이력에 추가하지 않습니다.";
      return;
    }
    $("answer").classList.remove("empty");
    $("answer").textContent = body.answer;
    renderSources(body.sources);
    status(body.found ? "답변과 근거를 받았습니다. 원문을 열어 내용을 확인하세요." : "답변을 받았지만 근거를 찾지 못했습니다.", "success");
    if ([...body.answer].length <= 4000) {
      $("history").value = pretty([...request.messages, {role: "user", content: request.question}, {role: "assistant", content: body.answer}].slice(-12));
      countHistory();
    } else { status("답변을 받았습니다. 4,000자를 초과해 이번 대화는 이력에 자동 추가하지 않았습니다."); }
    for (const followup of Array.isArray(body.followups) ? body.followups : []) {
      if (typeof followup !== "string" || !followup.trim() || [...followup].length > 2000) continue;
      const button = document.createElement("button");
      button.type = "button"; button.textContent = followup;
      button.addEventListener("click", () => { $("question").value = followup; updateCount(); $("question").focus(); });
      $("followups").append(button);
    }
  } catch (error) {
    status(`테스트 도구 연결 오류 · ${error.message}`, "error");
    $("response-json").textContent = pretty({error: error.message});
  } finally { setBusy(false); }
});

function updateCount() { $("char-count").textContent = `${[...$("question").value].length} / 2,000`; }
$("question").addEventListener("input", updateCount);
$("history").addEventListener("input", countHistory);
$("check-health").addEventListener("click", checkHealth);
for (const button of document.querySelectorAll("[data-question]")) button.addEventListener("click", () => {
  $("question").value = button.dataset.question;
  updateCount(); $("question").focus();
});
$("reset").addEventListener("click", () => {
  $("history").value = "[]"; $("question").value = ""; latest = null;
  countHistory(); updateCount();
  $("answer").textContent = "질문을 보내면 실제 답변과 근거가 여기에 표시됩니다.";
  $("answer").classList.add("empty");
  $("sources").replaceChildren(); $("followups").replaceChildren();
  $("request-json").textContent = "{}"; $("response-json").textContent = "{}";
  $("metrics").hidden = true; $("download").disabled = true;
  status("새 대화를 시작합니다."); $("question").focus();
});
$("download").addEventListener("click", () => {
  if (!latest) return;
  const url = URL.createObjectURL(new Blob([pretty(latest)], {type: "application/json"}));
  const link = document.createElement("a"); link.href = url; link.download = "hs-rule-api-result.json";
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
api("/api/config").then((value) => {
  config = value; $("api-url").textContent = config.api_url;
  $("submit").disabled = false; checkHealth();
}).catch((error) => { $("api-url").textContent = "설정 확인 실패"; status(error.message, "error"); });
