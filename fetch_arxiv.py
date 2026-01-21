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
ARTICLE_INSIGHT_CACHE: Dict[str, Tuple[List[str], str]] = {}

# KEYWORD_API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
# KEYWORD_API_KEY = os.getenv("OPENROUTER_API_KEY", "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").strip()
# KEYWORD_API_MODEL = os.getenv("OPENROUTER_KEYWORD_MODEL", "deepseek/deepseek-r1-0528-qwen3-8b:free")
KEYWORD_API_URL = os.getenv("OPENROUTER_API_URL", "https://api.siliconflow.cn/v1/chat/completions")
KEYWORD_API_KEY = os.getenv("OPENROUTER_API_KEY")
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
JSONISH_REPLACEMENTS = {
    "；": ",",
    "，": ",",
    "：": ":",
    "“": '"',
    "”": '"',
    "’": "'",
    "‘": "'",
}


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
    summary: str = ""


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
    keywords, summary = fetch_keywords_and_summary(abstract, session)

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
        summary=summary,
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


def fetch_keywords_and_summary(abstract: str, session: requests.Session) -> Tuple[List[str], str]:
    fallback_summary = summarize_locally(abstract)
    if not abstract or not KEYWORD_API_KEY or not KEYWORD_API_URL:
        return [], fallback_summary

    cache_key = abstract.strip()
    if cache_key in ARTICLE_INSIGHT_CACHE:
        keywords_cached, summary_cached = ARTICLE_INSIGHT_CACHE[cache_key]
        return keywords_cached, summary_cached or fallback_summary

    prompt = dedent(
        """
        From the research abstract below, do the following:
        1. Extract up to {keyword_count} of the most informative keywords (single words or short noun phrases).
        2. Write one concise English sentence (max 30 words) summarizing the paper's main contribution.

        Respond with valid JSON using this shape:
        {{"keywords": ["keyword 1", "keyword 2"], "summary": "One-sentence summary."}}

        Abstract:
        {abstract}
        """
    ).strip().format(abstract=abstract.strip(), keyword_count=KEYWORD_TARGET_COUNT)

    payload = {
        "model": KEYWORD_API_MODEL,
        "messages": [
            {"role": "system", "content": "You analyze arXiv abstracts and return keywords plus a single-sentence summary."},
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

    attempt = 0
    data = None
    while attempt < 3:
        attempt += 1
        try:
            response = session.post(KEYWORD_API_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            print(f"Warning: keyword/summary API attempt {attempt} failed: {exc}")
            data = None
    if not data:
        ARTICLE_INSIGHT_CACHE[cache_key] = ([], fallback_summary)
        return [], fallback_summary

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    keywords = parse_keywords_response(content)[:KEYWORD_TARGET_COUNT]
    summary = parse_summary_response(content) or fallback_summary
    ARTICLE_INSIGHT_CACHE[cache_key] = (keywords, summary)
    return keywords, summary


def parse_keywords_response(content: str) -> List[str]:
    if not content:
        return []

    def _strip_code_fences(text: str) -> str:
        trimmed = text.strip()
        if trimmed.startswith("```"):
            trimmed = re.sub(r"^```(?:json)?", "", trimmed, flags=re.IGNORECASE).strip()
            if "```" in trimmed:
                trimmed = trimmed.rsplit("```", 1)[0].strip()
        return trimmed

    def _normalize_jsonish_text(text: str) -> str:
        normalized = _strip_code_fences(text)
        for src, dst in JSONISH_REPLACEMENTS.items():
            normalized = normalized.replace(src, dst)
        return normalized

    def _extract_keywords_from_dict(data: Dict[str, object]) -> List[str]:
        for key, value in data.items():
            if key.lower() == "keywords":
                if isinstance(value, list):
                    return [str(item).strip() for item in value if str(item).strip()]
                if isinstance(value, str):
                    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
        return []

    def _attempt_json_load(text: str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    normalized_content = _normalize_jsonish_text(content)
    candidates: List[str] = []
    parsed = _attempt_json_load(normalized_content)
    if isinstance(parsed, dict):
        candidates = _extract_keywords_from_dict(parsed)
    elif isinstance(parsed, list):
        candidates = [str(item).strip() for item in parsed if str(item).strip()]

    if not candidates:
        keywords_block = re.search(r'"?keywords?"?\s*:\s*\[(.*?)\]', normalized_content, re.IGNORECASE | re.DOTALL)
        if keywords_block:
            block = keywords_block.group(1)
            quoted_items = re.findall(r'"([^"]+)"', block)
            if quoted_items:
                candidates = [item.strip() for item in quoted_items if item.strip()]
            else:
                candidates = [part.strip(" \"'") for part in re.split(r"[;,]", block) if part.strip(" \"'")]

    if not candidates:
        parts = [part.strip(" •-\t") for part in re.split(r"[\n,;]+", normalized_content) if part.strip()]
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


def parse_summary_response(content: str) -> str:
    if not content:
        return ""

    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        if isinstance(summary, str):
            return summary.strip()
    return ""


def summarize_locally(abstract: str) -> str:
    if not abstract:
        return ""
    normalized = " ".join(abstract.strip().split())
    if not normalized:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    summary = next((sentence.strip() for sentence in sentences if sentence.strip()), normalized)
    words = summary.split()
    if len(words) > 35:
        summary = " ".join(words[:35]).rstrip(",.;: ") + "…"
    return summary


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
        "summary": article.summary,
    }


def extract_payload_from_html(html_content: str) -> Dict[str, object]:
    match = re.search(
        r'<script type="application/json" id="digest-data">(.*?)</script>',
        html_content,
        re.DOTALL,
    )
    if not match:
        raise ValueError("digest-data payload not found")
    data = match.group(1)
    return json.loads(data)


def load_payload_from_file(path: Path) -> Dict[str, object]:
    content = path.read_text(encoding="utf-8")
    return extract_payload_from_html(content)


def find_latest_digest(directories: Iterable[Path]) -> Path | None:
    candidates: List[Path] = []
    for directory in directories:
        if directory.exists():
            candidates.extend(directory.glob("index-*.html"))
    candidates = sorted(candidates, reverse=True)
    return candidates[0] if candidates else None


def resolve_archive_dir(output_path: Path) -> Path:
    return output_path.parent / "archive"


def build_redirect_html(target_filename: str) -> str:
    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <title>Loading Digest...</title>
          <script>
            (function() {{
              const target = "{target_filename}";

              function injectLatest(html) {{
                history.replaceState(null, "", "index.html");
                document.open();
                document.write(html);
                document.close();
              }}

              function showFallback() {{
                const status = document.getElementById("digest-status");
                if (!status) {{
                  return;
                }}
                const link = document.createElement("a");
                link.href = target;
                link.textContent = target;
                status.textContent = "Latest digest: ";
                status.appendChild(link);
              }}

              if (!window.fetch) {{
                window.location.href = target;
                return;
              }}

              fetch(target, {{ credentials: "same-origin", cache: "no-store" }})
                .then(function(response) {{
                  if (!response.ok) {{
                    throw new Error("Failed to load latest digest");
                  }}
                  return response.text();
                }})
                .then(injectLatest)
                .catch(function(error) {{
                  console.error(error);
                  showFallback();
                }});
            }})();
          </script>
        </head>
        <body>
          <p id="digest-status">Loading latest digest...</p>
        </body>
        </html>
        """
    ).strip()










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

    favorites_default = "\\n".join(payload.get("preferences", {}).get("favorite_authors", []))
    keywords_default = "\\n".join(payload.get("preferences", {}).get("keywords", []))

    template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>arXiv Daily Digest</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-app: #f6f7f9;
      --bg-surface: #ffffff;
      --bg-panel: #f1f3f5;
      --text-primary: #1f2933;
      --text-secondary: #52606d;
      --brand: #1d4ed8;
      --brand-dim: rgba(29, 78, 216, 0.08);
      --brand-glow: rgba(29, 78, 216, 0.18);
      --accent: #64748b;
      --border: #e5e7eb;
      --border-active: rgba(29, 78, 216, 0.35);
      --danger: #b91c1c;
      --success: #0f766e;
      --font-mono: "IBM Plex Mono", "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      --font-sans: "Inter", system-ui, sans-serif;
      --font-serif: "Georgia", "Times New Roman", serif;
      --sidebar-width: 200px;
    }

    *, *::before, *::after { box-sizing: border-box; }
    body {
      font-family: var(--font-sans);
      margin: 0;
      background: var(--bg-app);
      color: var(--text-primary);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    body.modal-open {
      overflow: hidden;
    }
    a { color: var(--brand); text-decoration: none; font-weight: 500; }
    a:hover { text-decoration: underline; }

    /* Header */
    header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 50;
      padding: 12px 0;
      box-shadow: 0 1px 2px rgba(0,0,0,0.02);
    }
    .inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .header-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .page-title {
      font-family: var(--font-serif);
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text-primary);
      margin: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .page-title .version {
      font-family: var(--font-mono);
      font-size: 0.7rem;
      color: var(--text-secondary);
      background: var(--bg-panel);
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: normal;
    }
    .header-actions { display: flex; gap: 10px; align-items: center; }

    /* Controls Deck */
    .control-deck {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      padding: 4px 0;
    }
    .search-group {
      flex: 1 1 200px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      transition: border-color 0.2s;
    }
    .search-group:focus-within { border-color: var(--brand); }
    .search-group input {
      border: none;
      background: transparent;
      width: 100%;
      font-size: 0.9rem;
      color: var(--text-primary);
      font-family: var(--font-sans);
    }
    .search-group input:focus { outline: none; }
    
    .filters-group { display: flex; gap: 8px; flex-wrap: wrap; }
    select, button.toggle-btn, .source-button, .display-mode__button {
      appearance: none;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: 0.8rem;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-family: var(--font-sans);
      transition: all 0.15s;
    }
    select:hover, button:hover { border-color: var(--text-secondary); color: var(--text-primary); }
    button.is-active {
      background: var(--brand);
      color: #fff;
      border-color: var(--brand);
    }
    button.is-active:hover { opacity: 0.9; }

    .page-meta {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--text-secondary);
      display: flex;
      gap: 16px;
      border-top: 1px solid var(--border);
      padding-top: 8px;
    }

    /* Layout */
    .layout {
      display: flex;
      gap: 24px;
      max-width: 1200px;
      margin: 24px auto;
      padding: 0 20px;
    }
    .sidebar {
      width: var(--sidebar-width);
      flex-shrink: 0;
      position: sticky;
      top: 120px;
      height: fit-content;
      max-height: calc(100vh - 140px);
      overflow-y: auto;
      padding-right: 8px;
    }
    body.sidebar-collapsed .sidebar {
      display: none;
    }
    body.sidebar-collapsed .layout {
      gap: 0;
    }
    body.sidebar-collapsed .content {
      max-width: 1000px;
      margin: 0 auto;
    }
    #sidebar-toggle {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-secondary);
      border-radius: 4px;
      padding: 4px 8px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
    }
    #sidebar-toggle:hover {
      border-color: var(--text-primary);
      color: var(--text-primary);
    }
    body.sidebar-collapsed #sidebar-toggle {
      background: var(--bg-panel);
      color: var(--brand);
    }
    .nav-title {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-secondary);
      margin-bottom: 12px;
      font-weight: 600;
    }
    .nav-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 2px; }
    .nav-item a {
      display: block;
      padding: 4px 8px;
      color: var(--text-secondary);
      border-radius: 4px;
      font-size: 0.8rem;
    }
    .nav-item a:hover { color: var(--text-primary); }
    .nav-item a.is-active { color: var(--brand); font-weight: 600; }
    .nav-list.nav-level-2 { padding-left: 12px; margin-top: 2px; border-left: 1px solid var(--border); margin-left: 4px; }

    /* Content */
    .content { flex: 1; min-width: 0; }
    .content-section { margin-bottom: 60px; }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 20px;
      border-bottom: 2px solid var(--text-primary);
      padding-bottom: 8px;
    }
    .section-header h2 {
      margin: 0;
      font-family: var(--font-serif);
      font-size: 1.4rem;
      color: var(--text-primary);
    }
    .section-summary { color: var(--text-secondary); font-size: 0.9rem; margin: 4px 0 0; }
    .section-toggle {
      background: none; border: none; color: var(--brand); cursor: pointer; font-size: 0.8rem; font-family: var(--font-sans);
    }

    /* Cards/Papers */
    .paper {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 24px;
      margin-bottom: 16px;
      transition: box-shadow 0.2s, transform 0.2s;
    }
    .paper:hover {
      box-shadow: 0 4px 12px rgba(0,0,0,0.05);
      border-color: #d1d5db;
    }
    .paper.is-read { opacity: 0.6; background: #fafafa; }
    .paper.paper--highlight { border-left: 4px solid var(--brand); }
    .paper.paper--expanded { border-color: var(--brand); }
    
    .paper-header-row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 12px;
    }
    .paper-title-col { flex: 1; min-width: 0; }
    .paper-actions-col {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
      white-space: nowrap;
    }
    .paper-actions-col .link-button { white-space: nowrap; }
    .paper-tags-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }
    .paper h3 {
      margin: 0;
      font-family: var(--font-sans);
      font-size: 1.1rem;
      font-weight: 600;
      line-height: 1.4;
    }
    .paper h3 a { color: var(--text-primary); }
    .paper h3 a:hover { color: var(--brand); }
    .paper .meta {
      font-size: 0.85rem;
      color: var(--text-secondary);
      margin-bottom: 12px;
    }
    .paper .meta .authors { color: var(--text-primary); font-weight: 500; }
    .paper .summary {
      font-family: var(--font-serif);
      font-size: 0.95rem;
      color: #374151;
      margin-bottom: 16px;
      line-height: 1.6;
    }
    .paper .subjects {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--text-secondary);
      margin-bottom: 12px;
    }
    .paper .links a {
      font-size: 0.8rem;
      margin-right: 16px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .keyword-tag {
      background: var(--bg-panel);
      color: var(--text-secondary);
      font-size: 0.7rem;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid var(--border);
      margin-left: 0;
      font-weight: normal;
      vertical-align: middle;
      font-family: var(--font-mono);
    }

    /* Read List (Minimal/Academic) */
    .readlist-card {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      margin-bottom: 24px;
    }
    .readlist-header {
      padding: 10px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: transparent;
    }
    .readlist-header h2 { margin: 0; font-size: 0.95rem; font-weight: 600; }
    .readlist-subtitle { margin: 0; font-size: 0.8rem; color: var(--text-secondary); display: none; }
    .readlist-count-row {
      padding: 6px 16px;
      font-size: 0.75rem;
      color: var(--text-secondary);
      border-bottom: 1px solid var(--border);
    }
    .readlist-body {
      max-height: 280px;
      overflow-y: auto;
      padding: 0 16px 12px;
    }
    .readlist-item {
      padding: 8px 0;
      border-bottom: 1px solid var(--border);
      font-size: 0.9rem;
    }
    .readlist-item:last-child { border-bottom: none; }
    .readlist-item__container {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      width: 100%;
    }
    .readlist-link-group {
      display: flex;
      align-items: baseline;
      gap: 8px;
      flex: 1;
      min-width: 0;
    }
    .readlist-link-group a {
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .readlist-link-group a:hover { color: var(--brand); }
    .readlist-index { font-family: var(--font-mono); color: var(--text-secondary); font-size: 0.72rem; min-width: 1.5em; }
    .readlist-remove-btn {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: color 0.2s ease, background 0.2s ease;
    }
    .readlist-remove-btn:hover {
      color: var(--danger);
      background: rgba(185, 28, 28, 0.08);
    }
    .readlist-clear {
      background: none; border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; font-size: 0.7rem; cursor: pointer;
    }
    .readlist-clear:hover { border-color: var(--danger); color: var(--danger); }

    /* Preferences */
    .preferences-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
    .preferences-card h2 { margin-top: 0; font-size: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 16px; }
    .preferences-group { margin-bottom: 16px; }
    .preferences-label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 8px; }
    .chip-set { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip { background: var(--bg-panel); padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; border: 1px solid var(--border); }
    .preferences-edit { background: none; border: 1px solid var(--border); padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 0.8rem; }
    .preferences-edit:hover { border-color: var(--text-primary); }
    .preferences-status { font-size: 0.8rem; color: var(--text-secondary); margin-top: 8px; min-height: 1.2em; }
    textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: var(--font-mono); font-size: 0.85rem; }
    .preferences-actions { margin-top: 12px; display: flex; gap: 8px; }
    .preferences-actions button { padding: 6px 12px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg-surface); cursor: pointer; font-size: 0.8rem; }
    .preferences-actions button[type="submit"] { background: var(--text-primary); color: #fff; border-color: var(--text-primary); }

    /* Stats */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
    .stat-card { background: var(--bg-surface); border: 1px solid var(--border); padding: 16px; border-radius: 8px; }
    .stat-card h3 { margin: 0 0 8px; font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
    .stat-card p { margin: 0; font-size: 1.5rem; font-weight: 600; color: var(--text-primary); }
    .stat-card ul { padding-left: 20px; margin: 0; font-size: 0.85rem; color: var(--text-secondary); }

    /* Modal */
    .abstract-modal {
      position: fixed; inset: 0; z-index: 1000;
      display: none; align-items: center; justify-content: center;
      visibility: hidden; opacity: 0; transition: opacity 0.2s ease;
      padding: 20px;
      pointer-events: none;
    }
    .abstract-modal.is-open {
      display: flex;
      visibility: visible;
      opacity: 1;
      pointer-events: auto;
    }
    .abstract-modal__backdrop {
      position: absolute; inset: 0;
      background: rgba(0,0,0,0.6);
      backdrop-filter: blur(4px);
    }
    .abstract-modal__dialog {
      position: relative;
      width: 100%; max-width: 700px; max-height: 85vh;
      background: var(--bg-surface);
      border-radius: 12px;
      box-shadow: 0 20px 50px rgba(0,0,0,0.2);
      display: flex; flex-direction: column;
      z-index: 10;
      overflow: hidden;
    }
    .abstract-modal__header {
      padding: 16px 24px;
      border-bottom: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: center;
      background: var(--bg-panel);
    }
    .abstract-modal__title { margin: 0; font-size: 1.1rem; font-weight: 600; font-family: var(--font-sans); }
    .abstract-modal__close {
      background: none; border: none; cursor: pointer; font-size: 0.9rem; color: var(--text-secondary);
    }
    .abstract-modal__close:hover { color: var(--text-primary); }
    .abstract-modal__body { padding: 24px; overflow-y: auto; font-family: var(--font-serif); }
    .abstract-modal__meta { font-family: var(--font-sans); font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 20px; display: flex; flex-direction: column; gap: 8px; }
    .abstract-modal__abstract { font-size: 1rem; line-height: 1.7; color: var(--text-primary); margin-bottom: 24px; }
    .abstract-modal__actions { display: flex; gap: 12px; padding-top: 20px; border-top: 1px solid var(--border); }
    .abstract-modal__actions a {
      padding: 8px 16px; border-radius: 6px; font-family: var(--font-sans); font-size: 0.9rem; text-decoration: none;
    }
    .abstract-modal__actions .primary { background: var(--text-primary); color: #fff; }
    .abstract-modal__actions .primary:hover { opacity: 0.9; }
    .abstract-modal__actions .secondary { background: var(--bg-panel); color: var(--text-primary); }
    .abstract-modal__actions .secondary:hover { background: #e5e7eb; }

    /* Interactive buttons */
    .link-button {
      background: none; border: 1px solid var(--border); color: var(--text-secondary);
      font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; cursor: pointer; margin-left: 8px;
      vertical-align: middle; font-family: var(--font-sans);
    }
    .link-button:hover { border-color: var(--text-primary); color: var(--text-primary); }
    .link-button.is-active { background: var(--text-primary); color: #fff; border-color: var(--text-primary); }

    /* Mobile */
    @media (max-width: 768px) {
      .layout { flex-direction: column; gap: 20px; }
      .sidebar { width: 100%; position: static; max-height: none; }
      .control-deck { flex-direction: column; align-items: stretch; }
      .header-top { flex-direction: column; align-items: flex-start; gap: 12px; }
      .header-actions { width: 100%; justify-content: space-between; }
    }

    body.modal-open { overflow: hidden; }

    /* Logic States (preserved for script.js) */
    .content-section.is-hidden { display: none; }
    .content-section.is-collapsed .section-body { display: none; }
    .paper.paper--expanded { border-color: var(--brand); box-shadow: 0 0 0 2px var(--brand-dim); }
    body.display-mode-title .paper .abstract, body.display-mode-title .paper .subjects, body.display-mode-title .paper .summary { display: none; }
    body.display-mode-authors .paper .abstract, body.display-mode-authors .paper .summary { display: none; }
    footer {
      text-align: center; font-size: 0.8rem; color: var(--text-secondary); padding: 40px 0; border-top: 1px solid var(--border); margin-top: 40px;
    }
  </style>
</head>
<body class="display-mode-authors">
  <header>
    <div class="inner">
      <div class="header-top">
        <h1 class="page-title">
          <button id="sidebar-toggle" type="button" aria-label="Toggle sidebar" title="Toggle sidebar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>
          </button>
          arXiv Daily Digest
        </h1>
        <div class="header-actions">
          <div class="source-switcher" id="source-switcher" role="group"></div>
          <form class="date-switcher" id="date-switcher-form">
            <input type="date" id="date-switcher-input" />
            <button type="submit">→</button>
          </form>
        </div>
      </div>
      <div class="control-deck">
        <div class="search-group">
          <span class="icon">🔍</span>
          <input type="text" id="search-input" placeholder="Search title, author, keyword" />
        </div>
        <div class="filters-group">
          <select id="sort-select">
            <option value="newest">SORT: NEWEST</option>
            <option value="relevance">SORT: RELEVANCE</option>
          </select>
          <button id="unread-toggle" class="toggle-btn">Unread</button>
          <button id="highlight-toggle" class="toggle-btn is-active">Highlight</button>
        </div>
        <div class="display-mode" id="display-mode-controls"></div>
      </div>
      <div class="page-meta">
        <span id="meta-source">SRC: __DEFAULT_LABEL__</span>
        <span id="meta-date">DATE: __HEADER_DATE__</span>
        <span id="meta-total">COUNT: __TOTAL_PAPERS__</span>
      </div>
    </div>
  </header>
  
  <div class="layout">
    <aside class="sidebar">
      <div class="nav-title">On this page</div>
      <nav aria-label="Section navigation"></nav>
    </aside>
    
    <main id="main-content" class="content">
      <section id="workspace" class="content-section" data-static-section="true">
        <div class="section-header">
          <h2>Workspace</h2>
          <p class="section-summary">Track favorite authors/keywords and manage your read list.</p>
        </div>
        <div class="workspace-grid">
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
          <div class="readlist-card">
            <div class="readlist-header">
              <div>
                <h2>Read list</h2>
                <p class="readlist-subtitle">Save papers to revisit.</p>
              </div>
              <button type="button" id="read-list-clear" class="readlist-clear" aria-label="Clear saved papers">Clear</button>
            </div>
            <div class="readlist-count-row">
              <span class="readlist-count" id="read-list-count">0</span>
              <span>saved</span>
            </div>
            <div id="read-list-body" class="readlist-body"></div>
          </div>
        </div>
      </section>
      
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
          <span class="abstract-modal__summary" id="abstract-modal-summary" hidden></span>
        </div>
        <div class="abstract-modal__abstract" id="abstract-modal-abstract"></div>
        <div class="abstract-modal__actions">
          <a href="#" target="_blank" rel="noopener" class="primary" id="abstract-modal-original">Open on AlphaXiv</a>
          <a href="#" target="_blank" rel="noopener" class="secondary" id="abstract-modal-arxiv">Open on arXiv</a>
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
    parser.add_argument(
        "--style-only",
        action="store_true",
        help="Update styling/template only using the latest existing digest without fetching new content.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.style_only:
        candidates = []
        if args.output.exists():
            candidates.append(args.output)
        archive_dir = resolve_archive_dir(args.output)
        latest = find_latest_digest([archive_dir, args.output.parent])
        if latest and latest not in candidates:
            candidates.append(latest)

        payload = None
        source_html: Path | None = None
        for path in candidates:
            try:
                payload = load_payload_from_file(path)
                source_html = path
                break
            except Exception:
                continue

        if payload is None or source_html is None:
            raise SystemExit(
                "Style-only mode requires an existing digest HTML (e.g., index-YYYY-MM-DD.html) to reuse its content.",
            )

        html = build_html(payload)
        write_output(html, source_html)
        try:
            redirect_target = source_html.relative_to(args.output.parent).as_posix()
        except ValueError:
            redirect_target = source_html.name
        redirect_html = build_redirect_html(redirect_target)
        write_output(redirect_html, args.output)
        return

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

    generated_at_dt = datetime.now().astimezone()
    payload = {
        "generated_at": generated_at_dt.strftime("%Y-%m-%d %H:%M %Z"),
        "sources": sources_payload,
        "preferences": config,
        "default_source": default_source_key,
    }

    archive_dir = resolve_archive_dir(args.output)
    archive_dir.mkdir(parents=True, exist_ok=True)

    dated_filename = archive_dir / f"index-{generated_at_dt.strftime('%Y-%m-%d')}.html"
    html = build_html(payload)
    write_output(html, dated_filename)

    redirect_target = dated_filename.relative_to(args.output.parent).as_posix()
    redirect_html = build_redirect_html(redirect_target)
    write_output(redirect_html, args.output)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
