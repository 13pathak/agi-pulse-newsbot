"""Video takes: send the bot a YouTube link, get facts and draft X posts back.

Experimental and self-contained. It reads the video's public captions through
youtube-transcript-api (an unofficial caption reader), asks Gemini for concrete
facts and short post drafts grounded in that transcript, and replies in Telegram.
To remove the feature, delete the files listed in VIDEO_TAKES.md.

Modes:
  poll        read new Telegram messages, answer YouTube links, save state
  fetch-test  only fetch the transcript for --url and print stats (no Gemini)
  analyze     analyze --url and print the result (no Telegram, no state)
  send        analyze --url and send the result to the Telegram chat
"""
import argparse
import html
import json
import logging
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state" / "video_takes.json"
NEWS_CONFIG = ROOT / "config.yaml"
IST = timezone(timedelta(hours=5, minutes=30))

MAX_PER_DAY = 15            # Gemini calls for link requests per IST day
MAX_TRANSCRIPT_CHARS = 150_000  # roughly a 2.5-hour talk; longer is trimmed
CHUNK_SECONDS = 20          # transcript lines are merged into chunks this long
MAX_POST_CHARS = 280
TELEGRAM_LIMIT = 4096

log = logging.getLogger("video_takes")


class TakesError(Exception):
    """A failure whose message is safe to show the user in Telegram."""


# ---------------------------------------------------------------- links

VIDEO_ID = r"([A-Za-z0-9_-]{11})"
LINK_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:[^\s#]*&)?v=" + VIDEO_ID),
    re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(?:shorts|live|embed)/" + VIDEO_ID),
    re.compile(r"(?:https?://)?youtu\.be/" + VIDEO_ID),
]


def find_video_id(text):
    """Return the first YouTube video ID linked in the text, or None."""
    best = None
    for pattern in LINK_PATTERNS:
        match = pattern.search(text or "")
        if match and (best is None or match.start() < best.start()):
            best = match
    return best.group(1) if best else None


def youtube_url(video_id, seconds=None):
    return f"https://www.youtube.com/watch?v={video_id}" + (f"&t={int(seconds)}s" if seconds is not None else "")


