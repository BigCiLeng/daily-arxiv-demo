# Repository Guidelines

## Project Structure & Module Organization
This repository centers on `fetch_arxiv.py`, the CLI that scrapes the cs.CV/cs.RO feeds and renders digest data. Supporting assets live alongside it: `config.json` holds default author and keyword selections, `index.html` is the generated output, and `sample.html` is a captured rendering useful for quick UI checks. `script.js` powers the interactive panels once the HTML is loaded. Keep any new modules in the repository root; place static assets next to the HTML so the relative links stay intact.

## Build, Test, and Development Commands
- `python3 -m venv .venv && source .venv/bin/activate`: create and enter the local virtualenv.
- `pip install -r requirements.txt`: install the minimal runtime (requests + BeautifulSoup).
- `python fetch_arxiv.py --config config.json --output index.html`: generate the live dashboard.
- `python fetch_arxiv.py --date 2024-01-01 --output sample.html`: reproduce a specific date for debugging changes in the parser.

## Coding Style & Naming Conventions
Python code follows PEP 8 with 4-space indentation and type hints where possible; favor descriptive snake_case function names (`collect_articles`, `render_sections`). Keep imported constants uppercase (`ARXIV_SOURCES`) and module-level helper classes in PascalCase. For browser scripts, mirror the existing `script.js` style: 2-space indentation, `const`/`let` over `var`, camelCase for variables, and IIFE wrappers to avoid globals. Run `python -m black fetch_arxiv.py` before committing substantial Python edits.

## Testing Guidelines
There is no automated suite yet. Validate changes by running the generator against both the default feed and an older date, then diff the produced HTML for unexpected changes (`git diff index.html`). When modifying client behavior, open the generated file in a browser and exercise the source switcher and preference panel to confirm localStorage flows still work.

## Commit & Pull Request Guidelines
Use concise, imperative commit subjects no longer than 72 characters (e.g., `Add section parser for replacements`). Include a short body describing scrape or rendering side effects. Pull requests should summarize the change set, note how you validated the HTML output, and attach screenshots when tweaking the front-end layout. Link to any tracked issue and call out follow-up work so reviewers can plan next steps.

## Configuration & Security Notes
Avoid committing personalized author or keyword lists; share reusable defaults through `config.json` instead. When introducing new configuration switches, surface them as CLI flags first and document them in `README.md`. Respect arXiv’s rate limits by keeping HTTP headers and request pacing consistent.
