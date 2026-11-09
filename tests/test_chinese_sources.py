"""Chinese discovery and English-only delivery, with no live model or Telegram calls."""
import json
import urllib.parse
from copy import deepcopy

import pytest
import yaml

from test_newsbot import CONFIG, NOW, Resp, env, rss  # noqa: F401
import newsbot


TITLE = "星辰智能发布大模型，完成2亿元融资"
BLURB = "公司称模型支持中文和英文，目前仅开放研究预览。融资金额为人民币2亿元。"
URL = "https://chinese.test/model"
HEADLINE = "Xingchen AI announces model and funding round"
POST = "Xingchen AI announced a Chinese-English model research preview and raised 200 million yuan."
NOTE = "Check access terms and whether the reported funding round has closed."


def chinese_feed(env, monkeypatch):
    config = deepcopy(CONFIG)
    config["keywords"] = ["大模型"]
    config["feeds"] = [{"name": "Chinese News", "url": "https://chinese.test/feed",
                        "ai_only": False, "language": "zh"}]
    env["cfg"].write_text(yaml.safe_dump(config), encoding="utf-8")
    content = rss([(TITLE, URL, BLURB, NOW),
                   ("新款手机上市", "https://chinese.test/phone", "屏幕更亮了", NOW)])
    monkeypatch.setattr(newsbot.requests, "get", lambda *_a, **_kw: Resp(content=content))
    return config


def response(**overrides):
    return {"picks": [{"primary_id": "c0", "importance": 8,
                       "headline": HEADLINE, "post": POST, "editor_note": NOTE,
                       **overrides}]}


@pytest.mark.parametrize("text", ["国产人工智能企业发布产品", "新大模型发布", "AI模型上线",
                                  "发布DeepSeek模型", "deepseek发布更新", "AI-powered tools"])
def test_chinese_keywords_and_adjacent_latin_names_match(text):
    pattern = newsbot.build_keyword_regex(["人工智能", "大模型", "DeepSeek"])
    assert pattern.search(text)


@pytest.mark.parametrize("text", ["新款手机上市", "paid campaign", "AIrtel", "DeepSeeker", "ai模型"])
def test_keyword_matching_does_not_match_inside_latin_words(text):
    assert not newsbot.build_keyword_regex(["人工智能", "大模型", "DeepSeek"]).search(text)


def test_empty_keywords_do_not_match_everything():
    assert not newsbot.build_keyword_regex([]).search("普通新闻")
    assert newsbot.build_keyword_regex([]).search("AI模型")


def test_chinese_feed_reaches_gemini_and_only_english_reaches_telegram(env, monkeypatch):
    chinese_feed(env, monkeypatch)
    prompts = []

    def translate(prompt, *_):
        prompts.append(prompt)
        items = json.loads(prompt.split("ITEMS:\n")[1].split("\n\nReply with")[0])
        assert len(items) == 1
        assert items[0]["title"] == TITLE and items[0]["blurb"] == BLURB
        assert items[0]["language"] == "zh"
        assert "TRANSLATION RULES" in prompt and "across languages" in prompt
        return response()

    monkeypatch.setattr(newsbot, "call_gemini", translate)
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert len(prompts) == 1 and len(env["sent"]) == 1
    message = env["sent"][0]
    assert HEADLINE in message["text"] and POST in message["text"] and NOTE in message["text"]
    assert not newsbot.HAN_RE.search(message["text"])
    buttons = message["reply_markup"]["inline_keyboard"]
    post = urllib.parse.parse_qs(urllib.parse.urlsplit(buttons[0][0]["url"]).query)["text"][0]
    assert POST in post and NOTE not in post and newsbot.x_len(post) <= 280
    assert buttons[1][0]["url"] == URL
    state = newsbot.load_state(env["state"])
    assert state["covered"][0]["headline"] == HEADLINE
    assert newsbot.item_id(URL, TITLE) in state["seen"]


@pytest.mark.parametrize("bad_fields", [{"headline": TITLE}, {"post": BLURB},
                                        {"editor_note": "请核实融资金额"}, {"headline": ""}])
def test_incomplete_translation_stays_unseen_and_recovers_next_run(env, monkeypatch, bad_fields):
    chinese_feed(env, monkeypatch)
    monkeypatch.setattr(newsbot, "call_gemini", lambda *_: response(**bad_fields))
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 1
    state = newsbot.load_state(env["state"])
    assert state["seen"] == {} and state["pending"] == [] and env["sent"] == []
    monkeypatch.setattr(newsbot, "call_gemini", lambda *_: response())
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert len(env["sent"]) == 1


def test_chinese_original_is_preserved_in_queue_but_not_delivery(env, monkeypatch):
    config = chinese_feed(env, monkeypatch)
    state = newsbot.load_state(env["state"])
    candidates = newsbot.collect_candidates(config, state, NOW)
    picks = newsbot.select_picks(response(), candidates, 6, 1)
    newsbot.enqueue_picks(state, picks, NOW, config["settings"])
    newsbot.save_state(state, NOW, env["state"])
    saved = newsbot.load_state(env["state"])["pending"][0]
    assert saved["item"]["title"] == TITLE and saved["item"]["snippet"] == BLURB
    assert saved["headline"] == HEADLINE
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))
    monkeypatch.setattr(newsbot, "call_gemini", lambda *_: pytest.fail("No extra translation call"))
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert len(env["sent"]) == 1 and not newsbot.HAN_RE.search(env["sent"][0]["text"])


def test_legacy_untranslated_queue_entry_is_held(env, monkeypatch):
    config = chinese_feed(env, monkeypatch)
    state = newsbot.load_state(env["state"])
    cands = newsbot.collect_candidates(config, state, NOW)
    picks = newsbot.select_picks(response(), cands, 6, 1)
    newsbot.enqueue_picks(state, picks, NOW, config["settings"])
    state["pending"][0]["post"] = BLURB
    newsbot.save_state(state, NOW, env["state"])
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 1
    assert env["sent"] == [] and len(newsbot.load_state(env["state"])["pending"]) == 1


def test_chinese_dry_run_displays_english_without_saving(env, monkeypatch, capsys):
    chinese_feed(env, monkeypatch)
    monkeypatch.setattr(newsbot, "call_gemini", lambda *_: response())
    assert newsbot.run(dry_run=True, now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    output = capsys.readouterr().out
    assert HEADLINE in output and POST in output and TITLE not in output
    assert env["sent"] == [] and not env["state"].exists()


def test_shipped_chinese_sources_filter_relevance_and_have_language_metadata():
    config = newsbot.load_config()
    sources = {f["name"]: f for f in config["feeds"] if f.get("language") == "zh"}
    assert {"QbitAI", "36Kr", "LeiPhone"} <= sources.keys()
    for feed in sources.values():
        assert feed.get("enabled", True) and not feed["ai_only"]
        assert feed["category"] == "news" and feed["max_candidates"] <= 3
    assert newsbot.build_keyword_regex(config["keywords"]).search("国内人工智能大模型发布")