def clock(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


# ---------------------------------------------------------------- transcript

def fetch_transcript(video_id):
    """Return {"language", "generated", "duration", "snippets": [(start, text)]}.

    Prefers English captions (manual, then automatic), then any caption track
    translated to English, then any caption track as is.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api import _errors as yt_errors
    except ImportError:
        raise TakesError("youtube-transcript-api is not installed") from None

    blocked = tuple(getattr(yt_errors, n) for n in ("RequestBlocked", "IpBlocked", "PoTokenRequired")
                    if hasattr(yt_errors, n))
    missing = tuple(getattr(yt_errors, n) for n in ("TranscriptsDisabled", "NoTranscriptFound")
                    if hasattr(yt_errors, n))
    unavailable = tuple(getattr(yt_errors, n) for n in ("VideoUnavailable", "VideoUnplayable",
                                                       "AgeRestricted", "InvalidVideoId")
                        if hasattr(yt_errors, n))
    api = YouTubeTranscriptApi()
    try:
        tracks = api.list(video_id)
        try:
            track = tracks.find_transcript(["en", "en-US", "en-GB"])
        except missing:
            track = next(iter(tracks))
            if not track.language_code.startswith("en") and track.is_translatable:
                try:
                    return _transcript(track.translate("en").fetch())
                except (yt_errors.CouldNotRetrieveTranscript, requests.RequestException, TakesError):
                    pass  # translation unavailable: use the original language; Gemini can read it
        fetched = track.fetch()
    except blocked:
        raise TakesError("YouTube blocked the transcript request from this server "
                         "(it often blocks cloud servers such as GitHub Actions).") from None
    except missing:
        raise TakesError("This video has no captions, so there is no transcript to read.") from None
    except unavailable:
        raise TakesError("YouTube says this video is unavailable, private or age-restricted.") from None
    except yt_errors.CouldNotRetrieveTranscript as exc:
        raise TakesError(f"Could not read the transcript ({type(exc).__name__}).") from None
    except StopIteration:
        raise TakesError("This video has no captions, so there is no transcript to read.") from None
    except requests.RequestException:
        raise TakesError("Network error while reading the transcript.") from None
    return _transcript(fetched)


def _transcript(fetched):
    snippets = [(float(s.start), s.text.replace("\n", " ").strip())
                for s in fetched.snippets if s.text and s.text.strip()]
    if not snippets:
        raise TakesError("The transcript is empty.")
    last = fetched.snippets[-1]
    return {"language": fetched.language_code, "generated": bool(fetched.is_generated),
            "duration": float(last.start) + float(last.duration or 0), "snippets": snippets}


def format_transcript(snippets, chunk_seconds=CHUNK_SECONDS, max_chars=MAX_TRANSCRIPT_CHARS):
    """Merge caption lines into "[m:ss] text" chunks; returns (text, trimmed)."""
    lines, chunk, chunk_start = [], [], None
    for start, text in snippets:
        if chunk_start is None:
            chunk_start = start
        if start - chunk_start >= chunk_seconds and chunk:
            lines.append(f"[{clock(chunk_start)}] {' '.join(chunk)}")
            chunk, chunk_start = [], start
        chunk.append(text)
    if chunk:
        lines.append(f"[{clock(chunk_start)}] {' '.join(chunk)}")
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            return "\n".join(out), True
        out.append(line)
        used += len(line) + 1
    return "\n".join(out), False


def video_title(video_id):
    """Public title/channel through YouTube oEmbed; never fatal."""
    try:
        response = requests.get("https://www.youtube.com/oembed",
                                params={"url": youtube_url(video_id), "format": "json"}, timeout=(5, 10))
        if response.ok:
            data = response.json()
            return str(data.get("title") or "")[:200], str(data.get("author_name") or "")[:100]
    except (requests.RequestException, ValueError):
        pass
    return "", ""


# ---------------------------------------------------------------- Gemini

PROMPT = """You turn an AI-related YouTube video into material for an X (Twitter) account
about AI. You only have the video's transcript (captions, possibly auto-generated,
so product names and numbers can be misspelled). Timestamps are in [m:ss] form.

Video title: {title}
Channel: {channel}

Return JSON only, in exactly this shape:
{{
  "ai_related": true,
  "facts": [{{"seconds": 754, "text": "specific claim, number, spec or result from the video, max 200 chars"}}],
  "posts": [{{"kind": "opinion", "text": "draft post"}}, {{"kind": "fact", "text": "draft post"}}],
  "check": "the one fact or figure most worth verifying before posting, and why, max 200 chars"
}}

Facts (4 to 6):
- Concrete and specific: exact hardware, models, numbers, prices, limits, results,
  problems hit and how they were solved. Skip sponsor reads, intros and generic claims.
- Each must be stated in the transcript; "seconds" is the start of the chunk where it is said.
- If the creator only claims something, say so ("claims", "says").

Posts (exactly 5: 3 with kind "opinion", 2 with kind "fact"):
- Each at most 260 characters, plain text, one or two sentences.
- Each built around one specific detail from the video (a number, part, model or
  problem), so a reader learns something concrete. Different detail per post.
- "opinion": a sharp take on what the detail means, phrased as an opinion, e.g.
  "I think ...", "Seems like ...", "The real bottleneck is ...". Opinions only:
  never invent personal experience (no "I built", "I tested", "my rig").
- "fact": a straight, punchy statement of a notable detail or common problem.
- No hashtags, emojis, links, @mentions, quotes from the title, or engagement bait.
  Do not mention "the video" or the creator; write as a standalone post.
- Every number or spec must come from the transcript. Fix obvious caption
  misspellings of well-known product names only when you are certain.

Set "ai_related" to false if the video is not substantially about AI, models,
AI hardware or AI tools (still fill the other fields from what is there).

