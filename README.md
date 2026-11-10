# AGI Pulse NewsBot

An automated AI-news desk. It monitors 58 enabled sources across news outlets, lab blogs, independent evaluations, policy announcements, research papers and open-model releases. It uses Google Gemini to rank stories and draft concise, factual English posts for X (Twitter), and delivers each draft to Telegram for human review. Nothing is published without an editor's approval.

```
 News feeds, lab blogs,      ──►  filter &   ──►  Gemini: group,    ──►  Telegram draft
 arXiv papers, open models        dedupe          score, write             [✅ Post on X]
                                                                           [📖 Read article]
                                                                                 │
                                                       tap ──► X opens with the post pre-filled
```

## Features

The separate [practical AI video recommender](VIDEO_RECOMMENDER.md) discovers and
reviews YouTube demonstrations across creative projects, everyday/no-code work and
AI application building. It has its own queue, daily limits and Telegram feedback;
automatic recommendations are disabled until API setup and preview checks pass.

- **Opinion takes for every story:** each delivered news draft is followed by three takes written from the full article, in a conversational voice ([details](ARTICLE_DETAILS.md)). The news draft itself stays straight news.
- **Detail packs:** official announcements scoring 7/10 or higher are followed by a [detail pack](ARTICLE_DETAILS.md): facts and figures from the full article, five drafts and the article's own charts. Any link can be turned into a pack from the Actions tab.
- **Broad coverage:** 58 enabled sources, including direct lab announcements, independent evaluations, official policy, global, Indian and Chinese reporting, research papers and open-model releases.
- **Chinese sources, English drafts:** Gemini translates supplied Chinese headlines and excerpts in the existing ranking call. Telegram receives an English story headline, post and editor note, with the original article link retained. Chinese source text stays in the queue for reference; untranslated drafts are held back.
- **Primary sources first:** category reservations and per-source caps prioritize lab announcements while preserving room for research, policy and original reporting. Identical substantive headlines and canonical URLs are collapsed before Gemini.
- **Bounded Gemini input:** at most 60 candidates and 48,000 characters for the complete prompt, including instructions, history and JSON. No extra model calls for prioritization or summary extraction.
- **Editorial ranking:** Gemini groups duplicate coverage into one story, scores each story 1–10 and drafts posts only for those above a configurable threshold.
- **Persistent editorial queue:** qualifying drafts survive missing feed entries and discovery lookback limits. Importance, short-lived urgency, bounded waiting time and story expiry determine the next Telegram draft. Each draft includes a short reason for its selection.
- **Human in the loop:** drafts go to Telegram with a one-tap **Post on X** button. No X API is used, so there are no API costs and every post is published by a person.
- **Optional desktop tab reuse:** the [Brave/Chrome/Edge helper](desktop/x-tab-reuse/README.md) reuses an available X tab when a posting link opens. Install it in your desktop browser; Android's X app behavior is unchanged.
- **Straight news drafts:** each post reports what happened with one useful concrete detail, without opinions, assessments or commentary. A separate editor note in Telegram flags one fact to verify. No fabricated personal experience or engagement bait.
- **Editor notes:** each new draft requests one story-specific check or angle for you to investigate. The note appears in Telegram and dry runs, never in the X posting link. Read the source and contribute your own perspective before publishing.
- **Optional reference images:** source pictures are off by default. You can enable them for reference; prefer your own visuals on X and check rights before reusing third-party images.
- **Resilient:** retries when Gemini is busy, falls back to backup models, skips broken feeds, and remembers stories and their duplicate aliases to suppress repeat drafts.
- **Runs free:** on GitHub Actions (public repository) with Gemini's free tier.

## How it works

Each run (every 30 minutes):

