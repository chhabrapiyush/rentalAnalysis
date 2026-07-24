# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Requires Python 3.11+ (`brew install python@3.11` if needed).

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
playwright install chromium       # one-time browser install
cp .env.example .env              # fill in ONEHOME_EMAIL and ONEHOME_PASSWORD
```

## Commands

```bash
# Run tests (no browser required)
.venv/bin/pytest -m "not integration" -v

# Validate your config before running
.venv/bin/rentalysis validate-config config.yaml

# Analyze specific listing URLs
.venv/bin/rentalysis analyze URL1 URL2 --output deals.xlsx

# Analyze from a file of URLs (one per line)
.venv/bin/rentalysis analyze --url-file my_urls.txt

# Auto-discover listings from a search results page
.venv/bin/rentalysis analyze --search-url "https://portal.onehome.com/search?..." --max-pages 3

# Override rent/price globally (useful when listing has no rent estimate)
.venv/bin/rentalysis analyze URL --rent 2500 --price 420000

# Show the browser (useful for debugging login or 2FA)
.venv/bin/rentalysis analyze URL --no-headless

# Force re-scrape (ignore cache)
.venv/bin/rentalysis analyze URL --no-cache
```

## Architecture

```
src/rentalanalysis/
├── models.py        — Pydantic models: PropertyListing, LoanConfig, ExpenseConfig, AnalysisConfig, AnalysisResult
├── calculator.py    — Pure financial math (no I/O): analyze_property, apply_property_overrides, amortization
├── scraper.py       — Playwright async scraper: login, scrape_listing, paginate_search_results
├── excel_export.py  — openpyxl report builder: Overview comparison sheet + per-property sheets
└── cli.py           — Typer CLI entry point wiring everything together
```

**Data flow**: CLI loads `.env` + `config.yaml` → Playwright scrapes listing URLs → `apply_property_overrides` merges per-property config → `analyze_property` computes all metrics → `build_workbook` writes the Excel file.

**Scraping strategy** (`scraper.py`): Uses a single persistent browser context across all URLs (one login per session). Extraction priority per field: JSON-LD → OpenGraph meta → CSS selectors. All field extractions return `None` on failure rather than crashing — partial data is fine.

**Per-property config overrides**: In `config.yaml`, the `property_overrides` dict is keyed by listing URL (prefix match). Supports overriding any `PropertyListing` field plus nested `loan` and `expenses` blocks. Applied by `apply_property_overrides` before analysis.

**Excel output**: `Overview` sheet has all properties side-by-side with cash-flow color coding (green/yellow/red). Each property gets its own sheet with vitals, assumptions, income waterfall, returns dashboard, and amortization snapshots at years 1/5/10/15/20/25/30.

**Caching**: Scraped listings are saved to `.cache/<url_md5>.json`. Use `--no-cache` to force re-scrape.

## Financial Formulas

- **Monthly payment**: `M = P × r(1+r)^n / ((1+r)^n - 1)` where `r = rate/12`, `n = years × 12`
- **NOI** = Effective Gross Income − Operating Expenses
- **EGI** = Gross Rental Income × (1 − vacancy_rate)
- **Cash Flow** = NOI − Annual Debt Service
- **Cap Rate** = NOI / Purchase Price
- **Cash-on-Cash** = Annual Cash Flow / (Down Payment + Closing Costs)
- **DSCR** = NOI / Annual Debt Service
- **GRM** = Purchase Price / Annual Gross Rent
