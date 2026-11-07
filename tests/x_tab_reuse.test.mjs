import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { createRouter, inspectXDocument, intentUrl, reusableTab } from "../desktop/x-tab-reuse/router.mjs";

const URL_A = `https://x.com/intent/tweet?text=${encodeURIComponent("AI update: नमस्ते & hello 🚀")}`;
const URL_B = "https://x.com/intent/tweet?text=Another%20story";
const tab = (id, url = "https://x.com/home", extra = {}) => ({
  id, url, windowId: 1, status: "complete", incognito: false, lastAccessed: id, ...extra,
});

function browser(initial = []) {
  const tabs = new Map(initial.map((item) => [item.id, structuredClone(item)]));
  const storage = {};
  const events = [];
  const unsafe = new Set();
  let clock = 1000;
  const api = {
    storage: { session: {
      get: async () => structuredClone(storage),
      set: async (value) => Object.assign(storage, structuredClone(value)),
    } },
    tabs: {
      query: async () => [...tabs.values()].map((item) => structuredClone(item)),
      get: async (id) => {
        if (!tabs.has(id)) throw new Error("Tab closed");
        return structuredClone(tabs.get(id));
      },
      update: async (id, update) => {
        if (!tabs.has(id)) throw new Error("Tab closed");
        events.push(["update", id, update]);
        Object.assign(tabs.get(id), update, { pendingUrl: update.url, status: "loading" });
      },
      remove: async (id) => { events.push(["remove", id]); tabs.delete(id); },
    },
    windows: { update: async (id, update) => { events.push(["focus", id, update]); } },
    scripting: { executeScript: async ({ target, func }) => {
      assert.equal(func, inspectXDocument);
      return [{ result: { url: tabs.get(target.tabId)?.url, safe: !unsafe.has(target.tabId) } }];
    } },
  };
  return {
    api, tabs, storage, events, unsafe,
    router: createRouter(api, () => clock),
    restart: () => createRouter(api, () => clock),
    advance: (ms) => { clock += ms; },
    incoming: (id = 9, url = URL_A, extra = {}) => {
      const item = tab(id, url, { status: "loading", ...extra });
      tabs.set(id, item);
      return item;
    },
  };
}

test("reuse an existing tab, preserve exact draft text, focus its window, then close only the incoming tab", async () => {
  const b = browser([tab(1, "https://x.com/home", { windowId: 4 })]);
  assert.equal(await b.router.created(b.incoming()), "reused");
  assert.deepEqual(b.events, [
    ["update", 1, { url: URL_A, active: true }], ["focus", 4, { focused: true }], ["remove", 9],
  ]);
  assert.equal(b.tabs.size, 1);
  assert.equal(new URL(b.tabs.get(1).url).searchParams.get("text"), "AI update: नमस्ते & hello 🚀");
});

test("without an existing X tab, keep the tab the browser already opened", async () => {
  const b = browser([tab(1, "https://example.org/")]);
  assert.equal(await b.router.created(b.incoming()), "kept");
  assert.deepEqual(b.events, []);
  assert.equal(b.tabs.get(9).url, URL_A);
});

test("prefer the most recently used available X tab across windows", async () => {
  const b = browser([tab(1), tab(2, "https://twitter.com/home", { lastAccessed: 100, windowId: 5 })]);
  await b.router.created(b.incoming());
  assert.equal(b.events[0][1], 2);
  assert.equal(b.tabs.get(1).url, "https://x.com/home");
});

test("preserve unsent inline work and try another available tab", async () => {
  const b = browser([tab(1), tab(2)]);
  b.unsafe.add(2);
  await b.router.created(b.incoming());
  assert.equal(b.events[0][1], 1);
  assert.equal(b.tabs.get(2).url, "https://x.com/home");
});

test("keep the incoming draft when all existing tabs are busy or inaccessible", async () => {
  for (const condition of ["editing", "inaccessible"]) {
    const b = browser([tab(1)]);
    if (condition === "editing") b.unsafe.add(1);
    else b.api.scripting.executeScript = async () => { throw new Error("Cannot inspect"); };
    assert.equal(await b.router.created(b.incoming()), "kept");
    assert.deepEqual(b.events, []);
  }
});

test("do not reuse compose, login, loading, discarded, private, or unrelated tabs", () => {
  const excluded = [
    tab(1, URL_A), tab(1, "https://x.com/compose/post"), tab(1, "https://x.com/i/flow/login"),
    tab(1, "https://x.com/home", { status: "loading" }), tab(1, "https://x.com/home", { discarded: true }),
    tab(1, "https://x.com/home", { incognito: true }), tab(1, "https://x.com.evil.test/"),
    tab(1, "https://x.com/home", { pendingUrl: "https://x.com/settings" }),
  ];
  for (const item of excluded) assert.equal(reusableTab(item, 9), false);
});

