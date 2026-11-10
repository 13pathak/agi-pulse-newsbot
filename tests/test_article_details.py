"""Offline checks for detail packs: article reading, number checks, queue and rendering."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import article_details as ad
import newsbot as nb

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)  # 11:30 IST, outside quiet hours
SETTINGS = {"quiet_hours_ist": ["23:00", "06:30"], "details_max_per_day": 2, "details_min_importance": 7}

PAGE = """<html><head><title>Fallback title</title>
<meta property="og:title" content="Model 5.2 launch"><meta property="og:image" content="https://x.test/hero.jpg">
</head><body>
<nav><p>Navigation text that must not be read because it is in the nav bar.</p></nav>
<a href="/other"><img src="/img/other-story.png" alt="Another story"></a>
<article>
 <header><img src="/img/site-logo.png" alt="logo"></header>
 <h2>Pricing<span>and</span><span>speed</span></h2>
 <p>The model costs $8 per million input tokens and is up to 30% faster than before, the company says. """ + "Filler sentence for length. " * 30 + """</p>
 <table><tr><th>Bench</th><th>New</th></tr><tr><td><span>Coding</span><span>v2</span></td><td>69.9%</td></tr></table>
 <figure><img src="/img/chart.png?w=640" srcset="/img/chart.png?w=640 640w, /img/chart.png?w=1920 1920w" alt="Scores">
  <figcaption>Benchmark scores by model</figcaption></figure>
 <img src="/img/avatar-jane.png" alt="Jane"><img src="/img/tiny.png" width="40" alt="dot">
 <img src="data:image/png;base64,AAAA" alt="inline"><img src="/img/diagram.svg" alt="vector">
