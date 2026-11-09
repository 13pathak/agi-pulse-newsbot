"""Offline checks for discovery, evidence gates, budgets, delivery and feedback."""
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video_recommender as vr

NOW = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


def config():
    return vr.load_config()


def state():
    result = {"version": 1, "videos": {}, "daily": {}, "preferences": {},
              "channel_uploads": {}, "update_offset": 0}
    vr.prepare_state(result, NOW, config()["settings"])
    return result


def video(number=1, category="creative", age=1):
    return {"id": f"vid{number:08d}", "title": f"Build a practical project {number}",
            "description": "A complete workflow with steps and results.",
            "channel": f"Creator {number}", "channel_id": f"channel{number}",
            "duration": 600, "published_at": (NOW - timedelta(days=age)).isoformat(),
            "discovered_at": NOW.isoformat(), "categories": [category], "status": "new"}


def review(category="creative", key="animated product storyboard tool a"):
    return {"accessible": True, "practical": True, "category": category, "difficulty": "beginner",
            "task": "Create a short animation", "tools": ["A model"], "workflow_key": key,
            "demonstrated": "Inputs, editing steps and a finished animation appear on screen.",
            "creator_claim": "Saves an hour; not independently verified", "requirements": "An account",
            "cost": "Not established", "caveat": "Only one example shown",
            "evidence": [{"seconds": 120, "observation": "The input scene is edited."},
                         {"seconds": 400, "observation": "The final result plays."}],
            "scores": {"evidence": 8, "reproducibility": 8, "relevance": 8, "clarity": 8}}


def ready(number=1, category="creative", **kw):
    result = video(number, category, **kw)
    result.update(status="ready", review=vr.validate_review(review(category, f"project {number} tool a"), result))
    return result


def raw_video(number=1, **updates):
    v = video(number)
    result = {"id": v["id"], "snippet": {"title": v["title"], "description": v["description"],
              "channelId": v["channel_id"], "channelTitle": v["channel"],
              "publishedAt": v["published_at"], "liveBroadcastContent": "none"},
              "contentDetails": {"duration": "PT10M"}, "status": {"privacyStatus": "public"}}
    result.update(updates)
    return result


@pytest.mark.parametrize("duration,expected", [("PT4M5S", 245), ("PT1H", 3600), ("P1DT2H", 93600),
                                               ("P0D", 0), ("invalid", 0), (None, 0)])
def test_duration(duration, expected):
    assert vr.duration_seconds(duration) == expected


@pytest.mark.parametrize("bad", ["live", "upcoming", "private", "old", "future", "short", "long", "undated"])
def test_discovery_excludes_unusable_videos(bad):
    raw = raw_video()
    if bad in ("live", "upcoming"):
        raw["snippet"]["liveBroadcastContent"] = bad
    elif bad == "private":
        raw["status"]["privacyStatus"] = "private"
    elif bad in ("old", "future"):
        raw["snippet"]["publishedAt"] = (NOW + timedelta(days=1 if bad == "future" else -40)).isoformat()
    elif bad == "undated":
        raw["snippet"].pop("publishedAt")
    else:
        raw["contentDetails"]["duration"] = "PT30S" if bad == "short" else "PT2H"
    assert vr.parse_video(raw, ["creative"], NOW, config()["settings"]) is None


def test_discovery_dedupes_ids_keeps_small_creators_and_honors_search_budget():
    cfg, st, calls = config(), state(), []
    st["daily"]["searches"] = cfg["settings"]["max_search_calls_per_day"] - 2

    class API:
        def get(self, resource, **params):
            calls.append((resource, params))
            if resource == "search":
                return {"items": [{"id": {"videoId": video()["id"]}}]}
            return {"items": [raw_video()]}

    vr.discover(cfg, st, API(), NOW)
    assert len(st["videos"]) == 1
    assert st["videos"][video()["id"]]["categories"] == ["creative", "everyday"]
    assert [r for r, _ in calls] == ["search", "search", "videos"]
    assert st["daily"]["searches"] == cfg["settings"]["max_search_calls_per_day"]
    assert all("viewCount" not in p for _, p in calls)


