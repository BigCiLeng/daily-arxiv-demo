#!/usr/bin/env python3
"""
Daily arXiv cs.CV digest generator.

Fetches the current day's submissions from https://arxiv.org/list/cs.CV/recent?skip=0&show=2000,
classifies them, and renders a static HTML page. Favorite authors and keywords
can be configured via a JSON file (see DEFAULT_CONFIG for the schema).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter, defaultdict
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Dict, Iterable, List, Tuple

import requests
from bs4 import BeautifulSoup

ARXIV_SOURCES = {
    "cs.CV": {
        "label": "Computer Vision (cs.CV)",
        "url": "https://arxiv.org/list/cs.CV/recent?skip=0&show=2000",
    },
    "cs.RO": {
        "label": "Robotics (cs.RO)",
        "url": "https://arxiv.org/list/cs.RO/recent?skip=0&show=2000",
    },
}
ARXIV_BASE_URL = "https://arxiv.org"
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}
DEFAULT_CONFIG = {
    "favorite_authors": [],
    "keywords": [],
}
DETAIL_ABSTRACT_CACHE: Dict[str, str] = {}
KEYWORD_CACHE: Dict[str, List[str]] = {}

# KEYWORD_API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
# KEYWORD_API_KEY = os.getenv("OPENROUTER_API_KEY", "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").strip()
# KEYWORD_API_MODEL = os.getenv("OPENROUTER_KEYWORD_MODEL", "deepseek/deepseek-r1-0528-qwen3-8b:free")
KEYWORD_API_URL = os.getenv("OPENROUTER_API_URL", "https://api.siliconflow.cn/v1/chat/completions")
KEYWORD_API_KEY = os.getenv("OPENROUTER_API_KEY", "sk-jezzaxcyhijfasbbjcgmomdkqluhumkpkdemcefqwhwjvwmg")
KEYWORD_API_MODEL = os.getenv("OPENROUTER_KEYWORD_MODEL", "Qwen/Qwen2.5-7B-Instruct")

KEYWORD_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "")
KEYWORD_X_TITLE = os.getenv("OPENROUTER_X_TITLE", "arXiv Daily Digest")
KEYWORD_TARGET_COUNT = max(1, int(os.getenv("OPENROUTER_KEYWORD_COUNT", "2")))
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "are",
    "was",
    "were",
    "can",
    "will",
    "into",
    "than",
    "when",
    "what",
    "which",
    "using",
    "used",
    "been",
    "also",
    "such",
    "their",
    "our",
    "between",
    "other",
    "more",
    "less",
    "these",
    "those",
    "while",
    "where",
    "whose",
    "they",
    "them",
    "towards",
    "toward",
    "your",
    "about",
    "over",
    "both",
    "each",
    "two",
    "three",
    "four",
    "five",
    "new",
    "per",
    "via",
    "upon",
    "onto",
    "within",
    "without",
    "across",
    "through",
    "throughout",
    "among",
    "amongst",
    "because",
    "since",
    "after",
    "before",
    "during",
    "whereas",
    "however",
    "there",
    "therein",
    "thereof",
    "thereby",
    "here",
    "herein",
    "hereof",
    "hereby",
    "very",
    "many",
    "much",
    "most",
    "any",
    "all",
    "some",
    "none",
    "few",
    "either",
    "neither",
    "not",
    "nor",
    "yet",
    "but",
    "though",
    "although",
    "ever",
    "every",
    "even",
    "still",
    "quite",
    "rather",
    "further",
    "around",
    "outside",
    "inside",
}

PHRASE_MAX_WORDS = 4


def split_long_phrase(words: List[str], max_words: int) -> List[List[str]]:
    if len(words) <= max_words:
        return [words]
    return [words[i : i + max_words] for i in range(len(words) - max_words + 1)]


def slugify(text: str, fallback: str = "section") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


def generate_candidate_phrases(text: str, max_words: int = PHRASE_MAX_WORDS) -> List[str]:
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    phrases: List[List[str]] = []
    current: List[str] = []
    for token in tokens:
        if not token:
            continue
        if token in STOPWORDS or (len(token) <= 2 and token.isalpha()):
            if current:
                phrases.extend(split_long_phrase(current, max_words))
                current = []
        else:
            if token.isdigit():
                continue
            current.append(token)
    if current:
        phrases.extend(split_long_phrase(current, max_words))
    return [" ".join(words) for words in phrases if words]


def extract_top_phrases(articles: Iterable[Article], top_n: int = 3) -> List[Tuple[str, int]]:
    phrase_counts: Counter[str] = Counter()
    phrase_lengths: Dict[str, int] = {}

    for article in articles:
        text = f"{article.title} {article.abstract}"
        for phrase in generate_candidate_phrases(text):
            if not phrase:
                continue
            phrase_counts[phrase] += 1
            phrase_lengths.setdefault(phrase, len(phrase.split()))

    if not phrase_counts:
        return []

    def sort_key(item: Tuple[str, int]) -> Tuple[int, int, str]:
        phrase, count = item
        length = phrase_lengths.get(phrase, 1)
        score = count * length
        return (-score, -count, phrase)

    sorted_phrases = sorted(phrase_counts.items(), key=sort_key)
    return sorted_phrases[:top_n]


@dataclass
class Article:
    arxiv_id: str
    abs_url: str
    pdf_url: str
    title: str
    authors: List[str]
    abstract: str
    primary_subject: str
    subjects: List[str]
    section_type: str
    submission_date: datetime
    keywords: List[str] = field(default_factory=list)


def load_config(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse config file {path}: {exc}") from exc

    config = DEFAULT_CONFIG.copy()
    config.update({k: v for k, v in data.items() if k in config})
    # Normalize entries to lowercase for matching.
    config["favorite_authors"] = [s.strip() for s in config["favorite_authors"] if s.strip()]
    config["keywords"] = [s.strip() for s in config["keywords"] if s.strip()]
    return config


def fetch_recent_page(list_url: str, session: requests.Session) -> BeautifulSoup:
    try:
        response = session.get(list_url, headers=HTTP_HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(f"Failed to fetch arXiv page {list_url}: {exc}") from exc
    soup = BeautifulSoup(response.text, "html.parser")
    # arXiv may return a block or login page without the expected structure.
    if not soup.find("div", id="dlpage"):
        raise SystemExit(
            f"The fetched page {list_url} does not contain the expected paper list; the request may have been blocked by arXiv."
        )
    return soup


def parse_sections(soup: BeautifulSoup) -> Iterable[Tuple[date, str, BeautifulSoup]]:
    dlpage = soup.find("div", id="dlpage")
    if not dlpage:
        raise SystemExit("Could not locate the #dlpage container on the arXiv page.")

    seen_dl_ids = set()
    headers = dlpage.find_all(["h2", "h3"])
    for header in headers:
        heading = header.get_text(" ", strip=True)
        if " for " not in heading:
            continue

        section_type, _, remainder = heading.partition(" for ")
        date_str, *_ = remainder.partition("(")
        date_str = date_str.strip()
        section_date = _parse_date(date_str)

        dl = header.find_next("dl")
        if not dl:
            continue
        dl_identity = id(dl)
        if dl_identity in seen_dl_ids:
            continue
        seen_dl_ids.add(dl_identity)

        yield section_date, section_type.strip(), dl

    if seen_dl_ids:
        return

    # Fallback: if no headers matched, traverse raw <dl> blocks and infer the date.
    for dl in dlpage.find_all("dl"):
        dateline_div = dl.find_previous("div", class_="list-dateline")
        if dateline_div:
            section_date = _parse_date(dateline_div.get_text(" ", strip=True))
        else:
            section_date = datetime.now().date()

        heading = dl.find_previous(["h2", "h3"])
        section_type = "Unlabeled"
        if heading:
            heading_text = heading.get_text(" ", strip=True)
            section_type = heading_text.split(" for ")[0] if " for " in heading_text else heading_text

        yield section_date, section_type, dl


def _parse_date(date_str: str) -> date:
    patterns = ["%a, %d %b %Y", "%d %b %Y"]
    for pattern in patterns:
        try:
            return datetime.strptime(date_str, pattern).date()
        except ValueError:
            continue
    # Return today's date if parsing fails so downstream logic can continue.
    return datetime.now().date()


def parse_articles_for_date(target_date: date, soup: BeautifulSoup, session: requests.Session) -> List[Article]:
    articles: List[Article] = []
    sections = list(parse_sections(soup))
    matching_sections = [(date, section_type, dl) for date, section_type, dl in sections if date == target_date]

    if not matching_sections and sections:
        # Fallback to the most recent date available.
        latest_date = max(date for date, *_ in sections)
        matching_sections = [(date, section_type, dl) for date, section_type, dl in sections if date == latest_date]
        print(f"No entries found for {target_date}. Falling back to the most recent date {latest_date} available on arXiv.")

    for section_date, section_type, dl in matching_sections:
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            article = extract_article(dt, dd, section_type, section_date, session)
            if article:
                articles.append(article)

    return articles


def extract_article(dt_tag, dd_tag, section_type: str, section_date: date, session: requests.Session) -> Article | None:
    id_anchor = dt_tag.find("a", title="Abstract")
    if not id_anchor:
        id_anchor = dt_tag.find("a", href=re.compile(r"/abs/"))
    if not id_anchor:
        return None

    arxiv_id = id_anchor.get_text(strip=True)
    if not arxiv_id:
        href = id_anchor.get("href", "")
        arxiv_id = href.rstrip("/").split("/")[-1]
    abs_url = requests.compat.urljoin(ARXIV_BASE_URL, id_anchor.get("href", ""))

    pdf_anchor = dt_tag.find("a", title="Download PDF")
    pdf_url = requests.compat.urljoin(ARXIV_BASE_URL, pdf_anchor["href"]) if pdf_anchor else ""

    title_div = dd_tag.find("div", class_="list-title")
    title = clean_descriptor_text(title_div, "Title:")

    authors_div = dd_tag.find("div", class_="list-authors")
    authors_raw = clean_descriptor_text(authors_div, "Authors:")
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()]

    abstract_div = dd_tag.find("p", class_="mathjax")
    if abstract_div is None:
        abstract_div = dd_tag.find("div", class_="mathjax")
    abstract = clean_descriptor_text(abstract_div, "Abstract:")
    full_abstract = fetch_full_abstract(abs_url, session)
    if full_abstract:
        abstract = full_abstract
    keywords = fetch_keywords_for_abstract(abstract, session)

    subjects_div = dd_tag.find("div", class_="list-subjects")
    subjects_text = clean_descriptor_text(subjects_div, "Subjects:")
    primary_subject_span = subjects_div.find("span", class_="primary-subject") if subjects_div else None
    primary_subject = primary_subject_span.get_text(strip=True) if primary_subject_span else subjects_text.split(";")[0].strip()
    subjects = [s.strip() for s in subjects_text.split(";") if s.strip()]

    return Article(
        arxiv_id=arxiv_id,
        abs_url=abs_url,
        pdf_url=pdf_url,
        title=title,
        authors=authors,
        abstract=abstract,
        primary_subject=primary_subject,
        subjects=subjects,
        section_type=section_type,
        submission_date=datetime.combine(section_date, datetime.min.time()).replace(tzinfo=timezone.utc),
        keywords=keywords,
    )


def fetch_full_abstract(abs_url: str, session: requests.Session) -> str:
    if not abs_url:
        return ""
    if abs_url in DETAIL_ABSTRACT_CACHE:
        return DETAIL_ABSTRACT_CACHE[abs_url]
    try:
        response = session.get(abs_url, headers=HTTP_HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    blockquote = soup.select_one("blockquote.abstract")
    if not blockquote:
        return ""

    text = blockquote.get_text(" ", strip=True)
    if text.lower().startswith("abstract:"):
        text = text[len("abstract:"):].strip()
    DETAIL_ABSTRACT_CACHE[abs_url] = text
    return text


def fetch_keywords_for_abstract(abstract: str, session: requests.Session) -> List[str]:
    if not abstract or not KEYWORD_API_KEY or not KEYWORD_API_URL:
        return []

    cache_key = abstract.strip()
    if cache_key in KEYWORD_CACHE:
        return KEYWORD_CACHE[cache_key]

    prompt = dedent(
        """
        Extract the two most informative keywords (single words or short noun phrases) from the research abstract below.
        Respond with a JSON object of the form {{"keywords": ["keyword 1", "keyword 2"]}} using at most two items.

        Abstract:
        {abstract}
        """
    ).strip().format(abstract=abstract.strip())

    payload = {
        "model": KEYWORD_API_MODEL,
        "messages": [
            {"role": "system", "content": "You extract concise research keywords."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {KEYWORD_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if KEYWORD_HTTP_REFERER:
        headers["HTTP-Referer"] = KEYWORD_HTTP_REFERER
    if KEYWORD_X_TITLE:
        headers["X-Title"] = KEYWORD_X_TITLE

    try:
        response = session.post(KEYWORD_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Warning: failed to fetch keywords from OpenRouter: {exc}")
        KEYWORD_CACHE[cache_key] = []
        return []

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    keywords = parse_keywords_response(content)[:KEYWORD_TARGET_COUNT]
    KEYWORD_CACHE[cache_key] = keywords
    return keywords


def parse_keywords_response(content: str) -> List[str]:
    if not content:
        return []

    candidates: List[str] = []
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            values = parsed.get("keywords")
            if isinstance(values, list):
                candidates = [str(item).strip() for item in values if str(item).strip()]
        elif isinstance(parsed, list):
            candidates = [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass

    if not candidates:
        parts = [part.strip(" •-\t") for part in re.split(r"[\n,;]+", content) if part.strip()]
        candidates = parts

    unique_keywords: List[str] = []
    seen = set()
    for keyword in candidates:
        key = keyword.lower()
        if key and key not in seen:
            seen.add(key)
            unique_keywords.append(keyword)
        if len(unique_keywords) >= KEYWORD_TARGET_COUNT:
            break

    return unique_keywords


def clean_descriptor_text(tag, descriptor_prefix: str) -> str:
    if not tag:
        return ""
    text = tag.get_text(" ", strip=True)
    if text.startswith(descriptor_prefix):
        return text[len(descriptor_prefix):].strip()
    return text


def classify_articles(articles: List[Article]) -> Dict[str, Dict[str, List[Article]]]:
    grouped: Dict[str, Dict[str, List[Article]]] = defaultdict(lambda: defaultdict(list))
    for article in articles:
        grouped[article.section_type][article.primary_subject].append(article)
    return grouped


def compute_statistics(articles: List[Article], grouped: Dict[str, Dict[str, List[Article]]]) -> Dict[str, object]:
    total = len(articles)
    total_authorships = sum(len(article.authors) for article in articles)
    unique_authors = len({author.lower() for article in articles for author in article.authors})

    section_counts = {
        section_type: sum(len(subject_articles) for subject_articles in subjects.values())
        for section_type, subjects in grouped.items()
    }

    author_counter: Counter[str] = Counter()
    for article in articles:
        author_counter.update(article.authors)
    top_authors = author_counter.most_common(5)

    keyword_counter: Counter[str] = Counter()
    for article in articles:
        keyword_counter.update([keyword for keyword in article.keywords if keyword])
    top_keywords = keyword_counter.most_common(5)
    if not top_keywords:
        top_keywords = extract_top_phrases(articles, top_n=3)
    avg_authors = (total_authorships / total) if total else 0.0

    return {
        "total": total,
        "total_authorships": total_authorships,
        "unique_authors": unique_authors,
        "section_counts": section_counts,
        "top_authors": top_authors,
        "top_phrases": top_keywords,
        "average_authors": avg_authors,
    }


def filter_by_authors(articles: Iterable[Article], favorite_authors: Iterable[str]) -> List[Article]:
    favorites_lower = [a.lower() for a in favorite_authors]
    if not favorites_lower:
        return []

    matched = []
    for article in articles:
        authors_lower = [a.lower() for a in article.authors]
        if any(fav in author for fav in favorites_lower for author in authors_lower):
            matched.append(article)
    return matched


def filter_by_keywords(articles: Iterable[Article], keywords: Iterable[str]) -> List[Article]:
    keywords_lower = [k.lower() for k in keywords]
    if not keywords_lower:
        return []
    matched = []
    for article in articles:
        haystack = f"{article.title} {article.abstract}".lower()
        if any(keyword in haystack for keyword in keywords_lower):
            matched.append(article)
    return matched


def article_to_dict(article: Article) -> Dict[str, object]:
    return {
        "arxiv_id": article.arxiv_id,
        "title": article.title,
        "abs_url": article.abs_url,
        "pdf_url": article.pdf_url,
        "authors": article.authors,
        "abstract": article.abstract,
        "primary_subject": article.primary_subject,
        "subjects": article.subjects,
        "section_type": article.section_type,
        "submission_date": article.submission_date.date().isoformat(),
        "keywords": article.keywords,
    }










def build_html(payload: Dict[str, object]) -> str:
    sources = payload.get("sources", {})
    default_source_key = payload.get("default_source")
    if not default_source_key or default_source_key not in sources:
        default_source_key = next(iter(sources), "")
    default_source = sources.get(default_source_key, {})
    default_stats = default_source.get("stats", {})
    header_date = default_source.get("date", "")
    total_papers = default_stats.get("total", 0)
    generated_at = payload.get("generated_at", "")
    default_label = default_source.get("label", default_source_key)
    default_url = default_source.get("url", "")

    favorites_default = "\n".join(payload.get("preferences", {}).get("favorite_authors", []))
    keywords_default = "\n".join(payload.get("preferences", {}).get("keywords", []))

    template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>arXiv Daily Digest</title>
  <style>
    :root {
      --bg-surface: #ffffff;
      --bg-app: #f4f6fb;
      --text-primary: #1f2933;
      --text-secondary: #4b5563;
      --brand: #2563eb;
      --brand-soft: rgba(37, 99, 235, 0.12);
      --border: #e2e8f0;
      --danger: #dc2626;
    }
    *, *::before, *::after {
      box-sizing: border-box;
    }
    body {
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      margin: 0;
      background: var(--bg-app);
      color: var(--text-primary);
      line-height: 1.6;
      scroll-behavior: smooth;
    }
    header {
      background: radial-gradient(circle at top left, rgba(37, 99, 235, 0.82), #0f172a);
      color: white;
      padding: 36px 0 48px;
      box-shadow: 0 30px 60px rgba(15, 23, 42, 0.35);
    }
    header .inner {
      max-width: 1320px;
      margin: 0 auto;
      padding: 0 32px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .page-title {
      font-size: clamp(2.2rem, 3vw, 2.8rem);
      font-weight: 700;
      margin: 0;
    }
    .page-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 18px;
      font-size: 0.95rem;
      color: rgba(248, 250, 252, 0.85);
    }
    .page-meta span::before {
      content: "•";
      margin: 0 8px 0 4px;
      opacity: 0.5;
    }
    .page-meta span:first-child::before {
      content: "";
      margin: 0;
    }
    .source-switcher {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 6px;
    }
    .source-button {
      appearance: none;
      border: 1px solid rgba(148, 163, 184, 0.35);
      background: rgba(15, 23, 42, 0.2);
      color: white;
      padding: 6px 16px;
      border-radius: 999px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
    }
    .source-button:hover,
    .source-button:focus {
      background: rgba(37, 99, 235, 0.35);
      border-color: rgba(96, 165, 250, 0.75);
      outline: none;
    }
    .source-button.is-active {
      background: white;
      color: var(--brand);
      border-color: transparent;
      box-shadow: 0 6px 18px rgba(14, 116, 244, 0.35);
      transform: translateY(-1px);
    }
    .display-mode {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 12px;
    }
    .display-mode__label {
      font-size: 0.9rem;
      font-weight: 600;
      color: rgba(248, 250, 252, 0.85);
      margin-right: 4px;
    }
    .display-mode__button {
      appearance: none;
      border: 1px solid rgba(148, 163, 184, 0.35);
      background: rgba(15, 23, 42, 0.2);
      color: white;
      padding: 6px 14px;
      border-radius: 999px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
    }
    .display-mode__button:hover,
    .display-mode__button:focus {
      background: rgba(37, 99, 235, 0.35);
      border-color: rgba(96, 165, 250, 0.75);
      outline: none;
    }
    .display-mode__button.is-active {
      background: white;
      color: var(--brand);
      border-color: transparent;
      box-shadow: 0 6px 18px rgba(14, 116, 244, 0.35);
      transform: translateY(-1px);
    }
    .layout {
      display: flex;
      gap: 32px;
      align-items: flex-start;
      max-width: 1320px;
      margin: -32px auto 48px;
      padding: 0 32px 64px;
    }
    .sidebar {
      position: sticky;
      top: 24px;
      flex: 0 0 300px;
      background: var(--bg-surface);
      border-radius: 20px;
      padding: 24px 20px;
      box-shadow: 0 24px 48px rgba(15, 23, 42, 0.12);
      border: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 18px;
      height: fit-content;
      max-height: calc(100vh - 48px);
    }
    .preferences-card {
      background: linear-gradient(140deg, rgba(37, 99, 235, 0.08), rgba(2, 132, 199, 0.1));
      border-radius: 16px;
      padding: 18px 20px;
      border: 1px solid rgba(15, 23, 42, 0.08);
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .preferences-card h2 {
      margin: 0;
      font-size: 1rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-secondary);
    }
    .preferences-view {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .preferences-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .preferences-label {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-secondary);
    }
    .preferences-empty {
      font-size: 0.9rem;
      color: var(--text-secondary);
    }
    .preferences-edit {
      align-self: flex-start;
      padding: 6px 16px;
      border-radius: 10px;
      border: none;
      background: var(--brand);
      color: white;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .preferences-edit:hover,
    .preferences-edit:focus {
      transform: translateY(-1px);
      box-shadow: 0 8px 20px rgba(37, 99, 235, 0.25);
      outline: none;
    }
    .preferences-card textarea {
      width: 100%;
      min-height: 70px;
      resize: vertical;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.4);
      padding: 10px 12px;
      font-size: 0.9rem;
      font-family: inherit;
      background: rgba(255, 255, 255, 0.82);
      color: var(--text-primary);
    }
    .preferences-actions {
      display: flex;
      gap: 10px;
      margin-top: 6px;
      flex-wrap: wrap;
    }
    .preferences-actions button {
      flex: 1 1 0;
      padding: 8px 12px;
      border-radius: 10px;
      border: none;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .preferences-actions button[type="submit"] {
      background: var(--brand);
      color: white;
      box-shadow: 0 10px 24px rgba(37, 99, 235, 0.32);
    }
    .preferences-actions button[type="submit"]:hover,
    .preferences-actions button[type="submit"]:focus {
      transform: translateY(-1px);
      outline: none;
    }
    .preferences-actions button.preferences-cancel,
    .preferences-actions button.preferences-reset {
      background: rgba(15, 23, 42, 0.08);
      color: var(--text-secondary);
    }
    .preferences-actions button.preferences-cancel:hover,
    .preferences-actions button.preferences-cancel:focus,
    .preferences-actions button.preferences-reset:hover,
    .preferences-actions button.preferences-reset:focus {
      background: rgba(15, 23, 42, 0.15);
      outline: none;
    }
    .preferences-status {
      min-height: 20px;
      font-size: 0.85rem;
      color: var(--text-secondary);
    }
    .nav-title {
      font-size: 0.9rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-secondary);
      margin-top: 4px;
    }
    .sidebar nav {
      overflow-y: auto;
      padding-right: 6px;
      flex: 1;
    }
    .sidebar nav::-webkit-scrollbar {
      width: 6px;
    }
    .sidebar nav::-webkit-scrollbar-thumb {
      background: rgba(148, 163, 184, 0.5);
      border-radius: 999px;
    }
    .nav-list {
      list-style: none;
      padding-left: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .nav-list.nav-level-2 {
      padding-left: 18px;
      gap: 4px;
    }
    .nav-list.nav-level-3 {
      padding-left: 18px;
      gap: 4px;
    }
    .nav-item a {
      display: block;
      padding: 8px 10px;
      border-radius: 10px;
      color: var(--text-secondary);
      text-decoration: none;
      transition: background 0.2s ease, color 0.2s ease;
    }
    .nav-item a:hover,
    .nav-item a:focus {
      background: var(--brand-soft);
      color: var(--brand);
      outline: none;
    }
    .nav-item a.is-active {
      background: var(--brand);
      color: white;
    }
    .content {
      flex: 1;
      min-width: 0;
    }
    .content-section {
      background: var(--bg-surface);
      border-radius: 24px;
      padding: 28px 32px;
      box-shadow: 0 24px 48px rgba(15, 23, 42, 0.08);
      border: 1px solid var(--border);
      margin-bottom: 36px;
      transition: box-shadow 0.25s ease, transform 0.25s ease;
      position: relative;
    }
    .content-section.is-hidden {
      display: none;
    }
    .content-section:not(.is-collapsed):hover {
      box-shadow: 0 28px 60px rgba(15, 23, 42, 0.12);
      transform: translateY(-2px);
    }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }
    .section-header h2 {
      margin: 0;
      font-size: 1.6rem;
    }
    .section-summary {
      margin: 0;
      font-size: 0.95rem;
      color: var(--text-secondary);
    }
    .section-toggle {
      appearance: none;
      border: none;
      background: var(--brand-soft);
      color: var(--brand);
      border-radius: 999px;
      font-weight: 600;
      padding: 6px 18px;
      cursor: pointer;
      transition: background 0.2s ease, transform 0.2s ease;
    }
    .section-toggle:hover,
    .section-toggle:focus {
      background: rgba(37, 99, 235, 0.2);
      outline: none;
      transform: translateY(-1px);
    }
    .section-body {
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .content-section.is-collapsed .section-body {
      display: none;
    }
    .stats-grid {
      display: grid;
      gap: 20px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }
    .stat-card {
      background: linear-gradient(145deg, rgba(37, 99, 235, 0.08), rgba(2, 132, 199, 0.08));
      border-radius: 18px;
      padding: 20px;
      border: 1px solid rgba(15, 23, 42, 0.08);
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .stat-card h3 {
      font-size: 1rem;
      margin: 0;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .stat-card p {
      margin: 0;
      font-size: 1.1rem;
      font-weight: 600;
    }
    .stat-card ul {
      margin: 0;
      padding-left: 18px;
      color: var(--text-secondary);
      font-size: 0.95rem;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .paper {
      background: var(--bg-surface);
      border-radius: 18px;
      padding: 22px 24px;
      border: 1px solid var(--border);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
      cursor: pointer;
      transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
    }
    .paper + .paper {
      margin-top: 18px;
    }
    .paper h3 {
      margin: 0 0 12px 0;
      font-size: 1.2rem;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .paper h3 .quick-view-button {
      flex: 0 0 auto;
    }
    .keyword-tags {
      display: inline-flex;
      gap: 6px;
      flex-wrap: wrap;
      margin: 0 4px;
    }
    .keyword-tag {
      background: rgba(15, 23, 42, 0.08);
      color: var(--text-secondary);
      font-size: 0.75rem;
      letter-spacing: 0.02em;
      padding: 4px 8px;
      border-radius: 999px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .paper h3 a {
      color: inherit;
      text-decoration: none;
    }
    .paper h3 a:hover {
      color: var(--brand);
    }
    .paper .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 0.95rem;
      color: var(--text-secondary);
      margin-bottom: 10px;
    }
    .paper .meta .id {
      font-weight: 600;
      color: var(--brand);
    }
    .paper .subjects {
      font-size: 0.95rem;
      color: var(--text-secondary);
      margin-bottom: 12px;
    }
    .paper .abstract {
      color: var(--text-primary);
      margin-bottom: 16px;
    }
    .paper .links {
      display: flex;
      gap: 16px;
      font-weight: 600;
      flex-wrap: wrap;
      margin: 0;
    }
    .paper .links a {
      color: var(--brand);
      text-decoration: none;
    }
    .paper .links a:hover {
      text-decoration: underline;
    }
    .paper .link-button {
      appearance: none;
      border: 1px solid rgba(37, 99, 235, 0.4);
      background: rgba(37, 99, 235, 0.08);
      color: var(--brand);
      padding: 6px 14px;
      border-radius: 999px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
    }
    .paper .link-button:hover,
    .paper .link-button:focus {
      background: rgba(37, 99, 235, 0.18);
      border-color: rgba(37, 99, 235, 0.55);
      outline: none;
      transform: translateY(-1px);
    }
    body.display-mode-title .paper:not(.paper--expanded) .quick-view-button,
    body.display-mode-authors .paper:not(.paper--expanded) .quick-view-button {
      padding: 4px 10px;
      font-size: 0.85rem;
    }
    .paper:hover {
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6), 0 12px 24px rgba(15, 23, 42, 0.12);
      transform: translateY(-1px);
    }
    .paper.paper--expanded {
      border-color: var(--brand);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6), 0 16px 32px rgba(37, 99, 235, 0.18);
    }
    body.display-mode-title .paper:not(.paper--expanded) .meta,
    body.display-mode-title .paper:not(.paper--expanded) .subjects,
    body.display-mode-title .paper:not(.paper--expanded) .abstract {
      display: none;
    }
    body.display-mode-authors .paper:not(.paper--expanded) .meta .id {
      display: none;
    }
    body.display-mode-authors .paper:not(.paper--expanded) .subjects,
    body.display-mode-authors .paper:not(.paper--expanded) .abstract {
      display: none;
    }
    body.display-mode-title .paper:not(.paper--expanded),
    body.display-mode-authors .paper:not(.paper--expanded) {
      padding: 12px 16px;
      border-radius: 12px;
      border-color: rgba(148, 163, 184, 0.6);
      box-shadow: none;
      background: rgba(255, 255, 255, 0.92);
      display: grid;
      grid-template-columns: 1fr;
      row-gap: 4px;
    }
    body.display-mode-title .paper:not(.paper--expanded) h3,
    body.display-mode-authors .paper:not(.paper--expanded) h3 {
      margin: 0;
      font-size: 1rem;
      line-height: 1.3;
    }
    body.display-mode-title .paper:not(.paper--expanded) h3 a,
    body.display-mode-authors .paper:not(.paper--expanded) h3 a {
      display: inline-block;
      max-width: 100%;
    }
    body.display-mode-authors .paper:not(.paper--expanded) .meta {
      margin-bottom: 0;
      gap: 6px;
      font-size: 0.85rem;
      line-height: 1.3;
    }
    body.display-mode-authors .paper:not(.paper--expanded) .authors {
      font-weight: 500;
      color: var(--text-secondary);
    }
    body.display-mode-title .paper:not(.paper--expanded) + .paper:not(.paper--expanded),
    body.display-mode-authors .paper:not(.paper--expanded) + .paper:not(.paper--expanded) {
      margin-top: 8px;
    }
    body.display-mode-title .paper:not(.paper--expanded) .links,
    body.display-mode-authors .paper:not(.paper--expanded) .links {
      gap: 8px;
    }
    body.display-mode-title .paper:not(.paper--expanded) .links a,
    body.display-mode-authors .paper:not(.paper--expanded) .links a {
      display: none;
    }
    body.display-mode-title .paper:not(.paper--expanded) .links .link-button,
    body.display-mode-authors .paper:not(.paper--expanded) .links .link-button {
      padding: 4px 10px;
      font-size: 0.85rem;
    }
    body.display-mode-title .paper:not(.paper--expanded) .keyword-tag,
    body.display-mode-authors .paper:not(.paper--expanded) .keyword-tag {
      font-size: 0.7rem;
      padding: 3px 6px;
    }
    body.modal-open {
      overflow: hidden;
    }
    .abstract-modal {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1200;
    }
    .abstract-modal.is-open {
      display: flex;
    }
    .abstract-modal__backdrop {
      position: absolute;
      inset: 0;
      background: rgba(15, 23, 42, 0.55);
    }
    .abstract-modal__dialog {
      position: relative;
      width: min(960px, 92vw);
      height: min(80vh, 720px);
      background: white;
      border-radius: 18px;
      box-shadow: 0 40px 80px rgba(15, 23, 42, 0.35);
      border: 1px solid rgba(148, 163, 184, 0.35);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: 1;
    }
    .abstract-modal__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid rgba(226, 232, 240, 0.8);
      gap: 12px;
    }
    .abstract-modal__title {
      margin: 0;
      font-size: 1.1rem;
      font-weight: 600;
      line-height: 1.4;
      color: var(--text-primary);
    }
    .abstract-modal__close {
      appearance: none;
      border: none;
      background: rgba(15, 23, 42, 0.04);
      color: var(--text-secondary);
      padding: 6px 10px;
      border-radius: 10px;
      cursor: pointer;
      font-weight: 600;
      transition: background 0.2s ease, color 0.2s ease;
    }
    .abstract-modal__close:hover,
    .abstract-modal__close:focus {
      background: rgba(37, 99, 235, 0.15);
      color: var(--brand);
      outline: none;
    }
    .abstract-modal__body {
      flex: 1;
      padding: 20px 24px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      overflow-y: auto;
      background: linear-gradient(135deg, rgba(248, 250, 252, 0.9), rgba(241, 245, 249, 0.6));
    }
    .abstract-modal__meta {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 0.92rem;
      color: var(--text-secondary);
    }
    .abstract-modal__id {
      font-weight: 600;
      color: var(--brand);
    }
    .abstract-modal__authors {
      flex: 1 1 100%;
    }
    .abstract-modal__subjects {
      flex: 1 1 100%;
    }
    .abstract-modal__abstract {
      font-size: 0.95rem;
      color: var(--text-primary);
      line-height: 1.65;
      white-space: pre-line;
    }
    .abstract-modal__actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: auto;
    }
    .abstract-modal__actions a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 16px;
      border-radius: 999px;
      font-weight: 600;
      text-decoration: none;
      transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
    }
    .abstract-modal__actions a.primary {
      background: var(--brand);
      color: white;
      box-shadow: 0 10px 24px rgba(37, 99, 235, 0.28);
    }
    .abstract-modal__actions a.primary:hover,
    .abstract-modal__actions a.primary:focus {
      background: #1d4ed8;
      transform: translateY(-1px);
      outline: none;
    }
    .abstract-modal__actions a.secondary {
      border: 1px solid rgba(37, 99, 235, 0.35);
      color: var(--brand);
      background: rgba(37, 99, 235, 0.1);
    }
    .abstract-modal__actions a.secondary:hover,
    .abstract-modal__actions a.secondary:focus {
      background: rgba(37, 99, 235, 0.18);
      outline: none;
      transform: translateY(-1px);
    }
    .empty-state {
      background: var(--bg-surface);
      border: 1px dashed var(--border);
      border-radius: 14px;
      padding: 18px 20px;
      color: var(--text-secondary);
    }
    .chip-set {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 2px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      padding: 4px 12px;
      border-radius: 999px;
      background: var(--brand-soft);
      color: var(--brand);
      font-size: 0.85rem;
      font-weight: 600;
    }
    .watcher-summary {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 0.93rem;
      color: var(--text-secondary);
    }
    .subject-grid {
      display: grid;
      gap: 28px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .category-block {
      padding: 12px 0 4px;
      border-top: 1px solid rgba(148, 163, 184, 0.3);
    }
    .category-block:first-of-type {
      border-top: none;
      padding-top: 0;
    }
    .category-block__header {
      display: flex;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 18px;
    }
    .category-block__header h3 {
      margin: 0;
      font-size: 1.35rem;
    }
    .subject-group {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 18px;
      border-radius: 16px;
      border: 1px solid rgba(148, 163, 184, 0.2);
      background: linear-gradient(145deg, rgba(255, 255, 255, 0.95), rgba(241, 245, 249, 0.6));
    }
    .subject-group__header {
      display: flex;
      align-items: baseline;
      gap: 12px;
    }
    .subject-group__header h4 {
      margin: 0;
      font-size: 1.05rem;
    }
    .count-chip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 2px 10px;
      border-radius: 999px;
      background: var(--brand-soft);
      color: var(--brand);
      font-size: 0.85rem;
      font-weight: 600;
    }
    .skip-link {
      position: absolute;
      top: -48px;
      left: 16px;
      background: var(--brand);
      color: white;
      padding: 10px 16px;
      border-radius: 8px;
      text-decoration: none;
      font-weight: 600;
      transition: top 0.2s ease;
      z-index: 10;
    }
    .skip-link:focus {
      top: 16px;
    }
    footer {
      background: #0f172a;
      color: rgba(248, 250, 252, 0.9);
      padding: 24px 32px;
      font-size: 0.9rem;
      text-align: center;
    }
    footer a {
      color: #93c5fd;
      text-decoration: none;
    }
    footer a:hover {
      text-decoration: underline;
    }
    .noscript-warning {
      max-width: 960px;
      margin: 32px auto;
      padding: 20px;
      background: rgba(254, 240, 138, 0.35);
      border: 1px solid rgba(250, 204, 21, 0.6);
      border-radius: 12px;
      color: #92400e;
      font-size: 0.95rem;
    }
    @media (max-width: 960px) {
      .layout {
        flex-direction: column;
        padding: 0 20px 40px;
        gap: 24px;
      }
      .sidebar {
        position: static;
        width: 100%;
        max-height: none;
      }
      .content {
        width: 100%;
      }
      .nav-list {
        flex-direction: row;
        flex-wrap: wrap;
        gap: 8px;
      }
      .nav-list.nav-level-2,
      .nav-list.nav-level-3 {
        width: 100%;
        padding-left: 0;
      }
      .nav-list.nav-level-2 .nav-item a,
      .nav-list.nav-level-3 .nav-item a {
        padding-left: 14px;
      }
    }
    @media (max-width: 640px) {
      header .inner {
        padding: 0 20px;
      }
      .content-section {
        padding: 22px;
      }
      .stats-grid {
        grid-template-columns: 1fr;
      }
      .subject-grid {
        grid-template-columns: 1fr;
      }
      .layout {
        margin-top: -24px;
      }
      .preferences-actions {
        flex-direction: column;
      }
      .preferences-actions button {
        flex: 1 1 auto;
      }
      .source-switcher {
        justify-content: flex-start;
      }
      .display-mode {
        justify-content: flex-start;
      }
    }
  </style>
</head>
<body class="display-mode-authors">
  <a class="skip-link" href="#main-content">Skip to content</a>
  <header>
    <div class="inner">
      <h1 class="page-title">arXiv cs Daily Digest</h1>
      <div class="page-meta">
        <span id="meta-source">Source: __DEFAULT_LABEL__</span>
        <span id="meta-date">Date: __HEADER_DATE__</span>
        <span id="meta-generated">Generated at: __GENERATED_AT__</span>
        <span id="meta-total">Total papers: __TOTAL_PAPERS__</span>
      </div>
      <div class="source-switcher" id="source-switcher" role="group" aria-label="Select arXiv category"></div>
      <div class="display-mode" id="display-mode-controls" role="group" aria-label="Select paper layout"></div>
    </div>
  </header>
  <noscript>
    <div class="noscript-warning">This dashboard requires JavaScript to filter sources and update preferences. Please enable JavaScript in your browser.</div>
  </noscript>
  <div class="layout">
    <aside class="sidebar">
      <div class="preferences-card">
        <h2>Tracking</h2>
        <div id="preferences-view" class="preferences-view">
          <div class="preferences-group">
            <span class="preferences-label">Favorite authors</span>
            <div class="chip-set" id="favorite-authors-view"></div>
          </div>
          <div class="preferences-group">
            <span class="preferences-label">Watched keywords</span>
            <div class="chip-set" id="keywords-view"></div>
          </div>
          <button type="button" id="edit-preferences" class="preferences-edit">Edit</button>
          <p class="preferences-status" id="preferences-status-view" role="status" aria-live="polite"></p>
        </div>
        <form id="preferences-form" hidden>
          <label for="favorite-authors-input" class="preferences-label">Favorite authors</label>
          <textarea id="favorite-authors-input" placeholder="One per line or comma separated">__FAVORITES_DEFAULT__</textarea>
          <label for="keywords-input" class="preferences-label">Watched keywords</label>
          <textarea id="keywords-input" placeholder="One per line or comma separated">__KEYWORDS_DEFAULT__</textarea>
          <div class="preferences-actions">
            <button type="submit">Save</button>
            <button type="button" id="cancel-preferences" class="preferences-cancel">Cancel</button>
            <button type="button" id="reset-preferences" class="preferences-reset">Reset</button>
          </div>
          <p class="preferences-status" id="preferences-status" role="status" aria-live="polite"></p>
        </form>
      </div>
      <div class="nav-title">On this page</div>
      <nav aria-label="Section navigation"></nav>
    </aside>
    <main id="main-content" class="content">
      <section id="overview" class="content-section is-collapsed is-hidden">
        <div class="section-header">
          <h2>All Papers</h2>
          <p class="section-summary" id="overview-summary"></p>
        </div>
        <div class="section-body" id="overview-body"></div>
      </section>
      <section id="stats" class="content-section" data-collapsible="true">
        <div class="section-header">
          <h2>Statistics</h2>
          <button type="button" class="section-toggle" data-target="stats" aria-expanded="true">Hide section</button>
        </div>
        <div class="section-body" id="stats-body"></div>
      </section>
      <section id="favorite" class="content-section is-collapsed is-hidden" data-collapsible="true">
        <div class="section-header">
          <h2>Favorite Authors</h2>
          <button type="button" class="section-toggle" data-target="favorite" aria-expanded="false">Show section</button>
        </div>
        <div class="section-body" id="favorite-body"></div>
      </section>
      <section id="keyword" class="content-section is-collapsed is-hidden" data-collapsible="true">
        <div class="section-header">
          <h2>Watched Keywords</h2>
          <button type="button" class="section-toggle" data-target="keyword" aria-expanded="false">Show section</button>
        </div>
        <div class="section-body" id="keywords-body"></div>
      </section>
      <section id="categories" class="content-section is-collapsed is-hidden" data-collapsible="true">
        <div class="section-header">
          <h2>Browse by Category</h2>
          <button type="button" class="section-toggle" data-target="categories" aria-expanded="false">Show section</button>
        </div>
        <div class="section-body" id="categories-body"></div>
      </section>
    </main>
  </div>
  <div class="abstract-modal" id="abstract-modal" aria-hidden="true">
    <div class="abstract-modal__backdrop" data-modal-dismiss="true"></div>
    <div class="abstract-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="abstract-modal-title">
      <div class="abstract-modal__header">
        <h2 class="abstract-modal__title" id="abstract-modal-title">Preview abstract</h2>
        <button type="button" class="abstract-modal__close" id="abstract-modal-close">Close</button>
      </div>
      <div class="abstract-modal__body" id="abstract-modal-body">
        <div class="abstract-modal__meta">
          <span class="abstract-modal__id" id="abstract-modal-id"></span>
          <span class="abstract-modal__authors" id="abstract-modal-authors"></span>
          <span class="abstract-modal__subjects" id="abstract-modal-subjects"></span>
        </div>
        <div class="abstract-modal__abstract" id="abstract-modal-abstract"></div>
        <div class="abstract-modal__actions">
          <a href="#" target="_blank" rel="noopener" class="primary" id="abstract-modal-original">Open on arXiv</a>
          <a href="#" target="_blank" rel="noopener" class="secondary" id="abstract-modal-pdf" hidden>Download PDF</a>
        </div>
      </div>
    </div>
  </div>
  <footer>
    Source: <a id="footer-source" href="__FOOTER_URL__" target="_blank" rel="noopener">__FOOTER_LABEL__</a>
  </footer>
  <script type="application/json" id="digest-data">__PAYLOAD_JSON__</script>
  <script>
__SCRIPT_CONTENT__
  </script>
</body>
</html>
"""

    script_path = Path(__file__).with_name("script.js")
    if script_path.exists():
        script_content = script_path.read_text(encoding="utf-8")
    else:
        script_content = "(() => { console.error('arXiv digest: script.js not found.'); })();"


    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") 

    html_output = template
    html_output = html_output.replace("__DEFAULT_LABEL__", html.escape(default_label))
    html_output = html_output.replace("__HEADER_DATE__", html.escape(header_date))
    html_output = html_output.replace("__GENERATED_AT__", html.escape(generated_at))
    html_output = html_output.replace("__TOTAL_PAPERS__", str(total_papers))
    html_output = html_output.replace("__FAVORITES_DEFAULT__", html.escape(favorites_default, quote=False))
    html_output = html_output.replace("__KEYWORDS_DEFAULT__", html.escape(keywords_default, quote=False))
    html_output = html_output.replace("__FOOTER_LABEL__", html.escape(default_label))
    html_output = html_output.replace("__FOOTER_URL__", html.escape(default_url, quote=True))
    html_output = html_output.replace("__PAYLOAD_JSON__", payload_json)
    html_output = html_output.replace("__SCRIPT_CONTENT__", script_content)

    return html_output


