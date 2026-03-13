"""Tests for auth middleware — JWT validation, token extraction, header rewrite."""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest

from gapp_run.auth.middleware import AuthMiddleware, _extract_token, _rewrite_auth_header

SIGNING_KEY = "test-secret-key"


def _make_jwt(sub="user@example.com", exp_offset=3600, **extra):
    """Create a signed JWT for testing."""
    payload = {
        "sub": sub,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        **extra,
    }
    return jwt.encode(payload, SIGNING_KEY, algorithm="HS256")


def _make_scope(*, token=None, query_token=None, path="/mcp"):
    """Build a minimal ASGI HTTP scope."""
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    query_string = f"token={query_token}" if query_token else ""
    return {
        "type": "http",
        "path": path,
        "headers": headers,
        "query_string": query_string.encode(),
    }


class TestTokenExtraction:
    def test_from_authorization_header(self):
        scope = _make_scope(token="my-jwt")
        assert _extract_token(scope) == "my-jwt"

    def test_from_query_param(self):
        scope = _make_scope(query_token="my-jwt")
        assert _extract_token(scope) == "my-jwt"

    def test_header_takes_precedence(self):
        scope = _make_scope(token="header-jwt", query_token="query-jwt")
        assert _extract_token(scope) == "header-jwt"

    def test_missing_returns_none(self):
        scope = _make_scope()
        assert _extract_token(scope) is None


class TestHeaderRewrite:
    def test_replaces_existing_header(self):
        headers = [(b"authorization", b"Bearer old"), (b"host", b"example.com")]
        result = _rewrite_auth_header(headers, "new-token")
        auth = dict(result)[b"authorization"]
        assert auth == b"Bearer new-token"

    def test_adds_header_if_missing(self):
        headers = [(b"host", b"example.com")]
        result = _rewrite_auth_header(headers, "new-token")
        auth = dict(result)[b"authorization"]
        assert auth == b"Bearer new-token"

    def test_preserves_other_headers(self):
        headers = [(b"host", b"example.com"), (b"authorization", b"Bearer old")]
        result = _rewrite_auth_header(headers, "new-token")
        assert dict(result)[b"host"] == b"example.com"


