"""Editorial decisions and durable queue behavior; all external services are faked."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
import yaml

from test_newsbot import CONFIG, NOW, Resp, candidate, env  # noqa: F401
import newsbot


def pick(number, importance=8, urgency="routine", age=0, category="news", **extra):
    item = candidate("Publisher", number, category, published=NOW - timedelta(hours=age))
    return {"item": item, "related": [], "importance": importance, "urgency": urgency,
            "headline": item["title"], "post": f"Factual announcement {number}.", **extra}


def state():
    return {"seen": {}, "covered": [], "pending": []}


def one_per_run(env):
    config = yaml.safe_load(env["cfg"].read_text())
    config["settings"]["max_drafts_per_run"] = 1
    env["cfg"].write_text(yaml.safe_dump(config))


def run(env, when=NOW, **kw):
    return newsbot.run(now=when, config_path=env["cfg"], state_path=env["state"], **kw)


def test_important_backlog_beats_routine_new_story_but_breaking_news_wins():
    queue = state()
    settings = CONFIG["settings"]
    newsbot.enqueue_picks(queue, [pick(1, importance=9, age=10, category="research")], NOW, settings)
    later = NOW + timedelta(hours=2)
    newsbot.enqueue_picks(queue, [pick(2, importance=7)], later, settings)
    assert queue["pending"][0]["headline"].endswith("1")
    newsbot.enqueue_picks(queue, [pick(3, importance=9, urgency="breaking")], later, settings)
    assert queue["pending"][0]["headline"].endswith("3")
    assert "Breaking" in queue["pending"][0]["selection_reason"]
    assert len(queue["pending"]) == 3  # lower-priority stories remain available


def test_waiting_helps_similar_stories_but_never_outweighs_large_importance_gap():
    queue = state()
    settings = CONFIG["settings"]
    newsbot.enqueue_picks(queue, [pick(1, age=2), pick(2, importance=6)], NOW, settings)
    later = NOW + timedelta(hours=12)
    fresh = pick(3)
    fresh["item"]["published"] = later
    newsbot.enqueue_picks(queue, [fresh], later, settings)
    assert [p["headline"].split()[-1] for p in queue["pending"]] == ["1", "3", "2"]


def test_urgency_expires_and_unknown_or_future_dates_get_no_breaking_boost():
    settings = CONFIG["settings"]
    queue = state()
    newsbot.enqueue_picks(queue, [pick(1, urgency="breaking", category="research")], NOW, settings)
    cached = queue["pending"][0]
    assert newsbot.story_priority(cached, NOW, settings)[0] == 10
    assert "Breaking" not in newsbot.story_priority(cached, NOW + timedelta(hours=6), settings)[1]
    assert "Time-sensitive" not in newsbot.story_priority(cached, NOW + timedelta(hours=12), settings)[1]
    for date in (None, NOW + timedelta(hours=1)):
        cached["published_at"] = newsbot.date_string(date)
        assert newsbot.story_priority(cached, NOW, settings)[0] == 8


def test_pending_survives_missing_feed_and_lookback_without_another_model_call(env, monkeypatch):
    one_per_run(env)
    run(env)
    saved = json.loads(env["state"].read_text())
    assert len(saved["pending"]) == 1
    assert saved["pending"][0]["importance"] == 8
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))
    monkeypatch.delenv("GEMINI_API_KEY")
    run(env, NOW + timedelta(hours=24))
    assert len(env["sent"]) == 2 and "Sarvam" in env["sent"][1]["text"]
    assert len(env["prompts"]) == 1
    assert json.loads(env["state"].read_text())["pending"] == []


def test_expired_stories_and_their_aliases_are_retired():
    queue = state()
    old = pick(1, age=35)
    old["related"] = [candidate("Other publisher", 2)]
    newsbot.enqueue_picks(queue, [old], NOW, CONFIG["settings"])
    newsbot.prune_pending(queue, NOW + timedelta(hours=1), CONFIG["settings"])
    assert queue["pending"] == []
    assert set(queue["seen"]) == {i["id"] for i in newsbot.story_items(old)}


def test_duplicate_coverage_keeps_clock_and_prefers_primary_source():
    queue = state()
    settings = CONFIG["settings"]
    newsbot.enqueue_picks(queue, [pick(1, age=5, editor_note="Check the reporting.")], NOW, settings)
    original = deepcopy(queue["pending"][0])
    duplicate = pick(2, urgency="breaking", category="primary", pending_id=original["item"]["id"],
                     editor_note="Check the release license.")
    newsbot.enqueue_picks(queue, [duplicate], NOW + timedelta(hours=3), settings)
    assert len(queue["pending"]) == 1
    merged = queue["pending"][0]
    assert merged["item"]["id"] == duplicate["item"]["id"]
    assert merged["item"]["published"] == duplicate["item"]["published"]
    assert merged["editor_note"] == duplicate["editor_note"]
    for field in ("first_seen_at", "expires_at", "urgency", "published_at"):
        assert merged[field] == original[field]
    assert merged["related"][0]["id"] == original["item"]["id"]
    another = pick(3, pending_id=original["id"])
    newsbot.enqueue_picks(queue, [another], NOW + timedelta(hours=4), settings)
    assert len(queue["pending"]) == 1  # source changes cannot invalidate pending references
    assert len(queue["pending"][0]["related"]) == 2
    assert queue["pending"][0]["editor_note"] == duplicate["editor_note"]
    assert queue["pending"][0]["id"] == original["id"]


def test_older_duplicate_evidence_shortens_deadline_without_changing_article_dates():
    queue = state()
    newsbot.enqueue_picks(queue, [pick(1)], NOW, CONFIG["settings"])
    original = deepcopy(queue["pending"][0])
    earlier = pick(2, age=10, pending_id=original["id"])
    newsbot.enqueue_picks(queue, [earlier], NOW, CONFIG["settings"])
    merged = queue["pending"][0]
    assert newsbot.article_date(merged["expires_at"]) == NOW + timedelta(hours=26)
    assert merged["item"]["published"] == NOW
    assert newsbot.article_date(merged["published_at"]) == NOW - timedelta(hours=10)


def test_overlapping_model_picks_merge_and_invalid_references_cannot_drop_queue():
    queue = state()
    first, second = pick(1), pick(2)
    second["related"] = [first["item"]]
    second["pending_id"] = "invented"
    second["supersedes_pending_ids"] = ["also-invented"]
    newsbot.enqueue_picks(queue, [first, second], NOW, CONFIG["settings"])
    assert len(queue["pending"]) == 1
    assert len(queue["pending"][0]["related"]) == 1


def test_valid_replacement_retires_superseded_story_but_expired_replacement_does_not():
    queue = state()
    old = pick(1)
    newsbot.enqueue_picks(queue, [old], NOW, CONFIG["settings"])
    stale = pick(2, age=40, supersedes_pending_ids=[old["item"]["id"]])
    newsbot.enqueue_picks(queue, [stale], NOW, CONFIG["settings"])
    assert queue["pending"][0]["item"]["id"] == old["item"]["id"]
    replacement = pick(3, supersedes_pending_ids=[old["item"]["id"]])
    newsbot.enqueue_picks(queue, [replacement], NOW, CONFIG["settings"])
    assert len(queue["pending"]) == 1
    assert queue["pending"][0]["item"]["id"] == replacement["item"]["id"]
    assert old["item"]["id"] in queue["seen"]


def test_queue_capacity_retains_highest_priority_and_remembers_evictions():
    queue = state()
    settings = {**CONFIG["settings"], "max_pending_stories": 2}
    newsbot.enqueue_picks(queue, [pick(1, importance=6), pick(2, importance=9), pick(3)], NOW, settings)
    assert [p["importance"] for p in queue["pending"]] == [9, 8]
    assert pick(1)["item"]["id"] in queue["seen"]


def test_full_queue_is_in_prompt_and_new_candidates_still_fit():
    queue = state()
    config = newsbot.load_config()
    entries = [pick(n) for n in range(40)]
    for entry in entries:
        entry["headline"] = "Long headline " * 20
        entry["item"]["snippet"] = "Long factual description " * 30
    newsbot.enqueue_picks(queue, entries, NOW, config["settings"])
    candidates = [candidate("Fresh", n, snippet="New source evidence. " * 30) for n in range(60)]
    fitted = newsbot.fit_prompt_budget(config, queue, candidates, NOW)
    prompt = newsbot.build_prompt(config, queue, fitted, NOW)
    assert 0 < len(fitted) < 60
    assert len(prompt) <= config["settings"]["max_prompt_chars"]
    assert all(entry["item"]["id"] in prompt for entry in queue["pending"])
    assert NOW.isoformat() in prompt and '"published_at"' in prompt


def test_new_model_call_receives_pending_context_and_failure_preserves_queue(env, monkeypatch):
    one_per_run(env)
    run(env)
    before = json.loads(env["state"].read_text())["pending"]
    new = candidate("The Verge", 9)
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([
        ({"name": "The Verge", "ai_only": True}, [new], None)]))
    prompts = []

    def unavailable(prompt, *_):
        prompts.append(prompt)
        raise newsbot.GeminiUnavailable("busy")

    monkeypatch.setattr(newsbot, "call_gemini", unavailable)
    run(env, NOW + timedelta(minutes=30))
    after = json.loads(env["state"].read_text())
    assert before[0]["item"]["id"] in prompts[0]
    assert after["pending"][0]["post"] == before[0]["post"]
    assert new["id"] not in after["seen"]
    assert len(env["sent"]) == 1  # no cached draft sent before checking new evidence


def test_partial_telegram_failure_preserves_unsent_draft_and_successful_delivery(env, monkeypatch):
    send = newsbot.send_telegram
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) == 2:
            raise newsbot.requests.ConnectionError("Telegram offline")
        return send(*args)

    monkeypatch.setattr(newsbot, "send_telegram", flaky)
    assert run(env) == 1   # failed run is visible, but state is saved
    saved = json.loads(env["state"].read_text())
    assert saved["daily"]["count"] == 1 and len(saved["covered"]) == 1
    assert len(saved["pending"]) == 1 and "Sarvam" in saved["pending"][0]["headline"]
    assert saved["pending"][0]["delivery_attempts"] == 1
    monkeypatch.setattr(newsbot, "send_telegram", send)
    assert run(env) == 0
    assert len(env["sent"]) == 2 and len(env["prompts"]) == 1


def test_rejected_draft_is_retired_and_next_story_is_delivered_same_run(env, monkeypatch):
    one_per_run(env)
    send = newsbot.send_telegram

    def reject_gpt6(token, chat, text, *rest):
        if "GPT-6" in text:
            raise newsbot.TelegramError(400, "Bad Request: can't parse entities")
        return send(token, chat, text, *rest)

    monkeypatch.setattr(newsbot, "send_telegram", reject_gpt6)
    assert run(env) == 1
    saved = json.loads(env["state"].read_text())
    assert len(env["sent"]) == 1 and "Sarvam" in env["sent"][0]["text"]   # queue not blocked
    assert saved["pending"] == [] and saved["daily"]["count"] == 1
    assert all("GPT-6" not in c["headline"] for c in saved["covered"])   # retired, not delivered
    # Marked seen: the next run neither re-drafts nor retries it (a retry would fail again).
    assert run(env, NOW + timedelta(minutes=30)) == 0 and len(env["sent"]) == 1


def test_temporary_failures_keep_all_drafts_beyond_six_attempts_then_recover(env, monkeypatch):
    one_per_run(env)
    send = newsbot.send_telegram

    def offline(*_):
        raise newsbot.requests.ConnectionError("Telegram offline")

    monkeypatch.setattr(newsbot, "send_telegram", offline)
    for attempt in range(1, 9):
        assert run(env, NOW + timedelta(minutes=30 * attempt)) == 1
        saved = json.loads(env["state"].read_text())
        head = saved["pending"][0]
        assert "GPT-6" in head["headline"] and head["delivery_attempts"] == attempt
        assert len(saved["pending"]) == 2 and saved["covered"] == []
        assert all(item["id"] not in saved["seen"] for p in saved["pending"] for item in newsbot.story_items(p))
    monkeypatch.setattr(newsbot, "send_telegram", send)
    assert run(env, NOW + timedelta(hours=5)) == 0
    assert len(env["sent"]) == 1 and "GPT-6" in env["sent"][0]["text"]
    assert len(json.loads(env["state"].read_text())["pending"]) == 1


@pytest.mark.parametrize("status,detail,parameters", [
    (400, "Bad Request: chat not found", {}),
    (400, "Bad Request: private chat not found", {}),
    (400, "Bad Request: group chat was upgraded to a supergroup chat", {"migrate_to_chat_id": -100123}),
    (400, "Unknown Telegram failure", {}),
    (400, "Bad Request: can't parse entities", {"migrate_to_chat_id": -100123}),
    (400, "Bad Request: message is too long", {"retry_after": 3600}),
    (401, "Unauthorized", {}), (403, "Forbidden", {}), (404, "Not Found", {}),
    (429, "Too Many Requests", {"retry_after": 3600}), (502, "Bad Gateway", {}),
])
def test_account_service_and_unknown_errors_preserve_entire_queue(env, monkeypatch, status, detail, parameters):
    one_per_run(env)
    calls = []

    def refuse(url, **kw):
        calls.append(url)
        return Resp(status=status, payload={"description": detail, "parameters": parameters})

    # Use the real delivery code and fake only Telegram, after filling the queue.
    send = newsbot.send_telegram
    def send_with_fake_http(*args):
        with monkeypatch.context() as patch:
            patch.setattr(newsbot.requests, "post", refuse)
            return send(*args)
    monkeypatch.setattr(newsbot, "send_telegram", send_with_fake_http)
    assert run(env) == 1
    saved = json.loads(env["state"].read_text())
    assert len(calls) == 1 and len(saved["pending"]) == 2
    assert saved["pending"][0]["delivery_attempts"] == 1
    assert saved["covered"] == [] and saved["daily"]["count"] == 0
    assert all(item["id"] not in saved["seen"] for p in saved["pending"] for item in newsbot.story_items(p))


@pytest.mark.parametrize("status,detail", [
    (400, "Bad Request: can't parse entities: unsupported start tag"),
    (400, "Bad Request: message is too long"),
    (400, "Bad Request: inline keyboard button URL 'x' is invalid: URL host is empty"),
    (413, "Request Entity Too Large"),
])
def test_only_confirmed_content_rejections_can_retire_drafts(status, detail):
    assert newsbot.TelegramError.from_response(Resp(status=status, payload={"description": detail})).rejected


def test_unparseable_telegram_error_is_retained_conservatively():
    response = Resp(status=400, content=b"<html>Unknown proxy error</html>")
    response.json = lambda: (_ for _ in ()).throw(ValueError("not JSON"))
    error = newsbot.TelegramError.from_response(response)
    assert not error.rejected and not error.photo_rejected


def test_rate_limit_cooldown_survives_restarts_and_delivery_resumes(env, monkeypatch):
    one_per_run(env)
    send = newsbot.send_telegram
    calls = []

    def limited(*args):
        calls.append(args)
        raise newsbot.TelegramError(429, "Too Many Requests", {"retry_after": 3600})

    monkeypatch.setattr(newsbot, "send_telegram", limited)
    assert run(env) == 1
    saved = json.loads(env["state"].read_text())
    assert newsbot.article_date(saved["telegram_retry_at"]) >= NOW + timedelta(hours=1)
    assert run(env, NOW + timedelta(minutes=30)) == 0 and len(calls) == 1
    assert len(json.loads(env["state"].read_text())["pending"]) == 2
    monkeypatch.setattr(newsbot, "send_telegram", send)
    assert run(env, NOW + timedelta(hours=1, minutes=1)) == 0
    assert len(env["sent"]) == 1
    assert "telegram_retry_at" not in json.loads(env["state"].read_text())


def test_picture_timeout_does_not_immediately_send_a_second_message(monkeypatch):
    calls = []

    def post(url, json=None, **kw):
        calls.append(url.rsplit("/", 1)[1])
        if url.endswith("sendPhoto"):
            raise newsbot.requests.ReadTimeout("slow image host")
        return type("R", (), {"ok": True})()

    monkeypatch.setattr(newsbot.requests, "post", post)
    with pytest.raises(newsbot.requests.ReadTimeout):
        newsbot.send_telegram("t", "1", "draft", [], photo="https://slow.test/x.png")
    assert calls == ["sendPhoto"]


def queue_picture_draft(env, monkeypatch, failure):
    """One queued draft with a picture; no new feed items, so Gemini is never called."""
    one_per_run(env)
    config = yaml.safe_load(env["cfg"].read_text())
    config["settings"]["attach_images"] = True
    env["cfg"].write_text(yaml.safe_dump(config))
    queued = pick(1, importance=9)
    queued["item"].update(images=True, image="https://slow.test/x.png")
    queue = state()
    newsbot.enqueue_picks(queue, [queued], NOW, CONFIG["settings"])
    newsbot.save_state(queue, NOW, env["state"])
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))
    calls = []

    def post(url, json=None, **kw):
        method = url.rsplit("/", 1)[1]
        calls.append(method)
        if method == "sendPhoto":
            raise failure("image host")
        return Resp()

    monkeypatch.setattr(newsbot.requests, "post", post)
    return calls


def test_picture_timeout_sends_text_only_on_next_run_instead_of_blocking(env, monkeypatch):
    calls = queue_picture_draft(env, monkeypatch, newsbot.requests.ReadTimeout)
    assert run(env) == 1 and calls == ["sendPhoto"]           # no immediate second send
    saved = json.loads(env["state"].read_text())
    assert saved["pending"][0]["text_only"] is True and saved["covered"] == []
    assert run(env, NOW + timedelta(minutes=30)) == 0
    assert calls == ["sendPhoto", "sendMessage"]             # picture skipped, draft delivered
    saved = json.loads(env["state"].read_text())
    assert saved["pending"] == [] and len(saved["covered"]) == 1


def test_connection_failure_keeps_the_picture_for_the_retry(env, monkeypatch):
    calls = queue_picture_draft(env, monkeypatch, newsbot.requests.ConnectTimeout)
    assert run(env) == 1
    assert "text_only" not in json.loads(env["state"].read_text())["pending"][0]
    run(env, NOW + timedelta(minutes=30))
    assert calls == ["sendPhoto", "sendPhoto"]               # Telegram unreachable, not the image


@pytest.mark.parametrize("status,detail", [
    (400, "Bad Request: chat not found"), (400, "Unknown rejection"),
    (401, "Unauthorized"), (429, "Too Many Requests"), (503, "Unavailable"),
])
def test_photo_destination_and_service_errors_do_not_attempt_text(monkeypatch, status, detail):
    calls = []
    def post(url, **kw):
        calls.append(url)
        return Resp(status=status, payload={"description": detail})
    monkeypatch.setattr(newsbot.requests, "post", post)
    with pytest.raises(newsbot.TelegramError):
        newsbot.send_telegram("t", "1", "draft", [], photo="https://example.test/x.png")
    assert len(calls) == 1 and calls[0].endswith("/sendPhoto")


def test_failed_drafts_still_expire_on_editorial_deadline(env, monkeypatch):
    one_per_run(env)
    def offline(*_):
        raise newsbot.requests.ConnectionError("Offline")
    monkeypatch.setattr(newsbot, "send_telegram", offline)
    assert run(env) == 1
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))
    assert run(env, NOW + timedelta(hours=48)) == 0
    assert json.loads(env["state"].read_text())["pending"] == []


def test_filter_logs_explain_each_exclusion_without_changing_selection(monkeypatch, caplog):
    config = deepcopy(CONFIG)
    config["settings"]["max_candidates"] = 1
    config["feeds"] = [{"name": "General", "ai_only": False}, {"name": "Lab", "ai_only": True}]
    items = [candidate("General", n) for n in range(6)]
    items[0]["published"] = NOW - timedelta(days=10)  # counted only once, as seen
    items[2]["published"] = NOW - timedelta(days=10)
    items[3].update(title="A camera announcement", snippet="New lens.")
    items[5]["url"] = items[4]["url"]  # duplicate after eligibility checks
    lab = candidate("Lab", 1, title="A camera announcement", snippet="New lens.")
    queue = state()
    queue["seen"][items[0]["id"]] = NOW.isoformat()
    queued = pick(1)
    queued["item"] = items[1]
    newsbot.enqueue_picks(queue, [queued], NOW, config["settings"])
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([
        (config["feeds"][0], items, None), (config["feeds"][1], [lab], None)]))
    with caplog.at_level("INFO", logger="newsbot"):
        chosen = newsbot.collect_candidates(config, queue, NOW)
    assert len(chosen) == 1
    assert "7 fetched; seen=1 queued=1 old=1 keyword_miss=1 eligible=3; duplicates=1 shortlist_omitted=1 prompt_omitted=0" in caplog.text
    lab_line = next(line for line in caplog.messages if line.startswith("filter ") and "Lab" in line)
    assert "keyword_miss=0 eligible=1" in lab_line  # AI-only feeds bypass keyword checks


def test_dry_run_leaves_queue_file_unchanged_and_sends_nothing(env):
    one_per_run(env)
    run(env)
    original = env["state"].read_bytes()
    run(env, NOW + timedelta(hours=1), dry_run=True)
    assert env["state"].read_bytes() == original
    assert len(env["sent"]) == 1


def test_legacy_state_loads_with_empty_queue_and_invalid_urgency_is_conservative(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"seen": {}, "covered": []}')
    assert newsbot.load_state(path)["pending"] == []
    item = candidate("Publisher", 1)
    for importance, urgency, expected in [(6, "breaking", "routine"), (8, "nonsense", "routine"),
                                          (99, "breaking", "breaking")]:
        result = {"picks": [{"primary_id": "c0", "importance": importance,
                             "urgency": urgency, "post": "A factual post."}]}
        selected = newsbot.select_picks(result, [item], 6, 1)[0]
        assert selected["urgency"] == expected and selected["importance"] <= 10