1. **Collect and shortlist:** fetch sources with bounded concurrency, filter by date and keywords, and collapse duplicate URLs and identical substantive headlines. Reserve category slots and rotate across sources, lending unused slots in priority order. Fetch public descriptions only for shortlisted entries that need them, subject to a separate request cap.
2. **Rank and write:** enforce the complete prompt-size limit, then send one batch of new items to Gemini, with publication times, the current time and compact context for every queued story. Gemini groups duplicate coverage, assesses importance and urgency, and drafts qualifying stories. It can identify duplicate or explicitly superseded queued stories. Source priority does not give marketing posts an automatic high score.
3. **Queue and select:** persist qualifying drafts, their sources and aliases, original publication/discovery times and expiry in `state/state.json`. Compare saved stories with new arrivals using the deterministic priority policy below. Cached stories need no further Gemini call when there are no new candidates.
4. **Deliver and remember:** send the highest-priority story (one per run by default) to Telegram with its headline, selection reason, character count, editor note and **Post on X** / **Read article** buttons. Reference pictures are optional. Mark a story delivered only after a successful send; save after each success. The workflow also saves state after a failed delivery, preserving unsent drafts and earlier successes. Candidates omitted by source, category or prompt limits remain unseen and can be reconsidered within their discovery lookback window.

### How the next story is chosen

Priority = importance (1–10) + urgency boost + waiting boost − age penalty:

- **Urgency:** a dated, major breaking story (importance at least 8) gets +2 during its first 6 hours. Timely stories get +0.5 during their first 12 hours; breaking stories fall to that same boost after 6 hours. All urgency boosts then disappear. Unknown or future publication dates receive no urgency boost.
- **Waiting:** grows from 0 to +0.75 over 12 hours in the queue, then stops growing. This helps comparable stories without making weak stories important simply because they waited.
- **Age:** subtracts up to 1 point over the story's lifetime. Ordinary news expires after 36 hours, primary announcements after 48, policy after 72, research/papers after 96 and open-model releases after 168. Expiry starts at publication, or discovery if undated; future dates cannot extend it. Duplicate coverage never restarts the clock.
- **Ties:** higher importance, then earlier queue entry, then a stable article ID. Expired, explicitly superseded and below-threshold stories are retired. If the queue exceeds 40 stories, retain the highest-priority ones and log the evictions.

For example, important older research can beat a routine new update, while a major breaking announcement can lead and leave that research queued. These weights are editorial defaults, not measured engagement predictions. The lookback controls discovery; queued stories have their own deadlines and remain available even when their feeds no longer list them.

The bot still controls **draft delivery**, not confirmed publication on X. Quiet hours and daily/per-run delivery caps still apply. If new candidates exist but Gemini is unavailable, retain the queue and retry next run before delivering potentially superseded drafts. Dry runs and candidate inspection do not send messages or save state. A network failure after Telegram accepts a message but before its success is recorded can still cause a retry; Telegram delivery is not transactional with the state file.

Only recognized draft errors (such as invalid markup, invalid button URLs or excessive message size) retire a draft and allow the next story to be tried in the same run. HTTP 400 alone is not sufficient: missing chats, chat migrations and unknown errors keep the queue intact. Telegram's error description and response parameters are inspected, with migration/rate-limit parameters taking precedence over content-error classification.

Token, chat, rate-limit, server and network failures stop delivery for that run and retain unsent drafts until their normal editorial expiry. Attempt counts are logged but never cause retirement. A `retry_after` response persists a delivery cooldown across runs; discovery and ranking can continue during the cooldown. Actual delivery failures still mark Actions as failed. Confirmed photo/media rejections fall back to text; connection/read timeouts and service errors do not trigger an immediate second send, because the photo's delivery outcome may be unknown. After a picture send times out (often a slow image host), the draft is marked text-only and the next run sends it without the picture, so one bad image can't block the queue; at worst this produces one duplicate draft. A connection failure keeps the picture, since Telegram itself was unreachable.

