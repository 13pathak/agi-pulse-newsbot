"""Offline tests: fake feeds, fake Gemini, fake Telegram.  Run: python -m pytest tests"""
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import newsbot  # noqa: E402

NOW = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(p)}</pubDate></item>" for t, l, d, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


FEEDS = {
    "https://verge.test/ai": rss([
        ("OpenAI launches GPT-6", "https://verge.test/gpt6?utm_source=rss", "A new model.", NOW),
        ("10 best AI gadgets on sale", "https://verge.test/deals", "Shopping.", NOW),
        ("Old story", "https://verge.test/old", "Stale.", datetime(2026, 11, 1, tzinfo=timezone.utc)),
    ]),
    "https://tc.test/ai": rss([("GPT-6 is here", "https://tc.test/gpt6", "OpenAI's new model.", NOW)]),
    "https://hindu.test/tech": rss([
        ("Sarvam AI unveils Hindi voice model", "https://hindu.test/sarvam", "Bengaluru startup.", NOW),
        ("New smartphone launched in India", "https://hindu.test/phone", "Camera upgrade.", NOW),
    ]),
}

CONFIG = {
    "settings": {"max_drafts_per_run": 3, "max_drafts_per_day": 10, "min_importance": 6,
                 "lookback_hours": 18, "max_candidates": 50,
                 "quiet_hours_ist": ["23:00", "06:30"], "include_link": True,
                 "gemini_model": "gemini-flash-latest",
                 "gemini_fallback_models": ["gemini-3.6-flash", "gemini-3.5-flash-lite"]},
    "persona": "Factual AI news writer.",
    "keywords": ["artificial intelligence", "OpenAI", "Sarvam"],
    "feeds": [
        {"name": "The Verge", "url": "https://verge.test/ai", "ai_only": True},
        {"name": "TechCrunch", "url": "https://tc.test/ai", "ai_only": True},
        {"name": "The Hindu", "url": "https://hindu.test/tech", "ai_only": False},
        {"name": "Broken", "url": "https://broken.test/feed", "ai_only": True},
    ],
}


class Resp:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code, self.content, self._payload = status, content, payload
        self.ok = status < 400
        self.text = content.decode(errors="ignore")

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    import yaml
    cfg.write_text(yaml.safe_dump(CONFIG))
    state = tmp_path / "state.json"
    sent, prompts, gemini_models = [], [], []

    def fake_get(url, **kw):
        if url in FEEDS:
            return Resp(content=FEEDS[url])
        return Resp(status=404)

    def fake_post(url, json=None, **kw):
        if "generativelanguage" in url:
            model = url.split("/models/")[1].split(":")[0]
            gemini_models.append(model)
            prompt = json["contents"][0]["parts"][0]["text"]
            prompts.append(prompt)
            items = __import__("json").loads(prompt.split("ITEMS:\n")[1].split("\n\nReply with")[0])
            ids = {i["title"]: i["id"] for i in items}
            picks = []
            if "OpenAI launches GPT-6" in ids:
                picks.append({"primary_id": ids["OpenAI launches GPT-6"],
                              "related_ids": [ids.get("GPT-6 is here")], "importance": 10,
                              "headline": "OpenAI releases GPT-6",
                              "post": "OpenAI just dropped GPT-6. Somewhere, GPT-5 is updating its LinkedIn to 'open to work' 💼"})
            if "Sarvam AI unveils Hindi voice model" in ids:
                picks.append({"primary_id": ids["Sarvam AI unveils Hindi voice model"], "related_ids": [],
                              "importance": 8, "headline": "Sarvam Hindi voice model",
                              "post": "Sarvam's new Hindi voice model is out. Finally an AI that can say 'bas thoda sa' with the right attitude."})
            if "10 best AI gadgets on sale" in ids:
                picks.append({"primary_id": ids["10 best AI gadgets on sale"], "related_ids": [],
                              "importance": 2, "headline": "deals", "post": "deals"})
            text = __import__("json").dumps({"picks": picks})
            return Resp(payload={"candidates": [{"content": {"parts": [{"text": text}]}}]})
        if "api.telegram.org" in url:
            sent.append(json)
            return Resp()
        raise AssertionError(url)

    monkeypatch.setattr(newsbot.requests, "get", fake_get)
    monkeypatch.setattr(newsbot.requests, "post", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    return {"cfg": cfg, "state": state, "sent": sent, "prompts": prompts, "models": gemini_models}


def test_full_run(env):
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    sent = env["sent"]
    assert len(sent) == 2                                   # GPT-6 + Sarvam; deals filtered by score
    prompt = env["prompts"][0]
    assert "Old story" not in prompt                        # too old
    assert "smartphone" not in prompt                       # not AI (keyword filter)
    assert "Sarvam" in prompt
    first = sent[0]
    assert "importance 10/10" in first["text"] and "Also covered by: TechCrunch" in first["text"]
    intent = first["reply_markup"]["inline_keyboard"][0][0]["url"]
    post = urllib.parse.unquote(intent.split("text=")[1])
    assert post.endswith("via The Verge https://verge.test/gpt6?utm_source=rss")
    assert newsbot.x_len(post) <= 280
    st = json.loads(env["state"].read_text())
    assert st["daily"]["count"] == 2 and len(st["covered"]) == 2


def test_second_run_sends_nothing_new(env):
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    env["sent"].clear()
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    assert env["sent"] == [] and len(env["prompts"]) == 1   # no Gemini call when nothing new


def test_quiet_hours(env):
    night = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)  # 00:30 IST
    newsbot.run(now=night, config_path=env["cfg"], state_path=env["state"])
    assert env["sent"] == [] and not env["state"].exists()


