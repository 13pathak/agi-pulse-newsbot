"""Editorial guidance stays separate from published text and survives queueing."""
import html
import json
import urllib.parse

import pytest
import yaml

from test_newsbot import CONFIG, NOW, candidate, env  # noqa: F401
import newsbot


def draft(note="Check license terms & benchmark conditions."):
    item = candidate("Lab", 1, category="primary")
    result = {"picks": [{"primary_id": "c0", "importance": 8,
                         "post": "The lab released a model for research use.",
                         "editor_note": note}]}
    return newsbot.select_picks(result, [item], 6, 1)[0]


def queue_for_run(env, monkeypatch, selected):
    state = newsbot.load_state(env["state"])
    newsbot.enqueue_picks(state, [selected], NOW, CONFIG["settings"])
    newsbot.save_state(state, NOW, env["state"])
    monkeypatch.setattr(newsbot, "fetch_sources", lambda *_: iter([]))


def test_editor_note_survives_disk_and_is_excluded_from_x_link(env, monkeypatch):
    selected = draft()
    queue_for_run(env, monkeypatch, selected)
    assert newsbot.load_state(env["state"])["pending"][0]["editor_note"] == selected["editor_note"]
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    message = env["sent"][0]
    assert "AI-assisted draft" in message["text"]
    assert "Editor note (not posted): " + html.escape(selected["editor_note"]) in message["text"]
    link = message["reply_markup"]["inline_keyboard"][0][0]["url"]
    post = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["text"][0]
    expected = newsbot.compose_post(selected["post"], selected["item"]["source"],
                                    selected["item"]["url"], True)
    assert post == expected  # no editor note, review reminder or AI label in the prefill
    assert newsbot.x_len(post) <= 280
    assert not env["prompts"]  # no extra model call to deliver a cached note


@pytest.mark.parametrize("note", [None, 7, ["not a string"], {"instruction": "not a string"}])
def test_malformed_optional_editor_note_does_not_drop_a_good_story(note):
    selected = draft(note)
    assert selected["post"] and selected["editor_note"] == ""


def test_editor_note_is_bounded_plain_text_and_escaped_in_telegram():
    selected = draft('<a href="https://example.test">Check</a> terms & evidence. ' + "detail " * 100)
    assert len(selected["editor_note"]) <= 181
    assert "<a" not in selected["editor_note"]
    rendered = newsbot.telegram_message(selected, selected["post"])
    assert "terms &amp; evidence" in rendered
    assert "https://example.test" not in rendered


def test_legacy_queued_draft_without_note_still_delivers(env, monkeypatch):
    queue_for_run(env, monkeypatch, draft())
    state = json.loads(env["state"].read_text())
    state["pending"][0].pop("editor_note")
    env["state"].write_text(json.dumps(state))
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert len(env["sent"]) == 1
    assert "Editor note (not posted)" not in env["sent"][0]["text"]


@pytest.mark.parametrize("explicit_false", [False, True])
def test_source_images_require_opt_in_without_fetching_og_image(env, monkeypatch, explicit_false):
    selected = draft()
    selected["item"].update(images=True, image="")
    queue_for_run(env, monkeypatch, selected)
    if explicit_false:
        config = yaml.safe_load(env["cfg"].read_text())
        config["settings"]["attach_images"] = False
        env["cfg"].write_text(yaml.safe_dump(config))

    def unexpected_image_fetch(*_):
        pytest.fail("Source images must not be fetched without opt-in")

    monkeypatch.setattr(newsbot, "og_image", unexpected_image_fetch)
    assert newsbot.run(now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert len(env["sent"]) == 1 and "text" in env["sent"][0]


def test_editor_note_in_dry_run_leaves_queue_untouched(env, monkeypatch, capsys):
    selected = draft()
    queue_for_run(env, monkeypatch, selected)
    before = env["state"].read_bytes()
    assert newsbot.run(dry_run=True, now=NOW, config_path=env["cfg"], state_path=env["state"]) == 0
    assert "Editor note (not posted): " + selected["editor_note"] in capsys.readouterr().out
    assert env["sent"] == [] and env["state"].read_bytes() == before
