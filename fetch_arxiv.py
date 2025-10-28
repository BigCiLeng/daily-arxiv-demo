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
import sys
from collections import Counter, defaultdict
import re
from dataclasses import dataclass
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


def fetch_recent_page(list_url: str) -> BeautifulSoup:
    try:
        response = requests.get(list_url, headers=HTTP_HEADERS, timeout=20)
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


def parse_articles_for_date(target_date: date, soup: BeautifulSoup) -> List[Article]:
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
            article = extract_article(dt, dd, section_type, section_date)
            if article:
                articles.append(article)

    return articles


def extract_article(dt_tag, dd_tag, section_type: str, section_date: date) -> Article | None:
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
    )


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

    top_phrases = extract_top_phrases(articles, top_n=3)
    avg_authors = (total_authorships / total) if total else 0.0

    return {
        "total": total,
        "total_authorships": total_authorships,
        "unique_authors": unique_authors,
        "section_counts": section_counts,
        "top_authors": top_authors,
        "top_phrases": top_phrases,
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
    }
    .paper + .paper {
      margin-top: 18px;
    }
    .paper h3 {
      margin: 0 0 12px 0;
      font-size: 1.2rem;
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
    }
    .paper .links a {
      color: var(--brand);
      text-decoration: none;
    }
    .paper .links a:hover {
      text-decoration: underline;
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
    }
  </style>