class TestAuthMiddleware:
    """Integration tests for the full middleware chain using a temp auth mount."""

    @pytest.fixture
    def auth_mount(self, tmp_path):
        return str(tmp_path)

    @pytest.fixture
    def write_credential(self, auth_mount):
        def _write(email, credential_data):
            email_hash = hashlib.sha256(email.encode()).hexdigest()
            path = f"{auth_mount}/{email_hash}.json"
            with open(path, "w") as f:
                json.dump(credential_data, f)
            return email_hash
        return _write

    def _make_middleware(self, auth_mount):
        """Create middleware wrapping a no-op ASGI app that records calls."""
        calls = []

        async def inner_app(scope, receive, send):
            calls.append(scope)
            body = b'{"ok": true}'
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})

        mw = AuthMiddleware(inner_app, signing_key=SIGNING_KEY, auth_mount=auth_mount)
        return mw, calls

    async def _call(self, mw, scope):
        """Call middleware and capture the response."""
        responses = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            responses.append(msg)

        await mw(scope, receive, send)
        return responses

    @pytest.mark.asyncio
    async def test_valid_jwt_rewrites_header(self, auth_mount, write_credential):
        write_credential("user@example.com", {
            "strategy": "bearer",
            "credential": "upstream-token-123",
        })
        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("user@example.com")
        scope = _make_scope(token=token)

        await self._call(mw, scope)

        assert len(calls) == 1
        forwarded_auth = dict(calls[0]["headers"])[b"authorization"]
        assert forwarded_auth == b"Bearer upstream-token-123"

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        scope = _make_scope()

        responses = await self._call(mw, scope)

        assert responses[0]["status"] == 401
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_invalid_jwt_returns_403(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        scope = _make_scope(token="not-a-real-jwt")

        responses = await self._call(mw, scope)

        assert responses[0]["status"] == 403
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_401(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt(exp_offset=-10)  # already expired
        scope = _make_scope(token=token)

        responses = await self._call(mw, scope)

        assert responses[0]["status"] == 401
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_missing_credential_file_returns_403(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("nobody@example.com")
        scope = _make_scope(token=token)

        responses = await self._call(mw, scope)

        assert responses[0]["status"] == 403
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_query_param_token_works(self, auth_mount, write_credential):
        write_credential("user@example.com", {
            "strategy": "bearer",
            "credential": "upstream-token",
        })
        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("user@example.com")
        scope = _make_scope(query_token=token)

        await self._call(mw, scope)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_health_endpoint_bypasses_auth(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        scope = _make_scope(path="/health")

        responses = await self._call(mw, scope)

        # Health goes to inner app (no auth required)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_lifespan_passes_through(self, auth_mount):
        mw, calls = self._make_middleware(auth_mount)
        scope = {"type": "lifespan"}

        await self._call(mw, scope)

        assert len(calls) == 1
        assert calls[0]["type"] == "lifespan"


class TestGoogleOAuth2Mediation:
    """Full ASGI middleware chain with google_oauth2 credentials.

    Mocks only the Google HTTP refresh call — everything else (JWT validation,
    file I/O, strategy resolution, header rewrite) runs for real.
    """

    @pytest.fixture
    def auth_mount(self, tmp_path):
        return str(tmp_path)

    @pytest.fixture
    def write_credential(self, auth_mount):
        def _write(email, credential_data):
            email_hash = hashlib.sha256(email.encode()).hexdigest()
            path = f"{auth_mount}/{email_hash}.json"
            with open(path, "w") as f:
                json.dump(credential_data, f)
            return email_hash
        return _write

    def _make_middleware(self, auth_mount):
        calls = []

        async def inner_app(scope, receive, send):
            calls.append(scope)
            body = b'{"ok": true}'
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})

        mw = AuthMiddleware(inner_app, signing_key=SIGNING_KEY, auth_mount=auth_mount)
        return mw, calls

    async def _call(self, mw, scope):
        responses = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            responses.append(msg)

        await mw(scope, receive, send)
        return responses

    @pytest.mark.asyncio
    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    async def test_oauth2_token_profile_mediates(
        self, mock_creds_cls, mock_request_cls, auth_mount, write_credential,
    ):
        """Token profile (custom OAuth client) — valid token forwarded."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "upstream-access-token"
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        write_credential("user@example.com", {
            "strategy": "google_oauth2",
            "type": "authorized_user",
            "token": "stale-token",
            "refresh_token": "test-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        })

        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("user@example.com")
        responses = await self._call(mw, _make_scope(token=token))

        assert responses[0]["status"] == 200
        assert len(calls) == 1
        forwarded_auth = dict(calls[0]["headers"])[b"authorization"]
        assert forwarded_auth == b"Bearer upstream-access-token"

    @pytest.mark.asyncio
    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    async def test_oauth2_adc_profile_with_quota_project_mediates(
        self, mock_creds_cls, mock_request_cls, auth_mount, write_credential,
    ):
        """ADC profile (gcloud client + quota_project_id) — works identically."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "upstream-adc-token"
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        write_credential("user@example.com", {
            "strategy": "google_oauth2",
            "type": "authorized_user",
            "token": "stale-adc-token",
            "refresh_token": "test-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "gcloud-builtin-client-id",
            "client_secret": "gcloud-builtin-client-secret",
            "quota_project_id": "test-gcp-project",
        })

        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("user@example.com")
        responses = await self._call(mw, _make_scope(token=token))

        assert responses[0]["status"] == 200
        assert len(calls) == 1
        forwarded_auth = dict(calls[0]["headers"])[b"authorization"]
        assert forwarded_auth == b"Bearer upstream-adc-token"

    @pytest.mark.asyncio
    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    async def test_oauth2_refresh_writes_back_and_forwards(
        self, mock_creds_cls, mock_request_cls, auth_mount, write_credential,
    ):
        """Expired token triggers refresh; refreshed token forwarded to solution."""
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "test-refresh-token"
        mock_creds.token = "freshly-refreshed"
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_info.return_value = mock_creds

        email_hash = write_credential("user@example.com", {
            "strategy": "google_oauth2",
            "type": "authorized_user",
            "token": "expired-token",
            "refresh_token": "test-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        })

        mw, calls = self._make_middleware(auth_mount)
        token = _make_jwt("user@example.com")
        responses = await self._call(mw, _make_scope(token=token))

        assert responses[0]["status"] == 200
        forwarded_auth = dict(calls[0]["headers"])[b"authorization"]
        assert forwarded_auth == b"Bearer freshly-refreshed"
        mock_creds.refresh.assert_called_once()

        # Verify write-back to FUSE
        written = json.loads(open(f"{auth_mount}/{email_hash}.json").read())
        assert written["token"] == "freshly-refreshed"
