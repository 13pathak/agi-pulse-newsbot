const HOSTS = new Set(["x.com", "twitter.com"]);
const HOST_PATTERNS = ["https://x.com/*", "https://twitter.com/*"];
const STORAGE_KEY = "recentlyCreatedTabs";
const FRESH_TAB_MS = 60_000;

function xUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && HOSTS.has(url.hostname) && !url.port
      && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}

export function intentUrl(value) {
  const url = xUrl(value);
  return url && /^\/intent\/(tweet|post)\/?$/.test(url.pathname)
    && url.searchParams.get("text")?.trim() ? url.href : null;
}

function blankUrl(value) {
  return !value || ["about:blank", "chrome://newtab/", "brave://newtab/", "chrome://new-tab-page/"].includes(value);
}

export function reusableTab(tab, sourceId) {
  const url = xUrl(tab.pendingUrl || tab.url);
  return tab.id !== sourceId && Number.isInteger(tab.id) && !tab.incognito
    && !tab.discarded && tab.status === "complete" && !tab.pendingUrl && url !== null
    && !/^\/(?:i\/)?(?:compose|intent|flow)(?:\/|$)/.test(url.pathname);
}

// Runs only in the existing X tab. Return a boolean and URL, never any draft text.
// Generic edit controls protect inline posts, replies, messages and search fields.
export function inspectXDocument() {
  const editors = document.querySelectorAll(
    '[contenteditable="true"], textarea, input[type="text"], input[type="search"], input:not([type])',
  );
  const editing = [...editors].some((element) =>
    String(element.value ?? element.textContent ?? "").trim().length > 0);
  const attachments = document.querySelector('[data-testid="attachments"]');
  const files = [...document.querySelectorAll('input[type="file"]')]
    .some((element) => element.files?.length > 0);
  return {
    url: location.href,
    safe: document.readyState === "complete" && !editing && !files
      && !attachments?.childElementCount
      && !document.querySelector('[role="dialog"], [aria-modal="true"]'),
  };
}

export function createRouter(api, now = Date.now) {
  let serial = Promise.resolve();
  let recent;

  async function records() {
    if (!recent) {
      const stored = (await api.storage.session.get(STORAGE_KEY))[STORAGE_KEY];
      recent = stored && typeof stored === "object" ? stored : {};
    }
    for (const [id, createdAt] of Object.entries(recent)) {
      if (typeof createdAt !== "number" || now() - createdAt > FRESH_TAB_MS || createdAt > now()) {
        delete recent[id];
      }
    }
    return recent;
  }

  async function persist() {
    // Session storage contains only tab IDs and timestamps, never draft text or URLs.
    await api.storage.session.set({ [STORAGE_KEY]: recent });
  }

  function enqueue(task) {
    const result = serial.then(task).catch(() => {
      // Failure must never close the incoming draft or interfere with normal X links.
      console.warn("AGI Pulse: tab reuse unavailable; kept the incoming tab.");
      return "kept";
    });
    serial = result;
    return result;
  }

  async function stillIncoming(id, url) {
    const tab = await api.tabs.get(id);
    return !tab.incognito && intentUrl(tab.pendingUrl || tab.url) === url;
  }

  async function reuse(id, url) {
    if (!await stillIncoming(id, url)) return "kept";
    const tabs = await api.tabs.query({ url: HOST_PATTERNS, windowType: "normal" });
    const candidates = tabs.filter((tab) => reusableTab(tab, id))
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0) || a.id - b.id);
    for (const candidate of candidates) {
      let current;
      try {
        const [check] = await api.scripting.executeScript({
          target: { tabId: candidate.id }, func: inspectXDocument,
        });
        current = await api.tabs.get(candidate.id);
        if (!check?.result?.safe || check.result.url !== current.url || !reusableTab(current, id)) continue;
      } catch {
        continue; // Loading, inaccessible or closed tabs are not safe to replace.
      }
      if (!await stillIncoming(id, url)) return "kept";
      delete recent[current.id]; // Our navigation must not be treated as a new incoming link.
      await persist();
      await api.tabs.update(current.id, { url, active: true });
      await api.windows.update(current.windowId, { focused: true });
      // The user may have navigated the incoming tab while the target was opening.
      if (await stillIncoming(id, url)) await api.tabs.remove(id);
      return "reused";
    }
    return "kept"; // No available X tab: the browser already opened the required new one.
  }

  async function consider(id, value, complete = false) {
    await records();
    if (!(id in recent)) return "ignored";
    const url = intentUrl(value);
    if (url) {
      delete recent[id];
      await persist();
      return reuse(id, url);
    }
    if (!blankUrl(value) || (complete && !value)) {
      // Once another page has loaded, this is the user's tab; never close it later.
      delete recent[id];
      await persist();
    }
    return "ignored";
  }

  return {
    created: (tab) => enqueue(async () => {
      if (!Number.isInteger(tab.id) || tab.incognito || tab.discarded) return "ignored";
      await records();
      recent[tab.id] = now();
      // Bound metadata even when many blank tabs are opened at once.
      recent = Object.fromEntries(Object.entries(recent).sort((a, b) => b[1] - a[1]).slice(0, 256));
      await persist();
      return consider(tab.id, tab.pendingUrl || tab.url);
    }),
    updated: (id, change, tab) => enqueue(() =>
      consider(id, change.url || tab.pendingUrl || tab.url, change.status === "complete")),
    removed: (id) => enqueue(async () => {
      await records();
      delete recent[id];
      await persist();
    }),
  };
}