Transcript{trimmed}:
{transcript}
"""


def gemini_models():
    models = [os.environ.get("VIDEO_TAKES_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL")]
    try:
        settings = yaml.safe_load(NEWS_CONFIG.read_text(encoding="utf-8")).get("settings", {})
        models += [settings.get("gemini_model")] + list(settings.get("gemini_fallback_models") or [])
    except (OSError, AttributeError, yaml.YAMLError):
        pass
    models.append("gemini-flash-latest")
    return list(dict.fromkeys(m for m in models if m))


def call_gemini(key, prompt, models=None):
    last_error = "no model available"
    for model in models or gemini_models():
        # Same settings the video recommender validated live: without low thinking,
        # the model's reasoning can use up the output budget and cut the JSON short.
        generation = {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 8192}
        if model.startswith("gemini-3") or model == "gemini-flash-latest":
            generation["thinkingConfig"] = {"thinkingLevel": "LOW"}
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key}, timeout=(10, 120),
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": generation})
        except requests.RequestException:
            last_error = f"{model}: request failed"
            continue
        if not response.ok:
            last_error = f"{model}: HTTP {response.status_code}"
            log.warning("Gemini %s", last_error)
            if response.status_code in (401, 403):
                break  # bad key; other models will fail the same way
            continue  # 400 can be model-specific (unsupported setting), so try the next one
        try:
            candidate = response.json()["candidates"][0]
            if candidate.get("finishReason") not in (None, "STOP"):
                last_error = f"{model}: stopped early ({candidate.get('finishReason')})"
                log.warning("Gemini %s", last_error)
                continue
            text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
            data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
            if isinstance(data, dict):
                return data
            last_error = f"{model}: unexpected JSON"
        except (KeyError, IndexError, TypeError, ValueError):
            last_error = f"{model}: incomplete response"
    log.warning("Gemini failed: %s", last_error)
    raise TakesError("Gemini could not analyze the transcript right now. Send the link again later.")


def clean(value, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def validate(raw, duration):
    """Keep only well-formed facts and posts that fit on X."""
    facts = []
    for item in raw.get("facts") or []:
        if not isinstance(item, dict):
            continue
        text = clean(item.get("text"), 220)
        seconds = item.get("seconds")
        if not text:
            continue
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= duration + 5:
            seconds = None
        facts.append({"seconds": int(seconds) if seconds is not None else None, "text": text})
    posts = []
    for item in raw.get("posts") or []:
        if not isinstance(item, dict):
            continue
        text = clean(item.get("text"), 1000)
        if not text or len(text) > MAX_POST_CHARS or text in (p["text"] for p in posts):
            continue
        kind = "fact" if item.get("kind") == "fact" else "opinion"
        posts.append({"kind": kind, "text": text})
    if not posts:
        raise TakesError("Gemini returned no usable posts. Send the link again later.")
    return {"ai_related": raw.get("ai_related") is not False, "facts": facts[:6], "posts": posts[:6],
            "check": clean(raw.get("check"), 220)}


def analyze(video_id, gemini_key):
    transcript = fetch_transcript(video_id)
    text, trimmed = format_transcript(transcript["snippets"])
    title, channel = video_title(video_id)
    prompt = PROMPT.format(title=title or "unknown", channel=channel or "unknown",
                           trimmed=" (trimmed to the first part)" if trimmed else "", transcript=text)
    result = validate(call_gemini(gemini_key, prompt), transcript["duration"])
    result.update(video_id=video_id, title=title, channel=channel, language=transcript["language"],
                  generated=transcript["generated"], duration=transcript["duration"], trimmed=trimmed)
    return result


# ---------------------------------------------------------------- Telegram output

def x_intent(text):
    return "https://x.com/intent/tweet?text=" + urllib.parse.quote(text, safe="")


def render(result):
    """Return (html_text, inline_keyboard) for one analysis."""
    esc = html.escape
    vid = result["video_id"]
    head = f'🎬 <b>{esc(result["title"] or "YouTube video")}</b>'
    if result["channel"]:
        head += f' · {esc(result["channel"])}'
    source = "auto-generated" if result["generated"] else "creator"
    meta = f'<i>From {source} captions ({esc(result["language"])}), {clock(result["duration"])}'
    meta += ", only the first part was read" if result["trimmed"] else ""
    meta += "</i>"
    lines = [head, meta]
    if not result["ai_related"]:
        lines.append("⚠️ This doesn't look like an AI video.")

    post_lines = ["", "<b>Draft posts</b>"]
    for number, post in enumerate(result["posts"], 1):
        icon = "💭" if post["kind"] == "opinion" else "📌"
        post_lines.append(f'{number}. {icon} {esc(post["text"])} <i>({len(post["text"])})</i>')
    tail = [f'\n🔎 <b>Check:</b> {esc(result["check"])}'] if result["check"] else []

    fact_lines = ["", "<b>From the video</b>"] if result["facts"] else []
    for fact in result["facts"]:
        stamp = ""
        if fact["seconds"] is not None:
            stamp = f' (<a href="{esc(youtube_url(vid, fact["seconds"]), quote=True)}">{clock(fact["seconds"])}</a>)'
        candidate = f'• {esc(fact["text"])}{stamp}'
        if len("\n".join(lines + fact_lines + [candidate] + post_lines + tail)) > TELEGRAM_LIMIT - 50:
            break
        fact_lines.append(candidate)

    buttons = [{"text": f"✅ Post {n} on X", "url": x_intent(p["text"])} for n, p in enumerate(result["posts"], 1)]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard.append([{"text": "▶ Watch video", "url": youtube_url(vid)}])
    return "\n".join(lines + fact_lines + post_lines + tail)[:TELEGRAM_LIMIT], keyboard


def telegram(token, method, **payload):
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=(10, 30))
        data = response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError(f"Telegram {method} failed") from None
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description', response.status_code)}")
    return data.get("result")


def send(token, chat_id, text, keyboard=None, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    return telegram(token, "sendMessage", **payload)


# ---------------------------------------------------------------- polling

HELP = ("Send me a YouTube link to an AI video and I'll reply with facts from it and "
        "draft posts for X. Replies come on the next bot run (usually within 30 minutes).")


def load_state(path=STATE_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "update_offset": 0, "daily": {"date": "", "count": 0}}


def save_state(state, path=STATE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def authorized(message, chat_id, allowed_user=None):
    if str(message.get("chat", {}).get("id")) != str(chat_id):
        return False
    if allowed_user:
        return str(message.get("from", {}).get("id")) == str(allowed_user)
    # A private chat has one person. In a group anyone could post links, so a
    # group needs TELEGRAM_FEEDBACK_USER_ID, as the recommender's feedback does.
    return not str(chat_id).startswith("-")


def handle_message(message, state, now, gemini_key, analyzer=analyze):
    """Return (text, keyboard) to reply with, or None to stay silent."""
    text = message.get("text") or message.get("caption") or ""
    video_id = find_video_id(text)
    if not video_id:
        return (HELP, None) if text.strip() else None
    today = now.astimezone(IST).date().isoformat()
    daily = state.setdefault("daily", {"date": today, "count": 0})
    if daily.get("date") != today:
        daily.update(date=today, count=0)
    if daily["count"] >= MAX_PER_DAY:
        return f"Daily limit of {MAX_PER_DAY} videos reached. Send it again tomorrow.", None
    try:
        result = analyzer(video_id, gemini_key)
    except TakesError as exc:
        return f"⚠️ Couldn't analyze that video: {html.escape(str(exc))}", None
    daily["count"] += 1  # only successful analyses count toward the daily limit
    return render(result)


def poll(token, chat_id, gemini_key, allowed_user=None, state_path=STATE_PATH, now=None, analyzer=analyze,
         video_state_path=None):
    # One reader routes both messages and recommendation callbacks. A separately
    # configured Takes bot has its own inbox and only needs message updates.
    shared_inbox = not os.environ.get("TELEGRAM_TAKES_BOT_TOKEN")
    feedback_state = None
    if shared_inbox:
        import video_recommender as recommendations
        video_state_path = video_state_path or recommendations.STATE_PATH
        feedback_state = recommendations.load_state(video_state_path)
    if telegram(token, "getWebhookInfo").get("url"):
        raise RuntimeError("This Telegram bot has a webhook; polling would conflict with it")
    state = load_state(state_path)
    if shared_inbox:
        state["update_offset"] = max(state.get("update_offset", 0), feedback_state["update_offset"])
    updates = telegram(token, "getUpdates", offset=state.get("update_offset", 0), timeout=0,
                       limit=20, allowed_updates=["message", "callback_query"] if shared_inbox else ["message"])
    handled = 0
    for update in updates:
        update_id = update.get("update_id")
        if type(update_id) is not int or update_id < state.get("update_offset", 0):
            continue
        callback = update.get("callback_query")
        if callback and shared_inbox:
            feedback_user = allowed_user or (chat_id if int(chat_id) > 0 else None)
            accepted = bool(feedback_user) and recommendations.apply_feedback(
                feedback_state, update, chat_id, feedback_user)
            feedback_state["update_offset"] = update_id + 1
            recommendations.save_state(feedback_state, video_state_path)
            state["update_offset"] = update_id + 1
            save_state(state, state_path)
            if callback.get("id"):
                try:
                    telegram(token, "answerCallbackQuery", callback_query_id=callback["id"],
                             text="Feedback saved" if accepted else "Feedback already recorded or unavailable")
                except RuntimeError:
                    pass  # Telegram can expire the acknowledgement before the next scheduled poll.
            handled += 1
            continue
        # Advance first: a message that crashes the bot must not block the queue forever.
        state["update_offset"] = update_id + 1
        save_state(state, state_path)
        message = update.get("message") or {}
        if not authorized(message, chat_id, allowed_user):
            continue
        reply = handle_message(message, state, now or datetime.now(timezone.utc), gemini_key, analyzer)
        save_state(state, state_path)
        if reply:
            text, keyboard = reply
            try:
                send(token, chat_id, text, keyboard, reply_to=message.get("message_id"))
            except RuntimeError as exc:
                log.error("Reply failed: %s", exc)
                send(token, chat_id, "⚠️ I analyzed that video but couldn't send the result. "
                     "Please send the link again.", reply_to=message.get("message_id"))
        handled += 1
    log.info("Handled %d message(s)", handled)
    return handled


# ---------------------------------------------------------------- CLI

def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["poll", "fetch-test", "analyze", "send"], default="analyze")
    parser.add_argument("--url", help="YouTube link (fetch-test, analyze, send)")
    args = parser.parse_args(argv)

    if args.mode == "poll":
        token = os.environ.get("TELEGRAM_TAKES_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id, key = os.environ.get("TELEGRAM_CHAT_ID"), os.environ.get("GEMINI_API_KEY")
        if not (token and chat_id and key):
            log.error("Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and GEMINI_API_KEY")
            return 1
        poll(token, chat_id, key, os.environ.get("TELEGRAM_FEEDBACK_USER_ID"))
        return 0

    video_id = find_video_id(args.url or "")
    if not video_id:
        log.error("Pass a YouTube link with --url")
        return 1
    try:
        if args.mode == "fetch-test":
            t = fetch_transcript(video_id)
            text, trimmed = format_transcript(t["snippets"])
            print(f"OK: {len(t['snippets'])} caption lines, language {t['language']}, "
                  f"{'auto-generated' if t['generated'] else 'creator'} captions, "
                  f"{clock(t['duration'])} long, {len(text)} chars{' (trimmed)' if trimmed else ''}")
            print(text[:600])
            return 0
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            log.error("Set GEMINI_API_KEY")
            return 1
        result = analyze(video_id, key)
    except TakesError as exc:
        log.error("%s", exc)
        return 2
    text, keyboard = render(result)
    if args.mode == "send":
        token = os.environ.get("TELEGRAM_TAKES_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        send(token, os.environ["TELEGRAM_CHAT_ID"], text, keyboard)
        log.info("Sent to Telegram")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
