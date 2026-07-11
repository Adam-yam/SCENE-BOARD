#!/usr/bin/env python3

import json
import pathlib
import re
import sys
from datetime import datetime, date, timezone, timedelta
from typing import Optional

import requests

ROOT = pathlib.Path(__file__).parent.parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://artist.mnetplus.world/",
}

API_BASE = "https://artist.mnetplus.world/svc/stg/rescene-official/space/api/v1/calendar"

def build_params(year: int, month: int) -> dict:
    start_kst = datetime(year, month, 1, 0, 0, 0)
    start_utc = start_kst - timedelta(hours=9)

    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    end_kst = datetime(last_day.year, last_day.month, last_day.day, 23, 59, 59)
    end_utc = end_kst - timedelta(hours=9)

    return {
        "startAt":          start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startAtForAllDay": f"{year}-{month:02d}-01",
        "endAt":            end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endAtForAllDay":   f"{last_day.year}-{last_day.month:02d}-{last_day.day:02d}",
    }

def extract_date(ev: dict) -> Optional[str]:
    if ev.get("allDay"):
        raw = ev.get("startAtAllDay", "")
    else:
        raw = ev.get("startAt", ev.get("startAtAllDay", ""))
    if not raw:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(raw))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

def extract_time(ev: dict) -> Optional[str]:
    if ev.get("allDay"):
        return None
    raw = ev.get("startAt", "")
    if not raw:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", str(raw))
    if not m:
        return None
    try:
        dt_utc = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )
    except ValueError:
        return None
    dt_kst = dt_utc + timedelta(hours=9)
    # 자정(00:00)만 찍힌 이벤트는 사실상 시간 미정인 경우가 많아 제외
    if dt_kst.hour == 0 and dt_kst.minute == 0:
        return None
    return f"{dt_kst.hour:02d}:{dt_kst.minute:02d}"

LABEL_MAP = {
    "공연":    "공연",
    "팬사인회": "팬사인회",
    "음방":    "음방",
    "방송":    "방송",
    "예능":    "예능",
    "행사":    "기타",
    "기념일":  "기타",
}
TYPE_KEYWORDS = {
    "음방":    ["음방", "음악방송", "inkigayo", "인기가요", "뮤직뱅크", "music bank",
                "show champion", "엠카운트다운", "mcountdown", "the show"],
    "방송":    ["방송", "출연", "인터뷰", "interview", "라디오", "radio"],
    "공연":    ["콘서트", "concert", "showcase", "쇼케이스", "팬미팅", "fanmeeting",
                "공연", "페스티벌", "festival", "live", "kcon"],
    "팬사인회": ["팬사인", "fansign", "사인회", "팬이벤트", "영상통화"],
    "예능":    ["예능", "버라이어티", "variety", "웹예능"],
}

def classify_type(ev: dict) -> str:
    label_name = (ev.get("label") or {}).get("name", "")
    if label_name in LABEL_MAP:
        return LABEL_MAP[label_name]
    tl = ev.get("title", "").lower()
    for t, kws in TYPE_KEYWORDS.items():
        if any(k in tl for k in kws):
            return t
    return "기타"

def crawl_month(year: int, month: int) -> list:
    params = build_params(year, month)
    try:
        resp = requests.get(API_BASE, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[MnetPlus] {year}/{month:02d} API 오류: {e}", file=sys.stderr)
        return []

    events = []
    for ev in data.get("events", []):
        d     = extract_date(ev)
        title = ev.get("title", "").strip()
        if not d or not title:
            continue
        label_name = (ev.get("label") or {}).get("name", "")
        if label_name == "기념일":
            continue
        events.append({
            "date":   d,
            "time":   extract_time(ev),
            "title":  title,
            "detail": "",
            "type":   classify_type(ev),
            "source": "mnetplus",
        })

    print(f"[MnetPlus] {year}/{month:02d} → {len(events)}개", file=sys.stderr)
    return events

def load_existing_events(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("events", []) or []
    except Exception as e:
        print(f"[스케줄 크롤러 v4] 기존 schedule.json 로드 실패: {e}", file=sys.stderr)
        return []

def main():
    print("[스케줄 크롤러 v4] 시작", file=sys.stderr)

    today  = date.today()
    months = [(today.year, today.month)]
    if today.month == 12:
        months.append((today.year + 1, 1))
    else:
        months.append((today.year, today.month + 1))

    out_path = ROOT / "schedule.json"

    fresh_events = []
    for y, m in months:
        fresh_events.extend(crawl_month(y, m))

    existing_events = load_existing_events(out_path)

    merged = {}
    for ev in existing_events:
        key = (ev.get("date"), ev.get("title"))
        merged[key] = ev
    for ev in fresh_events:
        key = (ev["date"], ev["title"])
        merged[key] = ev  # 새로 크롤링한 데이터로 갱신(시간/타입 등 변경 반영)

    deduped = sorted(merged.values(), key=lambda e: (e.get("date", ""), e.get("time") or ""))

    output = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events":  deduped,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[스케줄 크롤러 v4] 완료 — {len(deduped)}개 저장 → {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