def test_daily_cap(env):
    import yaml
    cfg = yaml.safe_load(env["cfg"].read_text())
    cfg["settings"]["max_drafts_per_day"] = 1
    env["cfg"].write_text(yaml.safe_dump(cfg))
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    assert len(env["sent"]) == 1 and "GPT-6" in env["sent"][0]["text"]


def test_per_run_cap_holds_extra_stories_for_next_run(env):
    import yaml
    cfg = yaml.safe_load(env["cfg"].read_text())
    cfg["settings"]["max_drafts_per_run"] = 1
    env["cfg"].write_text(yaml.safe_dump(cfg))
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    assert len(env["sent"]) == 1 and "GPT-6" in env["sent"][0]["text"]
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    assert len(env["sent"]) == 2 and "Sarvam" in env["sent"][1]["text"]   # held, not lost
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    assert len(env["sent"]) == 2                                           # nothing left


def test_x_length_and_trimming():
    long_body = "word " * 100
    post = newsbot.compose_post(long_body, "The Hindu", "https://example.com/" + "a" * 200, True)
    assert newsbot.x_len(post) <= 280 and "…" in post
    assert newsbot.x_len("hi https://example.com/very/long/path") == 3 + 23
    assert newsbot.x_len("😀") == 2 and newsbot.x_len("नमस्ते") == 6


def test_no_link_mode():
    post = newsbot.compose_post("GPT-6 is out.", "The Hindu", "https://hindu.test/x", False)
    assert post == "GPT-6 is out.\n\nvia The Hindu"


def test_blank_line_between_hook_and_take():
    expected = "Microsoft offered buyouts.\n\nNothing says the future is here.\n\nvia The New York Times"
    for body in ("Microsoft offered buyouts.\nNothing says the future is here.",
                 "Microsoft offered buyouts.\n\n\n  Nothing says the future is here.  "):
        assert newsbot.compose_post(body, "The New York Times", "", False) == expected


def test_shipped_config_is_valid():
    cfg = newsbot.load_config()
    names = [f["name"] for f in cfg["feeds"]]
    assert "The New York Times" in names and "The Wall Street Journal" in names
    assert cfg["settings"]["include_link"] is False and cfg["settings"]["show_source"] is False


def test_no_source_credit_mode():
    body = "OpenAI will keep expanding its data centres.\nThe CFO said so on Friday."
    post = newsbot.compose_post(body, "The Hindu", "https://hindu.test/x", False, show_source=False)
    assert post == "OpenAI will keep expanding its data centres.\n\nThe CFO said so on Friday."
    assert "via" not in post


def test_url_normalization_dedupes_tracking():
    a = newsbot.item_id("https://Site.com/story/?utm_source=x", "t")
    b = newsbot.item_id("https://site.com/story", "t")
    assert a == b


def fake_gemini(monkeypatch, replies):
    """replies: model name -> status code (200 = valid empty answer)."""
    calls = []
    ok = {"candidates": [{"content": {"parts": [{"text": '{"picks": []}'}]}}]}

    def post(url, **kw):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return Resp(payload=ok) if replies[model] == 200 else Resp(status=replies[model])

    monkeypatch.setattr(newsbot.requests, "post", post)
    monkeypatch.setattr(newsbot.time, "sleep", lambda s: None)
    return calls


