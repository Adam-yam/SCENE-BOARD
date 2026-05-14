#!/usr/bin/env python3

import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

import requests

ROOT = pathlib.Path(__file__).parent.parent

API_KEY    = os.environ["YOUTUBE_API_KEY"]
CHANNEL_ID = "UCtKtCiaWRz-d3EZn2xd1mdA"
MAX_LONG   = 6
MAX_SHORT  = 6

def iso8601_to_seconds(duration: str) -> int:
    m = re.match(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        duration or "",
    )
    if not m:
        return 0
    days, hours, minutes, seconds = (int(v or 0) for v in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds

def search_videos(duration: str, max_results: int) -> list[dict]:
    search_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key":           API_KEY,
            "channelId":     CHANNEL_ID,
            "part":          "snippet",
            "order":         "date",
            "type":          "video",
            "videoDuration": duration,
            "maxResults":    max_results,
        },
        timeout=15,
    )
    search_resp.raise_for_status()
    items = search_resp.json().get("items", [])
    if not items:
        return []

    video_ids = ",".join(item["id"]["videoId"] for item in items)
    detail_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": API_KEY, "id": video_ids, "part": "contentDetails"},
        timeout=15,
    )
    detail_resp.raise_for_status()
    durations = {
        v["id"]: iso8601_to_seconds(v["contentDetails"].get("duration", ""))
        for v in detail_resp.json().get("items", [])
    }

    videos = []
    for item in items:
        snippet  = item["snippet"]
        video_id = item["id"]["videoId"]
        title    = snippet["title"]
        date_raw = snippet["publishedAt"]
        thumb    = (
            snippet.get("thumbnails", {}).get("medium") or
            snippet.get("thumbnails", {}).get("default") or {}
        ).get("url", "")
        date_str = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).strftime("%Y.%m.%d")

        secs = durations.get(video_id, 9999)
        if secs <= 60:
            video_url  = f"https://www.youtube.com/shorts/{video_id}"
            video_type = "short"
        else:
            video_url  = f"https://www.youtube.com/watch?v={video_id}"
            video_type = duration

        videos.append({
            "id":        video_id,
            "title":     title,
            "url":       video_url,
            "thumbnail": thumb,
            "date":      date_str,
            "type":      video_type,
        })

    print(f"[YouTube] {duration} → {len(videos)}개 수집", file=sys.stderr)
    return videos

def main():
    longs   = search_videos("long",   MAX_LONG)
    mediums = search_videos("medium", MAX_LONG)
    shorts  = search_videos("short",  MAX_SHORT)

    long_combined = sorted(longs + mediums, key=lambda v: v["date"], reverse=True)[:MAX_LONG]
    for v in long_combined:
        v["type"] = "long"

    true_shorts = [v for v in shorts if v["type"] == "short"]

    output = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videos":  long_combined + true_shorts,
    }

    out_path = ROOT / "youtube.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(
        f"[완료] 롱폼 {len(long_combined)}개 + 숏폼 {len(true_shorts)}개 → {out_path}",
        file=sys.stderr,
    )

if __name__ == "__main__":
    main()
