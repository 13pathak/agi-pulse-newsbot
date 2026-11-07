# AGI Pulse NewsBot

An automated AI-news desk. It monitors 55 enabled sources across news outlets, lab blogs, independent evaluations, policy announcements, research papers and open-model releases. It uses Google Gemini to rank stories and draft concise, factual posts for X (Twitter), and delivers each draft to Telegram for human review. Nothing is published without an editor's approval.

```
 News feeds, lab blogs,      ──►  filter &   ──►  Gemini: group,    ──►  Telegram draft
 arXiv papers, open models        dedupe          score, write             [✅ Post on X]
                                                                           [📖 Read article]
                                                                                 │
                                                       tap ──► X opens with the post pre-filled
```

## Features

- **Broad coverage:** 55 enabled sources, including direct lab announcements, independent evaluations, official policy, global and Indian reporting, research papers and open-model releases.
- **Primary sources first:** category reservations and per-source caps prioritize lab announcements while preserving room for research, policy and original reporting. Identical substantive headlines and canonical URLs are collapsed before Gemini.
- **Bounded Gemini input:** at most 60 candidates and 48,000 characters for the complete prompt, including instructions, history and JSON. No extra model calls for prioritization or summary extraction.
- **Editorial ranking:** Gemini groups duplicate coverage into one story, scores each story 1–10 and drafts posts only for those above a configurable threshold.
- **Human in the loop:** drafts go to Telegram with a one-tap **Post on X** button. No X API is used, so there are no API costs and every post is published by a person.
- **Factual style:** posts are written as plain, wire-service news in one or two sentences, with no opinions, hype or invented details. The rules follow the engagement signals in X's open-source ranking code.
- **Images:** drafts include the source's preview image, ready to attach to the post.
- **Resilient:** retries when Gemini is busy, falls back to backup models, skips broken feeds, and never sends the same story twice.
- **Runs free:** on GitHub Actions (public repository) with Gemini's free tier.

## How it works

Each run (every 30 minutes):

1. **Collect and shortlist:** fetch sources with bounded concurrency, filter by date and keywords, and collapse duplicate URLs and identical substantive headlines. Reserve category slots and rotate across sources, lending unused slots in priority order. Fetch public descriptions only for shortlisted entries that need them, subject to a separate request cap.
2. **Rank and write:** enforce the complete prompt-size limit, then send one batch to Gemini. Gemini groups remaining paraphrases of the same story, scores importance and writes a post for each qualifying story. Source priority does not give marketing posts an automatic high score.
3. **Deliver:** send the top story (one per run by default) to Telegram with its picture, the original headline, a character count, and **Post on X** / **Read article** buttons.
4. **Remember:** save seen items, their duplicate aliases and covered headlines to `state/state.json`. Strong stories held for another run keep all their aliases unseen. Candidates omitted by source, category or prompt limits also remain unseen and can be reconsidered while within their lookback window.

## Sources

| Category | Examples |
|---|---|
| Global tech press | The New York Times, The Wall Street Journal, The Verge, TechCrunch, MIT Technology Review, Ars Technica, Wired, The Decoder, The Guardian |
| Business and markets | Bloomberg, Financial Times, CNBC, Business Insider, The Information, South China Morning Post, Tom's Hardware |
| India | The Hindu, The Economic Times, Mint, India Today, Inc42, YourStory, MediaNama, Analytics India Magazine, Entrackr |
| AI labs and companies | OpenAI, Anthropic, Claude product updates, Google DeepMind, Google AI, Microsoft, NVIDIA, Hugging Face, Mistral, Meta AI, Ai2, Sarvam, xAI, ElevenLabs, AWS AI |
| Evaluations and research blogs | Epoch AI, Artificial Analysis, METR, Google Research, Microsoft Research, Apple ML Research, Simon Willison |
| Specialist reporting | IEEE Spectrum AI, Rest of World, 404 Media AI, SemiAnalysis |
| Official policy | NIST, UK AI Security Institute government announcements (not its complete research blog) |
| Papers | arXiv papers trending on Hugging Face Daily Papers (abstracts only, with a minimum upvote count) |
| Open models | New releases from major labs trending on Hugging Face (DeepSeek, Qwen, Kimi, Meta, Mistral, Sarvam and others) |

The full list is in [`config.yaml`](config.yaml). Run `python newsbot.py --check-feeds` to test each one. It returns a failure status for broken/empty sources and warns about missing dates, missing blurbs and feeds older than 45 days. An RSS-summary warning can be expected for Hugging Face: selected entries are enriched from public article metadata later.

