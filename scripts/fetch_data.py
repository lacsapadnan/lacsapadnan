#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

Needs a token in GH_TOKEN (or GITHUB_TOKEN). The workflow's default token only
sees public contributions; to include private ones (the way your profile graph
does) store a PAT with `read:user` as the PROFILE_TOKEN repo secret.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "contributions.json"
LOGIN = os.environ.get("GITHUB_REPOSITORY_OWNER") or "lacsapadnan"
LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

QUERY = """
query($login: String!) {
  user(login: $login) {
    pinnedItems(first: 6, types: REPOSITORY) {
      nodes { ... on Repository {
        name description url stargazerCount forkCount pushedAt
        primaryLanguage { name color }
      } }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""


def gql(token: str) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode(),
        headers={"Authorization": f"Bearer {token}", "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


def dump(data: dict) -> str:
    """Pretty JSON, but one line per day / repo so diffs stay readable."""
    s = json.dumps(data, ensure_ascii=False, indent=1)
    s = re.sub(r'\{\s+"date": ("[^"]+"),\s+"count": (\d+),\s+"level": (\d)\s+\}', r'{"date": \1, "count": \2, "level": \3}', s)
    s = re.sub(r'\{\s+("name".*?)\s+\}', lambda m: "{" + re.sub(r"\s*\n\s*", " ", m.group(1)) + "}", s, flags=re.S)
    return s + "\n"


def main() -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN (or GITHUB_TOKEN) first.")
    user = gql(token)

    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": d["date"], "count": d["contributionCount"], "level": LEVELS[d["contributionLevel"]]}
        for w in cal["weeks"]
        for d in w["contributionDays"]
    ]
    days.sort(key=lambda d: d["date"])

    repos = [
        {
            "name": n["name"],
            "description": n["description"] or "",
            "language": (n["primaryLanguage"] or {}).get("name", ""),
            "color": (n["primaryLanguage"] or {}).get("color") or "#a3a8a2",
            "stars": n["stargazerCount"],
            "forks": n["forkCount"],
            "url": n["url"],
            "pushed_at": (n["pushedAt"] or "")[:10],
        }
        for n in user["pinnedItems"]["nodes"]
        if n
    ]

    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    data = {
        "login": LOGIN,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": cal["totalContributions"],
        "days": days,
        "repos": repos or old.get("repos", []),  # nothing pinned -> keep the last known list
    }
    same = {k: v for k, v in old.items() if k != "fetched_at"} == {k: v for k, v in data.items() if k != "fetched_at"}
    if same:  # nothing changed: don't rewrite the file, so the workflow makes no empty daily commit
        print("no changes in contribution data")
        return
    OUT.write_text(dump(data), encoding="utf-8")
    print(f"{len(days)} days, {data['total']} contributions, {len(data['repos'])} repos -> {OUT}")


if __name__ == "__main__":
    main()
