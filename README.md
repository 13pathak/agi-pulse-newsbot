# AGI Pulse NewsBot

An automated AI-news desk. It monitors 30+ news outlets, lab blogs, research papers and open-model releases, uses Google Gemini to rank stories and draft concise, factual posts for X (Twitter), and delivers each draft to Telegram for human review. Nothing is published without an editor's approval.

```
 News feeds, lab blogs,      ──►  filter &   ──►  Gemini: group,    ──►  Telegram draft
 arXiv papers, open models        dedupe          score, write             [✅ Post on X]
                                                                           [📖 Read article]
                                                                                 │
                                                       tap ──► X opens with the post pre-filled
```

## Features

- **Broad coverage:** 34 sources across global and Indian tech press, business media, AI lab blogs, research papers (arXiv via Hugging Face Daily Papers) and trending open-model releases.
- **Editorial ranking:** Gemini groups duplicate coverage into one story, scores each story 1–10 and drafts posts only for those above a configurable threshold.
- **Human in the loop:** drafts go to Telegram with a one-tap **Post on X** button. No X API is used, so there are no API costs and every post is published by a person.
- **Factual style:** posts are written as plain, wire-service news in one or two sentences, with no opinions, hype or invented details. The rules follow the engagement signals in X's open-source ranking code.
- **Images:** drafts include the source's preview image, ready to attach to the post.
- **Resilient:** retries when Gemini is busy, falls back to backup models, skips broken feeds, and never sends the same story twice.
- **Runs free:** on GitHub Actions (public repository) with Gemini's free tier.

## How it works

Each run (every 30 minutes):

1. **Collect:** fetch every enabled source, keep items from the lookback window that haven't been seen before, and keyword-filter general tech feeds down to AI stories.
2. **Rank and write:** send the candidates to Gemini in one request. Gemini groups items about the same story, scores importance and writes a post for each story at or above `min_importance`.
3. **Deliver:** send the top story (one per run by default) to Telegram with its picture, the original headline, a character count, and **Post on X** / **Read article** buttons.
4. **Remember:** save seen items and covered headlines to `state/state.json` so stories aren't repeated. Strong stories that didn't fit in this run are kept for the next one.

## Sources

| Category | Examples |
|---|---|
| Global tech press | The New York Times, The Wall Street Journal, The Verge, TechCrunch, MIT Technology Review, Ars Technica, Wired, The Decoder, The Guardian |
| Business and markets | Bloomberg, Financial Times, CNBC, Business Insider, The Information, South China Morning Post, Tom's Hardware |
| India | The Hindu, The Economic Times, Mint, India Today, Inc42, YourStory, MediaNama, Analytics India Magazine |
| AI labs and companies | OpenAI, Anthropic, Google DeepMind, Google AI, Microsoft, NVIDIA, Hugging Face, Simon Willison |
| Research | arXiv papers trending on Hugging Face Daily Papers (abstracts only, with a minimum upvote count) |
| Open models | New releases from major labs trending on Hugging Face (DeepSeek, Qwen, Kimi, Meta, Mistral, Sarvam and others) |

The full list is in [`config.yaml`](config.yaml). Run `python newsbot.py --check-feeds` to test each one.

## Setup

