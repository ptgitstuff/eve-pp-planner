"""Tests for what the app remembers between sessions.
Run:  python -m tests.test_settings
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.colonies import load_character_data, merge_raw, save_character_data
from eve_pi.settings import coerce, load_json, save_json, window_size

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_files(folder):
    print("settings files:")
    path = os.path.join(folder, "sub", "settings.json")
    check(load_json(path) == {}, "no file means no settings")
    save_json(path, {"staging": "Jita", "jumps": 6})
    check(load_json(path) == {"staging": "Jita", "jumps": 6}, "values round trip")
    check(not os.path.exists(path + ".tmp"), "no temporary file left behind")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    check(load_json(path) == {}, "a damaged file means no settings")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("[1, 2]")
    check(load_json(path) == {}, "a file of the wrong shape means no settings")


def test_values():
    print("checking saved values:")
    check(coerce(6, 5) == 6 and coerce("Jita", "") == "Jita", "matching types are used")
    check(coerce(False, True) is False, "booleans are used")
    check(coerce("six", 5) == 5, "the wrong type falls back")
    check(coerce(1, True) is True and coerce(True, 5) == 5, "booleans and numbers aren't mixed")
    check(coerce(None, "x") == "x", "missing values fall back")

    check(window_size("1000x700", 1920, 1080) == "1000x700", "a good size is kept")
    check(window_size("4000x3000", 1920, 1080) == "1920x1080", "too big shrinks to the screen")
    check(window_size("1x1", 1920, 1080) is None, "a tiny window is ignored")
    check(window_size("1000x700+5000+0", 1920, 1080) is None, "positions aren't accepted")
    check(window_size(None, 1920, 1080) is None and window_size(7, 1920, 1080) is None,
          "other values are ignored")


def _read(colonies=True):
    return {"errors": [], "skills": {"skills": [{"skill_id": 2495, "active_skill_level": 3}]},
            "colonies": [{"planet_id": 1}, {"planet_id": 2}] if colonies else None,
            "details": {1: {"pins": ["one"]}, 2: {"pins": ["two"]}} if colonies else {}}


def test_merge():
    print("keeping figures through a failed read:")
    good = _read()
    check(merge_raw(None, good) is good, "the first read is used as it is")
    check(merge_raw(good, _read()) == _read(), "a good read replaces the old one")

    outage = {"errors": ["down"], "skills": None, "colonies": None, "details": {}}
    merged = merge_raw(good, outage)
    check(merged["skills"] == good["skills"], "skills are kept")
    check(merged["colonies"] == good["colonies"], "colonies are kept")
    check(merged["details"] == good["details"], "colony details are kept")
    check(merged["errors"][0] == "down" and len(merged["errors"]) == 2,
          "the failure is still shown, and says old figures are in use")

    partial = _read()
    del partial["details"][2]
    partial["errors"] = ["one colony failed"]
    merged = merge_raw(good, partial)
    check(merged["details"][2] == {"pins": ["two"]}, "one failed colony keeps its details")
    check(merged["details"][1] == {"pins": ["one"]}, "the others stay current")

    gone = _read()
    gone["colonies"] = [{"planet_id": 1}]
    gone["details"] = {1: {"pins": ["new"]}}
    merged = merge_raw(good, gone)
    check(merged["colonies"] == [{"planet_id": 1}] and 2 not in merged["details"],
          "a colony you've removed doesn't come back")
    check(merged["errors"] == [], "a clean read has no warning")
    check(merge_raw({"colonies": None, "skills": None}, outage)["errors"] == ["down"],
          "with nothing to keep, there's no such claim")


def test_character_data(folder):
    print("saved character figures:")
    path = os.path.join(folder, "character_data.json")
    check(load_character_data(path) == {}, "no file means no figures")
    save_character_data(path, {90001: {**_read(), "errors": ["old"]}})
    loaded = load_character_data(path)
    check(list(loaded) == [90001], "character IDs come back as numbers")
    check(list(loaded[90001]["details"]) == [1, 2], "planet IDs come back as numbers")
    check(loaded[90001]["errors"] == [], "old errors aren't brought back")
    check(loaded[90001]["skills"] == _read()["skills"], "skills come back")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"x": {}, "5": "nonsense", "6": {"details": {"bad": {}}}}')
    check(load_character_data(path) == {}, "damaged entries are skipped")


def main() -> int:
    folder = tempfile.mkdtemp()
    try:
        test_files(folder)
        test_values()
        test_merge()
        test_character_data(folder)
    finally:
        shutil.rmtree(folder)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
