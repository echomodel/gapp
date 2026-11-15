"""The entry file: `format` is read first and decides whether anything
else is; a definition records the gapp that wrote it."""

import pytest

from gapp import __version__
from gapp.admin.sdk import entryfile, fleetstore
from gapp.admin.sdk.entryfile import EntryFileRefusal
from gapp.admin.sdk.fleetstore import FleetUnavailable


@pytest.fixture
def local_machine(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "gapp"


def refusal(text):
    with pytest.raises(EntryFileRefusal) as caught:
        entryfile.parse(text, "The file")
    return caught.value


def test_no_text_is_a_fleet_with_nothing_in_it():
    assert entryfile.parse(None) == {"format": 1, "deployments": {}}
    assert entryfile.parse("  \n") == {"format": 1, "deployments": {}}


def test_a_written_file_starts_with_its_format_and_reads_back_the_same():
    doc = {"deployments": {"my-app": entryfile.new_definition(id="my-app", project="my-proj", repo="me/my-app")},
           "format": 1}
    text = entryfile.dump(doc)
    assert text.startswith("format: 1\ndeployments:\n")
    assert entryfile.parse(text) == doc


def test_a_file_with_no_format_is_refused_and_says_nothing_was_changed():
    e = refusal("my-app:\n  id: my-app\n  project: my-proj\n")
    assert e.code == "no-format"
    assert "has no 'format' at its top" in e.message and "changed nothing" in e.message


def test_a_newer_format_is_refused_before_the_rest_is_looked_at():
    e = refusal("format: 2\ndeployments: [this, is, not, a, mapping]\n")
    assert e.code == "newer-format"
    assert "format 2" in e.message and "Install a newer gapp" in e.message


def test_an_older_format_is_refused():
    assert refusal("format: 0\ndeployments: {}\n").code == "older-format"


@pytest.mark.parametrize("value", ["'1'", "true", "1.5", "[1]"])
def test_a_format_that_is_not_a_whole_number_is_refused(value):
    assert refusal(f"format: {value}\n").code == "bad-format"


def test_a_file_that_is_not_a_mapping_is_refused():
    assert refusal("- not\n- a mapping\n").code == "bad-file"
    assert refusal("format: 1\ndeployments:\n  my-app: not-a-definition\n").code == "bad-file"


def test_keys_this_gapp_does_not_know_survive_a_round_trip():
    text = ("format: 1\ndeployments:\n  theirs:\n    id: theirs\n    project: p-12345\n    gapp: 9.9.9\n"
            "    settings:\n      from_the_future: [1, 2]\nsomething_later: {a: 1}\n")
    doc = entryfile.parse(text)
    again = entryfile.parse(entryfile.dump(doc))
    assert again["something_later"] == {"a": 1}
    assert again["deployments"]["theirs"]["settings"] == {"from_the_future": [1, 2]}


def test_a_new_definition_records_this_gapps_version_and_empty_settings():
    d = entryfile.new_definition(id="my-app", project="my-proj", repo="me/my-app", ref="main")
    assert d == {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "ref": "main",
                 "gapp": __version__, "settings": {}}
    assert "repo" not in entryfile.new_definition(id="x", project="my-proj")


def test_a_release_candidate_comes_before_its_release_and_after_the_one_before():
    key = entryfile.version_key
    assert key("4.0.0rc13") < key("4.0.0rc14") < key("4.0.0") < key("4.0.1") < key("4.1.0") < key("5.0.0")
    with pytest.raises(EntryFileRefusal):
        key("latest")


def test_standing_compares_the_definitions_version_to_this_gapp(monkeypatch):
    monkeypatch.setattr(entryfile, "__version__", "4.1.0")
    assert entryfile.standing({"gapp": "4.1.0"}, "a") == "current"
    assert entryfile.standing({"gapp": "4.0.0"}, "a") == "older"
    assert entryfile.standing({"gapp": "4.2.0"}, "a") == "newer"
    with pytest.raises(EntryFileRefusal) as caught:
        entryfile.standing({}, "a")
    assert caught.value.code == "no-version"


def test_an_older_definition_is_refused_with_the_upgrade_command(monkeypatch):
    monkeypatch.setattr(entryfile, "__version__", "4.1.0")
    entryfile.require_current({"gapp": "4.1.0"}, "my-app", "deploy it from this machine")
    with pytest.raises(EntryFileRefusal) as caught:
        entryfile.require_current({"gapp": "4.0.0"}, "my-app", "deploy it from this machine")
    assert caught.value.code == "older-definition"
    assert caught.value.message.endswith("gapp upgrade --deployment my-app")


def test_a_newer_definition_is_refused_and_names_the_gapp_to_install(monkeypatch):
    monkeypatch.setattr(entryfile, "__version__", "4.1.0")
    with pytest.raises(EntryFileRefusal) as caught:
        entryfile.require_current({"gapp": "4.2.0"}, "my-app", "change it")
    assert caught.value.code == "newer-definition"
    assert "Install gapp 4.2.0 or later to change it" in caught.value.message


def test_the_local_fleet_is_kept_in_the_entry_files_shape(local_machine):
    fleetstore.write({"my-app": {"id": "my-app", "project": "my-proj"}})
    text = fleetstore.location().path.read_text()
    assert text.startswith("format: 1\ndeployments:\n  my-app:\n")
    assert fleetstore.read() == {"my-app": {"id": "my-app", "project": "my-proj"}}


def test_a_local_file_without_a_format_is_neither_read_nor_written_over(local_machine):
    where = fleetstore.location()
    where.path.parent.mkdir(parents=True)
    old = "my-app:\n  id: my-app\n  project: my-proj\n"
    where.path.write_text(old)
    with pytest.raises(FleetUnavailable) as caught:
        fleetstore.read()
    assert "has no 'format' at its top" in str(caught.value)
    with pytest.raises(FleetUnavailable):
        fleetstore.write({"other": {"id": "other", "project": "my-proj"}})
    assert where.path.read_text() == old


def test_writing_the_local_fleet_keeps_top_level_keys_it_does_not_know(local_machine):
    where = fleetstore.location()
    where.path.parent.mkdir(parents=True)
    where.path.write_text("format: 1\ndeployments: {}\nsomething_later: 7\n")
    fleetstore.write({"my-app": {"id": "my-app", "project": "my-proj"}})
    assert entryfile.parse(where.path.read_text())["something_later"] == 7