test("recognize only HTTPS X intent links with a draft", () => {
  for (const value of ["https://x.com.evil.test/intent/tweet?text=x", "http://x.com/intent/tweet?text=x",
    "https://x.com/intent/tweet", "https://x.com/home?text=x", "https://user@x.com/intent/tweet?text=x",
    "https://x.com:8443/intent/tweet?text=x", "not a URL"]) assert.equal(intentUrl(value), null);
  assert.equal(intentUrl(URL_A), URL_A);
  assert.ok(intentUrl("https://twitter.com/intent/post?text=hello"));
});

test("a blank new tab can resolve to an intent after the worker restarts", async () => {
  const b = browser([tab(1)]);
  await b.router.created(b.incoming(9, "about:blank"));
  const restarted = b.restart();
  b.tabs.get(9).url = URL_A;
  assert.equal(await restarted.updated(9, { url: URL_A }, b.tabs.get(9)), "reused");
  assert.equal(b.tabs.size, 1);
  assert.ok(!JSON.stringify(b.storage).includes("text="));
});

test("never close an existing tab just because it later navigates to an intent", async () => {
  const b = browser([tab(1), tab(9, URL_A)]);
  assert.equal(await b.router.updated(9, { url: URL_A }, b.tabs.get(9)), "ignored");
  assert.deepEqual(b.events, []);
});

test("a new tab becomes user-owned after a different initial page loads", async () => {
  const b = browser([tab(1)]);
  await b.router.created(b.incoming(9, "https://example.org/"));
  b.tabs.get(9).url = URL_A;
  await b.router.updated(9, { url: URL_A }, b.tabs.get(9));
  assert.deepEqual(b.events, []);
});

test("blank-tab tracking expires instead of closing a tab much later", async () => {
  const b = browser([tab(1)]);
  await b.router.created(b.incoming(9, "about:blank"));
  b.advance(61_000);
  b.tabs.get(9).url = URL_A;
  await b.router.updated(9, { url: URL_A }, b.tabs.get(9));
  assert.deepEqual(b.events, []);
});

test("duplicate events cannot route or close the same incoming tab twice", async () => {
  const b = browser([tab(1)]);
  const incoming = b.incoming();
  await Promise.all([b.router.created(incoming), b.router.updated(9, { url: URL_A }, incoming)]);
  assert.equal(b.events.filter(([kind]) => kind === "remove").length, 1);
});

test("two rapid clicks do not replace the first draft with the second", async () => {
  const b = browser([tab(1)]);
  await Promise.all([b.router.created(b.incoming(9, URL_A)), b.router.created(b.incoming(10, URL_B))]);
  assert.equal(b.tabs.get(1).url, URL_A);
  assert.equal(b.tabs.get(10).url, URL_B);
});

test("recheck a target which navigated while its page was being inspected", async () => {
  const b = browser([tab(1)]);
  b.api.scripting.executeScript = async () => {
    b.tabs.get(1).url = "https://x.com/compose/post";
    return [{ result: { url: "https://x.com/home", safe: true } }];
  };
  await b.router.created(b.incoming());
  assert.deepEqual(b.events, []);
});

test("navigation failure retains the incoming draft", async () => {
  const b = browser([tab(1)]);
  b.api.tabs.update = async () => { throw new Error("Target vanished"); };
  assert.equal(await b.router.created(b.incoming()), "kept");
  assert.equal(b.tabs.get(9).url, URL_A);
  assert.deepEqual(b.events, []);
});

test("do not close the source if the user changed it while routing", async () => {
  const b = browser([tab(1)]);
  b.api.windows.update = async () => { b.tabs.get(9).url = "https://example.org/"; };
  await b.router.created(b.incoming());
  assert.equal(b.tabs.get(9).url, "https://example.org/");
  assert.ok(!b.events.some(([kind]) => kind === "remove"));
});

test("draft guard checks inline text, selected files, attachments and dialogs", () => {
  for (const kind of ["empty", "text", "file", "attachments", "dialog", "loading"]) {
    const result = runInNewContext(`(${inspectXDocument.toString()})()`, {
      location: { href: "https://x.com/home" },
      document: {
        readyState: kind === "loading" ? "loading" : "complete",
        querySelectorAll: (selector) => selector === 'input[type="file"]'
          ? [{ files: kind === "file" ? [{}] : [] }]
          : [{ textContent: kind === "text" ? "Unsent personal draft" : "" }],
        querySelector: (selector) => selector.includes("attachments")
          ? { childElementCount: kind === "attachments" ? 1 : 0 }
          : kind === "dialog" ? {} : null,
      },
    });
    assert.equal(result.safe, kind === "empty");
    assert.deepEqual(Object.keys(result).sort(), ["safe", "url"]);
  }
});

test("extension permissions are restricted to X and do not modify Telegram links", async () => {
  const manifest = JSON.parse(await readFile(new URL("../desktop/x-tab-reuse/manifest.json", import.meta.url)));
  assert.deepEqual(manifest.host_permissions, ["https://x.com/*", "https://twitter.com/*"]);
  assert.equal(manifest.incognito, "not_allowed");
  assert.deepEqual(manifest.permissions, ["scripting", "storage"]);
});
