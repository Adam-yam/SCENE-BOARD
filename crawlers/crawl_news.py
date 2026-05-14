#!/usr/bin/env python3

import asyncio
import html
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).parent.parent

NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

NAVER_HEADERS = {
    "X-Naver-Client-Id":     NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

FETCH_HEADERS = {
    "User-Agent": NAVER_HEADERS["User-Agent"],
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SEARCH_QUERIES = ["리센느", "RESCENE"]
DISPLAY        = 20
MAX_ARTICLES   = 20
THUMB_TIMEOUT  = 6
THUMB_CONCUR   = 8

def parse_pub_date(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc).strftime("%Y.%m.%d")
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return datetime.now(timezone.utc).strftime("%Y.%m.%d")

def clean_text(t: str) -> str:
    t = html.unescape(t)
    t = re.sub(r"<[^>]+>", "", t)
    return t.strip()

def crawl_naver_api(query: str) -> list[dict]:
    url = "https://openapi.naver.com/v1/search/news.json"
    params = {
        "query":   query,
        "display": DISPLAY,
        "start":   1,
        "sort":    "date",
    }
    articles = []
    try:
        resp = requests.get(url, headers=NAVER_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            title    = clean_text(item.get("title", ""))
            link     = item.get("originallink") or item.get("link", "")
            pub_date = parse_pub_date(item.get("pubDate", ""))
            source_m = re.search(r"https?://(?:www\.)?([^/]+)", link)
            source   = source_m.group(1) if source_m else "네이버뉴스"

            if title and link:
                articles.append({
                    "title":     title,
                    "url":       link,
                    "date":      pub_date,
                    "source":    source,
                    "thumbnail": None,
                })

        print(f"[Naver API] '{query}' → {len(articles)}개", file=sys.stderr)
    except Exception as e:
        print(f"[Naver API] '{query}' 오류: {e}", file=sys.stderr)

    return articles

def merge_articles(lists: list[list[dict]], max_count: int = MAX_ARTICLES) -> list[dict]:
    seen_titles: set[str] = set()
    seen_urls:   set[str] = set()
    merged = []
    for article_list in lists:
        for a in article_list:
            title_key = re.sub(r"\s+", "", a["title"].lower())[:40]
            url_key   = a["url"].split("?")[0]
            if title_key in seen_titles or url_key in seen_urls:
                continue
            seen_titles.add(title_key)
            seen_urls.add(url_key)
            merged.append(a)
    merged.sort(key=lambda x: x.get("date", ""), reverse=True)
    return merged[:max_count]

async def fetch_og_image(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
):
    if article.get("thumbnail"):
        return

    async with sem:
        try:
            async with session.get(
                article["url"],
                headers=FETCH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=THUMB_TIMEOUT),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    return
                if "html" not in resp.headers.get("Content-Type", ""):
                    return
                chunk = await resp.content.read(8192)
                text  = chunk.decode("utf-8", errors="ignore")

                m = re.search(
                    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                    text, re.IGNORECASE,
                ) or re.search(
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                    text, re.IGNORECASE,
                )
                if m:
                    img = m.group(1).strip()
                    if img.startswith("http"):
                        article["thumbnail"] = img
                        print(f"  [og] {article['title'][:28]}… OK", file=sys.stderr)
        except Exception:
            pass

async def enrich_thumbnails(articles: list[dict]):
    need = [a for a in articles if not a.get("thumbnail")]
    if not need:
        return
    print(f"[썸네일] og:image 수집 — {len(need)}개", file=sys.stderr)
    sem  = asyncio.Semaphore(THUMB_CONCUR)
    conn = aiohttp.TCPConnector(limit=THUMB_CONCUR, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        await asyncio.gather(*[fetch_og_image(session, a, sem) for a in need])
    filled = sum(1 for a in need if a.get("thumbnail"))
    print(f"[썸네일] {filled}/{len(need)}개 성공", file=sys.stderr)

def main():
    print("[뉴스 크롤러 v3] 시작", file=sys.stderr)

    all_articles = []
    for q in SEARCH_QUERIES:
        all_articles += crawl_naver_api(q)
        time.sleep(0.3)

    articles = merge_articles([all_articles])

    asyncio.run(enrich_thumbnails(articles))

    for a in articles:
        if not a.get("thumbnail"):
            a["thumbnail"] = None

    output = {
        "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "articles": articles,
    }

    out_path = ROOT / "news.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    filled = sum(1 for a in articles if a.get("thumbnail"))
    print(
        f"[뉴스 크롤러 v3] 완료 — {len(articles)}개 / 썸네일 {filled}개 → {out_path}",
        file=sys.stderr,
    )

if __name__ == "__main__":
    main()
