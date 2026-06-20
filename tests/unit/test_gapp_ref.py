"""Tests for CI gapp-ref pinning.

The generated CI workflow pins the reusable gapp workflow to the installed
gapp version's release tag (vX.Y.Z), falling back to main HEAD only when
that tag isn't published yet.
"""

from unittest.mock import MagicMock, patch

from gapp.admin.sdk import ci


def _result(returncode=0, stdout=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


@patch("importlib.metadata.version", return_value="3.5.1")
@patch("gapp.admin.sdk.ci.subprocess.run")
def test_pins_to_version_tag_when_published(mock_run, _ver):
    # tag-existence check succeeds
    mock_run.return_value = _result(returncode=0)
    assert ci._resolve_gapp_ref("echomodel/gapp") == "v3.5.1"
    # only the tag check ran — no HEAD lookup needed
    assert mock_run.call_count == 1


@patch("importlib.metadata.version", return_value="3.9.9")
@patch("gapp.admin.sdk.ci.subprocess.run")
def test_falls_back_to_head_when_tag_missing(mock_run, _ver):
    # tag check fails (404), then HEAD lookup returns a sha
    mock_run.side_effect = [_result(returncode=1), _result(returncode=0, stdout="abc1234\n")]
    assert ci._resolve_gapp_ref("echomodel/gapp") == "abc1234"


@patch("importlib.metadata.version", return_value="3.9.9")
@patch("gapp.admin.sdk.ci.subprocess.run")
def test_falls_back_to_main_when_tag_and_head_unavailable(mock_run, _ver):
    mock_run.side_effect = [_result(returncode=1), _result(returncode=1, stdout="")]
    assert ci._resolve_gapp_ref("echomodel/gapp") == "main"
