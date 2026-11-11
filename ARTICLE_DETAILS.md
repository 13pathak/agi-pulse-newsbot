# Detail packs

A detail pack turns one full article into material you can post: the facts and
figures below the headline, five draft posts built around them, and the charts
or pictures from inside the article.

## What arrives in Telegram

1. **The article's pictures** (up to four), as an album. Gemini looks at the
   pictures inside the article body and keeps charts, tables, diagrams,
   screenshots and informative photos; decorative art is left out. If the article
   has no usable picture, its preview image is sent instead.
2. **One message** with:
   - 5–6 details worth posting (prices, benchmark scores, limits, dates);
   - 5 drafts under 280 characters, all opinion takes anchored in a specific detail,
     each with its own **✅ Post on X** button and a note saying which picture
     goes with it;
   - one claim to verify, and a **📖 Read article** button.

The "Post on X" button can only pre-fill text. To use a picture, save it from
Telegram and attach it to the post yourself. The pictures belong to the
publisher: credit them (for example "Chart: Anthropic") and reuse them at your
own judgement.

## Takes for every other story

With `takes_for_all_stories: true`, each news draft that does not get a full pack is
followed by a shorter **💭 Takes** message: three opinion takes written from the full
article, up to three details, no pictures. The news draft itself stays a straight
summary, so you can see what happened and then choose a take to post.

- Official stories below the pack threshold, or past the daily pack limit, get
  takes instead of a pack.
- If the article cannot be read (paywall, login, browser-only page), the takes are
  written from the feed's summary and the message says so. Treat those as thinner.
- At most `takes_max_per_day` (30) per IST day. Each one is two extra Gemini
  requests: one to write the drafts and one to pick the best.
- Set `takes_for_all_stories: false` to turn this off and keep only the packs.

## The writing voice

The aim is drafts that read as written by a person, and that do not all look alike.

- **Tone.** Measured and critical (the obvious question a sensible reader would ask,
  or what is unproven, missing or hard) or plainly neutral. Never sarcastic, jokey
  or excited. To change it, set `takes_voice` in `config.yaml`; the built-in wording
  is `DEFAULT_VOICE` in `article_details.py`.
- **Context.** Every draft must contain the news itself (who did what, with the key
  figure), so it makes sense on X to someone who has not seen the story. Drafts that
  name nobody and nothing from the article are ranked last.
- **Different shapes.** Each pack draws a fresh mix from eight shapes (news then a
  doubt, question first, view first, one sentence, comparison, what is missing,
  concede then object, who it affects) and three lengths, so a day's posts do not
  share one template. They are `SHAPES` and `LENGTHS` in `article_details.py`.
- **Machine tells are filtered out.** Drafts are checked against over 100 stock
  words and phrases ("signals", "landscape", "the future of", "honestly" and so
  on) and against patterns such as long dashes, semicolons, lists of three, "not
  just X" contrasts, "The catch?" openers and -ing openers. A draft with any of
  them is discarded when at least two clean drafts remain. The lists are
  `TELL_WORDS` and `TELL_PATTERNS`.
- **A second pass picks the best.** Gemini writes four more drafts than are needed.
  A second Gemini request scores the survivors for how human they read and whether
  the news is clear, and the top three (takes) or five (packs) are kept. If that
  request fails, the first-pass order is used. Set `takes_judge: false` to skip it.

## When a pack is made

**Automatically.** When the news bot delivers a draft that includes an official
source (the `category: primary` feeds in `config.yaml`) and scored at least
`details_min_importance` (7), a pack for that official article follows in the same
run. At most `details_max_per_day` (6) per IST day, never during quiet hours, and
never twice for the same article.

**On request, for any link.** Send the link to the bot in Telegram. The bot answers
at once ("⏳ Reading the article") and the pack follows as soon as it is written,
usually within a minute or two. This does not wait for the half-hourly news run:
each link starts its own run of the separate **Telegram requests** workflow. It
needs the one-time relay setup below.

Without the relay, the same thing works by hand: **Actions → Telegram requests →
Run workflow**, paste the article link into `url`. This also works from the GitHub
mobile app.

## Sending links from Telegram: one-time setup