def test_channel_uploads_use_cached_playlist_instead_of_search():
    cfg, st, calls = config(), state(), []
    cfg["channels"] = [{"handle": "@creator", "category": "applications"}]
    st["daily"]["searches"] = cfg["settings"]["max_search_calls_per_day"]

    class API:
        def get(self, resource, **params):
            calls.append(resource)
            return {"channels": {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUverified"}}}]},
                    "playlistItems": {"items": [{"contentDetails": {"videoId": video()["id"]}}]},
                    "videos": {"items": [raw_video()]}}[resource]

    vr.discover(cfg, st, API(), NOW)
    vr.discover(cfg, st, API(), NOW)
    assert calls == ["channels", "playlistItems", "videos", "playlistItems"]
    assert st["videos"][video()["id"]]["categories"] == ["applications"]


def test_partial_discovery_is_retained_when_later_search_fails():
    cfg, st, searches = config(), state(), []

    class API:
        def get(self, resource, **params):
            if resource == "search":
                searches.append(params)
                if len(searches) == 2:
                    raise vr.ServiceError("quota")
                return {"items": [{"id": {"videoId": video()["id"]}}]}
            return {"items": [raw_video()]}

    with pytest.raises(vr.ServiceError, match="quota"):
        vr.discover(cfg, st, API(), NOW)
    assert video()["id"] in st["videos"] and st["daily"]["searches"] == 2
    assert vr.utc(st["next_discovery_at"]) == NOW + timedelta(hours=1)


def test_metadata_batches_are_saved_before_later_batch_failure():
    cfg, st, saved = config(), state(), []

    class API:
        def get(self, resource, **params):
            if resource == "search":
                return {"items": [{"id": {"videoId": video(i)["id"]}} for i in range(60)]}
            ids = params["id"].split(",")
            if len(ids) < 50:
                raise vr.ServiceError("metadata unavailable")
            return {"items": [raw_video(i) for i in range(50)]}

    with pytest.raises(vr.ServiceError, match="metadata unavailable"):
        vr.discover(cfg, st, API(), NOW, lambda: saved.append(deepcopy(st)))
    assert len(saved[-1]["videos"]) == 50


@pytest.mark.parametrize("seconds", [-1, 600, 99999, True, "120"])
def test_review_requires_in_range_integer_timestamps(seconds):
    raw = review()
    raw["evidence"] = [{"seconds": seconds, "observation": "A result"}]
    with pytest.raises(vr.ServiceError, match="timestamp"):
        vr.validate_review(raw, video())


def test_unavailable_video_is_retryable_and_nonpractical_video_is_rejected():
    with pytest.raises(vr.ServiceError, match="unavailable"):
        vr.validate_review({"accessible": False}, video())
    assert vr.validate_review({"accessible": True, "practical": False}, video()) is None


@pytest.mark.parametrize("decision", [None, "false", 0])
def test_invalid_practical_decision_does_not_permanently_reject_video(decision):
    with pytest.raises(vr.ServiceError, match="decision"):
        vr.validate_review({"accessible": True, "practical": decision}, video())


def test_untrusted_review_is_bounded_and_unknown_cost_is_explicit():
    raw = review()
    raw.update(task='<a href="https://evil.test">Task</a>' * 200, cost=None, tools={"bad": "type"})
    raw["scores"]["evidence"] = True
    checked = vr.validate_review(raw, video())
    assert len(checked["task"]) <= 180 and "<a" not in checked["task"]
    assert checked["cost"] == "Not established in the reviewed video"
    assert checked["scores"]["evidence"] == 0 and checked["tools"] == []


def test_selection_is_diverse_and_high_score_cannot_bypass_evidence_gate():
    st, cfg = state(), config()
    records = [ready(1), ready(2), ready(3, "everyday"), ready(4, "applications")]
    weak = ready(5, "applications", age=0)
    weak["review"]["scores"] = {"evidence": 0, "reproducibility": 10, "relevance": 10, "clarity": 10}
    records.append(weak)
    st["videos"] = {v["id"]: v for v in records}
    picked = vr.selected_videos(st, cfg, NOW)
    assert len(picked) == 3 and {v["review"]["category"] for v in picked} == set(vr.CATEGORY_IDS)
    assert weak not in picked
    st["daily"]["sent"] = 3
    assert vr.selected_videos(st, cfg, NOW) == []


def test_duplicate_workflow_and_same_creator_do_not_repeat():
    st, cfg = state(), config()
    old, duplicate, same_creator, other = ready(1), ready(2), ready(3, "everyday"), ready(4, "applications")
    old["status"] = "sent"
    duplicate["review"]["workflow_key"] = old["review"]["workflow_key"]
    same_creator["channel_id"] = other["channel_id"]
    st["videos"] = {v["id"]: v for v in [old, duplicate, same_creator, other]}
    picked = vr.selected_videos(st, cfg, NOW)
    assert duplicate not in picked and len(picked) == 1


def test_strong_older_demo_beats_fresh_weak_demo():
    st, cfg = state(), config()
    older, fresh = ready(1, age=8), ready(2, age=0)
    older["review"]["scores"] = {key: 10 for key in older["review"]["scores"]}
    fresh["review"]["scores"] = {key: 6 for key in fresh["review"]["scores"]}
    st["videos"] = {v["id"]: v for v in [older, fresh]}
    assert vr.selected_videos(st, cfg, NOW) == [older]


def test_review_budget_cache_and_api_failure_do_not_invent_evidence(monkeypatch):
    cfg, st, calls = config(), state(), []
    cfg["settings"]["max_reviews_per_run"] = 1
    st["videos"] = {v["id"]: v for v in [video(1), video(2, "everyday")]}

    def gemini(key, model, prompt, video_id=None):
        calls.append(video_id)
        if video_id is None:
            return {"candidates": [{"id": video(i)["id"], "category": c, "relevance": 8}
                                    for i, c in [(1, "creative"), (2, "everyday")]]}
        if video_id == video(1)["id"]:
            raise vr.ServiceError("Video unavailable")
        return review("everyday")

    monkeypatch.setattr(vr, "gemini_json", gemini)
    vr.review_candidates(cfg, st, NOW, "key")
    assert st["videos"][video(1)["id"]]["status"] == "retry"
    assert "review" not in st["videos"][video(1)["id"]]
    vr.review_candidates(cfg, st, NOW + timedelta(minutes=30), "key")
    assert calls == [None, video(1)["id"], video(2)["id"]]  # screen reused
    assert st["daily"]["reviews"] == 2 and st["daily"]["video_seconds"] == 1200
    st["daily"]["reviews"] = cfg["settings"]["max_reviews_per_day"]
    vr.review_candidates(cfg, st, NOW + timedelta(days=1), "key")
    assert len(calls) == 3


def test_empty_screening_is_bounded_across_runs(monkeypatch):
    cfg, st, calls = config(), state(), []
    st["videos"][video()["id"]] = video()
    monkeypatch.setattr(vr, "gemini_json", lambda *a: calls.append(a) or {"candidates": []})
    for i in range(12):
        vr.review_candidates(cfg, st, NOW + timedelta(minutes=30 * i), "key")
    assert len(calls) == 1 and st["daily"]["screens"] == 1


def test_video_minute_budget_cannot_be_exceeded(monkeypatch):
    cfg, st = config(), state()
    cfg["settings"]["max_video_minutes_per_day"] = 1
    v = video()
    v["screen"] = {"category": "creative", "relevance": 9}
    st["videos"][v["id"]] = v
    monkeypatch.setattr(vr, "gemini_json", lambda *_: pytest.fail("budget exceeded"))
    vr.review_candidates(cfg, st, NOW, "key")
    assert st["daily"]["reviews"] == 0


def callback(action="useful", user=123, chat=123, update=5):
    return {"update_id": update, "callback_query": {"id": "callback", "from": {"id": user},
             "message": {"chat": {"id": chat}}, "data": f"vid:{action}:{video()['id']}"}}


def test_feedback_authentication_replay_and_bounded_personalization():
    st = state()
    v = ready()
    v["status"] = "sent"
    st["videos"][v["id"]] = v
    assert not vr.apply_feedback(st, callback(user=999), "123", "123")
    assert not vr.apply_feedback(st, callback(chat=999), "123", "123")
    assert vr.apply_feedback(st, callback("technical"), "123", "123")
    assert not vr.apply_feedback(st, callback("useful"), "123", "123")
    assert st["preferences"]["creative"]["difficulty"] == 0
    assert "123" not in json.dumps(st)


def test_feedback_persists_before_next_poll_and_survives_expired_callback(monkeypatch):
    st, saved = state(), []
    v = ready()
    v["status"] = "sent"
    st["videos"][v["id"]] = v

    def telegram(token, method, **payload):
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getUpdates":
            return [callback()]
        assert saved[-1]["update_offset"] == 6
        raise vr.ServiceError("callback expired")

    monkeypatch.setattr(vr, "telegram_call", telegram)
    vr.poll_feedback(st, "secret", "123", "123", lambda: saved.append(deepcopy(st)))
    vr.poll_feedback(st, "secret", "123", "123")
    assert st["preferences"]["creative"]["interest"] == 1


def test_existing_telegram_webhook_is_not_replaced(monkeypatch):
    monkeypatch.setattr(vr, "telegram_call", lambda *a, **kw: {"url": "https://existing.test"})
    with pytest.raises(vr.ServiceError, match="webhook"):
        vr.poll_feedback(state(), "secret", "123", "123")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = tmp_path / "videos.json"
    st = state()
    st["videos"] = {v["id"]: v for v in [ready(1), ready(2, "everyday"), ready(3, "applications")]}
    st["next_discovery_at"] = (NOW + timedelta(hours=12)).isoformat()
    vr.save_state(st, path)
    monkeypatch.setenv("YOUTUBE_API_KEY", "youtube-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(vr, "poll_feedback", lambda *args: None)
    monkeypatch.setattr(vr, "discover", lambda *args: None)
    monkeypatch.setattr(vr, "review_candidates", lambda *args: None)
    return path


@pytest.mark.parametrize("mode", ["inspect", "dry-run"])
def test_preview_never_sends_consumes_feedback_or_changes_state(runtime, monkeypatch, mode, capsys):
    before = runtime.read_bytes()
    monkeypatch.setattr(vr, "poll_feedback", lambda *a: pytest.fail("preview consumed feedback"))
    monkeypatch.setattr(vr, "send_telegram", lambda *a: pytest.fail("preview sent a message"))
    if mode == "inspect":
        monkeypatch.setattr(vr, "review_candidates", lambda *a: pytest.fail("inspect called Gemini"))
    assert vr.run(mode, state_path=runtime, now=NOW) == 0
    assert runtime.read_bytes() == before
    assert "secret" not in capsys.readouterr().out


def test_delivery_failure_preserves_queue_and_prior_success(runtime, monkeypatch):
    calls = []

    def send(*args):
        calls.append(args)
        if len(calls) == 2:
            raise vr.requests.ReadTimeout("https://api.telegram.org/botSECRET")

    monkeypatch.setattr(vr, "send_telegram", send)
    with pytest.raises(vr.ServiceError, match="retained") as error:
        vr.run("normal", state_path=runtime, now=NOW)
    assert "SECRET" not in str(error.value)
    saved = vr.load_state(runtime)
    assert saved["daily"]["sent"] == 1
    assert sum(v["status"] == "ready" for v in saved["videos"].values()) == 2
    monkeypatch.setattr(vr, "send_telegram", lambda *args: calls.append(args))
    assert vr.run("normal", state_path=runtime, now=NOW + timedelta(minutes=30)) == 0
    assert len(calls) == 4 and vr.load_state(runtime)["daily"]["sent"] == 3
    assert vr.run("normal", state_path=runtime, now=NOW + timedelta(hours=1)) == 0
    assert len(calls) == 4


def test_telegram_cooldown_does_not_retry_early(runtime, monkeypatch):
    st = vr.load_state(runtime)
    st["telegram_retry_at"] = (NOW + timedelta(hours=1)).isoformat()
    vr.save_state(st, runtime)
    monkeypatch.setattr(vr, "send_telegram", lambda *a: pytest.fail("sent during cooldown"))
    assert vr.run("normal", state_path=runtime, now=NOW) == 0


def test_before_digest_time_processes_feedback_only(runtime, monkeypatch):
    monkeypatch.setattr(vr, "discover", lambda *a: pytest.fail("early discovery"))
    monkeypatch.setattr(vr, "review_candidates", lambda *a: pytest.fail("early review"))
    monkeypatch.setattr(vr, "send_telegram", lambda *a: pytest.fail("early delivery"))
    assert vr.run("normal", state_path=runtime, now=NOW - timedelta(hours=2)) == 0


def test_shared_inbox_delivery_never_starts_second_update_reader(runtime, monkeypatch):
    monkeypatch.setenv("VIDEO_FEEDBACK_POLLING", "false")
    monkeypatch.setattr(vr, "poll_feedback", lambda *a: pytest.fail("second Telegram reader"))
    assert vr.run("normal", state_path=runtime, now=NOW - timedelta(hours=2)) == 0


def test_telegram_card_escapes_untrusted_content_and_uses_known_urls():
    v = ready()
    v["review"]["task"] = 'Use <script> & "demo"'
    text, buttons = vr.recommendation(v, config())
    assert "<script>" not in text and "&lt;script&gt; &amp;" in text
    assert "not been independently reproduced" in text
    assert "&amp;t=120s" in text
    assert buttons[0][0]["url"] == vr.youtube_url(v["id"])
    assert all(len(b["callback_data"].encode()) <= 64 for row in buttons[1:] for b in row)


def test_corrupt_state_fails_closed_and_old_metadata_expires(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        vr.load_state(path)
    st = state()
    v = ready()
    v["discovered_at"] = (NOW - timedelta(days=31)).isoformat()
    st["videos"][v["id"]] = v
    vr.prepare_state(st, NOW, config()["settings"])
    assert not st["videos"]


def test_youtube_error_does_not_expose_key_or_response_body(monkeypatch):
    class Response:
        ok = False
        status_code = 403

        def json(self):
            return {"error": {"message": "key=SECRET", "errors": [{"reason": "accessNotConfigured"}]}}

    monkeypatch.setattr(vr.requests, "get", lambda *a, **kw: Response())
    with pytest.raises(vr.ServiceError) as error:
        vr.YouTube("SECRET").get("search", q="test")
    assert "SECRET" not in str(error.value) and "accessNotConfigured" in str(error.value)


def test_youtube_uses_documented_key_parameter_and_sanitizes_network_errors(monkeypatch):
    def request(url, **kwargs):
        assert kwargs["params"] == {"q": "test", "key": "SECRET"}
        raise vr.requests.RequestException(url + "?key=SECRET")

    monkeypatch.setattr(vr.requests, "get", request)
    with pytest.raises(vr.ServiceError) as error:
        vr.YouTube("SECRET").get("search", q="test")
    assert "SECRET" not in str(error.value)
