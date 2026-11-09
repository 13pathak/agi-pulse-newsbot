# Video takes (experimental)

Send the Telegram bot a link to an AI-related YouTube video. On its next run the
bot replies with:

- 4–6 concrete facts from the video (hardware, numbers, problems, results), each
  with a clickable timestamp so you can check it;
- 5 draft posts for X (3 opinion takes, 2 straight facts), each under 280
  characters with its own **✅ Post on X** button;
- one figure worth double-checking before you post.

It works from the video's **transcript** (captions), not by having Gemini watch
the video. That is cheap and fast, but it misses anything only shown on screen,
and auto-captions can garble model and part names.

## How it gets the transcript, and the main risk

YouTube's official API only lets a video's owner download its captions, so this
uses [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api),
an unofficial reader of YouTube's public caption data. YouTube often blocks such
requests from cloud servers, including GitHub Actions. When that happens the bot
replies "YouTube blocked the transcript request" instead of inventing anything.
The same command usually works from a home computer.

Test it before relying on it: **Actions → Video Takes → Run workflow**, mode
`fetch-test`, with a YouTube link. It only fetches the transcript and prints its
size and opening lines; no Gemini call, no Telegram message.

## Using it

1. Send a YouTube link (`youtube.com/watch`, `youtu.be`, `/shorts/`, `/live/`) to
   the bot. Other text gets a short help reply.
2. The workflow runs after each news run (about every 30 minutes) and hourly as a
   fallback, so expect the reply within about 30 minutes, not instantly.
3. For an immediate answer, run **Video Takes** manually with mode `send` and the
   link; the result is sent to your Telegram chat. Mode `analyze` prints it in the
   Actions log instead.

Only messages from `TELEGRAM_CHAT_ID` (and, if set, from `TELEGRAM_FEEDBACK_USER_ID`)
are answered. At most 15 videos are analyzed per IST day. Transcripts longer than
about 150,000 characters (roughly 2.5 hours) are trimmed to their first part.

## One bot, one reader

The Video Takes workflow is the single reader for the existing Telegram bot. It
routes incoming links to transcript analysis and recommendation button callbacks to
the practical video recommender's saved preferences. Both workflows use the same
concurrency group so they cannot overwrite each other's video state. Enabling the
recommender therefore does not require a second Telegram bot.

An optional `TELEGRAM_TAKES_BOT_TOKEN` still supports a separate Takes bot. When that
secret is present, Takes reads messages from that bot and the recommender polls its
own feedback inbox. Do not start an additional local polling process while either
workflow is active. If you remove Video Takes, set `VIDEO_FEEDBACK_POLLING` to
`'true'` in the recommender workflow so it resumes collecting its own feedback.

## State and privacy

`state/video_takes.json` stores only the Telegram update offset and today's count.
No message text, chat IDs or results are saved. The repository is public, so this
file is public too.
Recommendation feedback is saved separately in `state/videos.json`, as described
in [VIDEO_RECOMMENDER.md](VIDEO_RECOMMENDER.md).

## Local commands

```bash
pip install -r requirements-video-takes.txt
python video_takes.py --mode fetch-test --url "https://www.youtube.com/watch?v=vDmjw-c_R2k"
python video_takes.py --mode analyze --url "https://www.youtube.com/watch?v=vDmjw-c_R2k"
python -m pytest tests/test_video_takes.py -q
```

`analyze` and `send` need `GEMINI_API_KEY`; `send` and `poll` also need
`TELEGRAM_BOT_TOKEN` (or `TELEGRAM_TAKES_BOT_TOKEN`) and `TELEGRAM_CHAT_ID`.

## Removing the feature

Delete these files; nothing else in the repository depends on them:

- `video_takes.py`
- `VIDEO_TAKES.md`
- `requirements-video-takes.txt`
- `.github/workflows/video-takes.yml`
- `tests/test_video_takes.py`
- `state/video_takes.json` (created after the first poll)
