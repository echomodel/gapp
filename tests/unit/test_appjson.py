"""What gapp takes from an app's `app.json`: its `env` block, read by a
deploy from the commit it builds, and by nothing else. The definition
wins; `app.json` fills in what the definition leaves out."""

import json
import subprocess

import pytest

from gapp.admin.sdk import appjson
from gapp.admin.sdk.appjson import AppJsonRefusal


def declared(env):
    return appjson.parse(json.dumps({"env": env}))


# -- reading the file --


def test_each_variable_is_read_with_its_default_and_what_it_needs():
    found = declared({
        "LOG_LEVEL": {"description": " debug or info ", "value": "info"},
        "API_URL": {"description": "Upstream API address"},
        "TRACE": {"required": False},
        "APP_KEY": {"generator": "secret"},
    })
    assert found == {
        "LOG_LEVEL": {"value": "info", "required": True, "generated": False, "description": "debug or info"},
        "API_URL": {"value": None, "required": True, "generated": False, "description": "Upstream API address"},
        "TRACE": {"value": None, "required": False, "generated": False, "description": None},
        "APP_KEY": {"value": None, "required": True, "generated": True, "description": None},
    }


def test_a_bare_value_is_the_default_and_values_become_text():
    found = declared({"PORT_HINT": 8080, "DEBUG": True, "NAME": "x"})
    assert [found[k]["value"] for k in ("PORT_HINT", "DEBUG", "NAME")] == ["8080", "true", "x"]


def test_every_other_key_is_ignored_never_refused():
    text = json.dumps({"name": "my-app", "addons": ["something"], "formation": {"web": {"quantity": 2}},
                       "env": {"LOG_LEVEL": {"value": "info", "something_new": 1, "generator": "other"}}})
    assert appjson.parse(text) == {
        "LOG_LEVEL": {"value": "info", "required": True, "generated": False, "description": None}}


def test_a_file_with_no_env_block_has_said_nothing():
    assert appjson.parse('{"name": "my-app"}') is None
    assert appjson.parse('{"env": null}') is None
    assert appjson.parse('{"env": {}}') == {}


@pytest.mark.parametrize("text, says", [
    ("{", "is not valid JSON"),
    ("[]", "is not an object at its top level"),
    ('{"env": []}', "'env' is not an object of variable names"),
    ('{"env": {"A": []}}', "env.A is neither a value nor an object"),
    ('{"env": {"A": {"value": {"x": 1}}}}', "env.A: 'value' is text"),
    ('{"env": {"1BAD": "x"}}', "'1BAD' is not a variable name"),
])
def test_a_file_gapp_cannot_use_is_refused_with_what_is_wrong(text, says):
    with pytest.raises(AppJsonRefusal) as caught:
        appjson.parse(text)
    assert says in caught.value.message and caught.value.message.startswith("app.json")


# -- it is read from the commit, never from the working tree --


def test_it_is_read_from_the_commit_that_is_built(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert appjson.read(tmp_path) is None                                # no commit, no file
    (tmp_path / "app.json").write_text('{"env": {"LOG_LEVEL": {"value": "info"}}}')
    git("add", "app.json")
    commit = git("commit-tree", git("write-tree"), "-m", "first")
    git("update-ref", "HEAD", commit)
    (tmp_path / "app.json").write_text('{"env": {"LOG_LEVEL": {"value": "debug"}}}')     # not committed
    assert appjson.read(tmp_path)["LOG_LEVEL"]["value"] == "info"


# -- the definition wins; app.json fills in what it leaves out --


APP = {
    "LOG_LEVEL": {"value": "info"},
    "API_URL": {"description": "Upstream API address"},
    "TRACE": {"required": False},
    "APP_KEY": {"generator": "secret"},
}


def test_a_default_is_used_for_a_variable_the_definition_does_not_set():
    filled = appjson.fill({}, declared({"LOG_LEVEL": {"value": "info"}}))
    assert filled["settings"] == {"env": {"LOG_LEVEL": {"value": "info"}}}
    assert filled["defaults"] == ["LOG_LEVEL"] and filled["missing"] == [] and filled["unlisted"] == []


def test_the_definitions_value_wins_over_the_default():
    filled = appjson.fill({"env": {"LOG_LEVEL": {"value": "debug"}}}, declared({"LOG_LEVEL": {"value": "info"}}))
    assert filled["settings"]["env"] == {"LOG_LEVEL": {"value": "debug"}} and filled["defaults"] == []


def test_a_required_variable_with_no_default_and_a_generated_secret_must_come_from_the_definition():
    filled = appjson.fill({}, declared(APP))
    assert filled["missing"] == [
        {"name": "API_URL", "description": "Upstream API address", "generated": False},
        {"name": "APP_KEY", "description": None, "generated": True},
    ]
    assert filled["settings"] == {"env": {"LOG_LEVEL": {"value": "info"}}}      # nothing is generated or invented


def test_an_optional_variable_with_no_default_is_left_unset():
    filled = appjson.fill({}, declared({"TRACE": {"required": False}}))
    assert filled == {"settings": {}, "defaults": [], "missing": [], "unlisted": []}


def test_whatever_the_definition_sets_satisfies_app_json_whatever_kind_it_asked_for():
    for entry in ({"value": "plain"}, {"secret": "app-key"}, {"secret": "app-key", "generate": True}):
        filled = appjson.fill({"env": {"APP_KEY": entry, "API_URL": {"value": "u"}}}, declared(APP))
        assert filled["missing"] == [] and filled["settings"]["env"]["APP_KEY"] == entry


def test_a_variable_the_app_no_longer_asks_to_generate_keeps_the_definitions_secret():
    mine = {"env": {"APP_KEY": {"secret": "app-key", "generate": True}}}
    filled = appjson.fill(mine, declared({"APP_KEY": {"value": "a-default"}}))
    assert filled["settings"] == mine and filled["defaults"] == []


def test_a_variable_the_definition_sets_that_app_json_does_not_list_is_reported_not_refused():
    filled = appjson.fill({"env": {"LOG_LEVLE": {"value": "debug"}, "LOG_LEVEL": {"value": "debug"}}},
                          declared({"LOG_LEVEL": {"value": "info"}}))
    assert filled["unlisted"] == ["LOG_LEVLE"] and filled["missing"] == []
    assert filled["settings"]["env"]["LOG_LEVLE"] == {"value": "debug"}          # still passed
    assert appjson.unlisted_lines("my-app", filled["unlisted"]) == [
        "'my-app' sets LOG_LEVLE, and this version's app.json does not list it. The app may not read it."]


def test_an_app_with_no_app_json_or_no_env_block_is_never_checked():
    mine = {"env": {"ANYTHING": {"value": "x"}}}
    assert appjson.fill(mine, None) == {"settings": mine, "defaults": [], "missing": [], "unlisted": []}


def test_the_refusal_lists_every_missing_variable_with_its_command():
    text = appjson.missing_text("my-app", appjson.fill({}, declared(APP))["missing"])
    assert text.splitlines() == [
        "'my-app' cannot be deployed yet: its app.json needs 2 variables the definition does not set. "
        "Nothing was built or changed.",
        "  API_URL (Upstream API address)",
        "    a plain value:       gapp env set API_URL <value> --deployment my-app",
        "    a secret:            gapp env set API_URL --secret --deployment my-app",
        "  APP_KEY",
        "    a generated secret:  gapp env set APP_KEY --secret --generate --deployment my-app",
    ]
