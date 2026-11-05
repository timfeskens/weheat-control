"""Async cloud transport. Never retry a submitted settings mutation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp

from .const import API, CLIENT_ID, ISSUER, REDIRECT_URI

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type Settings = dict[str, str | int | float | bool | list[int | float]]


class WeheatError(Exception):
    """Safe error message without credentials or upstream response bodies."""


class AuthenticationError(WeheatError):
    """Login must be renewed."""


def json_value(value: object) -> Json:
    """Validate the JSON boundary rather than trusting decoded types."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): json_value(item) for key, item in value.items()}
    raise WeheatError("Unexpected response format")


def object_value(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise WeheatError("Expected a response object")
    return value


def identifier(value: Json) -> str:
    if isinstance(value, str) and value:
        return value
    if type(value) is int and value >= 0:
        return str(value)
    raise WeheatError("Missing device or request identifier")


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    def as_dict(self) -> dict[str, str | float]:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at}

    @classmethod
    def from_saved(cls, value: object) -> Tokens:
        data = object_value(json_value(value))
        access, refresh, expiry = data.get("access_token"), data.get("refresh_token"), data.get("expires_at")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or type(expiry) not in (int, float)
            or not math.isfinite(expiry)
        ):
            raise AuthenticationError("Invalid saved login; sign in again")
        return cls(access, refresh, float(expiry))


@dataclass(frozen=True)
class Installation:
    id: str
    board_id: str


def login_request() -> tuple[str, str, str]:
    verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    url = f"{ISSUER}/protocol/openid-connect/auth?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return url, verifier, state


def callback_code(callback: str, state: str) -> str:
    try:
        url = urlsplit(callback.strip())
        query = parse_qs(url.query, keep_blank_values=True)
        if (
            f"{url.scheme}://{url.netloc}{url.path}" != REDIRECT_URI
            or url.fragment
            or query.get("state") != [state]
            or "error" in query
            or len(query.get("code", [])) != 1
            or not query["code"][0]
        ):
            raise ValueError
        return query["code"][0]
    except ValueError:
        raise AuthenticationError("Invalid callback or OAuth state") from None


class WeheatApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens | None = None,
        save_tokens: Callable[[Tokens], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self.tokens = tokens
        self.save_tokens = save_tokens
        self._refresh_lock = asyncio.Lock()

    async def _http(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        body: dict[str, Json] | None = None,
    ) -> Json:
        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                data=form,
                json=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status in (400, 401) and url.startswith(ISSUER):
                    raise AuthenticationError("Weheat login expired or was rejected")
                if response.status == 401:
                    raise AuthenticationError("Weheat login was rejected; reauthenticate")
                if response.status == 505:
                    raise WeheatError("Weheat rejected the recovered app version (HTTP 505); no retry performed")
                if not 200 <= response.status < 300:
                    raise WeheatError(f"Weheat returned HTTP {response.status}; no retry performed")
                # Bound response buffering; errors never include raw target content.
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise WeheatError("Weheat response exceeded the size limit")
                    chunks.append(chunk)
                return json_value(json.loads(b"".join(chunks)))
        except aiohttp.ClientError, TimeoutError, ValueError:
            raise WeheatError("Weheat connection or response failed; no automatic retry performed") from None

    async def exchange(self, code: str, verifier: str) -> Tokens:
        return await self._token(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": verifier,
            }
        )

    async def _token(self, form: dict[str, str]) -> Tokens:
        data = object_value(await self._http("POST", f"{ISSUER}/protocol/openid-connect/token", form=form))
        access = data.get("access_token")
        refresh = data.get("refresh_token", self.tokens.refresh_token if self.tokens else None)
        expiry = data.get("expires_in", 60)
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or type(expiry) not in (int, float)
            or not math.isfinite(expiry)
            or expiry <= 0
        ):
            raise AuthenticationError("Unexpected token response")
        tokens = Tokens(access, refresh, time.time() + expiry)
        self.tokens = tokens
        if self.save_tokens:
            await self.save_tokens(tokens)
        return tokens

    async def request(self, method: str, path: str, body: dict[str, Json] | None = None) -> Json:
        async with self._refresh_lock:
            if not self.tokens:
                raise AuthenticationError("No saved Weheat login")
            if self.tokens.expires_at < time.time() + 30:
                await self._token(
                    {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": self.tokens.refresh_token}
                )
            headers = {"Authorization": f"Bearer {self.tokens.access_token}"}
        # Read-only live verification: settings also rejects 4.5.14, but works
        # without it. Do not infer that mutation/version checks can be bypassed.
        versionless_read = method == "GET" and (
            path == "/installations/fully-expanded" or re.fullmatch(r"/installations/[^/]+/settings", path)
        )
        if not versionless_read:
            headers["x-version"] = "4.5.14"
        return await self._http(method, API + path, headers=headers, body=body)

    async def installations(self) -> list[Installation]:
        data = await self.request("GET", "/installations/fully-expanded")
        if isinstance(data, dict):
            data = data.get("data")
        if not isinstance(data, list):
            raise WeheatError("Unexpected installations response")
        result: list[Installation] = []
        for raw in data:
            item = object_value(raw)
            pump = item.get("heatPump")
            if isinstance(pump, dict) and pump.get("controlBoardId") is not None:
                result.append(Installation(identifier(item.get("id")), identifier(pump.get("controlBoardId"))))
        return result

    async def settings(self, installation_id: str) -> Settings:
        from urllib.parse import quote

        data = object_value(await self.request("GET", f"/installations/{quote(installation_id, safe='')}/settings"))
        if "data" in data:
            data = object_value(data["data"])
        settings: Settings = {}
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)):
                settings[key] = value
            elif isinstance(value, list) and all(type(item) in (int, float) for item in value):
                settings[key] = [item for item in value if type(item) in (int, float)]
        return settings

    async def patch(self, board_id: str, settings: Settings) -> str:
        from urllib.parse import quote

        data = object_value(
            await self.request(
                "PATCH",
                f"/control-boards/{quote(board_id, safe='')}/settings",
                {"settings": dict(settings), "updateImmediately": False},
            )
        )
        if "data" in data:
            data = object_value(data["data"])
        return identifier(data.get("settingsRequestId"))

    async def status(self, board_id: str, request_id: str) -> int:
        from urllib.parse import quote

        data = object_value(
            await self.request(
                "GET", f"/control-boards/{quote(board_id, safe='')}/settings/{quote(request_id, safe='')}"
            )
        )
        if "data" in data:
            data = object_value(data["data"])
        status = data.get("status")
        if type(status) is not int or status not in range(12):
            raise WeheatError("Unrecognized settings request status")
        return status
