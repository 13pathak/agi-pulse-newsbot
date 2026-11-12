// Telegram -> GitHub relay, run as a free Cloudflare Worker (setup: ARTICLE_DETAILS.md).
//
// Telegram calls this Worker the moment a message reaches the bot. The Worker
// starts the "Telegram requests" workflow (.github/workflows/telegram.yml): a
// message with a link gets a detail pack for that article.
// It answers Telegram in the same response, so it never needs the bot token.
//
// Worker secrets: GITHUB_TOKEN (fine-grained, Actions: read and write on the repo),
// GITHUB_REPO ("owner/name"), TELEGRAM_CHAT_ID, WEBHOOK_SECRET.
// Optional: GITHUB_REF (default "main").

const HELP = "Send me an article link and I'll reply with a detail pack: facts, figures and draft posts.";

const answer = (payload) => Response.json(payload);
const nothing = () => new Response("ok");

export function firstLink(message) {
  const text = message.text ?? message.caption ?? "";
  for (const entity of message.entities ?? message.caption_entities ?? []) {
    const link = entity.type === "text_link" ? entity.url
      : entity.type === "url" ? text.slice(entity.offset, entity.offset + entity.length) : "";
    if (/^https?:\/\//i.test(link)) return link;
    if (link && entity.type === "url") return `https://${link}`;  // "example.com/post" without a scheme
  }
  return text.match(/https?:\/\/[^\s<>"]+/i)?.[0] ?? "";
}

async function startWorkflow(env, inputs) {
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/telegram.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "telegram-relay",
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs }),
    });
  return response.status;
}

async function onMessage(message, env) {
  if (String(message.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return nothing();  // strangers get no reply
  const reply = (text) => answer({
    method: "sendMessage", chat_id: message.chat.id, text,
    reply_parameters: { message_id: message.message_id, allow_sending_without_reply: true },
    link_preview_options: { is_disabled: true },
  });
  const link = firstLink(message);
  if (!link) return reply(HELP);
  if (link.length > 2000) return reply("⚠️ That link is too long to process.");
  const status = await startWorkflow(env, { url: link }).catch(() => 0);
  return reply(status === 204
    ? "⏳ Reading the article. The detail pack will arrive here in a minute or two."
    : `⚠️ Could not start the run (GitHub answered ${status || "nothing"}). Check the relay's GitHub token.`);
}

// The bot's own buttons are plain links. A press here comes from a message sent by
// a removed feature; answer it so Telegram stops showing the button as loading.
function onButton(callback) {
  return answer({ method: "answerCallbackQuery", callback_query_id: callback.id,
    text: "This button is no longer in use" });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("Telegram relay is running.");
    if (!env.WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return nothing();
    }
    // Always answer 200 from here on: any other status makes Telegram resend the update.
    try {
      if (update.callback_query) return onButton(update.callback_query);
      if (update.message) return await onMessage(update.message, env);
    } catch (error) {
      console.error("relay failed:", error?.message);
    }
    return nothing();
  },
};