Discovery logs show per-source and total counts for `seen`, `queued`, `old`, `keyword_miss` and `eligible`. Each article is counted under the first applicable filter, in that order; counts cover items returned by the source adapters. `seen` means previously processed, which can include articles Gemini did not select, and does not imply publication. AI-only feeds bypass keyword matching; general feeds use the configured keywords in their titles and summaries. Separate counts explain duplicate removal, shortlist limits and prompt limits. An empty shortlist means no candidates reached Gemini, not that all fetched articles were judged unrelated to AI. When Gemini runs, a separate editorial log reports the candidate and qualifying-story counts.

## Sources

| Category | Examples |
|---|---|
| Global tech press | The New York Times, The Wall Street Journal, The Verge, TechCrunch, MIT Technology Review, Ars Technica, Wired, The Decoder, The Guardian |
| Business and markets | Bloomberg, Financial Times, CNBC, Business Insider, The Information, South China Morning Post, Tom's Hardware |
| India | The Hindu, The Economic Times, Mint, India Today, Inc42, YourStory, MediaNama, Analytics India Magazine, Entrackr |
| Chinese-language reporting | QbitAI (量子位), 36Kr (36氪), LeiPhone (雷峰网) |
| AI labs and companies | OpenAI, Anthropic, Claude product updates, Google DeepMind, Google AI, Microsoft, NVIDIA, Hugging Face, Mistral, Meta AI, Ai2, Sarvam, xAI, ElevenLabs, AWS AI |
| Evaluations and research blogs | Epoch AI, Artificial Analysis, METR, Google Research, Microsoft Research, Apple ML Research, Simon Willison |
| Specialist reporting | IEEE Spectrum AI, Rest of World, 404 Media AI, SemiAnalysis |
| Official policy | NIST, UK AI Security Institute government announcements (not its complete research blog) |
| Papers | arXiv papers trending on Hugging Face Daily Papers (abstracts only, with a minimum upvote count) |
| Open models | New releases from major labs trending on Hugging Face (DeepSeek, Qwen, Kimi, Meta, Mistral, Sarvam and others) |

The full list is in [`config.yaml`](config.yaml). Run `python newsbot.py --check-feeds` to test each one. It returns a failure status for broken/empty sources and warns about missing dates, missing blurbs and feeds older than 45 days. An RSS-summary warning can be expected for Hugging Face: selected entries are enriched from public article metadata later.

Endpoints were checked on 7 November 2026. VentureBeat and The Indian Express remain disabled. Stale Qwen/IndiaAI endpoints and unusable PIB/MeitY feeds have not been enabled. PIB/MeitY needs a reliable English adapter; the tested public listing returned HTTP 403. No paywalls are bypassed. SemiAnalysis uses its current newsletter feed, and 404 Media uses its current `ai` tag feed.

### Chinese ecosystem coverage

Public Chinese endpoints were checked separately on **9 November 2026**:

