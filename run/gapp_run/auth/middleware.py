"""ASGI middleware for JWT validation and credential mediation."""

import hashlib
from urllib.parse import parse_qs

import jwt

from gapp_run.auth.cache import CredentialCache


class AuthMiddleware:
    """Pure ASGI middleware — no BaseHTTPMiddleware, no streaming issues.

    Validates JWT from Authorization header or ?token= query param,
    resolves upstream credential via strategy, and rewrites the
    Authorization header with the upstream access token.
    """

    def __init__(self, app, *, signing_key: str, auth_mount: str):
        self.app = app
        self.signing_key = signing_key
        self.cache = CredentialCache(auth_mount)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # Lifespan, WebSocket — pass through
            return await self.app(scope, receive, send)

        # Skip auth for health endpoint
        path = scope.get("path", "")
        if path == "/health":
            return await self.app(scope, receive, send)

        # Extract JWT
        token = _extract_token(scope)
        if not token:
            return await _send_error(send, 401, "Missing authentication token")

        # Validate JWT
        try:
            claims = jwt.decode(token, self.signing_key, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return await _send_error(send, 401, "Token expired")
        except jwt.InvalidTokenError:
            return await _send_error(send, 403, "Invalid token")

        # Status tokens bypass credential mediation — pass request through as-is
        if claims.get("scope") == "status":
            return await self.app(scope, receive, send)

        email_hash = hashlib.sha256(claims["sub"].encode()).hexdigest()

        # Resolve upstream access token (also confirms user exists)
        try:
            access_token = self.cache.get_access_token(email_hash)
        except ValueError as e:
            return await _send_error(send, 502, f"Credential error: {e}")

        if access_token is None:
            return await _send_error(send, 403, "User not found or revoked")

        # Check revoke_before (only after confirming user exists)
        iat = claims.get("iat")
        if iat and self.cache.check_revoke_before(email_hash, iat):
            return await _send_error(send, 401, "Token has been invalidated")

        # Rewrite Authorization header
        scope["headers"] = _rewrite_auth_header(scope["headers"], access_token)
        await self.app(scope, receive, send)


def _extract_token(scope: dict) -> str | None:
    """Extract JWT from Authorization header or ?token= query param."""
    # Check Authorization header
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode()
    if auth.startswith("Bearer "):
        return auth[7:]

    # Check query param
    query_string = scope.get("query_string", b"").decode()
    if query_string:
        params = parse_qs(query_string)
        tokens = params.get("token", [])
        if tokens:
            return tokens[0]

    return None


def _rewrite_auth_header(headers: list[tuple[bytes, bytes]], token: str) -> list:
    """Replace the Authorization header with the upstream bearer token."""
    new_auth = f"Bearer {token}".encode()
    result = []
    found = False
    for key, value in headers:
        if key == b"authorization":
            result.append((key, new_auth))
            found = True
        else:
            result.append((key, value))
    if not found:
        result.append((b"authorization", new_auth))
    return result


async def _send_error(send, status: int, message: str) -> None:
    """Send a JSON error response."""
    import json
    body = json.dumps({"error": message}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })
