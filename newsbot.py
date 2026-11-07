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
    data.setdefault("pages", {})     # url -> {title, desc, image, published} for page-list sources
    return data


def save_state(state: dict, now: datetime, path: Path = STATE_PATH) -> None:
    week_ago = now - timedelta(days=7)
    two_days = now - timedelta(hours=48)
    state["seen"] = {k: v for k, v in state["seen"].items()
                     if datetime.fromisoformat(v) > week_ago}
    state["covered"] = [c for c in state["covered"]
                        if datetime.fromisoformat(c["ts"]) > two_days]
    pages = state.get("pages", {})
    state["pages"] = dict(list(pages.items())[-500:])   # newest 500 pages is plenty
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


def compose_post(body: str, source: str, url: str, include_link: bool,
                 show_source: bool = True) -> str:
    # One blank line between sentences/lines, however Gemini spaced them.
    body = "\n\n".join(line.strip() for line in body.splitlines() if line.strip())
    tail = ([f"via {source}"] if show_source else []) + ([url] if include_link and url else [])
    suffix = "\n\n" + " ".join(tail) if tail else ""
    return trim_to(body, X_LIMIT - x_len(suffix)) + suffix


# ───────────────────────── 1. fetch feeds ─────────────────────────

def fetch_hf_papers(feed: dict) -> list[dict]:
    """Hugging Face Daily Papers: community-upvoted arXiv papers, with full abstracts.
    Only papers with at least `min_upvotes` votes, the `max_items` most upvoted.
    The undated list isn't newest-first, so ask for each of the last few days by date."""
    today = datetime.now(timezone.utc).date()
    entries = []
    for back in range(feed.get("days", 3)):
        resp = requests.get(feed["url"], params={"date": (today - timedelta(days=back)).isoformat(), "limit": 100},
                            headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        entries += resp.json()
    papers, seen_ids = [], set()
    for entry in entries:
        p = entry.get("paper") or {}
        title, pid, upvotes = clean_text(p.get("title", ""), 300), p.get("id"), int(p.get("upvotes") or 0)
        if not title or not pid or pid in seen_ids or upvotes < feed.get("min_upvotes", 0):
            continue
        seen_ids.add(pid)
        # when it was listed on Daily Papers (not when it hit arXiv)
        when = p.get("submittedOnDailyAt") or entry.get("publishedAt") or p.get("publishedAt")
        org = (p.get("organization") or entry.get("organization") or {}).get("fullname", "")
        abstract = clean_text(p.get("summary", ""), feed.get("blurb_chars", 1500))
        link = f"https://arxiv.org/abs/{pid}"
        papers.append({
            "id": item_id(link, title),
            "title": title,
            "url": link,
            "source": feed["name"],
            "kind": "paper",
            "snippet": (f"Lab: {org}. " if org else "") + f"Upvotes: {upvotes}. Abstract: {abstract}",
            "published": datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None,
            "upvotes": upvotes,
            "image": entry.get("thumbnail") or "",   # Hugging Face's preview card for the paper
        })
    papers.sort(key=lambda x: x["upvotes"], reverse=True)
    return papers[: feed.get("max_items", 10)]


def page_meta(page: str, *names: str) -> str:
    """First <meta property|name=...> content found among `names`."""
    for n in names:
        n = re.escape(n)
        m = (re.search(rf'<meta[^>]+(?:property|name)="{n}"[^>]*\scontent="([^"]*)"', page)
             or re.search(rf'<meta[^>]+content="([^"]*)"[^>]*\s(?:property|name)="{n}"', page))
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def og_image(url: str) -> str:
    """The article's preview image (og:image), or "" if there isn't one."""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        return page_meta(r.text, "og:image", "twitter:image") if r.ok else ""
    except requests.RequestException:
        return ""


def fetch_page_list(feed: dict, cache: dict) -> list[dict]:
    """Sites with no RSS (e.g. Anthropic): read article links from a listing page,
    open each NEW link once for its title, summary, image and publish date, and
    remember it in `cache` so later runs don't fetch it again."""
    resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    base = feed.get("base", feed["url"])
    links = list(dict.fromkeys(urllib.parse.urljoin(base, h)
                               for h in re.findall(rf'href="({feed["link_pattern"]})"', resp.text)))
    fetched = 0
    for url in links:
        if url in cache or fetched >= feed.get("max_new_pages", 15):
            continue
        fetched += 1
        try:
            page = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15).text
        except requests.RequestException:
            continue
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page)
        cache[url] = {"title": page_meta(page, "og:title", "twitter:title"),
                      "desc": page_meta(page, "og:description", "description"),
                      "image": page_meta(page, "og:image", "twitter:image"),
                      "published": (m.group(1) if m else "")
                                   or page_meta(page, "article:published_time")}
    items = []
    for url in links:
        meta = cache.get(url) or {}
        if not meta.get("published") or not meta.get("title"):   # category pages etc.
            continue
        try:
            published = datetime.fromisoformat(meta["published"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        title = clean_text(meta["title"], 300)
        items.append({"id": item_id(url, title), "title": title, "url": url,
                      "source": feed["name"], "image": meta.get("image", ""),
                      "snippet": clean_text(meta.get("desc", ""), feed.get("blurb_chars", 400)),
                      "published": published})
    return items


REPACKAGED = re.compile(r"gguf|awq|gptq|fp8|fp4|nvfp4|mlx|exl2|bnb|int4|int8|4bit|8bit|quant|uncensored|abliterated",
                        re.IGNORECASE)


def model_card_excerpt(model_id: str, limit: int) -> str:
    """Plain-text start of a model's README, without front matter, images, tables or HTML."""
    try:
        r = requests.get(f"https://huggingface.co/{model_id}/raw/main/README.md",
                         headers={"User-Agent": USER_AGENT}, timeout=15)
        text = r.text if r.ok else ""
    except requests.RequestException:
        text = ""
    text = re.sub(r"\A---.*?---", " ", text, flags=re.DOTALL)        # YAML front matter
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                 # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)              # links -> text
    text = re.sub(r"^\s*\|.*$", " ", text, flags=re.MULTILINE)        # tables
    text = re.sub(r"<!--.*?-->|<[^>]+>", " ", text, flags=re.DOTALL)  # HTML, before stripping ">"
    text = re.sub(r"[#>*`_]+", " ", text)
    return clean_text(text, limit)


def fetch_hf_models(feed: dict, cache: dict) -> list[dict]:
    """New models from major labs that are trending on Hugging Face (often the first
    sign of an open-model release). Skips re-packaged copies and other uploaders."""
    resp = requests.get(feed["url"], params={"sort": "trendingScore", "limit": 100},
                        headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    orgs = {o.lower() for o in feed.get("orgs", [])}
    newest = datetime.now(timezone.utc) - timedelta(days=feed.get("max_age_days", 7))
    items = []
    for m in resp.json():
        mid, author = m.get("id", ""), (m.get("author") or m.get("id", "").split("/")[0])
        created = m.get("createdAt")
        if author.lower() not in orgs or not created or REPACKAGED.search(mid):
            continue
        published = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if published < newest:
            continue
        url = f"https://huggingface.co/{mid}"
        if url not in cache:   # read each model card once
            cache[url] = {"card": model_card_excerpt(mid, feed.get("blurb_chars", 1200))}
        name = mid.split("/", 1)[1]
        items.append({
            "id": item_id(url, mid),
            "title": f"{author} releases {name} on Hugging Face",
            "url": url,
            "source": feed["name"],
            "kind": "model",
            "snippet": (f"Open model {mid} by {author}. Task: {m.get('pipeline_tag') or 'unspecified'}. "
                        f"Likes: {m.get('likes', 0)}. Model card: {cache[url].get('card', '')}"),
            "published": published,
            "trend": m.get("trendingScore", 0),
            "image": f"https://cdn-thumbnails.huggingface.co/social-thumbnails/models/{mid}.png",
        })
    items.sort(key=lambda x: x["trend"], reverse=True)
    return items[: feed.get("max_items", 8)]


def fetch_feed(feed: dict, cache: dict | None = None) -> list[dict]:
    if feed.get("type") == "hf_papers":
        return fetch_hf_papers(feed)
    if feed.get("type") == "hf_models":
        return fetch_hf_models(feed, {} if cache is None else cache)
    if feed.get("type") == "page_list":
        return fetch_page_list(feed, {} if cache is None else cache)
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
        media = (e.get("media_content") or e.get("media_thumbnail") or [{}])[0].get("url") or next(
            (l.get("href") for l in e.get("links", []) if l.get("type", "").startswith("image")), "")
        items.append({
            "id": item_id(link, title),
            "title": title,
            "url": link,
            "source": feed["name"],
            "image": media or "",
            "snippet": clean_text(e.get("summary", "") or e.get("description", ""),
                                  feed.get("blurb_chars", 400)),
            "published": published,
        })
    return items


def collect_candidates(config: dict, state: dict, now: datetime) -> list[dict]:
    s = config["settings"]
    kw = build_keyword_regex(config.get("keywords", []))
    fresh, ids = [], set()
    for feed in config["feeds"]:
        if feed.get("enabled", True) is False:
            continue
        cutoff = now - timedelta(hours=feed.get("lookback_hours", s["lookback_hours"]))
        try:
            items = fetch_feed(feed, state.setdefault("pages", {}))
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
            it["images"] = bool(feed.get("images", False))   # may we offer this source's picture?
            fresh.append(it)
    fresh.sort(key=lambda i: i["published"] or now, reverse=True)
    # Papers are already capped per feed and can be up to 48 h old, so they don't
    # compete with the newest news for the max_candidates slots.
    papers = sorted((i for i in fresh if i.get("kind") == "paper"),
                    key=lambda i: i.get("upvotes", 0), reverse=True)
    models = [i for i in fresh if i.get("kind") == "model"]   # already capped, trend order
    models.sort(key=lambda i: i.get("trend", 0), reverse=True)
    news = [i for i in fresh if i.get("kind") not in ("paper", "model")]
    return (papers + models + news)[: s["max_candidates"]]


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
   Research papers (items with "kind": "paper"; the blurb is the abstract, its
   lab and its community upvotes): most papers are 4–6. Give 7–8 only to a paper
   from a major lab or with a striking, concrete result that non-researchers
   would care about (a new capability, a big jump on a well-known benchmark, a
   much cheaper or faster method, a safety finding).
   Open models (items with "kind": "model"; the blurb is the start of its model
   card): a new flagship open model from a major lab is 7–9; a small variant,
   a fine-tune or an update with nothing notable in the card is 4–5. Several
   sizes or variants of one release are the SAME story: group them.
   Skip any story that is essentially the same as one in ALREADY COVERED.
3. For every story scoring {min_importance} or more, write ONE X post.

POST RULES (tuned to X's ranking: it rewards posts people share, quote, reply
to, follow from and stop to read; it heavily punishes posts people mark "not
interested", mute or report)
- Straight news only. NO jokes, wit, opinions, commentary, predictions,
  rhetorical lines or questions. Every sentence must state a fact from the items.
- English, 100–240 characters, one or two sentences, each on its own line:
    Sentence 1 = the news: who did what. Start with the name people know
                 (OpenAI, Nvidia, Google, MeitY…) and put the most concrete
                 detail (a number, a scale, a name) early.
    Sentence 2 = optional, ONLY if the items contain it: the most useful extra
                 fact (a figure, a date, availability or price, who is affected,
                 what changes for users or for India). Omit it rather than pad.
- The post must be complete on its own: a reader should get the story without
  clicking anything. That is what makes people read, share and follow.
- Plain, calm, wire-service tone. No hype words (game-changer, huge, insane,
  massive, mind-blowing), no clickbait, no ALL CAPS, no "BREAKING" unless it is
  a 10. Hype and clickbait earn "not interested" clicks, which cost reach.
- Your OWN words — do not copy phrases from the headline or blurb.
- Use ONLY facts present in the items. Never invent numbers, names, dates or
  quotes. If the blurb is thin, write less, not more.
- No hashtags, no emojis, no @mentions.
- Do NOT include any link, source credit or outlet name as a credit ("via …").

PAPER POSTS (items with "kind": "paper")
- Base the post ONLY on the abstract in the blurb. Explain it in plain English
  for a non-researcher: what the researchers built or found, and the headline
  result with its number if the abstract gives one.
- Start with "New paper:" or, if a lab is named, "Researchers at <lab>". Name
  the model or method if it has a name.
- Do not call it a breakthrough, and do not say it is released, available or
  in products unless the abstract says so. No jargon without a plain meaning.

MODEL POSTS (items with "kind": "model")
- Start with "<Lab> released <model name>" as open weights on Hugging Face.
  Add only facts stated in the model card: size, what it does, a benchmark
  result, the license, languages. Never guess numbers the card doesn't give.
- If the card says almost nothing, keep the post to the release itself.

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
              "blurb": c["snippet"], **({"kind": c["kind"]} if c.get("kind") else {})}
             for i, c in enumerate(cands)]
    covered = "\n".join(f"- {c['headline']}" for c in state["covered"]) or "(none)"
    return PROMPT.format(persona=config["persona"].strip(),
                         min_importance=config["settings"]["min_importance"],
                         covered=covered,
                         items=json.dumps(items, ensure_ascii=False, indent=1))


TRANSIENT_STATUS = (429, 500, 502, 503, 504)


class GeminiUnavailable(Exception):
    """Gemini was slow or overloaded; safe to try again on the next run."""


def _post_gemini(url: str, body: dict, api_key: str, model: str, tries: int) -> requests.Response:
    """Up to `tries` attempts at one model. Raises GeminiUnavailable on timeouts / overload."""
    last = ""
    for attempt in range(tries):
        try:
            r = requests.post(url, json=body, timeout=(10, 90),
                              headers={"x-goog-api-key": api_key})
        except (requests.Timeout, requests.ConnectionError) as e:
            last = f"{type(e).__name__}"
        else:
            if r.status_code not in TRANSIENT_STATUS:
                return r
            last = f"HTTP {r.status_code}"
        if attempt < tries - 1:
            log.warning("Gemini %s (%s), retrying in 15s", model, last)
            time.sleep(15)
    raise GeminiUnavailable(f"{model}: {last}")


def call_gemini(prompt: str, api_key: str, models: list[str]) -> dict:
    """Try each model in order. The first gets two tries, backups one each, so the
    worst case (~6 min) stays inside the workflow's 10-minute limit."""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    models = list(dict.fromkeys(m for m in models if m))  # drop blanks and repeats
    problems, missing = [], []
    for i, model in enumerate(models):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = _post_gemini(url, body, api_key, model, tries=2 if i == 0 else 1)
        except GeminiUnavailable as e:
            problems.append(str(e))
            log.warning("%s; trying the next model", e)
            continue
        if r.status_code == 404:
            missing.append(model)
            log.warning("Gemini model %s not found (retired or misspelled) — update config.yaml", model)
            continue
        r.raise_for_status()
        break
    else:
        if missing and not problems:
            raise RuntimeError(f"none of the configured Gemini models exist: {', '.join(missing)}")
        raise GeminiUnavailable("; ".join(problems + [f"{m}: not found" for m in missing]))
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


def telegram_message(pick: dict, post: str, has_photo: bool = False) -> str:
    it = pick["item"]
    others = sorted({r["source"] for r in pick["related"]} - {it["source"]})
    lines = [f"📰 <b>{html.escape(it['source'])}</b> · importance {pick['importance']}/10",
             f"<i>{html.escape(it['title'])}</i>", "",
             html.escape(post), "",
             f"<code>{x_len(post)}/280 characters</code>"]
    if others:
        lines.append("Also covered by: " + html.escape(", ".join(others)))
    if has_photo:
        lines.append("🖼 Save this picture, then attach it to the post on X.")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, buttons: list[list[dict]],
                  photo: str = "") -> None:
    """Send the draft. With `photo`, send it as a picture with the draft as its caption
    (save it and attach it on X); if Telegram can't use the picture, send text only."""
    api = f"https://api.telegram.org/bot{token}"
    if photo and len(text) <= 1024:   # Telegram's caption limit
        r = requests.post(f"{api}/sendPhoto", timeout=30, json={
            "chat_id": chat_id, "photo": photo, "caption": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": buttons},
        })
        if r.ok:
            return
        log.warning("Telegram couldn't use the picture (%s); sending text only", r.status_code)
    r = requests.post(f"{api}/sendMessage", timeout=20, json={
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
        if feed.get("enabled", True) is False:
            print(f"OFF     {feed['name']}")
            continue
        try:
            items = fetch_feed(feed)
            newest = max((i["published"] for i in items if i["published"]), default=None)
            print(f"OK      {feed['name']:<26} {len(items):>3} items, newest {newest}")
        except Exception as exc:
            bad += 1
            print(f"FAILED  {feed['name']:<26} {exc}")
    off = sum(f.get("enabled", True) is False for f in config["feeds"])
    print(f"\n{len(config['feeds']) - off - bad} working, {bad} failed, {off} switched off")
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
    room = s["max_drafts_per_run"]
    if s.get("max_drafts_per_day"):   # 0 / null / missing = no daily limit
        room = min(room, s["max_drafts_per_day"] - state["daily"]["count"])
    if room <= 0:
        log.info("daily limit of %s drafts reached", s["max_drafts_per_day"])
        return 0

    cands = collect_candidates(config, state, now)
    log.info("%d new AI items", len(cands))
    if not cands:
        if not dry_run:   # keep what page-list sources learned, so pages aren't re-fetched
            save_state(state, now, state_path)
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY is not set")
        return 1
    model = os.environ.get("GEMINI_MODEL") or s.get("gemini_model", "gemini-flash-latest")
    fallbacks = s.get("gemini_fallback_models") or [s.get("gemini_fallback_model")]
    try:
        result = call_gemini(build_prompt(config, state, cands), api_key, [model, *fallbacks])
    except GeminiUnavailable as e:
        log.warning("Gemini unavailable (%s) — nothing sent; will retry next run", e)
        return 0
    qualified = select_picks(result, cands, s["min_importance"], len(cands))
    picks, held = qualified[:room], qualified[room:]
    # Good stories that didn't fit this run stay unseen, so the next run reconsiders them.
    held_ids = {c["id"] for p in held for c in [p["item"], *p["related"]]}
    log.info("Gemini picked %d stories (%d held for later runs)", len(picks), len(held))

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not dry_run and not (token and chat_id):
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1

    for pick in picks:
        it = pick["item"]
        post = compose_post(pick["post"], it["source"], it["url"], s.get("include_link", True),
                            s.get("show_source", True))
        photo = ""
        if s.get("attach_images", True) and it.get("images"):
            photo = it.get("image") or og_image(it["url"])
        if dry_run:
            print("─" * 60)
            print(f"[{pick['importance']}/10] {it['source']}: {it['title']}")
            print(post)
            print(f"({x_len(post)}/280)  {intent_url(post)[:90]}…")
            if photo:
                print(f"image: {photo}")
            continue
        buttons = [[{"text": "✅ Post on X", "url": intent_url(post)}],
                   [{"text": "📖 Read article", "url": it["url"]}]]
        send_telegram(token, chat_id, telegram_message(pick, post, bool(photo)), buttons, photo)
        state["covered"].append({"headline": pick["headline"], "ts": now.isoformat()})
        state["daily"]["count"] += 1

    if dry_run:
        return 0
    for c in cands:  # everything Gemini has judged is now "seen", except held picks
        if c["id"] not in held_ids:
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
