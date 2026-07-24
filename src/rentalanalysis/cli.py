from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .calculator import analyze_property, apply_property_overrides
from .excel_export import build_workbook
from .models import AnalysisConfig, AnalysisResult
from .scraper import AuthenticationError, scrape_listings

app = typer.Typer(help="Rental property investment analyzer — sources data from portal.onehome.com")
console = Console()
log = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


@app.command()
def analyze(
    urls: Optional[list[str]] = typer.Argument(None, help="One or more listing URLs"),
    url_file: Optional[Path] = typer.Option(None, "--url-file", "-f", help="File with one URL per line"),
    search_url: Optional[str] = typer.Option(None, "--search-url", help="Search results page to auto-paginate"),
    config_file: Path = typer.Option(Path("config.yaml"), "--config", "-c", help="Analysis config YAML"),
    output: Path = typer.Option(Path("analysis.xlsx"), "--output", "-o", help="Output Excel file path"),
    rent: Optional[float] = typer.Option(None, "--rent", help="Monthly rent override (applied to all listings)"),
    price: Optional[float] = typer.Option(None, "--price", help="Purchase price override"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Run browser headlessly"),
    max_pages: int = typer.Option(10, "--max-pages", help="Max search result pages to paginate"),
    cache: bool = typer.Option(True, "--cache/--no-cache", help="Use cached scrape results"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scrape listings, compute investor metrics, and export to Excel."""
    _setup_logging(verbose)
    load_dotenv()

    email = os.getenv("ONEHOME_EMAIL")
    password = os.getenv("ONEHOME_PASSWORD")
    if not email or not password:
        console.print("[red]Error:[/red] ONEHOME_EMAIL and ONEHOME_PASSWORD must be set in your .env file.")
        raise typer.Exit(1)

    # Load config
    if not config_file.exists():
        console.print(f"[yellow]Warning:[/yellow] {config_file} not found — using defaults.")
        cfg = AnalysisConfig()
    else:
        try:
            cfg = AnalysisConfig.from_yaml(config_file)
        except Exception as exc:
            console.print(f"[red]Error loading config:[/red] {exc}")
            raise typer.Exit(1)

    # Collect URLs
    all_urls: list[str] = list(urls or [])
    if url_file:
        if not url_file.exists():
            console.print(f"[red]Error:[/red] URL file {url_file} not found.")
            raise typer.Exit(1)
        all_urls += [u.strip() for u in url_file.read_text().splitlines() if u.strip()]

    # Scrape (includes search pagination if --search-url provided)
    try:
        if search_url and not all_urls:
            console.print(f"Paginating search results from: {search_url}")

        listings = asyncio.run(
            _scrape_with_search(
                all_urls, search_url, email, password, headless, cache, max_pages
            )
        )
    except AuthenticationError as exc:
        console.print(f"[red]Authentication failed:[/red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Scraping error:[/red] {exc}")
        raise typer.Exit(1)

    if not listings:
        console.print("[yellow]No listings found or scraped. Exiting.[/yellow]")
        raise typer.Exit(0)

    console.print(f"Scraped [bold]{len(listings)}[/bold] listings. Running analysis...")

    # Analyze
    results: list[AnalysisResult] = []
    for listing in listings:
        eff_listing, eff_cfg = apply_property_overrides(listing, cfg)
        try:
            result = analyze_property(eff_listing, eff_cfg, monthly_rent_override=rent, purchase_price_override=price)
            results.append(result)
        except ValueError as exc:
            console.print(f"[yellow]Skipping {listing.address}:[/yellow] {exc}")

    if not results:
        console.print("[red]No properties could be analyzed (missing rent estimates?).[/red]")
        raise typer.Exit(1)

    # Export
    build_workbook(results, output, cfg)
    console.print(f"\n[green]Saved:[/green] {output.resolve()}")

    # Summary table
    _print_summary(results)


async def _scrape_with_search(
    urls: list[str],
    search_url: Optional[str],
    email: str,
    password: str,
    headless: bool,
    cache: bool,
    max_pages: int,
) -> list:
    from playwright.async_api import async_playwright

    from .scraper import paginate_search_results, scrape_listing, _load_cache, _save_cache
    import random

    all_urls = list(urls)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        if search_url:
            discovered = await paginate_search_results(page, search_url, max_pages)
            all_urls = list(dict.fromkeys(all_urls + discovered))  # dedupe, preserve order

        results = []
        for url in all_urls:
            if cache:
                cached = _load_cache(url)
                if cached:
                    results.append(cached)
                    continue
            try:
                listing = await scrape_listing(page, url, email=email, password=password)
                if cache:
                    _save_cache(listing)
                results.append(listing)
            except Exception as exc:
                log.warning("Failed to scrape %s: %s", url, exc)
            await asyncio.sleep(random.uniform(1.5, 3.5))

        await browser.close()
    return results


def _print_summary(results: list[AnalysisResult]) -> None:
    table = Table(title="Deal Summary", show_lines=True)
    table.add_column("Address", style="bold", max_width=35)
    table.add_column("Price", justify="right")
    table.add_column("NOI/yr", justify="right")
    table.add_column("Cash Flow/mo", justify="right")
    table.add_column("Cap Rate", justify="right")
    table.add_column("CoC", justify="right")
    table.add_column("DSCR", justify="right")

    for r in results:
        cf_color = "green" if r.cash_flow_monthly > 0 else ("yellow" if r.cash_flow_monthly > -17 else "red")
        table.add_row(
            r.listing.address,
            f"${r.listing.list_price:,.0f}",
            f"${r.noi:,.0f}",
            f"[{cf_color}]${r.cash_flow_monthly:,.0f}[/{cf_color}]",
            f"{r.cap_rate:.2%}",
            f"{r.cash_on_cash:.2%}",
            f"{r.dscr:.2f}",
        )

    console.print(table)

    # OpEx sanity warnings: our calculated operating expenses exceed the listing's implied OpEx
    flagged = [r for r in results if r.opex_exceeds_listed and r.opex_variance is not None]
    for r in flagged:
        console.print(
            f"[yellow]⚠[/yellow] [bold]{r.listing.address}[/bold]: calculated OpEx exceeds the "
            f"listing by [red]${r.opex_variance:,.0f}[/red] "
            f"(listing NOI may be optimistic)."
        )


@app.command("validate-config")
def validate_config(
    config_file: Path = typer.Argument(Path("config.yaml")),
) -> None:
    """Load and validate a config.yaml file, printing the parsed values."""
    if not config_file.exists():
        console.print(f"[red]File not found:[/red] {config_file}")
        raise typer.Exit(1)
    try:
        cfg = AnalysisConfig.from_yaml(config_file)
        console.print("[green]Config is valid.[/green]\n")
        console.print(cfg.model_dump_json(indent=2))
    except Exception as exc:
        console.print(f"[red]Invalid config:[/red] {exc}")
        raise typer.Exit(1)
