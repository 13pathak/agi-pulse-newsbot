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
- At most `takes_max_per_day` (30) per IST day. Each one is one extra Gemini request.
- Set `takes_for_all_stories: false` to turn this off and keep only the packs.

## The writing voice

Every draft opens with the news itself in one short clause (who did what, with
the key figure) and then gives the reaction, so it makes sense on X to someone who
has not seen the story. Drafts that do not name anyone or anything from the article
are ranked last. Drafts are asked to sound like a person typing a thought on their
phone: plain spoken English, one idea, no press-release wording. Gemini writes seven takes;
the five most natural are kept, and any that use stock phrases ("signals",
"redefine", "the end of" and similar) are ranked last. To change the voice, set
`takes_voice` in `config.yaml`; the built-in wording is `DEFAULT_VOICE` in
`article_details.py`.

## When a pack is made

**Automatically.** When the news bot delivers a draft that includes an official
source (the `category: primary` feeds in `config.yaml`) and scored at least
`details_min_importance` (7), a pack for that official article follows in the same
run. At most `details_max_per_day` (6) per IST day, never during quiet hours, and
never twice for the same article.

**On request, for any link.** In GitHub: **Actions → AGI Pulse NewsBot → Run
workflow**, choose mode `details` and paste the article link. The pack arrives in
Telegram in about a minute. This also works from the GitHub mobile app.

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
- **It is one extra Gemini request per pack**, with up to six pictures attached.
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
and this file; remove the **Detail packs for official stories** step, the `details`
mode and the `url` input from `.github/workflows/newsbot.yml`; and remove
`queue_details` (the function and its one call) from `newsbot.py`.
