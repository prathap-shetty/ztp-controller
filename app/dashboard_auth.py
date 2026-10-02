"""Static operator token exchanged for a signed, expiring dashboard cookie."""

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr

router = APIRouter()
COOKIE = "ztp_dashboard"
TTL = 8 * 3600


def key(request):
    token = request.app.state.config.dashboard_token
    if not token:
        raise HTTPException(404, "Dashboard is not configured")
    return token.get_secret_value().encode()


def authenticated(request):
    secret = key(request)
    value = request.cookies.get(COOKIE, "")
    try:
        expiry, nonce, signature = value.split(".")
        message = expiry + "." + nonce
        valid = hmac.compare_digest(
            signature, hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        )
        return valid and int(time.time()) < int(expiry) <= int(time.time()) + TTL
    except (ValueError, TypeError):
        return False


def require_dashboard(request):
    if not authenticated(request):
        raise HTTPException(401, "Dashboard login required")


class Login(BaseModel):
    token: SecretStr = Field(max_length=512)


@router.post("/api/dashboard/login")
def login(body: Login, request: Request):
    secret = key(request)
    request.app.state.repository.rate_limit(
        "dashboard:" + (request.client.host if request.client else "unknown"), 10
    )
    if not hmac.compare_digest(body.token.get_secret_value().encode(), secret):
        raise HTTPException(401, "Invalid token")
    message = str(int(time.time()) + TTL) + "." + secrets.token_hex(16)
    signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        COOKIE,
        message + "." + signature,
        max_age=TTL,
        httponly=True,
        secure=request.app.state.config.dashboard_secure_cookie,
        samesite="strict",
        path="/",
    )
    return response


@router.post("/api/dashboard/logout")
def logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(COOKIE, path="/")
    return response


def login_page():
    return HTMLResponse(
        Path(__file__).with_name("dashboard_login.html").read_text(),
        headers={"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"},
    )