| Outlet | Useful coverage | Integration |
|---|---|---|
| [QbitAI / 量子位](https://www.qbitai.com/) | Chinese AI models, labs, research, startups and robotics; also global AI news | Enabled: [publisher RSS](https://www.qbitai.com/feed), 10 dated items in the check. An initial timeout recovered on retest. |
| [36Kr / 36氪](https://www.36kr.com/information/AI/) | Chinese startup funding, commercialization and company reporting | Enabled: [publisher RSS](https://www.36kr.com/feed), 30 dated items. The feed is broader than its AI section; use the `www` host, since the bare-host feed returned HTML. |
| [LeiPhone / 雷峰网](https://www.leiphone.com/) | AI industry reporting, products, chips and enterprise adoption | Enabled: [publisher RSS](https://www.leiphone.com/feed), 20 dated items. General technology items still need AI filtering. |
| [Jiqizhixin / 机器之心](https://www.jiqizhixin.com/) | AI research, models and technical coverage | Recommended for further integration. Old RSS endpoints redirect to a data-service page; the public article listing did not expose usable article links to the current parser. |
| [Zhidx / 智东西](https://zhidx.com/about) | Chinese AI industry, AI chips, robotics and computing infrastructure | Recommended for further integration. Its RSS returned HTTP 500; tested article HTML needs a title-extraction adapter before the existing page-list route can use it. |

All three enabled sources use `language: zh`, keyword filtering and a three-candidate cap within the existing reporting budget. Chinese keywords match unsegmented text, and Latin AI names match beside Chinese characters. Gemini favors original local ecosystem reporting and groups duplicate stories across languages. These outlets also publish global news and promotional material; publisher identity is not independent verification of every claim. Existing attribution, evidence and sponsored-content rules apply.

Translation covers the **supplied headline and excerpt**, not a full article. Gemini must produce English `headline`, `post` and `editor_note`, preserve uncertainty and currency, and correctly interpret Chinese numeric units (万 = 10,000; 亿 = 100,000,000). Telegram and dry runs display the English story headline. The Read article button opens the original Chinese source; the X button contains only the English post and configured attribution/link.

A Chinese-script guard rejects qualifying output with remaining Han characters, including an untranslated fallback headline. A failed translation leaves that batch unseen, preserves the existing queue, sends nothing and fails the run so the next run can retry within the discovery window. Cached untranslated drafts are held until corrected or expired. This guard detects leftover Chinese text, not translation accuracy or every possible non-English language. No extra model call or translation service is added.

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
| `min_importance` | `7` | Minimum Gemini score (1–10) for a story to be drafted. |
| `details_min_importance` | `7` | Official-source stories at or above this also get a [detail pack](ARTICLE_DETAILS.md). Remove to turn off. |
| `details_max_per_day` | `6` | Automatic detail packs per IST day. |
| `takes_for_all_stories` | `true` | Follow every other delivered draft with three opinion takes from its full article. |
| `takes_max_per_day` | `30` | Daily cap on those takes messages. |
| `lookback_hours` | `24` | Ignore items older than this (sources can override it). |
| `max_pending_stories` | `40` | Maximum saved qualifying drafts; retain the highest-priority stories. |
| `breaking_window_hours` | `6` | Breaking boost lasts this long; the smaller timely boost lasts twice as long. |
| `waiting_boost_hours` | `12` | Time to reach the capped +0.75 waiting boost. |
| `story_ttl_hours` | see above | Publication-based expiry for each source category. |
| `max_candidates` | `60` | Most items sent to Gemini per run. |
| `max_prompt_chars` | `48000` | Hard limit on the entire serialized prompt, not a token estimate. |
| `max_candidates_per_source` | `3` | Per-source cap; a feed's `max_candidates` can override it. |
| `candidate_slots` | see below | Category reservations; unused slots are reassigned. |
| `max_summary_fetches` | `12` | Maximum extra article-metadata requests per run, after shortlisting. |
| `fetch_workers` | `6` | Concurrent source fetches, bounded to 1–8. |
| `quiet_hours_ist` | `["23:00", "06:30"]` | No drafts in this window (IST); the backlog is handled afterwards. |
| `show_source` | `false` | End posts with "via <Outlet>". |
| `include_link` | `false` | End posts with the article link. |
| `attach_images` | `true` | Attach the article's own picture (feed image, or the page's og:image) for sources marked `images: true`. This does not grant reuse rights. |
| `gemini_model` | `gemini-flash-latest` | Main Gemini model. |
| `gemini_fallback_models` | see file | Tried in order if the main model is busy or retired. |

`persona` sets the voice Gemini writes in, and `keywords` decides which stories from general feeds count as AI news.

The default 60 slots reserve **24 for primary announcements, 8 for research/evaluations, 4 for policy, 4 for models, 4 for papers and 16 for reporting**. Within each category, selection rotates across sources. If a category lacks eligible items, its spare slots go to primary sources, policy, research, models, papers and reporting, in that order, without exceeding source caps. Lowering `max_candidates` proportionally scales reservations. The complete prompt limit can further reduce the batch; no discarded item is marked seen merely for exceeding a limit.

The prompt retains at most 40 recent covered headlines, each capped at 180 characters. All pending-story context, timestamps, longer research abstracts and METR's redaction caveats count toward the same prompt budget. Pending context is kept intact so duplicate and superseding new coverage can be compared against the queue; new candidates are reduced as needed. Raising the queue limit may require raising the prompt budget. Papers and models no longer automatically consume up to 18 slots ahead of all other news.

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
state/state.json              seen items, pending drafts and recent headlines (committed by the workflow)
tests/test_newsbot.py         offline test suite
tests/test_queue.py           editorial queue decisions, persistence and recovery tests
tests/test_editorial.py       editor-note isolation, queue compatibility and image opt-in
tests/x_tab_reuse.test.mjs     offline desktop tab-routing tests (Node.js)
desktop/x-tab-reuse/          optional Brave/Chrome/Edge extension
.github/workflows/newsbot.yml GitHub Actions workflow
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A run finishes in about 15 seconds | Quiet hours, no new items, or the daily cap was reached. The log says which. |
| `queue: 0 ready, 0 selected` | No unexpired qualifying drafts are available. |
| `Gemini unavailable … will retry next run` | Every model was busy. Existing drafts are retained; new items stay unseen and are retried. |
| `queue retired: … (undeliverable …)` | Telegram rejected that draft. Its article URL is in the log line if you want to post it by hand. |
| `Telegram delivery failed … draft retained until …` | Telegram, the destination, the network or an unrecognized error blocked delivery. Drafts stay queued until their editorial expiry. |
| `Telegram rate-limit cooldown until …` | Telegram requested a delay; delivery resumes after that timestamp. |
| `filters: … eligible=0` | Check `seen`, `queued`, `old` and `keyword_miss` counts to understand why discovery produced no candidates. |
| `model … not found` | Google retired a model. Update `gemini_fallback_models` from the [models page](https://aistudio.google.com/docs/models). |
| `feed FAILED` | The site blocked the request or changed its URL. Fix the URL or set `enabled: false`. |
| No runs at all | Check the external cron job and that its access token hasn't expired. |

## Editorial guidelines

- Drafts use each item's headline and public summary/feed excerpt (or a paper's abstract, or a model card). Each draft is one or two factual sentences, at most 240 characters before optional credits/links, with no opinion, assessment or commentary; final composition enforces the 280-character weighted limit.
- Facts must come from the supplied material. Direct inferences must be clearly conditional and grounded in it. A short excerpt cannot establish that the full source lacks information. Thin evidence should yield a short update and a specific check for the editor.
- Vendor claims and exclusive reporting need attribution in the sentence even when the trailing `via` credit is disabled. Distinguish measurements from forecasts and interpretation; preserve benchmark scope, release conditions and licensing caveats.
- The model must not invent the owner's personal beliefs, feelings, firsthand tests or expertise. No invented numbers or quotes, copy-pasted source sentences, hype or engagement bait.
- `editor_note` is separate editorial metadata, bounded and HTML-escaped for Telegram. It survives queueing and follows the draft when a primary source replaces it. Older queued drafts without notes still work; existing drafts are not regenerated just to change style.
- Read the full source, check the claims, and decide what you can personally contribute. The **Post on X** button prefills only the draft; edit it in X before publishing. Draft generation remains automated and human review is not an eligibility guarantee.
- Source images are optional reference material, not ready-to-reupload assets. Use your own visuals where useful and verify permission for any third-party material.

### X Original Content Rewards

As checked on 7 November 2026, [X's guidance](https://help.x.com/en/using-x/original-content-rewards) values substantive personal commentary over minimal rewrites or aggregation. It excludes content created or posted through automation, and discourages repeated engagement solicitation. The bot is therefore an editorial aid, not a rewards-eligibility solution. Prompt changes or manually clicking Post do not establish eligibility. Current entry criteria include 500 **verified** followers, 500,000 verified-user Home Timeline impressions over 90 days and a qualifying subscription, among other conditions; acceptance is not guaranteed.
