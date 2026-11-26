import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import vm from "node:vm";

// Run the actual UI helpers with a tiny DOM stand-in; no browser or npm install.
const source = readFileSync(new URL("./static/app.js", import.meta.url), "utf8");
function ui() {
  const nodes = new Map();
  const context = vm.createContext({
    URL,
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, {value: "[]", addEventListener() {}});
        return nodes.get(id);
      },
      querySelectorAll() { return []; },
    },
    fetch: () => new Promise(() => {}), // Leave boot-time health checks outside helper tests.
  });
  vm.runInContext(source, context);
  vm.runInContext('config = {browser_api_url: "http://localhost:8090/api"}', context);
  return (code) => vm.runInContext(code, context);
}

test("PDF source paths retain the configured API prefix", () => {
  const run = ui();
  assert.equal(run('sourceLink("/documents/abc/file#page=3")'), "http://localhost:8090/api/documents/abc/file#page=3");
});

test("official absolute URLs remain intact; executable or credential URLs have no link", () => {
  const run = ui();
  assert.equal(run('sourceLink("https://example.com/rules/33")'), "https://example.com/rules/33");
  for (const url of ["javascript:alert(1)", "data:text/html,test", "https://user:secret@example.com", ""]) {
    assert.equal(run(`sourceLink(${JSON.stringify(url)})`), null);
  }
});

test("history validates the public API roles and length limits", () => {
  const run = ui();
  const setHistory = (messages) => run(`$("history").value = ${JSON.stringify(JSON.stringify(messages))}`);
  setHistory([{role: "user", content: "휴학"}, {role: "assistant", content: "안내"}]);
  assert.equal(run("history().length"), 2);
  for (const messages of [[{role: "system", content: "no"}], [{role: "user", content: "x".repeat(4001)}],
    Array.from({length: 13}, () => ({role: "user", content: "ok"}))]) {
    setHistory(messages);
    assert.throws(() => run("history()"));
  }
});