GitHub Actions cannot listen to Telegram, so a small relay passes each message on.
It is `relay/worker.js`, run as a Cloudflare Worker (free plan, no card needed). It
holds no bot token: it only starts the workflow and answers Telegram.

1. **GitHub token.** Create a fine-grained personal access token limited to this
   repository with **Actions: Read and write** (the same kind as the one used by
   the cron-job.org timer; a separate token is easier to revoke).
2. **Worker.** At [dash.cloudflare.com](https://dash.cloudflare.com), create a
   Worker (the "Hello World" starter), replace its code with the contents of
   `relay/worker.js` and deploy. Copy its address (`https://<name>.<you>.workers.dev`).
3. **Worker secrets.** In the Worker's settings, under variables and secrets, add:

   | Name | Value |
   |---|---|
   | `GITHUB_TOKEN` | the token from step 1 |
   | `GITHUB_REPO` | `<owner>/<repo>`, e.g. `13pathak/agi-pulse-newsbot` |
   | `TELEGRAM_CHAT_ID` | the same chat ID the bot already uses |
   | `WEBHOOK_SECRET` | any long random string of letters and digits you make up |

   For a group chat, also add `TELEGRAM_FEEDBACK_USER_ID` (your numeric user ID).
4. **Point Telegram at the Worker.** Open this address once in a browser, with your
   own values filled in:

   ```
   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_ADDRESS>&secret_token=<WEBHOOK_SECRET>&allowed_updates=["message","callback_query"]
   ```

   The reply should be `"Webhook was set"`. Keep the `allowed_updates` part: without
   it Telegram keeps delivering button presses only, and links are never seen.
5. Send the bot a link. It should answer within a second or two.

Only messages from your chat are acted on; anyone else gets no reply. Requests
without the right `WEBHOOK_SECRET` are refused.

**Video feedback buttons** go through the relay too once the webhook is set (a bot
can have a webhook or be polled, not both). A press is acknowledged at once, saved
as a small file in `state/video_feedback/` and applied on the next video run.

**To undo:** open `https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook` and delete
the Worker. Video feedback goes back to polling by itself.

**If nothing happens:** `https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo`
shows the last delivery error. A "Could not start the run" reply means the GitHub
token is wrong, expired or lacks the Actions permission.

## Accuracy checks and limits

- **Numbers are checked against the article text.** A draft whose number appears
  nowhere in the text is removed, unless it is tied to a chart that is being sent;
  those drafts are kept and labelled "number read from a chart, verify".
- **Charts drawn by the page itself cannot be sent.** Some sites (Anthropic's among
  them) build charts from page code instead of image files. Their benchmark
  *tables* are still read as text, so the numbers reach the drafts, but there is no
  chart picture to attach; take a screenshot yourself if you want one.
- **Some pages cannot be read**: paywalls, logins, and pages that only render in a
  browser. Nothing is bypassed; a requested pack reports the failure in Telegram,
  an automatic one is skipped and logged.
- **It is two extra Gemini requests per pack**: one to write (with up to six
  pictures attached) and one to pick the best drafts.
- Opinion drafts are suggestions for a take, never invented personal experience.
  Read the article and make the post your own.

## Local commands

```bash
python article_details.py --url "https://example.com/article" --extract-only
python article_details.py --url "https://example.com/article" --print
python -m pytest tests/test_article_details.py -q
```

`--extract-only` shows the text and pictures that were read and needs no keys.
`--print` needs `GEMINI_API_KEY`. Without `--print`, the pack is sent to Telegram
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## State

The news bot's `state/state.json` gains three small entries: `details_queue`
(official articles waiting for a pack), `details_done` (article links already
handled, kept 14 days) and `details_daily` (today's count).

## Turning it off or removing it

To pause automatic packs, delete `details_min_importance` from `config.yaml`.

To remove the feature: delete `article_details.py`, `tests/test_article_details.py`
and this file; remove the **Detail packs for official stories** step from
`.github/workflows/newsbot.yml` and the `details` job from
`.github/workflows/telegram.yml`; and remove `queue_details` (the function and its
one call) from `newsbot.py`. The relay (`relay/worker.js`) also carries video
feedback, so keep it unless the webhook is removed as well.