def write_output(content: str, output_path: Path) -> None:
    output_path.write_text(content, encoding="utf-8")
    print(f"Wrote HTML output to {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a daily arXiv digest webpage")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Path to the JSON config file (with favorite_authors and keywords).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("index.html"),
        help="Output HTML file path.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date in YYYY-MM-DD format; defaults to today.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit("Invalid date format: expected YYYY-MM-DD")
    else:
        target_date = datetime.now().date()

    sources_payload: Dict[str, Dict[str, object]] = {}
    default_source_key = next(iter(ARXIV_SOURCES))

    session = requests.Session()
    session.headers.update(HTTP_HEADERS)

    for source_key, meta in ARXIV_SOURCES.items():
        soup = fetch_recent_page(meta["url"], session)
        articles = parse_articles_for_date(target_date, soup, session)
        if not articles:
            raise SystemExit(f"No papers were parsed from {meta['url']}. Please try again later.")

        grouped = classify_articles(articles)
        stats = compute_statistics(articles, grouped)

        actual_dates = sorted({article.submission_date.date() for article in articles})
        page_date = actual_dates[0] if actual_dates else target_date

        sources_payload[source_key] = {
            "label": meta["label"],
            "url": meta["url"],
            "date": page_date.isoformat(),
            "articles": [article_to_dict(article) for article in articles],
            "stats": stats,
        }

    session.close()

    payload = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "sources": sources_payload,
        "preferences": config,
        "default_source": default_source_key,
    }

    html = build_html(payload)
    write_output(html, args.output)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
