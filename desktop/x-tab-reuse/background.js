import { createRouter } from "./router.mjs";

const router = createRouter(chrome);

// Register synchronously so a sleeping MV3 worker receives the initiating event.
chrome.tabs.onCreated.addListener((tab) => { void router.created(tab); });
chrome.tabs.onUpdated.addListener((id, change, tab) => { void router.updated(id, change, tab); });
chrome.tabs.onRemoved.addListener((id) => { void router.removed(id); });
