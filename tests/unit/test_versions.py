"""The two numbers every release sets (COMPATIBLE_FROM, UPGRADES_FROM) and
the only code that compares gapp versions (gapp/admin/sdk/versions.py)."""

import pytest

import gapp
from gapp.admin.sdk import versions
from gapp.admin.sdk.entryfile import EntryFileRefusal


@pytest.fixture(autouse=True)
def as_gapp(monkeypatch):
    """This gapp is 4.3.0, works as-is with 4.1.0 on, and upgrades from 4.0.0 on."""
    monkeypatch.setattr(versions, "__version__", "4.3.0")
    monkeypatch.setattr(versions, "COMPATIBLE_FROM", "4.1.0")
    monkeypatch.setattr(versions, "UPGRADES_FROM", "4.0.0")


def test_the_numbers_are_ordered_as_promised():
    key = versions._key
    assert key(gapp.UPGRADES_FROM) <= key(gapp.COMPATIBLE_FROM) <= key(gapp.__version__)


@pytest.mark.parametrize("version, stands", [
    ("4.3.0", "current"), ("4.2.5", "compatible"), ("4.1.0", "compatible"),
    ("4.0.9", "upgrade-required"), ("4.0.0", "upgrade-required"), ("3.9.0", "too-old"), ("4.4.0", "newer"),
])
def test_standing(version, stands):
    assert versions.standing(version) == stands


def refusal(fn, *args, **kwargs):
    with pytest.raises(EntryFileRefusal) as caught:
        fn(*args, **kwargs)
    return caught.value


@pytest.mark.parametrize("version", ["4.3.0", "4.2.0", "4.1.0"])
def test_a_definition_from_compatible_from_on_is_worked_with(version):
    versions.check_definition({"gapp": version}, "my-app", "change it")


def test_a_definition_below_compatible_from_is_sent_to_upgrade():
    e = refusal(versions.check_definition, {"gapp": "4.0.5"}, "my-app", "change it")
    assert e.code == "upgrade-required"
    assert e.message == ("Deployment 'my-app' is on gapp 4.0.5, and gapp 4.3.0 works as-is only with 4.1.0 or later. "
                         "Upgrade it first, then change it: gapp upgrade --deployment my-app, "
                         "then deploy it with --upgrade.")


def test_a_definition_below_upgrades_from_names_the_gapp_to_install_first():
    e = refusal(versions.check_definition, {"gapp": "3.9.0"}, "my-app", "change it")
    assert e.code == "too-old" and "Install gapp 4.0.0, upgrade and deploy 'my-app' with it" in e.message


def test_a_newer_definition_names_the_gapp_to_install():
    e = refusal(versions.check_definition, {"gapp": "4.4.0"}, "my-app", "change it")
    assert e.code == "newer-definition" and "Install gapp 4.4.0 or later to change it" in e.message


def test_a_definition_with_no_version_is_refused():
    assert refusal(versions.check_definition, {}, "my-app", "change it").code == "no-version"


def test_reading_takes_any_version_but_a_newer_one():
    versions.check_readable({"gapp": "3.0.0"}, "my-app", "read it")
    assert refusal(versions.check_readable, {"gapp": "4.4.0"}, "my-app", "read it").code == "newer-definition"


@pytest.mark.parametrize("last", [None, "4.3.0", "4.1.0"])
def test_infrastructure_from_compatible_from_on_is_worked_with(last):
    versions.check_infrastructure(last, "my-app", "read its status")


def test_infrastructure_below_compatible_from_is_refused_unless_upgrading():
    e = refusal(versions.check_infrastructure, "4.0.5", "my-app", "deploy it")
    assert e.code == "upgrade-required" and e.message.startswith("'my-app' was last deployed by gapp 4.0.5")
    versions.check_infrastructure("4.0.5", "my-app", "deploy it", upgrading=True)


def test_infrastructure_below_upgrades_from_is_refused_even_when_upgrading():
    assert refusal(versions.check_infrastructure, "3.9.0", "my-app", "deploy it", upgrading=True).code == "too-old"


def test_infrastructure_from_a_newer_gapp_is_refused():
    e = refusal(versions.check_infrastructure, "4.4.0", "my-app", "deploy it")
    assert e.code == "newer-infrastructure" and "Install gapp 4.4.0 or later to deploy it" in e.message


def test_a_deploy_builds_only_with_the_definitions_version():
    versions.check_deploys({"gapp": "4.3.0"}, "my-app")
    e = refusal(versions.check_deploys, {"gapp": "4.2.0"}, "my-app")
    assert e.code == "other-version" and "gapp ci deploy --deployment my-app" in e.message
    assert "gapp upgrade --deployment my-app, then deploy it with --upgrade" in e.message
    assert refusal(versions.check_deploys, {"gapp": "4.4.0"}, "my-app").code == "newer-definition"


def test_an_upgrade_is_pending_when_the_definition_is_newer_than_its_infrastructure():
    assert versions.upgrade_pending({"gapp": "4.3.0"}, "4.2.0") is True
    assert versions.upgrade_pending({"gapp": "4.3.0"}, "4.3.0") is False
    assert versions.upgrade_pending({"gapp": "4.3.0"}, None) is False


def test_a_setting_newer_than_the_definitions_version_needs_an_upgrade(monkeypatch):
    monkeypatch.setattr(versions, "SETTINGS_SINCE", {"concurrency": "4.2.0"})
    versions.check_setting({"gapp": "4.2.0"}, "concurrency", "my-app")
    versions.check_setting({"gapp": "4.1.0"}, "memory", "my-app")
    e = refusal(versions.check_setting, {"gapp": "4.1.0"}, "concurrency", "my-app")
    assert e.code == "setting-too-new" and e.message.endswith("gapp upgrade --deployment my-app")
