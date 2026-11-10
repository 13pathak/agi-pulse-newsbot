# Practical AI video recommendations

An independent companion to the X-news bot. It finds videos about **creative AI
projects**, **everyday work and no-code automation**, and **building AI applications**.
Its rules favor practical demonstrations and tutorials, not newsworthiness.

## What it sends

Up to three recommendations per IST day, starting after **09:00 IST**, with at most
one per category and one per creator that day. Empty categories stay empty when
nothing meets the threshold. Each Telegram card contains the task, apparent result,
tools, difficulty, prerequisites, cost evidence, caveat, creator claims and up to
three timestamp links. A review is an AI assessment of a video, not independent
reproduction of its claims.

Feedback buttons are **Useful**, **Too basic**, **Too technical**, and **More like
this**. One vote per recommendation adjusts category interest or target difficulty
within bounded limits; it cannot override the evidence gate. Feedback is collected
on the next workflow run, usually after the existing half-hour news check, with an
hourly GitHub schedule as fallback. It is not an instant-response bot: Telegram's
button acknowledgement may time out before the saved vote is processed.

## Setup and activation

1. Enable **YouTube Data API v3** in a Google Cloud project and add a repository
   secret named `YOUTUBE_API_KEY`. Restrict that key to YouTube Data API v3. If it is
   absent, the bot tries the existing `GEMINI_API_KEY`; that works only if its project
   has YouTube enabled and the key's API restrictions permit YouTube access.
2. Existing `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` are reused.
   Optionally set repository variable `VIDEO_GEMINI_MODEL` to a model supporting
   public YouTube video input. The default is `gemini-3.5-flash-lite`, which passed
   the initial live video preview. The video model setting is independent of the news bot.
3. Run **Practical AI Videos → check-config** in Actions. It checks a public YouTube
   search and a small Gemini text request; it does not send Telegram messages.
4. Run **inspect**, then **dry-run**. Download the `video-review` artifact to inspect
   candidates, evidence, scores and selected IDs. Preview runs neither consume
   Telegram feedback nor save the persistent queue. A successful workflow can have
   zero qualified recommendations; check the report, not just the green status.
   Partial discovery/review reports are also retained when an API request fails.
5. After inspecting actual recommendations, set repository variable
   `YOUTUBE_RECOMMENDATIONS_ENABLED` to `true` to enable automatic runs. It defaults
   to disabled. An explicit manual **normal** run sends immediately if the time and
   daily cap allow it, regardless of that automatic-run switch.

For a private chat, only that chat's owner can submit feedback. In a group chat,
also set secret `TELEGRAM_FEEDBACK_USER_ID` to the authorized user's numeric ID.
Polling refuses to run if the Telegram bot already has a webhook, and never deletes
or replaces one. Do not run another `getUpdates` consumer against the same bot; if
one must exist, set `VIDEO_FEEDBACK_POLLING=false` so the recommender stops polling.

## Discovery and ranking

- Search queries rotate twice daily through all three categories. Optional verified
  channel IDs or public `@handles` in `video_config.yaml` are polled through their
  uploads playlists. The initial channel list is empty: search can discover small
  creators without a minimum view/subscriber threshold.
- Add current model names to `model_watchlist` to include rotating model-specific
  searches. It is a curated list in this version, not an automatic claim that the
  model mentioned in a title was actually used. General task searches still run.
- Public video metadata supplies duration and upload time. Live/upcoming streams,
  private videos, undated/future uploads and videos outside the configured age and
  duration bounds are excluded. Default range: 1–40 minutes, uploaded within 14 days.
- Gemini first screens a bounded metadata shortlist, then inspects individual public
  video URLs. It must distinguish claims from apparent demonstrations and return
  specific in-range timestamp evidence. Metadata alone cannot earn a recommendation.
  Failed/unavailable/invalid video reviews remain retryable, never receive invented
  summaries, and do not become permanent rejections.
- Ranking: demonstration evidence 30%, reproducibility 25%, relevance 20%, clarity
  15%, recency 10%. These are initial editorial heuristics, not trained predictions.
  Evidence must score at least 6/10 and the adjusted total must reach 65. Strong older
  tutorials can outrank weak new uploads. AI scores and timestamps can still be wrong.
- Exact normalized workflow keys suppress repeated task/tool recommendations during
  the retention window. This is not perfect semantic deduplication; two differently
  worded keys can represent the same workflow. Category/creator diversity adds a
  second restraint on repetitive recommendations.

## Limits, state and failure behavior

Defaults allow 12 searches, two metadata-screening calls, six individual video
reviews and 120 minutes of reviewed video per IST day in persistent normal mode.
Each run reviews at most three videos. Provider quotas may be lower; previews use
real API calls too, and because they do not save counters, repeated previews consume
additional quota. Budget attempts are persisted before calls; queued metadata and
reviews survive subsequent runs. Cached ready videos need no further model review.

`state/videos.json` is separate from the news state. It stores public video metadata,
AI reviews, delivery IDs/times, aggregate preferences, per-video feedback choices
and the update offset. If using the supplied public GitHub repository, this committed
state is public too. It stores no Telegram user/chat IDs, message text or credentials.
Video records expire within 30 days; aggregate category preferences remain. Preview
artifacts contain public video metadata and reviews and expire after seven days.

Successful sends are saved individually. Telegram service/rate-limit failures retain
unsent recommendations and preserve earlier successes; rate-limit cooldowns persist.
A timeout after Telegram accepts a message, or a failure to push the saved state,
can still cause a duplicate on retry. Delivery and Git persistence are not atomic.
Corrupt state fails closed instead of resetting delivery history.

The workflow follows completed **AGI Pulse NewsBot** runs and has an hourly fallback.
It uses its own concurrency group and persists only its own state file. GitHub timers
are best effort; 09:00 is the earliest delivery time, not an exact appointment.

## Local commands

```bash
python -m pytest -q
python video_recommender.py --mode check-config
python video_recommender.py --mode inspect --report artifacts/video-review.json
python video_recommender.py --mode dry-run --report artifacts/video-review.json
python video_recommender.py --mode normal
```

The default command mode is `dry-run`. Configuration is in `video_config.yaml`.

## API references checked on 8 November 2026

- [YouTube search](https://developers.google.com/youtube/data/reference/rest/v3/search/list)
  and [uploads playlists](https://developers.google.com/youtube/v3/guides/implementation/videos).
- [Public YouTube input in Gemini](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding?hl=en):
  preview feature, subject to availability, model support and changing quotas/pricing.
  This implementation uses official video input; it does not scrape caption endpoints
  or download/reupload creators' video files.
- [YouTube caption downloads](https://developers.google.com/youtube/data/reference/rest/v3/captions/download)
  require permission to edit the video and are not a public transcript fallback.