def test_gemini_skips_overloaded_and_retired_models(monkeypatch):
    calls = fake_gemini(monkeypatch, {"main": 503, "retired": 404, "good": 200})
    assert newsbot.call_gemini("p", "k", ["main", "retired", "good"]) == {"picks": []}
    assert calls == ["main", "main", "retired", "good"]   # 2 tries on main, 1 on each backup


def test_gemini_busy_everywhere_means_try_next_run(monkeypatch):
    fake_gemini(monkeypatch, {"main": 503, "retired": 404})
    with pytest.raises(newsbot.GeminiUnavailable):
        newsbot.call_gemini("p", "k", ["main", "retired"])


def test_gemini_all_models_retired_fails_loudly(monkeypatch):
    fake_gemini(monkeypatch, {"old1": 404, "old2": 404})
    with pytest.raises(RuntimeError, match="none of the configured Gemini models exist"):
        newsbot.call_gemini("p", "k", ["old1", "old2", "old1", None])


HF_PAYLOAD = [
    {"publishedAt": "2026-11-06T05:00:00.000Z",
     "paper": {"id": "2611.00001", "title": "Tiny Model Beats Giant", "upvotes": 85,
               "submittedOnDailyAt": "2026-11-06T00:00:00.000Z",
               "summary": "We show a 1B model matches a 70B model on MATH. " * 40,
               "organization": {"fullname": "Google"}}},
    {"publishedAt": "2026-11-06T05:00:00.000Z",
     "paper": {"id": "2611.00002", "title": "Niche Result", "upvotes": 3, "summary": "Minor."}},
    {"publishedAt": "2026-11-06T04:00:00.000Z",
     "paper": {"id": "2611.00003", "title": "Solid Agent Paper", "upvotes": 40, "summary": "Agents."}},
]


def test_hf_papers_filters_by_upvotes_and_keeps_abstract(monkeypatch):
    calls = []

    def get(url, params=None, **kw):     # same paper listed on two days must appear once
        calls.append(params["date"])
        return Resp(payload=HF_PAYLOAD if len(calls) % 3 != 0 else [])

    monkeypatch.setattr(newsbot.requests, "get", get)
    feed = {"name": "Hugging Face Papers", "type": "hf_papers", "url": "https://hf.test/api",
            "min_upvotes": 20, "max_items": 10, "blurb_chars": 1500}
    items = newsbot.fetch_feed(feed)
    assert [i["title"] for i in items] == ["Tiny Model Beats Giant", "Solid Agent Paper"]
    top = items[0]
    assert top["url"] == "https://arxiv.org/abs/2611.00001" and top["kind"] == "paper"
    assert top["snippet"].startswith("Lab: Google. Upvotes: 85. Abstract: We show")
    assert 1400 < len(top["snippet"]) < 1600          # whole abstract up to blurb_chars, not 400
    assert top["published"] == datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)   # listing date wins
    assert len(calls) == 3 and len(set(calls)) == 3      # today + 2 days back, by date
    feed["max_items"] = 1
    assert len(newsbot.fetch_feed(feed)) == 1


def test_papers_reach_gemini_marked_as_papers(env, monkeypatch):
    import yaml
    cfg = yaml.safe_load(env["cfg"].read_text())
    cfg["feeds"].append({"name": "Hugging Face Papers", "type": "hf_papers", "url": "https://hf.test/api",
                         "ai_only": True, "min_upvotes": 20, "lookback_hours": 48})
    env["cfg"].write_text(yaml.safe_dump(cfg))
    news_get = newsbot.requests.get
    monkeypatch.setattr(newsbot.requests, "get",
                        lambda url, **kw: Resp(payload=HF_PAYLOAD) if url == "https://hf.test/api" else news_get(url, **kw))
    monkeypatch.setattr(newsbot, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"])
    items = json.loads(env["prompts"][0].split("ITEMS:\n")[1].split("\n\nReply with")[0])
    papers = [i for i in items if i.get("kind") == "paper"]
    assert [p["title"] for p in papers] == ["Tiny Model Beats Giant", "Solid Agent Paper"]
    assert all("kind" not in i for i in items if i["source"] != "Hugging Face Papers")
