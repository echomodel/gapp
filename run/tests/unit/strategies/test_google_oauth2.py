"""Tests for the google_oauth2 credential strategy."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gapp_run.auth.strategies.google_oauth2 import get_access_token, _write_back


def _token_profile_credential(**overrides):
    """Credential from gwsa token profile (custom OAuth client)."""
    base = {
        "strategy": "google_oauth2",
        "type": "authorized_user",
        "token": "test-valid",
        "refresh_token": "test-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
    }
    base.update(overrides)
    return base


def _adc_profile_credential(**overrides):
    """Credential from gwsa ADC profile (gcloud's client + quota project)."""
    base = {
        "strategy": "google_oauth2",
        "type": "authorized_user",
        "token": "test-valid",
        "refresh_token": "test-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "gcloud-builtin-client-id",
        "client_secret": "gcloud-builtin-client-secret",
        "quota_project_id": "test-gcp-project",
    }
    base.update(overrides)
    return base


class TestGoogleOAuth2Strategy:
    def _make_credential(self, *, token="test-valid", expired=False):
        return _token_profile_credential(token=token)

    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_returns_valid_token_without_refresh(self, mock_creds_cls):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "test-fresh"
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        result = get_access_token(self._make_credential())

        assert result == "test-fresh"
        mock_creds.refresh.assert_not_called()

    @patch("gapp_run.auth.strategies.google_oauth2.Request")
    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_refreshes_expired_token(self, mock_creds_cls, mock_request_cls):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "test-refresh-token"
        mock_creds.token = "test-refreshed"
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        result = get_access_token(self._make_credential())

        assert result == "test-refreshed"
        mock_creds.refresh.assert_called_once()

    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_missing_refresh_token_raises(self, mock_creds_cls):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        with pytest.raises(ValueError, match="missing refresh_token"):
            get_access_token(self._make_credential())

    @patch("gapp_run.auth.strategies.google_oauth2.Request")
    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_writes_back_after_refresh(self, mock_creds_cls, mock_request_cls, tmp_path):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "test-refresh-token"
        mock_creds.token = "test-new"
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        cred_path = str(tmp_path / "cred.json")
        original = self._make_credential()
        with open(cred_path, "w") as f:
            json.dump(original, f)

        get_access_token(original, cred_path=cred_path)

        written = json.loads(open(cred_path).read())
        assert written["token"] == "test-new"
        # Original fields preserved
        assert written["refresh_token"] == "test-refresh-token"
        assert written["strategy"] == "google_oauth2"

    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_no_write_back_without_path(self, mock_creds_cls):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "test-ok"
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        # Should not raise even without cred_path
        result = get_access_token(self._make_credential(), cred_path=None)
        assert result == "test-ok"


class TestADCProfileCredential:
    """Tests that ADC credentials (with quota_project_id) work identically."""

    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_adc_credential_returns_token(self, mock_creds_cls):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "test-adc-token"
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        result = get_access_token(_adc_profile_credential())

        assert result == "test-adc-token"
        # Verify quota_project_id didn't interfere
        mock_creds_cls.from_authorized_user_info.assert_called_once()

    @patch("gapp_run.auth.strategies.google_oauth2.Request")
    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_adc_credential_refreshes(self, mock_creds_cls, mock_request_cls):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "test-refresh-token"
        mock_creds.token = "test-adc-refreshed"
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        result = get_access_token(_adc_profile_credential())

        assert result == "test-adc-refreshed"
        mock_creds.refresh.assert_called_once()

    @patch("gapp_run.auth.strategies.google_oauth2.Request")
    @patch("gapp_run.auth.strategies.google_oauth2.Credentials")
    def test_write_back_preserves_quota_project_id(self, mock_creds_cls, mock_request_cls, tmp_path):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "test-refresh-token"
        mock_creds.token = "test-adc-new"
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        cred_path = str(tmp_path / "cred.json")
        original = _adc_profile_credential()
        with open(cred_path, "w") as f:
            json.dump(original, f)

        get_access_token(original, cred_path=cred_path)

        written = json.loads(open(cred_path).read())
        assert written["token"] == "test-adc-new"
        assert written["quota_project_id"] == "test-gcp-project"
        assert written["strategy"] == "google_oauth2"
        assert written["client_id"] == "gcloud-builtin-client-id"
