# Reuse an X tab in Brave

This optional desktop extension handles the bot's existing **Post on X** links. It uses an available X tab in the same browser profile, focuses its window, and closes the extra tab opened by Telegram. If there is no available X tab, it leaves the newly opened tab in place. You still review and publish the post yourself.

The Telegram button and its URL are unchanged. Android's X app and browsers without this extension keep their existing behavior.

## Install once

1. Open `brave://extensions` in the Brave profile used for Telegram links.
2. Enable **Developer mode** if it is not already enabled.
3. Choose **Load unpacked** and select this directory, the one containing `manifest.json`:
   `C:\Hotmail\OneDrive\Extensions\agi-pulse-newsbot\desktop\x-tab-reuse`
4. Confirm **AGI Pulse — Reuse X Tab** is enabled. After updating its files, use the extension's **Reload** button.

It also works as an unpacked extension in current Chrome and Edge. Installation is per browser profile; installing it in one profile cannot control another profile or another browser.

## What to expect

- Open X to its home page, then click **Post on X** in Telegram: the existing X tab should receive the prefilled draft and become active.
- With no X tab open, the new X tab remains. The extension never creates a second replacement tab.
- A new tab may appear briefly before the extension switches to the existing one. A Telegram URL cannot prevent the browser from initially opening a tab.
- An existing composer, dialog, nonempty edit field, attached file or loading/inaccessible page is preserved. If every existing X tab is busy, the incoming draft stays in its new tab rather than replacing unfinished work. Two rapid clicks likewise preserve separate drafts.
- Only newly created tabs opening a supported HTTPS X posting URL are eligible for removal. Existing tabs navigating normally are left alone. Private windows are excluded.
- The helper also applies to matching X posting links opened from other apps; it cannot identify a Telegram sender from the URL alone.

## Permissions and privacy

Access is restricted to `https://x.com/*` and `https://twitter.com/*`. The scripting permission checks whether a candidate tab has unfinished editing work; it returns only the page URL and a safe/unsafe flag. Session storage retains only recent tab IDs and timestamps, not draft text. There are no analytics, remote scripts, extra network requests or automatic post submissions.

The browser APIs used are documented in Chrome's [Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs) and [Scripting API](https://developer.chrome.com/docs/extensions/reference/api/scripting). The extension fails back to the normal newly opened tab when reuse is unavailable.

## Verification

Run the offline routing and draft-protection tests from the repository root:

```text
node --test tests/x_tab_reuse.test.mjs
```

For a manual browser check, use an empty X home tab and click an existing Telegram draft button. Check the prefilled text and tab count without pressing X's Post button. Then check the no-existing-tab and unfinished-draft cases. X's page structure may change, so a real browser check is needed alongside the mocked API tests.
