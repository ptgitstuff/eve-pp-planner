"""Tests for EVE login, ESI reads and colony logic. CCP is faked; the only real
traffic is the login callback, which stays on this machine.
Run:  python -m tests.test_esi
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep, 1)[0])

from eve_pi.colonies import (NO_PROGRAM, RUNNING, STOPPED, build_character, parse_time,
                             slot_roster, totals)
from eve_pi.db import PIDatabase
from eve_pi.esi import (SCOPES, Character, EsiClient, EsiError, authorize_url, load_store,
                        login, make_pkce, parse_callback, protect, read_token, save_store,
                        unprotect)
from tests.make_official_fixture import P0_IDS, WATER, build_fixture

CLIENT = "client123"
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def fails(function, *args, **kwargs) -> str | None:
    try:
        function(*args, **kwargs)
    except EsiError as exc:
        return str(exc)
    return None


def make_token(character_id=90001, name="Test Pilot", audience=(CLIENT, "EVE Online"),
               issuer="https://login.eveonline.com"):
    claims = {"sub": f"CHARACTER:EVE:{character_id}", "name": name, "iss": issuer,
              "aud": list(audience), "exp": time.time() + 1200}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "error", None, None)


def test_pkce():
    print("login request:")
    verifier, challenge = make_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    check(challenge == expected.rstrip(b"=").decode(), "challenge is the SHA-256 of the verifier")
    check("=" not in challenge and len(verifier) >= 43, "unpadded and long enough")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(
        authorize_url(CLIENT, "s", challenge)).query)
    check(query["code_challenge_method"] == ["S256"], "asks for S256")
    check(query["scope"] == [" ".join(SCOPES)], "asks for both scopes")
    check("client_secret" not in query, "no secret")


def test_callback_parsing():
    print("login callback:")
    check(parse_callback("/callback?code=abc&state=s", "s") == "abc", "reads the code")
    check(parse_callback("/callback?code=abc&state=other", "s") is None, "wrong state ignored")
    check(parse_callback("/favicon.ico", "s") is None, "other paths ignored")
    check(fails(parse_callback, "/callback?error=access_denied&state=s", "s") is not None,
          "declining is reported")


def test_token_claims():
    print("reading tokens:")
    check(read_token(make_token(), CLIENT) == (90001, "Test Pilot"), "character read")
    check(fails(read_token, make_token(audience=("other",)), CLIENT) is not None,
          "another app's token is rejected")
    check(fails(read_token, make_token(issuer="https://evil.example"), CLIENT) is not None,
          "another issuer is rejected")
    check(fails(read_token, "garbage", CLIENT) is not None, "garbage is an error, not a crash")


def test_login_flow():
    print("whole login, with a real local callback:")
    sent = []

    def send(request):
        sent.append(urllib.parse.parse_qs(request.data.decode()))
        return json.dumps({"access_token": make_token(), "expires_in": 1200,
                           "refresh_token": "refresh-1"}).encode()

    def browser(url, code_suffix="code=thecode"):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        back = f"{query['redirect_uri'][0]}?{code_suffix}&state={query['state'][0]}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        threading.Thread(target=lambda: opener.open(back, timeout=10).read(),
                         daemon=True).start()

    character = login(CLIENT, open_browser=browser, send=send, port=0, timeout=10)
    check((character.id, character.name) == (90001, "Test Pilot"), "character returned")
    check(character.refresh_token == "refresh-1", "refresh token kept")
    check(sent[0]["code"] == ["thecode"] and sent[0]["client_id"] == [CLIENT],
          "code exchanged for this client")
    check("code_verifier" in sent[0] and "client_secret" not in sent[0],
          "uses PKCE with no secret")
    check(fails(login, CLIENT, open_browser=lambda u: browser(u, "error=access_denied"),
                send=send, port=0, timeout=10) is not None, "declined login is reported")
    check(fails(login, CLIENT, open_browser=lambda u: None, send=send, port=0,
                timeout=0.2) is not None, "no answer times out")

    def rejected(request):
        raise http_error(request.full_url, 400)

    check(fails(login, CLIENT, open_browser=browser, send=rejected, port=0,
                timeout=10) is not None, "rejected code is reported")


def test_client():
    print("reading ESI:")
    calls = []
    colonies = [{"planet_id": 1, "solar_system_id": 1001}, {"planet_id": 2, "solar_system_id": 1001}]

    def send(request):
        url = request.full_url
        calls.append(request)
        if "oauth/token" in url:
            return json.dumps({"access_token": make_token(), "expires_in": 1200,
                               "refresh_token": "refresh-2"}).encode()
        if url.endswith("/planets/2"):
            raise http_error(url, 500)
        if url.endswith("/planets/1"):
            return b'{"pins": []}'
        if url.endswith("/planets"):
            return json.dumps(colonies).encode()
        return b'{"skills": [], "total_sp": 0}'

    pilot = Character(90001, "Test Pilot", "refresh-1")
    raw = EsiClient(CLIENT, send=send).fetch_character(pilot)
    check(pilot.refresh_token == "refresh-2", "rotated refresh token is kept")
    tokens = [c for c in calls if "oauth/token" in c.full_url]
    check(len(tokens) == 1, "one refresh covers every call")
    reads = [c for c in calls if "oauth/token" not in c.full_url]
    check(all(c.get_header("Authorization") == "Bearer " + pilot.access_token for c in reads),
          "every read carries the token")
    check(all(c.get_header("X-compatibility-date") for c in reads), "sends the compatibility date")
    check(1 in raw["details"] and 2 not in raw["details"], "one bad colony keeps the others")
    check(len(raw["errors"]) == 1 and "500" in raw["errors"][0], "and is reported")

    def expired(request):
        raise http_error(request.full_url, 400)

    dead = EsiClient(CLIENT, send=expired).fetch_character(Character(1, "Old Pilot", "r"))
    check(len(dead["errors"]) == 1, "a dead login gives one clear error, not many")
    check("add it" in dead["errors"][0], "and says what to do")
    check(not any("r" == e for e in dead["errors"]), "errors hold no token")

    def offline(request):
        raise OSError("no network")

    down = EsiClient(CLIENT, send=offline).fetch_character(Character(1, "Pilot", "r"))
    check(down["errors"] and down["colonies"] is None,
          "offline is an error, and doesn't read as having no colonies")


def test_store():
    print("saved characters:")
    check(unprotect(protect("secret-token")) == "secret-token", "tokens round trip")
    check(unprotect("dpapi:not-valid") == "", "unreadable tokens come back empty")
    check(unprotect("nonsense") == "", "unknown formats come back empty")
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "sub", "characters.json")
    check(load_store(path) == ("", []), "no file means no characters")
    pilot = Character(5, "Pilot", "secret-token", access_token="access-secret")
    save_store(path, CLIENT, [pilot])
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    check("access-secret" not in text, "access tokens are never saved")
    if sys.platform == "win32":
        check("secret-token" not in text
              and base64.b64encode(b"secret-token").decode() not in text,
              "refresh tokens are encrypted on Windows")
    client_id, loaded = load_store(path)
    check(client_id == CLIENT and loaded[0].refresh_token == "secret-token", "reloads")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    check(load_store(path) == ("", []), "a damaged file means no characters")
    os.remove(path)
    os.rmdir(os.path.dirname(path))
    os.rmdir(folder)


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
SOON = "2026-11-06T12:00:00Z"
PAST = "2026-11-01T12:00:00Z"


def _extractor(product, quantity, expiry, cycle=1800):
    return {"pin_id": 1, "type_id": 2848, "latitude": 0, "longitude": 0, "expiry_time": expiry,
            "extractor_details": {"product_type_id": product, "qty_per_cycle": quantity,
                                  "cycle_time": cycle, "heads": [{"head_id": 0}, {"head_id": 1}]}}


def _raw():
    aqueous = P0_IDS["Aqueous Liquids"]
    magma = P0_IDS["Felsic Magma"]
    return {
        "errors": [],
        "skills": {"skills": [{"skill_id": 2495, "active_skill_level": 4},
                              {"skill_id": 2505, "active_skill_level": 5}]},
        "colonies": [
            {"planet_id": 2, "solar_system_id": 1001, "planet_type": "barren", "upgrade_level": 5,
             "last_update": "2026-11-05T09:00:00Z"},
            {"planet_id": 3, "solar_system_id": 1002, "planet_type": "lava", "upgrade_level": 2,
             "last_update": "2026-11-04T12:00:00Z"},
            {"planet_id": 777, "solar_system_id": 1001, "planet_type": "ice", "upgrade_level": 1,
             "last_update": "2026-11-05T09:00:00Z"},
        ],
        "details": {
            2: {"pins": [
                _extractor(aqueous, 1000, SOON),
                {"pin_id": 2, "schematic_id": 901, "contents": [{"type_id": aqueous, "amount": 50}]},
                {"pin_id": 3, "factory_details": {"schematic_id": 901}},
                {"pin_id": 4, "contents": [{"type_id": WATER, "amount": 300},
                                           {"type_id": aqueous, "amount": 25}]},
            ]},
            3: {"pins": [
                _extractor(magma, 2000, PAST),
                {"pin_id": 5, "extractor_details": {"heads": []}},
                {"pin_id": 6, "schematic_id": 999999},
            ]},
        },
    }


def test_colonies(db):
    print("colony logic:")
    pilot = build_character(90001, "Test Pilot", _raw(), db, now=NOW)
    check(pilot.max_planets == 5 and pilot.command_center_skill == 5, "slots come from skills")
    check(pilot.free_slots == 2, "free slots = slots - colonies")
    check([c.system for c in pilot.colonies] == ["Alpha", "Alpha", "Bravo"], "sorted by system")

    barren = next(c for c in pilot.colonies if c.planet_id == 2)
    check(barren.planet_type == "Barren", "planet type from the SDE")
    check(barren.command_center_level == 5, "command center level read")
    extractor = barren.extractors[0]
    check(extractor.product == "Aqueous Liquids", "extractor product named")
    check(abs(extractor.per_hour - 2000) < 1e-6, "1000 per 30 minutes = 2000/hr")
    check(extractor.heads == 2 and extractor.status is RUNNING, "heads and status")
    group = barren.factories[0]
    check((group.product, group.count) == ("Water", 2), "factories grouped by product")
    check(abs(group.max_per_hour - 80) < 1e-6, "2 factories at 20 per 30 minutes = 80/hr")
    check(barren.stock == {"Aqueous Liquids": 75, "Water": 300}, "storage adds up across pins")

    lava = next(c for c in pilot.colonies if c.planet_id == 3)
    check(lava.extractors[0].status is STOPPED, "a past end time is stopped")
    check(lava.extractors[1].status is NO_PROGRAM and lava.extractors[1].product is None,
          "an unprogrammed extractor is flagged")
    check(lava.factories == [], "unknown schematics are skipped")

    unknown = next(c for c in pilot.colonies if c.planet_id == 777)
    check(unknown.name == "Planet 777" and unknown.planet_type == "Ice",
          "unknown planets still show")
    check(not unknown.has_details, "missing details are marked")

    all_of = totals([pilot])
    check(all_of.extraction == {"Aqueous Liquids": 2000.0}, "only running extractors count")
    check(all_of.holdings == {"Aqueous Liquids": 75, "Water": 300}, "holdings summed")
    check(all_of.oldest_update == parse_time("2026-11-04T12:00:00Z"), "oldest update found")
    check((all_of.planets_used, all_of.max_planets, all_of.free_slots) == (3, 5, 2), "slot totals")

    no_skills = _raw()
    no_skills["skills"] = None
    blind = build_character(1, "Blind", no_skills, db, now=NOW)
    check(blind.max_planets is None and blind.free_slots is None, "unread skills stay unknown")
    both = totals([pilot, blind])
    check(both.unknown_slots == 1 and both.max_planets == 5, "unknown slots aren't guessed")
    check(both.extraction["Aqueous Liquids"] == 4000.0, "totals span characters")

    untrained = build_character(1, "New", {"skills": {"skills": []}, "colonies": [],
                                           "details": {}, "errors": []}, db, now=NOW)
    check(untrained.max_planets == 1, "everyone has one planet with no skill")
    maxed = build_character(1, "Max", {"skills": {"skills": [
        {"skill_id": 2495, "active_skill_level": 5}]}}, db, now=NOW)
    check(maxed.max_planets == 6, "level 5 gives six planets")
    unread = build_character(1, "Unread", {"skills": {"skills": []}, "colonies": None,
                                           "details": {}, "errors": ["x"]}, db, now=NOW)
    check(unread.max_planets == 1 and unread.free_slots is None,
          "an unread colony list never reads as free slots")
    check(totals([unread]).unknown_slots == 1, "and counts as unknown in totals")
    roster, unknown = slot_roster([pilot, blind, unread, untrained])
    check(unknown == ["Blind", "Unread"], "unknown characters are named, not guessed")
    check([(c.name, c.free) for c in roster] == [("Test Pilot", 2), ("New", 1)],
          "the roster has each character's free slots")
    check(roster[0].existing == {2, 3, 777}, "and the planets it already has")
    check(parse_time("bad") is None and parse_time(None) is None, "bad times stay unknown")
    check(parse_time(SOON) - NOW == timedelta(days=1), "times are read as UTC")


def main() -> int:
    test_pkce()
    test_callback_parsing()
    test_token_claims()
    test_login_flow()
    test_client()
    test_store()
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_colonies(db)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