</head>
<body>
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

    script_content = dedent("""
    (() => {
      const RAW_DATA = JSON.parse(document.getElementById('digest-data').textContent);
      const SOURCE_STORAGE_KEY = 'arxivDigestSource';
      const PREF_STORAGE_KEY = 'arxivDigestPreferences';

      const SOURCE_KEYS = Object.keys(RAW_DATA.sources || {});
      if (!SOURCE_KEYS.length) {
        return;
      }
      const generatedAt = RAW_DATA.generated_at || '';
      const initialPreferences = normalizePreferences(RAW_DATA.preferences || {});

      const state = {
        source: loadStoredSource(),
        preferences: loadStoredPreferences(),
        isEditingPreferences: false,
        activeSection: 'stats',
      };

      if (!RAW_DATA.sources[state.source]) {
        state.source = RAW_DATA.default_source && RAW_DATA.sources[RAW_DATA.default_source]
          ? RAW_DATA.default_source
          : SOURCE_KEYS[0];
      }

      const elements = {
        sourceSwitcher: document.getElementById('source-switcher'),
        nav: document.querySelector('.sidebar nav'),
        preferencesView: document.getElementById('preferences-view'),
        preferencesForm: document.getElementById('preferences-form'),
        favoriteAuthorsView: document.getElementById('favorite-authors-view'),
        keywordsView: document.getElementById('keywords-view'),
        favoritesInput: document.getElementById('favorite-authors-input'),
        keywordsInput: document.getElementById('keywords-input'),
        editPreferences: document.getElementById('edit-preferences'),
        cancelPreferences: document.getElementById('cancel-preferences'),
        resetPreferences: document.getElementById('reset-preferences'),
        preferencesStatusView: document.getElementById('preferences-status-view'),
        preferencesStatus: document.getElementById('preferences-status'),
        overviewSummary: document.getElementById('overview-summary'),
        overviewBody: document.getElementById('overview-body'),
        statsBody: document.getElementById('stats-body'),
        favoritesBody: document.getElementById('favorite-body'),
        keywordsBody: document.getElementById('keywords-body'),
        categoriesBody: document.getElementById('categories-body'),
        headerSource: document.getElementById('meta-source'),
        headerDate: document.getElementById('meta-date'),
        headerGenerated: document.getElementById('meta-generated'),
        headerTotal: document.getElementById('meta-total'),
        footerSource: document.getElementById('footer-source'),
      };

      if (elements.editPreferences) {
        elements.editPreferences.addEventListener('click', () => {
          state.isEditingPreferences = true;
          setStatus('');
          renderPreferencesPanel();
          if (elements.favoritesInput) {
            elements.favoritesInput.focus();
          }
        });
      }

      if (elements.cancelPreferences) {
        elements.cancelPreferences.addEventListener('click', () => {
          state.isEditingPreferences = false;
          setStatus('');
          renderPreferencesPanel();
        });
      }

      if (elements.preferencesForm) {
        elements.preferencesForm.addEventListener('submit', (event) => {
          event.preventDefault();
          const nextPrefs = normalizePreferences({
            favorite_authors: elements.favoritesInput ? elements.favoritesInput.value : '',
            keywords: elements.keywordsInput ? elements.keywordsInput.value : '',
          });
          state.preferences = nextPrefs;
          state.isEditingPreferences = false;
          savePreferences(nextPrefs);
          renderAll({ resetActiveSection: false });
          setStatus('Preferences saved.');
        });
      }

      if (elements.resetPreferences) {
        elements.resetPreferences.addEventListener('click', () => {
          state.preferences = normalizePreferences(initialPreferences);
          state.isEditingPreferences = true;
          savePreferences(state.preferences);
          renderAll({ resetActiveSection: false });
          setStatus('Preferences reset to defaults.');
        });
      }

      renderAll({ resetActiveSection: true });

      function renderAll(options = {}) {
        if (options.resetActiveSection) {
          state.activeSection = 'stats';
        }
        renderSourceButtons();
        renderPreferencesPanel();

        const sourceData = RAW_DATA.sources[state.source];
        if (!sourceData) {
          return;
        }

        const articles = sourceData.articles || [];
        updateHeader(sourceData);
        const overviewCount = renderOverview(sourceData, articles);
        renderStats(sourceData);
        const favoriteCount = renderFavorites(sourceData, articles);
        const keywordCount = renderKeywords(sourceData, articles);
        const categoriesNavItems = renderCategories(sourceData, articles);
        renderNavigation(sourceData, overviewCount, favoriteCount, keywordCount, categoriesNavItems);
        updateFooter(sourceData);
        attachSectionHandlers();
        setActiveSection(state.activeSection);
      }

      function renderSourceButtons() {
        const container = elements.sourceSwitcher;
        if (!container) return;
        container.innerHTML = SOURCE_KEYS.map((key) => {
          const label = RAW_DATA.sources[key].label || key;
          const active = key === state.source ? 'is-active' : '';
          return `<button type="button" class="source-button ${active}" data-source="${key}">${escapeHtml(label)}</button>`;
        }).join('');
        Array.from(container.querySelectorAll('button[data-source]')).forEach((button) => {
          button.addEventListener('click', () => {
            const nextSource = button.getAttribute('data-source');
            if (!nextSource || nextSource === state.source || !RAW_DATA.sources[nextSource]) return;
            state.source = nextSource;
            saveSource(nextSource);
            setStatus('');
            state.activeSection = 'stats';
            renderAll({ resetActiveSection: true });
          });
        });
      }

      function renderPreferencesPanel() {
        const favorites = state.preferences.favorite_authors || [];
        const keywords = state.preferences.keywords || [];

        if (elements.favoriteAuthorsView) {
          elements.favoriteAuthorsView.innerHTML = favorites.length
            ? favorites.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('')
            : '<span class="preferences-empty">None</span>';
        }
        if (elements.keywordsView) {
          elements.keywordsView.innerHTML = keywords.length
            ? keywords.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('')
            : '<span class="preferences-empty">None</span>';
        }

        if (state.isEditingPreferences) {
          if (elements.preferencesView) elements.preferencesView.hidden = true;
          if (elements.preferencesForm) elements.preferencesForm.hidden = false;
          updatePreferenceInputs();
        } else {
          if (elements.preferencesView) elements.preferencesView.hidden = false;
          if (elements.preferencesForm) elements.preferencesForm.hidden = true;
        }
      }

      function renderOverview(sourceData, articles) {
        const body = elements.overviewBody;
        if (!body) return 0;
        const summary = elements.overviewSummary;
        const total = articles.length;
        const sourceLabel = sourceData.label || state.source;
        const plural = total === 1 ? '' : 's';
        if (summary) {
          summary.textContent = total + ' paper' + plural + ' from ' + sourceLabel + '.';
        }
        body.innerHTML = articles.map(renderArticleCard).join('') || '<p class="empty-state">No papers available.</p>';
        return total;
      }

      function renderStats(sourceData) {
        const body = elements.statsBody;
        if (!body) return;
        const stats = sourceData.stats || {};
        const total = stats.total || 0;
        const uniqueAuthors = stats.unique_authors || 0;
        const totalAuthorships = stats.total_authorships || 0;
        const averageAuthors = (stats.average_authors || 0).toFixed(2);
        const topAuthors = (stats.top_authors || []).map(([name, count]) => `<li>${escapeHtml(name)} (${count})</li>`).join('') || '<li>None</li>';
        const topPhrases = (stats.top_phrases || []).map(([phrase, count]) => `<li>${escapeHtml(phrase)} (${count})</li>`).join('') || '<li>None</li>';
        const sectionCounts = Object.entries(stats.section_counts || {})
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([section, count]) => `<li>${escapeHtml(section)} (${count})</li>`)
          .join('') || '<li>None</li>';

        body.innerHTML = `
          <div class="stats-grid">
            <div class="stat-card">
              <h3>Papers</h3>
              <p>Total papers: ${total}</p>
              <p>Avg authors per paper: ${averageAuthors}</p>
            </div>
            <div class="stat-card">
              <h3>Authors</h3>
              <p>Unique authors: ${uniqueAuthors}</p>
              <p>Total author mentions: ${totalAuthorships}</p>
            </div>
            <div class="stat-card">
              <h3>Top Authors</h3>
              <ul>${topAuthors}</ul>
            </div>
            <div class="stat-card">
              <h3>Popular Phrases</h3>
              <ul>${topPhrases}</ul>
            </div>
            <div class="stat-card">
              <h3>Section Breakdown</h3>
              <ul>${sectionCounts}</ul>
            </div>
          </div>
        `;
      }

      function renderFavorites(sourceData, articles) {
        const body = elements.favoritesBody;
        if (!body) return 0;
        const favorites = state.preferences.favorite_authors || [];
        const matches = filterByFavoriteAuthors(articles, favorites);
        body.innerHTML = buildWatcherSectionContent(favorites, matches, 'Add authors in the sidebar to highlight researchers you care about.');
        return matches.length;
      }

      function renderKeywords(sourceData, articles) {
        const body = elements.keywordsBody;
        if (!body) return 0;
        const keywords = state.preferences.keywords || [];
        const matches = filterByKeywords(articles, keywords);
        body.innerHTML = buildWatcherSectionContent(keywords, matches, 'Track important topics by adding keywords in the sidebar.');
        return matches.length;
      }

      function buildWatcherSectionContent(items, matches, emptyMessage) {
        const chips = (items || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join('');
        const summary = items.length
          ? `<div class="watcher-summary">Watching <strong>${items.length}</strong> entr${items.length === 1 ? 'y' : 'ies'}.<div class="chip-set">${chips}</div></div>`
          : `<div class="watcher-summary">${emptyMessage}</div>`;
        const articlesHtml = matches.length
          ? matches.map(renderArticleCard).join('')
          : '<p class="empty-state">No papers matched the current filters.</p>';
        return `${summary}${articlesHtml}`;
      }

      function renderCategories(sourceData, articles) {
        const body = elements.categoriesBody;
        if (!body) return [];
        const groups = buildSectionGrouping(articles);
        if (!groups.length) {
          body.innerHTML = '<p class="empty-state">No categories available for this source.</p>';
          return [];
        }
        const sectionsHtml = groups.map(({ sectionId, sectionLabel, count, subjects }) => {
          const subjectHtml = subjects.map(({ subjectId, subjectLabel, items }) => `
            <div class="subject-group" id="${subjectId}">
              <div class="subject-group__header">
                <h4>${escapeHtml(subjectLabel)}</h4>
                <span class="count-chip">${formatCount(items.length)}</span>
              </div>
              ${items.map(renderArticleCard).join('')}
            </div>
          `).join('');
          return `
            <div class="category-block" id="${sectionId}">
              <div class="category-block__header">
                <h3>${escapeHtml(sectionLabel)}</h3>
                <span class="count-chip">${formatCount(count)}</span>
              </div>
              <div class="subject-grid">
                ${subjectHtml}
              </div>
            </div>
          `;
        }).join('');
        body.innerHTML = sectionsHtml;
        return groups.map(({ sectionId, sectionLabel, count, subjects }) => ({
          id: sectionId,
          label: `${sectionLabel} (${count})`,
          children: subjects.map(({ subjectId, subjectLabel, items }) => ({
            id: subjectId,
            label: `${subjectLabel} (${items.length})`,
          })),
        }));
      }

      function renderNavigation(sourceData, overviewCount, favoriteCount, keywordCount, categoriesNavItems) {
        if (!elements.nav) return;
        const navItems = [
          { id: 'stats', label: 'Statistics' },
          { id: 'overview', label: `All Papers (${overviewCount})` },
          { id: 'favorite', label: `Favorite Authors (${favoriteCount})` },
          { id: 'keyword', label: `Watched Keywords (${keywordCount})` },
          { id: 'categories', label: 'Browse by Category', children: categoriesNavItems },
        ];
        elements.nav.innerHTML = buildNavList(navItems);
      }

      function buildNavList(items, level = 1) {
        if (!items || !items.length) return '';
        const listClass = `nav-list nav-level-${level}`;
        const inner = items.map((item) => {
          const children = buildNavList(item.children || [], level + 1);
          return `<li class="nav-item nav-level-${level}"><a href="#${item.id}">${escapeHtml(item.label)}</a>${children}</li>`;
        }).join('');
        return `<ul class="${listClass}">${inner}</ul>`;
      }

      function updateHeader(sourceData) {
        if (elements.headerSource) elements.headerSource.textContent = `Source: ${sourceData.label || state.source}`;
        if (elements.headerDate) elements.headerDate.textContent = `Date: ${sourceData.date || ''}`;
        if (elements.headerGenerated) elements.headerGenerated.textContent = `Generated at: ${generatedAt}`;
        if (elements.headerTotal) elements.headerTotal.textContent = `Total papers: ${(sourceData.stats && sourceData.stats.total) || 0}`;
      }

      function updateFooter(sourceData) {
        if (!elements.footerSource) return;
        elements.footerSource.textContent = sourceData.label || state.source;
        if (sourceData.url) {
          elements.footerSource.setAttribute('href', sourceData.url);
        }
      }

      function attachSectionHandlers() {
        const toggles = Array.from(document.querySelectorAll('.section-toggle'));
        toggles.forEach((toggle) => {
          toggle.onclick = () => {
            const targetId = toggle.getAttribute('data-target');
            if (!targetId) return;
            const section = document.getElementById(targetId);
            if (!section) return;
            const willExpand = section.classList.contains('is-collapsed');
            setSectionState(section, willExpand);
            if (willExpand) {
              state.activeSection = section.id;
              setActiveSection(section.id);
            }
          };
        });
        const navLinks = elements.nav ? Array.from(elements.nav.querySelectorAll('a[href^="#"]')) : [];
        navLinks.forEach((link) => {
          link.onclick = (event) => {
            const href = link.getAttribute('href');
            if (!href || !href.startsWith('#')) return;
            const targetId = href.slice(1);
            const targetElement = document.getElementById(targetId);
            if (!targetElement) return;
            const container = targetElement.classList.contains('content-section')
              ? targetElement
              : targetElement.closest('.content-section');
            if (!container) return;
            event.preventDefault();
            state.activeSection = container.id;
            setActiveSection(container.id, targetElement);
          };
        });
      }

      function setActiveSection(sectionId, focusTarget) {
        state.activeSection = sectionId || 'stats';
        const sections = Array.from(document.querySelectorAll('.content-section'));
        sections.forEach((section) => {
          const isActive = section.id === state.activeSection;
          section.classList.toggle('is-hidden', !isActive);
          if (section.dataset.collapsible === 'true') {
            setSectionState(section, isActive);
          } else {
            section.classList.toggle('is-collapsed', !isActive);
          }
        });
        const activeSection = document.getElementById(state.activeSection);
        if (activeSection) {
          const scrollTarget = focusTarget && activeSection.contains(focusTarget) ? focusTarget : activeSection;
          expandAncestors(scrollTarget);
          scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        if (elements.nav) {
          const navLinks = Array.from(elements.nav.querySelectorAll('a[href^="#"]'));
          navLinks.forEach((link) => {
            const href = link.getAttribute('href');
            const id = href ? href.slice(1) : '';
            link.classList.toggle('is-active', id === state.activeSection);
          });
        }
      }

      function setSectionState(section, expanded) {
        if (expanded) {
          section.classList.add('is-expanded');
          section.classList.remove('is-collapsed');
        } else {
          section.classList.add('is-collapsed');
          section.classList.remove('is-expanded');
        }
        const toggle = section.querySelector('.section-toggle');
        if (toggle) {
          toggle.setAttribute('aria-expanded', String(expanded));
          toggle.textContent = expanded ? 'Hide section' : 'Show section';
        }
      }

      function expandAncestors(element) {
        if (!element) return;
        let parent = element.closest('[data-collapsible="true"]');
        while (parent) {
          setSectionState(parent, true);
          parent = parent.parentElement ? parent.parentElement.closest('[data-collapsible="true"]') : null;
        }
      }

      function renderArticleCard(article) {
        const authors = escapeHtml(article.authors.join(', '));
        const subjects = escapeHtml(article.subjects.join('; '));
        const abstract = escapeHtml(article.abstract);
        const pdfLink = article.pdf_url ? `<a href="${article.pdf_url}" target="_blank" rel="noopener">PDF</a>` : '';
        return `
          <article class="paper">
            <h3><a href="${article.abs_url}" target="_blank" rel="noopener">${escapeHtml(article.title)}</a></h3>
            <p class="meta">
              <span class="id">${escapeHtml(article.arxiv_id)}</span>
              <span class="authors">${authors}</span>
            </p>
            <p class="subjects">${subjects}</p>
            <p class="abstract">${abstract}</p>
            <p class="links"><a href="${article.abs_url}" target="_blank" rel="noopener">Abstract</a> ${pdfLink}</p>
          </article>
        `;
      }

      function buildSectionGrouping(articles) {
        const sections = new Map();
        articles.forEach((article) => {
          const sectionKey = article.section_type || 'Other';
          const subjectKey = article.primary_subject || 'Other';
          if (!sections.has(sectionKey)) {
            sections.set(sectionKey, new Map());
          }
          const subjectMap = sections.get(sectionKey);
          if (!subjectMap.has(subjectKey)) {
            subjectMap.set(subjectKey, []);
          }
          subjectMap.get(subjectKey).push(article);
        });

        return Array.from(sections.entries())
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([sectionName, subjectMap]) => {
            const sectionId = `category-${slugify(sectionName)}`;
            const subjects = Array.from(subjectMap.entries())
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([subjectName, items]) => ({
                subjectId: `${sectionId}-${slugify(subjectName, 'subject')}`,
                subjectLabel: subjectName,
                items,
              }));
            const count = subjects.reduce((sum, entry) => sum + entry.items.length, 0);
            return {
              sectionId,
              sectionLabel: sectionName,
              count,
              subjects,
            };
          });
      }

      function filterByFavoriteAuthors(articles, favoriteAuthors) {
        const favorites = (favoriteAuthors || []).map((name) => name.toLowerCase()).filter(Boolean);
        if (!favorites.length) return [];
        return articles.filter((article) => {
          const authorLower = article.authors.map((name) => name.toLowerCase());
          return favorites.some((fav) => authorLower.some((author) => author.includes(fav)));
        });
      }

      function filterByKeywords(articles, keywords) {
        const needles = (keywords || []).map((kw) => kw.toLowerCase()).filter(Boolean);
        if (!needles.length) return [];
        return articles.filter((article) => {
          const haystack = `${article.title} ${article.abstract}`.toLowerCase();
          return needles.some((needle) => haystack.includes(needle));
        });
      }

      function updatePreferenceInputs() {
        if (elements.favoritesInput) {
          elements.favoritesInput.value = state.preferences.favorite_authors.join('\\n');
        }
        if (elements.keywordsInput) {
          elements.keywordsInput.value = state.preferences.keywords.join('\\n');
        }
      }

      function setStatus(message) {
        if (elements.preferencesStatus) {
          elements.preferencesStatus.textContent = state.isEditingPreferences ? message : '';
        }
        if (elements.preferencesStatusView) {
          elements.preferencesStatusView.textContent = state.isEditingPreferences ? '' : message;
        }
      }

      function escapeHtml(value) {
        return String(value || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function slugify(text, fallback = 'section') {
        const slug = String(text || '')
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-+|-+$/g, '');
        return slug || fallback;
      }

      function formatCount(value) {
        const count = Number(value) || 0;
        return `${count} paper${count === 1 ? '' : 's'}`;
      }

      function normalizePreferences(raw) {
        const normalizeList = (value) => {
          if (Array.isArray(value)) {
            const cleaned = value.map((item) => String(item).trim()).filter((item) => item);
            return Array.from(new Set(cleaned));
          }
          if (typeof value === 'string') {
            const cleaned = value
              .split(/[\\n,]/)
              .map((item) => item.trim())
              .filter((item) => item);
            return Array.from(new Set(cleaned));
          }
          return [];
        };
        return {
          favorite_authors: normalizeList(raw.favorite_authors),
          keywords: normalizeList(raw.keywords),
        };
      }

      function loadStoredSource() {
        try {
          return localStorage.getItem(SOURCE_STORAGE_KEY) || '';
        } catch (_) {
          return '';
        }
      }

      function saveSource(value) {
        try {
          localStorage.setItem(SOURCE_STORAGE_KEY, value);
        } catch (_) {}
      }

      function loadStoredPreferences() {
        try {
          const raw = localStorage.getItem(PREF_STORAGE_KEY);
          return raw ? normalizePreferences(JSON.parse(raw)) : normalizePreferences(initialPreferences);
        } catch (_) {
          return normalizePreferences(initialPreferences);
        }
      }

      function savePreferences(prefs) {
        try {
          localStorage.setItem(PREF_STORAGE_KEY, JSON.stringify(prefs));
        } catch (_) {}
      }
    })();
    """)

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

    for source_key, meta in ARXIV_SOURCES.items():
        soup = fetch_recent_page(meta["url"])
        articles = parse_articles_for_date(target_date, soup)
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