</article>
<footer><p>Footer text.</p><img src="/img/footer-art.png"></footer></body></html>"""


def test_article_text_prefers_body_and_keeps_tables():
    text = ad.ArticleHTML(PAGE).text()
    assert "## Pricing and speed" in text
    assert "$8 per million input tokens" in text
    assert "Bench | New" in text and "Coding v2 | 69.9%" in text
    assert "Navigation text" not in text and "Footer text" not in text


def test_candidate_images_keep_only_in_article_pictures():
    parsed = ad.ArticleHTML(PAGE)
    images = ad.candidate_images(parsed.images, "https://x.test/news/launch")
    assert [i["url"] for i in images] == ["https://x.test/img/chart.png?w=1920"]
    assert images[0]["caption"] == "Benchmark scores by model" and images[0]["alt"] == "Scores"


def test_image_key_merges_cdn_sizes():
    a = "https://x.test/_next/image?url=%2Fa.png&w=640"
    b = "https://x.test/_next/image?url=%2Fa.png&w=1920"
    assert ad.image_key(a) == ad.image_key(b) == "/a.png"
    assert ad.image_key("https://x.test/a.png?w=1") == "https://x.test/a.png"


@pytest.mark.parametrize("post,missing", [
    ("Costs $8 per million tokens and is 30% faster.", []),
    ("Scores 69.9% on coding.", []),
    ("Scores 71.2% on coding.", ["71.2"]),
    ("Used by 1,200 firms.", []),
    ("Now 9 times cheaper.", ["9"]),          # "9" only appears inside 69.9 in the source
    ("No figures at all.", []),
])
def test_unsupported_numbers(post, missing):
    source = "It costs $8 per million tokens, is 30% faster, scores 69.9% and has 1200 customers."
    assert ad.unsupported_numbers(post, source) == missing


def article():
    return {"url": "https://x.test/news/launch", "title": "Model 5.2 launch", "desc": "",
            "preview": "https://x.test/hero.jpg",
            "text": "It costs $8 per million tokens, is 30% faster and scores 69.9% on Coding v2."}


def images():
    return [{"id": "i1", "url": "https://x.test/hero.png", "alt": "", "caption": "", "mime": "image/png", "data": b"h"},
            {"id": "i2", "url": "https://x.test/chart.png", "alt": "Scores", "caption": "", "mime": "image/png", "data": b"c"}]


def raw():
    return {"details": [{"text": "Costs $8 per million input tokens", "image": None},
                        {"text": "Tops rivals on agentic coding", "image": "i2"}, "junk"],
            "posts": [{"kind": "fact", "text": "The model costs $8 per million tokens.", "image": None},
                      {"kind": "take", "text": "I think 30% faster matters more than the score.", "image": "i1"},
                      {"kind": "fact", "text": "It scores 88.1% on the hardest test.", "image": "i2", "from_chart": True},
                      {"kind": "fact", "text": "It beats rivals by 12 points.", "image": None},
                      {"kind": "fact", "text": "x" * 281, "image": None}],
            "images": [{"id": "i1", "kind": "decorative", "caption": "abstract art"},
                       {"id": "i2", "kind": "chart", "caption": "Benchmark scores"},
                       {"id": "i9", "kind": "chart", "caption": "not attached"}],
            "check": "Benchmark conditions"}


def test_validate_drops_invented_numbers_and_decorative_pictures():
    pack = ad.validate(raw(), article(), images())
    assert [i["id"] for i in pack["album"]] == ["i2"]
    assert pack["details"] == [{"text": "Costs $8 per million input tokens", "picture": None, "unverified": False},
                               {"text": "Tops rivals on agentic coding", "picture": 1, "unverified": False}]
    assert [p["text"] for p in pack["posts"]] == ["The model costs $8 per million tokens.",
                                                  "I think 30% faster matters more than the score.",
                                                  "It scores 88.1% on the hardest test."]
    assert pack["posts"][1]["picture"] is None            # decorative picture is not offered
    assert pack["posts"][2] == {"text": "It scores 88.1% on the hardest test.",
                                "picture": 1, "verify_chart": True}
    assert pack["dropped"] == 1                           # "12 points" is in neither text nor chart


def test_validate_requires_a_usable_draft():
    with pytest.raises(ad.DetailsError):
        ad.validate({"posts": [{"text": "Up 55% overall."}]}, article(), [])


def pack():
    result = ad.validate(raw(), article(), images())
    result.update(url="https://x.test/news/launch", title="Model <5.2> launch", source="Example Lab",
                  preview="https://x.test/hero.jpg")
    return result


def test_render_labels_chart_numbers_and_builds_buttons():
    text, keyboard = ad.render(pack())
    assert "Model &lt;5.2&gt; launch" in text and "Example Lab" in text
    assert "goes with picture 1" in text and "number read from a chart, verify" in text
    assert "1 draft(s) removed" in text and "Chart: Example Lab" in text
    assert len(text) <= ad.TELEGRAM_LIMIT
    assert [b["text"] for row in keyboard for b in row] == ["✅ Post 1 on X", "✅ Post 2 on X", "✅ Post 3 on X",
                                                           "📖 Read article"]
    assert keyboard[0][0]["url"].startswith("https://x.com/intent/tweet?text=The%20model")


def pick(importance=8, category="primary", related=()):
    item = {"id": "a", "url": "https://lab.test/news/x", "source": "Lab", "category": category}
    return {"item": item, "related": list(related), "importance": importance}


def test_newsbot_queues_only_important_official_stories():
    state = {}
    nb.queue_details(state, pick(), SETTINGS, NOW)
    nb.queue_details(state, pick(importance=6), SETTINGS, NOW)
    nb.queue_details(state, pick(category="news"), SETTINGS, NOW)
    nb.queue_details(state, pick(), {}, NOW)  # setting absent: feature off
    official = {"id": "b", "url": "https://lab.test/official", "source": "Lab", "category": "primary"}
    nb.queue_details(state, pick(category="news", related=[official]), SETTINGS, NOW)
    assert [e["url"] for e in state["details_queue"]] == ["https://lab.test/news/x", "https://lab.test/official"]


def entry(url, hours_ago=0):
    return {"url": url, "source": "Lab", "queued_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_process_queue_sends_once_and_respects_limits():
    sent = []
    state = {"details_queue": [entry("https://a.test/1"), entry("https://a.test/old", 13),
                               entry("https://a.test/2"), entry("https://a.test/3")],
             "details_done": {"stale": (NOW - timedelta(days=20)).isoformat()}}
    build = lambda url, key, settings, source: {"url": url}
    count = ad.process_queue(state, SETTINGS, NOW, "t", "c", "k", build, lambda t, c, p: sent.append(p["url"]))
    assert count == 2 and sent == ["https://a.test/1", "https://a.test/2"]
    assert [e["url"] for e in state["details_queue"]] == ["https://a.test/3"]   # daily limit of 2 reached
    assert "stale" not in state["details_done"] and state["details_daily"]["count"] == 2
    state["details_queue"].append(entry("https://a.test/1"))                  # already done: dropped silently
    state["details_daily"]["count"] = 0
    assert ad.process_queue(state, SETTINGS, NOW, "t", "c", "k", build, lambda t, c, p: sent.append(p["url"])) == 1
    assert sent[-1] == "https://a.test/3" and state["details_queue"] == []


def test_process_queue_failures_and_quiet_hours():
    state = {"details_queue": [entry("https://a.test/unreadable"), entry("https://a.test/busy")]}

    def build(url, key, settings, source):
        if "unreadable" in url:
            raise ad.DetailsError("no text")
        raise nb.GeminiUnavailable("busy")

    for attempt in (1, 2):
        assert ad.process_queue(state, SETTINGS, NOW, "t", "c", "k", build, None) == 0
        assert [e["url"] for e in state["details_queue"]] == ["https://a.test/busy"]
        assert state["details_queue"][0]["attempts"] == attempt
    ad.process_queue(state, SETTINGS, NOW, "t", "c", "k", build, None)
    assert state["details_queue"] == []                    # given up after three attempts

    night = datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)  # 00:30 IST
    state = {"details_queue": [entry("https://a.test/1")]}
    assert ad.process_queue(state, SETTINGS, night, "t", "c", "k", lambda *a: pytest.fail("quiet hours"), None) == 0
    assert len(state["details_queue"]) == 1


def test_requested_pack_retries_once_then_reports_gemini_overload(monkeypatch):
    calls, notices = [], []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(ad.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    def busy(url, key, settings):
        calls.append("build")
        raise nb.GeminiUnavailable("all models: HTTP 503")

    monkeypatch.setattr(ad, "build_pack", busy)
    monkeypatch.setattr(nb, "send_telegram", lambda token, chat, text, buttons: notices.append(text))
    assert ad.main(["--url", "https://x.test/a"]) == 2
    assert calls == ["build", ("sleep", 45), "build"]
    assert "overloaded" in notices[0] and "https://x.test/a" in notices[0]

    calls.clear()
    results = iter([nb.GeminiUnavailable("busy"), pack()])

    def recovers(url, key, settings):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    sent = []
    monkeypatch.setattr(ad, "build_pack", recovers)
    monkeypatch.setattr(ad, "send_pack", lambda token, chat, p: sent.append(p["url"]))
    assert ad.main(["--url", "https://x.test/a"]) == 0 and sent == ["https://x.test/news/launch"]


def test_calculated_figures_in_details_are_flagged():
    data = raw()
    data["details"] = [{"text": "Found 10.2% more bugs than before", "image": None}]
    result = ad.validate(data, article(), images())
    assert result["details"][0]["unverified"] is True
    result.update(url="https://x.test/a", title="T", source="Lab", preview="")
    text, _ = ad.render(result)
    assert "figure not found in the article text" in text and "📌" not in text
    assert "every one an opinion take" in ad.PROMPT and '"kind": "fact"' not in ad.PROMPT


def test_stiff_drafts_go_last_and_only_five_are_kept():
    data = raw()
    data["posts"] = [{"text": "This signals a new era for enterprise AI."},
                     {"text": "Embedding apps highlights the shift."}] + [
                     {"text": f"Honestly not sure take {n} holds up."} for n in "abcde"]
    posts = ad.validate(data, article(), images())["posts"]
    assert len(posts) == 5 and not any(ad.STIFF.search(p["text"]) for p in posts)
    assert posts[0]["text"] == "Honestly not sure take a holds up."


def test_voice_can_be_set_in_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(ad, "fetch_article", lambda url: {**article(), "images": []})
    monkeypatch.setattr(ad, "download_images", lambda candidates: [])
    monkeypatch.setattr(ad, "call_gemini", lambda prompt, imgs, key, models: seen.update(prompt=prompt) or raw())
    ad.build_pack("https://x.test/a", "k", {"takes_voice": "Write like a pirate."})
    assert "Write like a pirate." in seen["prompt"] and "types a thought on their phone" not in seen["prompt"]
    ad.build_pack("https://x.test/a", "k", {})
    assert "types a thought on their phone" in seen["prompt"]