Endpoints were checked on 7 November 2026. VentureBeat and The Indian Express remain disabled. Stale Qwen/IndiaAI endpoints and unusable PIB/MeitY feeds have not been enabled. PIB/MeitY needs a reliable English adapter; the tested public listing returned HTTP 403. No paywalls are bypassed. SemiAnalysis uses its current newsletter feed, and 404 Media uses its current `ai` tag feed.

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
| `inspect-candidates` | Previews the prioritized shortlist and exact prompt character count; no Gemini call, Telegram message or state save |
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
| `max_prompt_chars` | `48000` | Hard limit on the entire serialized prompt, not a token estimate. |
| `max_candidates_per_source` | `3` | Per-source cap; a feed's `max_candidates` can override it. |
| `candidate_slots` | see below | Category reservations; unused slots are reassigned. |
| `max_summary_fetches` | `12` | Maximum extra article-metadata requests per run, after shortlisting. |
| `fetch_workers` | `6` | Concurrent source fetches, bounded to 1–8. |
| `quiet_hours_ist` | `["23:00", "06:30"]` | No drafts in this window (IST); the backlog is handled afterwards. |
| `show_source` | `false` | End posts with "via <Outlet>". |
| `include_link` | `false` | End posts with the article link. |
| `attach_images` | `true` | Send the source's image with drafts from sources marked `images: true`. |
| `gemini_model` | `gemini-flash-latest` | Main Gemini model. |
| `gemini_fallback_models` | see file | Tried in order if the main model is busy or retired. |

`persona` sets the voice Gemini writes in, and `keywords` decides which stories from general feeds count as AI news.

The default 60 slots reserve **24 for primary announcements, 8 for research/evaluations, 4 for policy, 4 for models, 4 for papers and 16 for reporting**. Within each category, selection rotates across sources. If a category lacks eligible items, its spare slots go to primary sources, policy, research, models, papers and reporting, in that order, without exceeding source caps. Lowering `max_candidates` proportionally scales reservations. The complete prompt limit can further reduce the batch; no discarded item is marked seen merely for exceeding a limit.

The prompt retains at most 40 recent covered headlines, each capped at 180 characters. Longer research abstracts and METR's redaction caveats count toward the same prompt budget. Papers and models no longer automatically consume up to 18 slots ahead of all other news.

### Source types

| `type` | Use | Notable options |
|---|---|---|
| *(omitted)* | Any RSS or Atom feed | `ai_only`, `images`, `enabled`, `blurb_chars`, `prefer_content`, `fetch_summary` |
| `page_list` | Sites without a feed: reads a listing page and opens each new article once | `link_pattern`, `exclude_pattern`, `base`, `max_new_pages` |
| `hf_papers` | Hugging Face Daily Papers (arXiv) | `min_upvotes`, `max_items`, `days` |
| `hf_models` | New models trending on Hugging Face | `orgs`, `max_age_days`, `max_items` |

Set `ai_only: true` for sources that only cover AI; with `ai_only: false`, only items matching `keywords` are kept.

Set `category` to `primary`, `research`, `policy` or `news` (the default). HF paper/model sources infer their own categories. Date-only lab and research pages generally use a 48-hour lookback. Page-list sources accept relative or same-host absolute links and can use dates from individual article cards when the linked launch page has no date metadata. They never invent a publication date from the current time.

RSS extraction uses public `content:encoded` when a summary is too short or `prefer_content: true`; figures and captions are removed from those excerpts. `fetch_summary: true` fills thin shortlisted items from cached public Open Graph descriptions, within the extra-request cap. Hugging Face additionally uses paragraphs from its public `blog-content` container because its metadata often says only "A Blog post by..."; navigation and captions are excluded. METR gets a longer excerpt so both redaction caveats and findings reach Gemini.

## Running locally

```bash
pip install -r requirements.txt pytest
export GEMINI_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python newsbot.py --check-feeds
python newsbot.py --inspect-candidates
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

- Posts are written in Gemini's own words from each item's headline and public summary/feed excerpt (or a paper's abstract, or a model card). Posts must not copy source wording.
- Vendor benchmark claims must be attributed; independent measurements, forecasts and opinions must remain distinguishable.
- Gemini is instructed never to invent numbers, names, dates or quotes. Drafts should still be checked against the source before posting.
- Images belong to their publishers. News photos are often licensed from agencies such as Getty, Reuters or AP; check before attaching them.
