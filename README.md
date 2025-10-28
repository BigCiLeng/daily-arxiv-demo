# arXiv cs.CV Daily Digest

This tool fetches the most recent computer-vision papers from [https://arxiv.org/list/cs.CV/recent?skip=0&show=2000](https://arxiv.org/list/cs.CV/recent?skip=0&show=2000) and renders a static HTML dashboard. The generated page includes:

- All papers published on the chosen date with titles, authors, abstracts, subjects, and PDF links;
- A configurable "Favorite Authors" panel and a "Watched Keywords" panel (collapsed by default so the landing view stays focused on the full list, and editable directly on the page with local storage persistence);
- Category-based navigation (new submissions, cross-lists, replacements, etc.);
- A statistics bar with total counts, author breakdown, popular multi-word phrases, and quick navigation links;
- A sticky, scrollable sidebar navigation tree that includes every category and subject heading for quick jumping;
- A source switcher that lets you toggle between cs.CV and cs.RO feeds on the fly without regenerating the page.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python fetch_arxiv.py --config config.json --output index.html
```

The command writes `index.html` in the current directory. Open it with any browser.

> **Heads-up:** arXiv typically publishes the latest list in the evening (US Eastern Time). If no entries are available for the current day, the script will automatically fall back to the most recent date on the page and report that in the console.

## In-page controls

- **Source switcher:** Use the buttons under the page header to switch between the Computer Vision (cs.CV) and Robotics (cs.RO) feeds. The digest re-renders instantly using preloaded data.
- **Tracking panel:** In the left sidebar, enter favorite authors or keywords (comma or newline separated). The selections are stored in `localStorage` so they persist between visits.
- **Edit-on-demand tracking:** Click `Edit` to change favorites/keywords. After saving, the panel returns to a read-only summary; use `Edit` again to make further tweaks, or `Reset` while editing to fall back to the config defaults.
- **Collapsible sections:** Statistics is the only section shown initially. Other sections (All Papers, Favorite Authors, Watched Keywords, Categories) appear when you use the “On this page” navigation or their toggle buttons.

## Configure authors and keywords

`config.json` provides the watch lists:

```json
{
  "favorite_authors": [
    "Jitendra Malik",
    "Fei-Fei Li"
  ],
  "keywords": [
    "diffusion",
    "multimodal"
  ]
}
```

- `favorite_authors`: case-insensitive matches against the author list (partials are fine, e.g. a surname only).
- `keywords`: case-insensitive matches against the title and abstract.

Point to an alternative config file with `--config /path/to/file.json`.

## Command-line options

```bash
python fetch_arxiv.py --help
```

- `--output`: output HTML file path (default: `index.html`).
- `--date YYYY-MM-DD`: force a specific date; the script still depends on the content published on arXiv.

## Operational tips

1. Add the script to a scheduler (e.g. `cron`) to refresh the digest daily.
2. The output is static HTML and can be hosted on any web server or shared directly.
3. Keep an eye on arXiv markup changes and tweak the parser if the layout shifts.

## License

MIT
