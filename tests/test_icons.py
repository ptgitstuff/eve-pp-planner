"""Tests for the on-disk icon cache (no network: downloads are faked).
Run:  python -m tests.test_icons
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.chain_map import build_chain_graph
from eve_pi.db import PIDatabase
from eve_pi.icons import PNG_SIGNATURE, cached_icons, fetch_icons, icon_path
from tests.make_official_fixture import WATER, build_fixture

_passed = 0
PNG = PNG_SIGNATURE + b"fake image data"


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_cache(folder):
    print("download and cache:")
    calls = []

    def download(url):
        calls.append(url)
        return PNG

    ids = {"Water": 101, "Silicon": 106}
    check(cached_icons(ids, folder) == {}, "nothing cached to begin with")
    got = fetch_icons(ids, folder, download=download)
    check(set(got) == {"Water", "Silicon"}, "both icons returned")
    check(len(calls) == 2, "one download per icon")
    check(any("/types/101/icon?size=32" in u for u in calls), "uses the type's URL")
    with open(got["Water"], "rb") as fh:
        check(fh.read() == PNG, "file holds the downloaded bytes")

    calls.clear()
    fetch_icons(ids, folder, download=download)
    check(not calls, "cached icons aren't downloaded again")


def test_failures(folder):
    print("failed downloads:")

    def offline(url):
        raise OSError("no network")

    check(fetch_icons({"Oxygen": 102}, folder, download=offline) == {},
          "offline leaves the icon out without raising")
    check(fetch_icons({"Oxygen": 102}, folder, download=lambda u: b"<html>") == {},
          "a non-PNG response isn't kept")
    check(not os.path.exists(icon_path(folder, 102)), "nothing written for failures")


def test_graph_ids():
    print("graph type IDs:")
    path = build_fixture()
    db = PIDatabase(path)
    try:
        graph = build_chain_graph(db)
    finally:
        db.close()
        os.remove(path)
    check(graph.type_ids["Water"] == WATER, "products carry their SDE type ID")
    check(graph.type_ids["Barren"] == 2016, "planet types carry their type ID")
    check("Aqueous Liquids" in graph.type_ids, "P0 resources carry their type ID")


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        test_cache(folder)
        test_failures(folder)
    test_graph_ids()
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
