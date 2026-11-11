import assert from "node:assert/strict";
import test from "node:test";
import worker, { firstLink } from "../relay/worker.js";

const ENV = { GITHUB_TOKEN: "gh-token", GITHUB_REPO: "owner/repo", TELEGRAM_CHAT_ID: "123", WEBHOOK_SECRET: "s3cret" };
const VIDEO = "abcdefghijk";

async function send(update, { secret = "s3cret", github = 204, env = ENV } = {}) {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, auth: init.headers.Authorization, body: JSON.parse(init.body) });
    return new Response(null, { status: github });
  };
  try {
    const response = await worker.fetch(new Request("https://relay.test/", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secret }, body: JSON.stringify(update),
    }), env);
    const text = await response.text();
    return { status: response.status, reply: text.startsWith("{") ? JSON.parse(text) : null, calls };
  } finally {
    globalThis.fetch = realFetch;
  }
}

const message = (text, extra = {}) => ({ update_id: 1, message: { message_id: 9, chat: { id: 123 }, text, ...extra } });
const button = (data, from = 123, chat = 123) => ({
  update_id: 77, callback_query: { id: "cb1", data, from: { id: from }, message: { chat: { id: chat } } },
});

test("a link starts the detail-pack workflow and is acknowledged at once", async () => {
  const { status, reply, calls } = await send(message("look https://lab.test/post?a=1 nice"));
  assert.equal(status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.github.com/repos/owner/repo/actions/workflows/telegram.yml/dispatches");
  assert.equal(calls[0].auth, "Bearer gh-token");
  assert.deepEqual(calls[0].body, { ref: "main", inputs: { url: "https://lab.test/post?a=1" } });
  assert.equal(reply.method, "sendMessage");
  assert.equal(reply.chat_id, 123);
  assert.equal(reply.reply_parameters.message_id, 9);
  assert.match(reply.text, /^⏳/);
});

test("links are found in hidden links, captions and bare domains", () => {
  assert.equal(firstLink({ text: "read this", entities: [{ type: "text_link", offset: 0, length: 4, url: "https://a.test/x" }] }),
    "https://a.test/x");
  assert.equal(firstLink({ caption: "pic https://b.test/y", caption_entities: [{ type: "url", offset: 4, length: 16 }] }),
    "https://b.test/y");
  assert.equal(firstLink({ text: "c.test/z", entities: [{ type: "url", offset: 0, length: 8 }] }), "https://c.test/z");
  assert.equal(firstLink({ text: "no link here" }), "");
});

test("a message without a link gets help and starts nothing", async () => {
  const { reply, calls } = await send(message("/start"));
  assert.equal(calls.length, 0);
  assert.match(reply.text, /article link/);
});

test("other chats and wrong secrets are ignored", async () => {
  const stranger = await send(message("https://lab.test/post", { chat: { id: 999 } }));
  assert.deepEqual([stranger.status, stranger.reply, stranger.calls.length], [200, null, 0]);
  const forged = await send(message("https://lab.test/post"), { secret: "guess" });
  assert.deepEqual([forged.status, forged.calls.length], [403, 0]);
  const unset = await send(message("https://lab.test/post"), { secret: "", env: { ...ENV, WEBHOOK_SECRET: "" } });
  assert.equal(unset.status, 403);
});

test("a GitHub failure is reported in the chat", async () => {
  const { status, reply } = await send(message("https://lab.test/post"), { github: 401 });
  assert.equal(status, 200);
  assert.match(reply.text, /401/);
});

test("a video vote is relayed with its update number", async () => {
  const { reply, calls } = await send(button(`vid:useful:${VIDEO}`));
  assert.deepEqual(calls[0].body.inputs, { feedback: `vid:useful:${VIDEO}`, update_id: "77" });
  assert.deepEqual(reply, { method: "answerCallbackQuery", callback_query_id: "cb1", text: "Feedback received" });
});

test("votes from other users, other chats or with odd data are refused", async () => {
  for (const update of [button(`vid:useful:${VIDEO}`, 555), button(`vid:useful:${VIDEO}`, 123, 999), button("vid:useful:x; rm -rf")]) {
    const { reply, calls } = await send(update);
    assert.equal(calls.length, 0);
    assert.equal(reply.text, "Feedback unavailable");
  }
  const group = { ...ENV, TELEGRAM_CHAT_ID: "-100", TELEGRAM_FEEDBACK_USER_ID: "555" };
  const { calls } = await send(button(`vid:more:${VIDEO}`, 555, -100), { env: group });
  assert.equal(calls.length, 1);
});