### 1. Gemini API key
Create a key at [Google AI Studio](https://aistudio.google.com) (**Get API key → Create API key**). A dedicated project keeps this bot's free quota separate from other uses.

### 2. Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the **token**.
2. Send any message to the new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id` value. This is the **chat ID**.

### 3. Repository and secrets
1. Fork or copy this repository. A **public** repository gets unlimited free GitHub Actions minutes; a private one is limited to 2,000 minutes a month on the free plan, which this schedule can exceed.
2. Under **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

Optionally, add a repository **variable** `GEMINI_MODEL` to override the model in `config.yaml`.

### 4. Scheduling
GitHub's built-in `schedule` trigger runs on a best-effort basis and often skips slots, so the workflow is started from outside through the GitHub API. Any cron service works; [cron-job.org](https://cron-job.org) is free.

1. Create a fine-grained personal access token limited to this repository, with **Actions: Read and write** permission.
2. Create a cron job (for example at minutes `7,37` of every hour) that sends:

```
POST https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/newsbot.yml/dispatches

Authorization: Bearer <TOKEN>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json

{"ref":"main","inputs":{"mode":"normal"}}
```

A `204 No Content` response means the run started.

### 5. Verify
From the **Actions** tab, run the **AGI Pulse NewsBot** workflow manually in each mode:

| Mode | What it does |
|---|---|
| `check-feeds` | Tests every source and reports which work |
| `dry-run` | Prints the drafts Gemini writes, without sending or saving anything |
| `normal` | Full run: drafts are sent to Telegram |

## Configuration

All settings live in [`config.yaml`](config.yaml).

| Setting | Default | Description |
|---|---|---|
| `max_drafts_per_run` | `1` | Drafts sent per run. Keeping it at 1 spaces posts out. |
| `max_drafts_per_day` | `0` | Daily cap; `0` means no limit. |
| `min_importance` | `6` | Minimum Gemini score (1–10) for a story to be drafted. |
| `lookback_hours` | `18` | Ignore items older than this (sources can override it). |
| `max_candidates` | `60` | Most items sent to Gemini per run. |
| `quiet_hours_ist` | `["23:00", "06:30"]` | No drafts in this window (IST); the backlog is handled afterwards. |
| `show_source` | `false` | End posts with "via <Outlet>". |
| `include_link` | `false` | End posts with the article link. |
| `attach_images` | `true` | Send the source's image with drafts from sources marked `images: true`. |
| `gemini_model` | `gemini-flash-latest` | Main Gemini model. |
| `gemini_fallback_models` | see file | Tried in order if the main model is busy or retired. |

`persona` sets the voice Gemini writes in, and `keywords` decides which stories from general feeds count as AI news.

### Source types

| `type` | Use | Notable options |
|---|---|---|
| *(omitted)* | Any RSS or Atom feed | `ai_only`, `images`, `enabled`, `blurb_chars` |
| `page_list` | Sites without a feed: reads a listing page and opens each new article once | `link_pattern`, `base`, `max_new_pages` |
| `hf_papers` | Hugging Face Daily Papers (arXiv) | `min_upvotes`, `max_items`, `days` |
| `hf_models` | New models trending on Hugging Face | `orgs`, `max_age_days`, `max_items` |

Set `ai_only: true` for sources that only cover AI; with `ai_only: false`, only items matching `keywords` are kept.

## Running locally

```bash
pip install -r requirements.txt pytest
export GEMINI_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python newsbot.py --check-feeds
python newsbot.py --dry-run
python -m pytest -q
```

The test suite runs offline, with fake feeds, a fake Gemini and a fake Telegram.

## Project structure

```
newsbot.py                    the bot: sources, filtering, Gemini, Telegram
config.yaml                   settings, persona, keywords and sources
state/state.json              seen items and recent headlines (committed by the workflow)
tests/test_newsbot.py         offline test suite
.github/workflows/newsbot.yml GitHub Actions workflow
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A run finishes in about 15 seconds | Quiet hours, no new items, or the daily cap was reached. The log says which. |
| `Gemini picked 0 stories` | Nothing scored at or above `min_importance`. |
| `Gemini unavailable … will retry next run` | Every model was busy. The items stay unseen and are retried. |
| `model … not found` | Google retired a model. Update `gemini_fallback_models` from the [models page](https://aistudio.google.com/docs/models). |
| `feed FAILED` | The site blocked the request or changed its URL. Fix the URL or set `enabled: false`. |
| No runs at all | Check the external cron job and that its access token hasn't expired. |

## Editorial guidelines

- Posts are written in Gemini's own words from each item's headline and summary (or a paper's abstract, or a model card). Article text is never copied.
- Gemini is instructed never to invent numbers, names, dates or quotes. Drafts should still be checked against the source before posting.
- Images belong to their publishers. News photos are often licensed from agencies such as Getty, Reuters or AP; check before attaching them.
