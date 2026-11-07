# AGI Pulse NewsBot 🤖📰

Your personal AI-news intern. It reads the AI news so you don't have to doom-scroll at 2 AM, writes posts in the @AGI_Pulse voice, and sends them to your Telegram. **You** decide what gets posted.

```
 RSS feeds ──► filter & dedupe ──► Gemini (rank + write) ──► Telegram draft
 (NYT, WSJ, Verge,                                           [✅ Post on X]
  The Hindu, OpenAI…)                                        [📖 Read article]
                                                                   │
                                          tap ──► X opens with the text filled in
                                                  ──► edit if you want ──► Post
```

- Runs every 30 minutes on GitHub Actions. Free.
- Gemini's free tier is plenty, since it makes only one call per run.
- No X API, so no per-post or link charges. Because you post every draft yourself, your account stays a human account with no bot label needed.
- Quiet hours run from 23:00 to 06:30 IST. Anything published overnight is waiting for you in the morning.
- Daily cap of 10 drafts and an importance threshold, so you only get the stories worth posting.

---

## Setup (about 15 minutes, one time)

### 1. Get a Gemini API key
1. Go to **https://aistudio.google.com** → sign in → **Get API key** → **Create API key**.
2. When it asks for a project, pick **Create API key in new project**. Gemini's free limits are counted per project, so the news bot then never eats into the quota your Self Observation script uses.
3. Copy the key somewhere safe. You'll paste it in step 4.

### 2. Make a Telegram bot
You've done this before for the Unit Test bot, so it's the same routine:
1. In Telegram, open **@BotFather** → `/newbot` → pick a name such as *AGI Pulse Desk* → copy the **token**.
2. Open your new bot and send it any message, for example `hi`.
3. In a browser, open `https://api.telegram.org/bot<TOKEN>/getUpdates` (replace `<TOKEN>`). Find `"chat":{"id":123456789` and copy that number. That's your **chat ID**. It's probably the same as your UT bot's.

### 3. Put the code on GitHub
1. Create a new repository on github.com, for example `agi-pulse-newsbot`.
   - **Public** gives unlimited free Actions minutes. Your keys stay secret either way, because they live in Secrets, not in the code.
   - **Private** also works, but every-30-minute runs use about 1,450 of your 2,000 free minutes a month. If you go private, change the cron to `0 * * * *` (hourly).
2. Upload all the files from this folder, keeping the `.github/workflows/` folder exactly where it is.

### 4. Add your secrets
Go to the repo → **Settings → Secrets and variables → Actions → New repository secret**, and add three:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | key from step 1 |
| `TELEGRAM_BOT_TOKEN` | token from step 2 |
| `TELEGRAM_CHAT_ID` | chat ID from step 2 |

### 5. Test it
Go to the repo → **Actions** tab → enable workflows if GitHub asks → **AGI Pulse NewsBot** → **Run workflow**:
1. Run it with **check-feeds** first. The log shows which feeds work. If a feed fails, just set `enabled: false` for it in `config.yaml`, or fix its URL.
2. Then run **dry-run**. The log shows the drafts Gemini wrote, without sending anything.
3. Then run **normal**. Drafts arrive on Telegram. 🎉

After that it runs on its own every 30 minutes.

---

## Daily use
A draft arrives on Telegram, and you have three choices:
- **✅ Post on X**: X opens with the post already written. Add your own spin and tap Post.
- **📖 Read article**: check the facts first. Recommended, because Gemini only sees the headline and blurb.
- **Ignore it**: the bot won't send that story again.

## Tweaking (all in `config.yaml`)
- **Too many drafts?** Raise `min_importance` to 7, or set `max_drafts_per_day` to a number (0 means no daily limit).
- **Too few?** Lower `min_importance` to 5.
- **Voice not quite you?** Edit the `persona` text. That's where the bot's personality lives.
- **Add a news source:** copy a feed block and paste in its RSS URL. Use `ai_only: false` for general tech feeds; the keyword filter then keeps only the AI stories.
- **Pictures:** drafts from lab blogs (OpenAI, Anthropic, Google, NVIDIA, Hugging Face) and papers arrive in Telegram with the post's picture. Save it and attach it on X; the X link can only carry text. News outlets' photos are usually licensed (Getty, Reuters…), so those feeds don't send pictures. Add `images: true` to a feed to change that, or set `attach_images: false` to turn pictures off.
- **Sites without RSS:** Anthropic has no feed, so the bot reads its news page (`type: page_list`) and opens each new article once.
- **New open models:** the `Hugging Face Models` source watches Hugging Face's trending list for models released in the last week by the labs in `orgs` (DeepSeek, Qwen, Kimi, Meta, Mistral, Google, Sarvam…). Re-packaged copies (GGUF, FP8…) are skipped. Gemini reads each model's card. Add a lab's Hugging Face name to `orgs` to follow it.
- **Research papers:** the `Hugging Face Papers` source brings in arXiv papers the community upvoted on Hugging Face Daily Papers. Gemini sees only each paper's abstract, and paper posts start with "New paper:" or "Researchers at <lab>". Raise `min_upvotes` (default 20) for fewer, bigger papers, or set `enabled: false` to switch papers off.
- **Source credit and links:** posts have no "via The Hindu" credit and no link. Set `show_source: true` to add the credit, or `include_link: true` to add the article link. The 📖 Read article button on Telegram is always there for you.
- **Different Gemini model:** add a repo *variable* (not a secret) named `GEMINI_MODEL`, for example `gemini-3.6-flash` (current names: https://aistudio.google.com/docs/models).

## Troubleshooting
- **No drafts at all:** check the Actions log. "0 new AI items" means everything was already seen; "Gemini picked 0" means nothing passed `min_importance`.
- **GitHub emails you that the workflow failed:** open the log. The usual causes are a mistyped secret or Gemini rate limits (it retries on its own; one failed run is harmless).
- **Runs stop happening:** runs are started by a cron-job.org timer through GitHub's API. Check that the job is still enabled there and that its GitHub token hasn't expired.
- **Run it on your own PC:** `pip install -r requirements.txt`, set the three environment variables, then `python newsbot.py --dry-run`.

## Ground rules the bot follows
- It writes summaries in its own words from the headline and blurb. It never copies article text.
- It's told never to invent numbers, names or quotes. Still, read the article before you post anything surprising. Your name is on the post, not Gemini's.
