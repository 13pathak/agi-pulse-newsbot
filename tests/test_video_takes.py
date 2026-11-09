"""Offline checks for the experimental video-takes feature."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video_takes as vt

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
VID = "vDmjw-c_R2k"


@pytest.mark.parametrize("text", [
    f"https://www.youtube.com/watch?v={VID}",
    f"look at this https://youtube.com/watch?feature=share&v={VID}&t=30s",
    f"https://youtu.be/{VID}?si=abc",
    f"https://m.youtube.com/shorts/{VID}",
    f"www.youtube.com/live/{VID}",
])
def test_find_video_id(text):
    assert vt.find_video_id(text) == VID


def test_find_video_id_rejects_other_links_and_takes_first():
    assert vt.find_video_id("https://example.com/watch?v=vDmjw-c_R2k") is None
    assert vt.find_video_id("no link here") is None
    assert vt.find_video_id(f"https://youtu.be/aaaaaaaaaaa then https://youtu.be/{VID}") == "aaaaaaaaaaa"


def test_format_transcript_chunks_and_trims():
    snippets = [(0.2, "a"), (5, "b"), (21, "c"), (3700, "d")]
    text, trimmed = vt.format_transcript(snippets)
    assert text.splitlines() == ["[0:00] a b", "[0:21] c", "[1:01:40] d"]
    assert not trimmed
    text, trimmed = vt.format_transcript(snippets, max_chars=15)
    assert text == "[0:00] a b" and trimmed


def test_validate_drops_bad_items():
    raw = {"ai_related": False,
           "facts": [{"seconds": 754, "text": "  112 PCIe Gen 5 lanes "}, {"seconds": 99999, "text": "late"},
                     {"seconds": True, "text": "bool"}, "junk", {"text": ""}],
           "posts": [{"kind": "opinion", "text": "I think VRAM matters."}, {"kind": "fact", "text": "x" * 281},
                     {"kind": "weird", "text": "I think VRAM matters."}, {"kind": "fact", "text": "Fact."}],
           "check": "Lane count"}
    result = vt.validate(raw, duration=1800)
    assert result["facts"] == [{"seconds": 754, "text": "112 PCIe Gen 5 lanes"},
                               {"seconds": None, "text": "late"}, {"seconds": None, "text": "bool"}]
    assert result["posts"] == [{"kind": "opinion", "text": "I think VRAM matters."},
                               {"kind": "fact", "text": "Fact."}]
    assert result["ai_related"] is False


def test_validate_requires_a_post():
    with pytest.raises(vt.TakesError):
        vt.validate({"posts": [{"text": "y" * 300}]}, 100)


def result(**extra):
    base = {"video_id": VID, "title": "Local AI <rig>", "channel": "LTT", "language": "en",
            "generated": True, "duration": 1848, "trimmed": False, "ai_related": True,
            "facts": [{"seconds": 754, "text": "Needs a W9-3495X & 7 slots"}],
            "posts": [{"kind": "opinion", "text": "I think dual 5090s hit the memory wall."},
                      {"kind": "fact", "text": "112 PCIe Gen 5 lanes."}],
            "check": "Lane count"}
    base.update(extra)
    return base


def test_render_escapes_and_builds_buttons():
    text, keyboard = vt.render(result())
    assert "Local AI &lt;rig&gt;" in text and "W9-3495X &amp; 7 slots" in text
    assert f"watch?v={VID}&amp;t=754s" in text and "12:34" in text
    assert "(39)" in text  # character count of post 1
    assert [b["text"] for b in keyboard[0]] == ["✅ Post 1 on X", "✅ Post 2 on X"]
    assert keyboard[0][0]["url"].startswith("https://x.com/intent/tweet?text=I%20think")
    assert keyboard[-1][0]["url"] == f"https://www.youtube.com/watch?v={VID}"


def test_render_stays_under_telegram_limit():
    facts = [{"seconds": i, "text": "f" * 220} for i in range(6)]
    posts = [{"kind": "fact", "text": "p" * 280} for _ in range(6)]
    text, _ = vt.render(result(facts=facts * 3, posts=posts))
    assert len(text) <= vt.TELEGRAM_LIMIT
    assert "Draft posts" in text and "Check:" in text


def message(text, chat=42, user=42, mid=7):
    return {"message_id": mid, "chat": {"id": chat}, "from": {"id": user}, "text": text}


def test_authorized():
    assert vt.authorized(message("x"), "42")
    assert not vt.authorized(message("x", chat=5), "42")
    assert not vt.authorized(message("x", user=5), "42", allowed_user="42")


def test_handle_message_help_limit_and_errors():
    state = {"daily": {"date": "2026-11-08", "count": 0}}
    assert vt.handle_message(message("hello"), state, NOW, "k")[0] == vt.HELP
    assert vt.handle_message({"text": ""}, state, NOW, "k") is None

    def blocked(video_id, key):
        raise vt.TakesError("YouTube blocked <it>")
    text, keyboard = vt.handle_message(message(f"https://youtu.be/{VID}"), state, NOW, "k", blocked)
    assert "blocked &lt;it&gt;" in text and keyboard is None
    assert state["daily"]["count"] == 0  # failures don't count

    text, keyboard = vt.handle_message(message(f"https://youtu.be/{VID}"), state, NOW, "k",
                                       lambda video_id, key: result())
    assert "Draft posts" in text and state["daily"]["count"] == 1

    state["daily"]["count"] = vt.MAX_PER_DAY
    assert "Daily limit" in vt.handle_message(message(f"https://youtu.be/{VID}"), state, NOW, "k")[0]
    state["daily"]["date"] = "2026-11-07"  # new IST day resets the count
    vt.handle_message(message(f"https://youtu.be/{VID}"), state, NOW, "k", lambda v, k: result())
    assert state["daily"] == {"date": "2026-11-08", "count": 1}


def test_poll_answers_authorized_links_and_saves_offset(tmp_path, monkeypatch):
    calls = []
    updates = [
        {"update_id": 10, "message": message(f"https://youtu.be/{VID}", mid=1)},
        {"update_id": 11, "message": message(f"https://youtu.be/{VID}", chat=999, user=999, mid=2)},
        {"update_id": 12, "message": message("hi", mid=3)},
    ]

    def fake_telegram(token, method, **payload):
        calls.append((method, payload))
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getUpdates":
            assert payload["allowed_updates"] == ["message", "callback_query"] and payload["offset"] == 0
            return updates
        return {"message_id": 100}

    monkeypatch.setattr(vt, "telegram", fake_telegram)
    monkeypatch.delenv("TELEGRAM_TAKES_BOT_TOKEN", raising=False)
    path = tmp_path / "takes.json"
    handled = vt.poll("t", "42", "k", state_path=path, now=NOW, analyzer=lambda v, k: result(),
                      video_state_path=tmp_path / "videos.json")
    assert handled == 2
    sent = [p for m, p in calls if m == "sendMessage"]
    assert [p["reply_parameters"]["message_id"] for p in sent] == [1, 3]
    assert all(p["chat_id"] == "42" for p in sent)
    state = vt.load_state(path)
    assert state["update_offset"] == 13 and state["daily"]["count"] == 1
    assert "text" not in path.read_text()  # no message text stored


def test_shared_inbox_handles_links_and_feedback_without_stealing_updates(monkeypatch, tmp_path):
    import video_recommender as vr

    monkeypatch.delenv("TELEGRAM_TAKES_BOT_TOKEN", raising=False)
    path, video_path = tmp_path / "takes.json", tmp_path / "videos.json"
    feedback_state = vr.load_state(video_path)
    feedback_state["update_offset"] = 5
    feedback_state["videos"][VID] = {"status": "sent", "review": {"category": "creative"}}
    vr.save_state(feedback_state, video_path)
    updates = [{"update_id": 5, "message": message("hello")}]
    for number, user in ((6, 999), (7, 42), (8, 42)):
        updates.append({"update_id": number, "callback_query": {
            "id": str(number), "from": {"id": user}, "message": message("card"),
            "data": f"vid:useful:{VID}"}})
    acknowledgements = []

    def telegram(token, method, **payload):
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getUpdates":
            assert payload["allowed_updates"] == ["message", "callback_query"]
            assert payload["offset"] in (5, 9)
            return updates
        if method == "answerCallbackQuery":
            assert vt.load_state(path)["update_offset"] == int(payload["callback_query_id"]) + 1
            assert vr.load_state(video_path)["update_offset"] == vt.load_state(path)["update_offset"]
            acknowledgements.append(payload["text"])
            raise RuntimeError("expired callback")

    monkeypatch.setattr(vt, "telegram", telegram)
    assert vt.poll("t", "42", "k", state_path=path, video_state_path=video_path) == 4
    assert vt.poll("t", "42", "k", state_path=path, video_state_path=video_path) == 0
    assert vr.load_state(video_path)["preferences"]["creative"]["interest"] == 1
    assert acknowledgements == ["Feedback already recorded or unavailable", "Feedback saved",
                                "Feedback already recorded or unavailable"]


def test_separate_takes_bot_only_polls_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TAKES_BOT_TOKEN", "separate-token")
    video_path = tmp_path / "videos.json"
    video_path.write_text("invalid video state must not be read")

    def telegram(token, method, **payload):
        if method == "getWebhookInfo":
            return {"url": ""}
        assert payload["allowed_updates"] == ["message"]
        return []

    monkeypatch.setattr(vt, "telegram", telegram)
    assert vt.poll("t", "42", "k", state_path=tmp_path / "takes.json", video_state_path=video_path) == 0
