import hmac
import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import settings


def configured_api_token() -> str | None:
    token = os.environ.get("NOOFY_API_TOKEN", settings.noofy_api_token or "")
    token = token.strip()
    return token or None


def is_job_query_token_request(request: Request) -> bool:
    path = request.url.path
    if request.method != "GET":
        return False
    if path.startswith("/api/jobs/") and (path.endswith("/events") or path.endswith("/outputs/view")):
        return True
    return path.startswith("/api/gallery/") and (
        path.endswith("/image") or path.endswith("/thumbnail")
    )


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def request_token(request: Request) -> str | None:
    if is_job_query_token_request(request):
        return request.query_params.get("token")
    return bearer_token(request)


class LocalApiTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        expected_token = configured_api_token()
        if expected_token is None or not request.url.path.startswith("/api/"):
            return await call_next(request)

        actual_token = request_token(request)
        if actual_token is None or not hmac.compare_digest(actual_token, expected_token):
            return JSONResponse({"detail": "Invalid or missing API token"}, status_code=401)

        return await call_next(request)
