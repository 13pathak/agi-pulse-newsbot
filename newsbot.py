#!/usr/bin/env python3
"""
AGI Pulse NewsBot
=================
Every run (GitHub Actions, every 30 min):
  1. Reads AI news RSS feeds listed in config.yaml
  2. Drops anything already seen, too old, or not about AI
  3. Asks Gemini to group duplicate stories, score importance, and write
     an X post for the best ones (in the @AGI_Pulse voice)
  4. Sends each draft to your Telegram with a "Post on X" button that opens
     X with the text pre-filled — you review, edit if you like, and tap Post.

Nothing is ever posted to X without you. No X API, no API bill.

Usage:
  python newsbot.py               # normal run
  python newsbot.py --dry-run     # print drafts, don't send, don't save state
  python newsbot.py --check-feeds # test every feed and report which work
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state" / "state.json"
IST = timezone(timedelta(hours=5, minutes=30))
USER_AGENT = "Mozilla/5.0 (compatible; AGI-Pulse-NewsBot/1.0)"
X_LIMIT = 280
X_URL_LEN = 23  # X counts every link as 23 characters

log = logging.getLogger("newsbot")


# ───────────────────────── config & state ─────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("state file unreadable, starting fresh")
            data = {}
    else:
        data = {}
    data.setdefault("seen", {})      # item_id -> ISO time first seen
    data.setdefault("covered", [])   # [{"headline", "ts"}] stories already drafted
    data.setdefault("daily", {"date": "", "count": 0})
    return data


def save_state(state: dict, now: datetime, path: Path = STATE_PATH) -> None:
    week_ago = now - timedelta(days=7)
    two_days = now - timedelta(hours=48)
    state["seen"] = {k: v for k, v in state["seen"].items()
                     if datetime.fromisoformat(v) > week_ago}
    state["covered"] = [c for c in state["covered"]
                        if datetime.fromisoformat(c["ts"]) > two_days]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


# ───────────────────────── helpers ─────────────────────────

def in_quiet_hours(now_ist: datetime, window: list[str] | None) -> bool:
    if not window:
        return False
    start, end = (datetime.strptime(t, "%H:%M").time() for t in window)
    t = now_ist.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window crosses midnight


TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|cmp$|ito$)")


def normalize_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k)]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"),
                                    urllib.parse.urlencode(q), ""))


def item_id(url: str, title: str) -> str:
    key = normalize_url(url) if url else title.lower().strip()
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def clean_text(raw: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def build_keyword_regex(keywords: list[str]) -> re.Pattern:
    parts = [re.escape(k) for k in keywords]
    return re.compile(r"(?i:\b(?:" + "|".join(parts) + r")\b)|\bAI\b")


def x_char_weight(ch: str) -> int:
    """X's counting: Latin, Devanagari etc. count 1; CJK and emoji count 2."""
    cp = ord(ch)
    if (cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F
            or 0x2032 <= cp <= 0x2037):
        return 1
    if cp in (0xFE0F, 0x200D):  # emoji variation selector / joiner
        return 0
    return 2


URL_RE = re.compile(r"https?://\S+")


def x_len(text: str) -> int:
    total, last = 0, 0
    for m in URL_RE.finditer(text):
        total += sum(x_char_weight(c) for c in text[last:m.start()]) + X_URL_LEN
        last = m.end()
    return total + sum(x_char_weight(c) for c in text[last:])


def trim_to(text: str, budget: int) -> str:
    text = text.strip()
    if x_len(text) <= budget:
        return text
    while text and x_len(text + "…") > budget:
        cut = text.rsplit(" ", 1)[0] if " " in text else text[:-1]
        text = cut.rstrip(" ,;:-—")
    return text + "…"


def compose_post(body: str, source: str, url: str, include_link: bool) -> str:
    suffix = f"\n\nvia {source}"
    if include_link and url:
        suffix += f" {url}"
    return trim_to(body, X_LIMIT - x_len(suffix)) + suffix


# ───────────────────────── 1. fetch feeds ─────────────────────────

def fetch_feed(feed: dict) -> list[dict]:
    resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a valid feed ({parsed.bozo_exception})")
    items = []
    for e in parsed.entries:
        title = clean_text(e.get("title", ""), 300)
        link = e.get("link", "")
        if not title or not link:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = (datetime(*ts[:6], tzinfo=timezone.utc) if ts else None)
        items.append({
            "id": item_id(link, title),
            "title": title,
            "url": link,
            "source": feed["name"],
            "snippet": clean_text(e.get("summary", "") or e.get("description", "")),
            "published": published,
        })
    return items


