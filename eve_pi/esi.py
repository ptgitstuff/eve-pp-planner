"""Log characters in with EVE SSO and read their planets and skills from ESI.

This is a desktop app, so it uses the PKCE login: no client secret, just the
client ID of an application the user registers at developers.eveonline.com.

Access tokens stay in memory. Refresh tokens are long-lived, so they are stored
encrypted to the Windows user (DPAPI) where that's available.

The token signature isn't checked because the standard library has no RSA. Tokens
come straight from CCP's token endpoint over TLS, and they are only sent back to
CCP, so the claims are read for the character ID and sanity-checked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
ESI_URL = "https://esi.evetech.net"
COMPATIBILITY_DATE = "2020-01-01"
SCOPES = ("esi-planets.manage_planets.v1", "esi-skills.read_skills.v1")
CALLBACK_PORT = 8766
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
DEVELOPER_SITE = "https://developers.eveonline.com/applications"

_USER_AGENT = "eve-pi-planner/3.0 (personal PI planning tool)"
_ISSUERS = {"login.eveonline.com", "https://login.eveonline.com",
            "https://login.eveonline.com/"}
_SUBJECT = re.compile(r"^CHARACTER:EVE:(\d+)$")
_RENEW_EARLY_SECONDS = 60

Send = Callable[[urllib.request.Request], bytes]


class EsiError(Exception):
    """Something failed; the message is safe to show and never holds a token."""


def _send(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


# ---------------------------------------------------------------------- login
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """(verifier, challenge)."""
    verifier = _b64url(secrets.token_bytes(32))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorize_url(client_id: str, state: str, challenge: str,
                  redirect_uri: str = CALLBACK_URL) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "scope": " ".join(SCOPES), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def parse_callback(path: str, state: str) -> str | None:
    """The login code from a callback request, or None if the request isn't the
    one we are waiting for. Raises EsiError if the user declined."""
    parts = urllib.parse.urlsplit(path)
    if parts.path != "/callback":
        return None
    query = urllib.parse.parse_qs(parts.query)
    if query.get("state", [""])[0] != state:
        return None
    if "error" in query:
        raise EsiError(f"EVE login was cancelled ({query['error'][0]}).")
    return query.get("code", [None])[0]


def _wait_for_code(server: HTTPServer, state: str, timeout: float) -> str:
    found: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                found["code"] = parse_callback(self.path, state)
            except EsiError as exc:
                found["error"] = exc
            done = found.get("code") or found.get("error")
            self.send_response(200 if done else 404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if done:
                self.wfile.write(b"Eve PI Planner: you can close this window.")

        def log_message(self, *_args):
            pass

    server.RequestHandlerClass = Handler
    server.timeout = 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server.handle_request()
        if found.get("error"):
            raise found["error"]
        if found.get("code"):
            return found["code"]
    raise EsiError("EVE login timed out. Click Add character to try again.")


def _token_request(fields: dict[str, str], send: Send) -> dict:
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={"User-Agent": _USER_AGENT, "Host": "login.eveonline.com",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        reply = json.loads(send(request).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise EsiError("EVE rejected this login. Remove the character and "
                           "add it again.") from exc
        raise EsiError(f"EVE login failed (HTTP {exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise EsiError("Couldn't reach EVE login. Check your connection.") from exc
    if not isinstance(reply, dict) or not reply.get("access_token"):
        raise EsiError("EVE login sent an unexpected reply.")
    return reply


def read_token(access_token: str, client_id: str) -> tuple[int, str]:
    """(character ID, character name) from an access token's claims."""
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        audience = claims["aud"]
        audience = [audience] if isinstance(audience, str) else audience
        subject = _SUBJECT.match(claims["sub"])
        name = claims["name"]
        issuer = claims["iss"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise EsiError("EVE login sent a token this app can't read.") from exc
    if issuer not in _ISSUERS or subject is None or client_id not in audience:
        raise EsiError("EVE login sent a token that isn't for this application.")
    return int(subject.group(1)), name


@dataclass
class Character:
    id: int
    name: str
    refresh_token: str
    access_token: str = ""
    expires_at: float = 0.0


def _apply_tokens(character: Character, reply: dict) -> None:
    character.access_token = reply["access_token"]
    character.expires_at = time.time() + float(reply.get("expires_in", 0))
    # CCP may rotate the refresh token, so always keep the newest one.
    character.refresh_token = reply.get("refresh_token") or character.refresh_token


def login(client_id: str, open_browser: Callable[[str], object] = webbrowser.open,
          send: Send = _send, port: int = CALLBACK_PORT,
          timeout: float = 180.0) -> Character:
    """Run the browser login and return the character the user picked."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    try:
        server = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    except OSError as exc:
        raise EsiError(f"Port {port} is in use, so the login can't finish. Close "
                       "whatever is using it and try again.") from exc
    try:
        redirect = f"http://localhost:{server.server_address[1]}/callback"
        open_browser(authorize_url(client_id, state, challenge, redirect))
        code = _wait_for_code(server, state, timeout)
    finally:
        server.server_close()
    reply = _token_request({"grant_type": "authorization_code", "code": code,
                            "code_verifier": verifier, "client_id": client_id}, send)
    char_id, name = read_token(reply["access_token"], client_id)
    if not reply.get("refresh_token"):
        raise EsiError("EVE login didn't grant lasting access. Try again.")
    character = Character(char_id, name, reply["refresh_token"])
    _apply_tokens(character, reply)
    return character


# ------------------------------------------------------------------------ data
class EsiClient:
    """Authenticated reads. Safe to use from a worker thread, one at a time."""

    def __init__(self, client_id: str, send: Send = _send):
        self.client_id = client_id
        self._send = send

    def _access_token(self, character: Character) -> str:
        if (not character.access_token
                or time.time() >= character.expires_at - _RENEW_EARLY_SECONDS):
            if not character.refresh_token:
                raise EsiError(f"{character.name}: remove this character and add "
                               "it again.")
            _apply_tokens(character, _token_request(
                {"grant_type": "refresh_token", "client_id": self.client_id,
                 "refresh_token": character.refresh_token}, self._send))
        return character.access_token

    def _get(self, character: Character, path: str):
        request = urllib.request.Request(ESI_URL + path, headers={
            "User-Agent": _USER_AGENT, "Accept": "application/json",
            "X-Compatibility-Date": COMPATIBILITY_DATE,
            "Authorization": "Bearer " + self._access_token(character)})
        try:
            return json.loads(self._send(request).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise EsiError(f"{character.name}: ESI refused access (HTTP "
                               f"{exc.code}). Remove the character and add it "
                               "again.") from exc
            if exc.code in (420, 429):
                raise EsiError("ESI is rate limiting requests. Try again in a "
                               "minute.") from exc
            raise EsiError(f"{character.name}: ESI error (HTTP {exc.code}).") from exc
        except (OSError, ValueError) as exc:
            raise EsiError("Couldn't reach ESI. Check your connection.") from exc

    def fetch_character(self, character: Character) -> dict:
        """Everything for one character as raw ESI data. A failed call becomes an
        entry in ``errors``, so one bad colony doesn't lose the rest."""
        # "colonies" stays None if it can't be read, which isn't the same as none.
        raw: dict = {"colonies": None, "details": {}, "skills": None, "errors": []}

        def note(exc: EsiError) -> None:
            if str(exc) not in raw["errors"]:
                raw["errors"].append(str(exc))

        base = f"/characters/{character.id}"
        try:
            raw["skills"] = self._get(character, f"{base}/skills")
        except EsiError as exc:
            note(exc)
        try:
            raw["colonies"] = self._get(character, f"{base}/planets")
        except EsiError as exc:
            note(exc)
        for colony in raw["colonies"] or ():
            planet_id = colony["planet_id"]
            try:
                raw["details"][planet_id] = self._get(character,
                                                      f"{base}/planets/{planet_id}")
            except EsiError as exc:
                note(exc)
        return raw


# ----------------------------------------------------------------------- store
class _Blob:
    """Windows DPAPI through ctypes, tied to the logged-in Windows user."""

    @staticmethod
    def _call(function_name: str, data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD),
                        ("data", ctypes.POINTER(ctypes.c_char))]

        buffer = ctypes.create_string_buffer(data, len(data))
        source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        result = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(crypt32, function_name)
        function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(DataBlob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, None, None, None, 0,
                        ctypes.byref(result)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            kernel32.LocalFree(result.data)


def protect(token: str) -> str:
    raw = token.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_Blob._call("CryptProtectData", raw)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")


def unprotect(stored: str) -> str:
    """The token, or "" if it can't be read (another user or machine, say)."""
    kind, _, body = stored.partition(":")
    try:
        raw = base64.b64decode(body)
        if kind == "dpapi":
            raw = _Blob._call("CryptUnprotectData", raw)
        elif kind != "plain":
            return ""
        return raw.decode("utf-8")
    except (OSError, ValueError, AttributeError):
        return ""


def default_store_path() -> str:
    from .sde import default_cache_dir
    return os.path.join(default_cache_dir(), "characters.json")


def load_store(path: str) -> tuple[str, list[Character]]:
    """(client ID, characters). Missing or damaged files just mean none yet."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        characters = [Character(int(c["id"]), str(c["name"]), unprotect(c["token"]))
                      for c in data.get("characters", [])]
        return str(data.get("client_id", "")), characters
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "", []


def save_store(path: str, client_id: str, characters: list[Character]) -> None:
    data = {"client_id": client_id,
            "characters": [{"id": c.id, "name": c.name, "token": protect(c.refresh_token)}
                           for c in characters]}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    if sys.platform != "win32":
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
