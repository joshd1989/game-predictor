#!/usr/bin/env python3
"""Decide whether a GitHub Actions run should refresh data and deploy the site.

Scheduled runs fire at 13:00, 14:00, 20:00 and 21:00 UTC so that one slot lands
on 09:00 America/Toronto and one on 16:00, in both EST and EDT. A run outside
those local windows, or a second slot after a refresh already landed, does nothing.
workflow_dispatch always refreshes. A push deploys the site without rerunning the pipeline.
"""
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/Toronto")


def emit(pipeline, deploy):
    line = f"pipeline={'true' if pipeline else 'false'}\ndeploy={'true' if deploy else 'false'}\n"
    print(line, end="")
    dest = os.environ.get("GITHUB_OUTPUT")
    if dest:
        with open(dest, "a", encoding="utf-8") as f:
            f.write(line)


def last_age_hours(now):
    path = os.path.join(os.getcwd(), "site", "data.json")
    try:
        as_of = json.load(open(path, encoding="utf-8")).get("asOf")
        last = dt.datetime.strptime(as_of, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return (now - last).total_seconds() / 3600


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    if event == "push":
        emit(False, True)
        return
    if event != "schedule":
        emit(True, True)
        return
    now = dt.datetime.now(ET)
    mins = now.hour * 60 + now.minute
    morning = 8 * 60 + 45 <= mins <= 10 * 60 + 20
    afternoon = 15 * 60 + 45 <= mins <= 17 * 60 + 20
    if not (morning or afternoon):
        emit(False, False)
        return
    age = last_age_hours(now)
    if age is not None and age < 3:
        emit(False, False)
        return
    emit(True, True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        emit(True, True)