def collect_candidates(config: dict, state: dict, now: datetime) -> list[dict]:
    s = config["settings"]
    kw = build_keyword_regex(config.get("keywords", []))
    cutoff = now - timedelta(hours=s["lookback_hours"])
    fresh, ids = [], set()
    for feed in config["feeds"]:
        if feed.get("enabled", True) is False:
            continue
        try:
            items = fetch_feed(feed)
            log.info("feed OK     %-26s %3d items", feed["name"], len(items))
        except Exception as exc:  # one broken feed must not stop the bot
            log.warning("feed FAILED %-26s %s", feed["name"], exc)
            continue
        for it in items:
            if it["id"] in state["seen"] or it["id"] in ids:
                continue
            if it["published"] and it["published"] < cutoff:
                continue
            if not feed.get("ai_only", False) and not kw.search(it["title"] + " " + it["snippet"]):
                continue
            ids.add(it["id"])
            fresh.append(it)
    fresh.sort(key=lambda i: i["published"] or now, reverse=True)
    return fresh[: s["max_candidates"]]


# ───────────────────────── 2. Gemini ─────────────────────────

PROMPT = """{persona}

TASK
Below are fresh AI-news items from several outlets (only the headline and the
feed's short blurb). Do three things:
1. Group items that report the SAME story.
2. Score each story's importance for an audience of AI enthusiasts, 1–10:
   9–10 = major model release from a top lab, landmark regulation, giant
          funding/acquisition, industry-shaking event
   7–8  = notable launch, significant research result, important Indian AI
          policy or Indian AI startup news
   5–6  = interesting but niche
   1–4  = opinion columns, listicles, how-tos, shopping deals, podcasts,
          sponsored posts, minor updates
   Add +1 for India-specific AI news (we have an Indian audience).
   Skip any story that is essentially the same as one in ALREADY COVERED.
3. For every story scoring {min_importance} or more, write ONE X post.

POST RULES
- Max 200 characters. English. Lead with the news, end with a one-line witty take.
- Your OWN words — do not copy phrases from the headline or blurb.
- Use ONLY facts present in the items. Never invent numbers, names, dates or
  quotes. If the blurb is thin, write less, not more.
- At most one emoji and at most one hashtag. No "BREAKING" unless it is a 10.
- Do NOT include any link or "via" credit; that is added automatically.

ALREADY COVERED (last 48 h):
{covered}

ITEMS:
{items}

Reply with JSON only, in exactly this shape:
{{"picks": [{{"primary_id": "c0", "related_ids": ["c4"], "importance": 8,
  "headline": "neutral 6–10 word summary of the story", "post": "the X post"}}]}}
Order picks from most to least important. Return {{"picks": []}} if nothing qualifies.
"""


def build_prompt(config: dict, state: dict, cands: list[dict]) -> str:
    items = [{"id": f"c{i}", "source": c["source"], "title": c["title"],
              "blurb": c["snippet"]} for i, c in enumerate(cands)]
    covered = "\n".join(f"- {c['headline']}" for c in state["covered"]) or "(none)"
    return PROMPT.format(persona=config["persona"].strip(),
                         min_importance=config["settings"]["min_importance"],
                         covered=covered,
                         items=json.dumps(items, ensure_ascii=False, indent=1))


def call_gemini(prompt: str, api_key: str, model: str, fallback: str | None = None) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    for attempt in range(3):
        r = requests.post(url, json=body, timeout=120,
                          headers={"x-goog-api-key": api_key})
        if r.status_code == 404 and fallback and fallback != model:
            log.warning("model %s not found, falling back to %s", model, fallback)
            return call_gemini(prompt, api_key, fallback, None)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            wait = 20 * (attempt + 1)
            log.warning("Gemini %s, retrying in %ss", r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        break
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def select_picks(result: dict, cands: list[dict], min_importance: int, limit: int) -> list[dict]:
    by_id = {f"c{i}": c for i, c in enumerate(cands)}
    picks = []
    for p in result.get("picks", []):
        primary = by_id.get(str(p.get("primary_id")))
        post = (p.get("post") or "").strip()
        try:
            score = int(p.get("importance", 0))
        except (TypeError, ValueError):
            continue
        if not primary or not post or score < min_importance:
            continue
        related = [by_id[r] for r in p.get("related_ids", []) if r in by_id and by_id[r] is not primary]
        picks.append({"item": primary, "related": related, "importance": score,
                      "headline": (p.get("headline") or primary["title"]).strip(), "post": post})
    picks.sort(key=lambda p: p["importance"], reverse=True)
    return picks[:limit]


# ───────────────────────── 3. Telegram ─────────────────────────

def intent_url(text: str) -> str:
    return "https://x.com/intent/tweet?text=" + urllib.parse.quote(text, safe="")


def telegram_message(pick: dict, post: str) -> str:
    it = pick["item"]
    others = sorted({r["source"] for r in pick["related"]} - {it["source"]})
    lines = [f"📰 <b>{html.escape(it['source'])}</b> · importance {pick['importance']}/10",
             f"<i>{html.escape(it['title'])}</i>", "",
             html.escape(post), "",
             f"<code>{x_len(post)}/280 characters</code>"]
    if others:
        lines.append("Also covered by: " + html.escape(", ".join(others)))
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, buttons: list[list[dict]]) -> None:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {"inline_keyboard": buttons},
    })
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")


# ───────────────────────── main ─────────────────────────

def check_feeds(config: dict) -> int:
    bad = 0
    for feed in config["feeds"]:
        try:
            items = fetch_feed(feed)
            newest = max((i["published"] for i in items if i["published"]), default=None)
            print(f"OK      {feed['name']:<26} {len(items):>3} items, newest {newest}")
        except Exception as exc:
            bad += 1
            print(f"FAILED  {feed['name']:<26} {exc}")
    print(f"\n{len(config['feeds']) - bad} working, {bad} failed")
    return 0


def run(dry_run: bool = False, now: datetime | None = None,
        config_path: Path = CONFIG_PATH, state_path: Path = STATE_PATH) -> int:
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    config = load_config(config_path)
    s = config["settings"]

    if in_quiet_hours(now_ist, s.get("quiet_hours_ist")) and not dry_run:
        log.info("quiet hours (%s IST) — sleeping", now_ist.strftime("%H:%M"))
        return 0

    state = load_state(state_path)
    today = now_ist.date().isoformat()
    if state["daily"]["date"] != today:
        state["daily"] = {"date": today, "count": 0}
    room = min(s["max_drafts_per_run"], s["max_drafts_per_day"] - state["daily"]["count"])
    if room <= 0:
        log.info("daily limit of %s drafts reached", s["max_drafts_per_day"])
        return 0

    cands = collect_candidates(config, state, now)
    log.info("%d new AI items", len(cands))
    if not cands:
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY is not set")
        return 1
    model = os.environ.get("GEMINI_MODEL") or s.get("gemini_model", "gemini-flash-latest")
    result = call_gemini(build_prompt(config, state, cands), api_key, model,
                         s.get("gemini_fallback_model"))
    picks = select_picks(result, cands, s["min_importance"], room)
    log.info("Gemini picked %d stories", len(picks))

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not dry_run and not (token and chat_id):
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1

    for pick in picks:
        it = pick["item"]
        post = compose_post(pick["post"], it["source"], it["url"], s.get("include_link", True))
        if dry_run:
            print("─" * 60)
            print(f"[{pick['importance']}/10] {it['source']}: {it['title']}")
            print(post)
            print(f"({x_len(post)}/280)  {intent_url(post)[:90]}…")
            continue
        buttons = [[{"text": "✅ Post on X", "url": intent_url(post)}],
                   [{"text": "📖 Read article", "url": it["url"]}]]
        send_telegram(token, chat_id, telegram_message(pick, post), buttons)
        state["covered"].append({"headline": pick["headline"], "ts": now.isoformat()})
        state["daily"]["count"] += 1

    if dry_run:
        return 0
    for c in cands:  # everything Gemini has judged is now "seen"
        state["seen"][c["id"]] = now.isoformat()
    save_state(state, now, state_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print drafts; don't send or save state")
    ap.add_argument("--check-feeds", action="store_true", help="test every feed and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.check_feeds:
        return check_feeds(load_config())
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
