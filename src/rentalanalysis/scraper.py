from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, async_playwright

from .models import PropertyListing

log = logging.getLogger(__name__)

CACHE_DIR = Path(".cache")


class AuthenticationError(Exception):
    pass


class ScrapingError(Exception):
    pass


def _cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_cache(url: str) -> Optional[PropertyListing]:
    path = _cache_path(url)
    if path.exists():
        try:
            return PropertyListing.model_validate_json(path.read_text())
        except Exception:
            pass
    return None


def _save_cache(listing: PropertyListing) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(listing.url).write_text(listing.model_dump_json())


def _safe_float(v: Any) -> Optional[float]:
    """Extract the first number from a value like '$1,850/mo' or '2 units'."""
    if v is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    return int(f) if f is not None else None


def _is_login_page(url: str) -> bool:
    return any(kw in url.lower() for kw in ("login", "signin", "sign-in", "auth"))


async def login(page: Page, email: str, password: str) -> None:
    await page.goto("https://portal.onehome.com/", timeout=60_000)
    await asyncio.sleep(2)

    # If we're already past the login page (e.g. session cookie), skip
    if not _is_login_page(page.url):
        log.info("Already authenticated, skipping login.")
        return

    # Wait for any email input to appear
    email_sel = "input[type='email'], input[name='email'], input[name='username'], input[placeholder*='email' i], input[placeholder*='username' i]"
    try:
        await page.wait_for_selector(email_sel, timeout=30_000)
    except Exception:
        # Take a screenshot to help debug
        await page.screenshot(path="login_debug.png")
        raise AuthenticationError(
            "Login form did not appear. Screenshot saved to login_debug.png for inspection."
        )

    await page.fill(email_sel, email)
    await asyncio.sleep(0.5)

    pwd_sel = "input[type='password'], input[name='password']"
    await page.fill(pwd_sel, password)
    await asyncio.sleep(0.3)

    # Try submit button — broad selector, first visible button after filling fields
    submit_sel = "button[type='submit'], input[type='submit'], button:has-text('Sign'), button:has-text('Log'), button:has-text('Continue'), button:has-text('Next')"
    submit_btn = await page.query_selector(submit_sel)
    if submit_btn:
        await submit_btn.click()
    else:
        await page.keyboard.press("Enter")

    # Wait until we leave the login page
    try:
        await page.wait_for_function(
            "() => !window.location.href.match(/login|signin|sign-in|auth/i)",
            timeout=15_000,
        )
    except Exception:
        await page.screenshot(path="login_failed.png")
        raise AuthenticationError(
            "Login failed — still on login page. Check credentials or see login_failed.png."
        )

    log.info("Logged in successfully.")


async def _extract_json_ld(page: Page) -> dict:
    scripts = await page.query_selector_all("script[type='application/ld+json']")
    for script in scripts:
        try:
            text = await script.inner_text()
            data = json.loads(text)
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") in ("RealEstateListing", "Product", "Place"):
                return data
        except Exception:
            continue
    return {}


async def _get_text(page: Page, selector: str) -> Optional[str]:
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return None


async def _get_meta(page: Page, prop: str) -> Optional[str]:
    try:
        el = await page.query_selector(f"meta[property='{prop}'], meta[name='{prop}']")
        if el:
            return await el.get_attribute("content")
    except Exception:
        pass
    return None


async def _extract_dt_dd_map(page: Page) -> dict[str, str]:
    """Extract all dt.label / dd.detail pairs into a dict keyed by the DT text."""
    pairs: dict[str, str] = await page.evaluate("""
        () => {
            const result = {};
            document.querySelectorAll('dt.label').forEach(dt => {
                const dd = dt.nextElementSibling;
                if (dd) {
                    result[dt.innerText.trim().replace(/:$/, '')] = dd.innerText.trim();
                }
            });
            return result;
        }
    """)
    return pairs or {}


async def _extract_rent_roll(page: Page) -> list[dict]:
    """Extract the per-unit rent roll from the Property Details tab.

    OneHome renders each unit as a repeating dt/dd group keyed by 'Unit #'.
    """
    groups: list[dict] = await page.evaluate("""
        () => {
            const UNIT_FIELDS = ['Monthly Rent','Beds','Baths','Units of This Type','Total Rent','Description'];
            const dts = Array.from(document.querySelectorAll('dt'));
            const units = [];
            let cur = null;
            for (const dt of dts) {
                const label = dt.innerText.trim().replace(/:$/, '');
                const dd = dt.nextElementSibling;
                const val = dd ? dd.innerText.trim() : null;
                if (label === 'Unit #') {
                    if (cur) units.push(cur);
                    cur = {unit: val};
                } else if (cur && UNIT_FIELDS.includes(label)) {
                    cur[label] = val;
                }
            }
            if (cur) units.push(cur);
            return units;
        }
    """)
    return groups or []


async def _safe_goto(page: Page, url: str) -> None:
    """Navigate resiliently: prefer networkidle, fall back if the SPA never idles."""
    try:
        await page.goto(url, timeout=45_000, wait_until="networkidle")
    except Exception:
        try:
            await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
        except Exception as exc:
            log.warning("Navigation issue for %s: %s", url, exc)


async def scrape_listing(page: Page, url: str, email: str = "", password: str = "") -> PropertyListing:
    await _safe_goto(page, url)
    await asyncio.sleep(2.5)

    # If redirected to login, attempt authentication
    if _is_login_page(page.url) and email:
        log.info("Redirected to login. Attempting authentication...")
        await login(page, email, password)
        await _safe_goto(page, url)
        await asyncio.sleep(2.5)

    # --- Price (visible on Overview tab) ---
    raw_price = await _get_text(page, ".price-change, [data-qa*='price']")
    list_price = _safe_float(raw_price) or 0.0

    # --- Address ---
    address = (
        await _get_text(page, "[data-qa='address-line1'], [class*='address']")
        or await _get_meta(page, "og:title")
        or url
    )

    # --- Beds / Baths / Sqft from Overview stats ---
    raw_beds = await _get_text(page, "[data-qa*='bed']")
    beds = _safe_float(raw_beds) or 0.0

    raw_baths = await _get_text(page, "[data-qa*='bath']")
    baths = _safe_float(raw_baths) or 0.0

    raw_sqft = await _get_text(page, "[data-qa*='sqft']")
    sqft = _safe_float(raw_sqft)

    # --- Click Property Details tab to reveal financial DT/DD data ---
    # Guarded: a failed click must not lose the overview data already gathered.
    details_tab = await page.query_selector("button[aria-label='Property Details'], button[aria-label*='Property Details']")
    if details_tab:
        try:
            await details_tab.click()
            await asyncio.sleep(1.5)
        except Exception as exc:
            log.warning("Could not open Property Details tab for %s: %s", url, exc)

    details = await _extract_dt_dd_map(page)
    log.debug("Extracted %d property detail fields", len(details))

    # Per-unit rent roll (repeating "Unit #" groups)
    raw_roll = await _extract_rent_roll(page)
    rent_roll = []
    for u in raw_roll:
        rent_roll.append({
            "unit": u.get("unit"),
            "monthly_rent": _safe_float(u.get("Monthly Rent")),
            "beds": u.get("Beds"),
            "baths": u.get("Baths"),
            "description": u.get("Description"),
        })
    log.debug("Extracted %d rent-roll units", len(rent_roll))

    # Prefer DT/DD data over overview for beds/baths/sqft if available
    if not beds and details.get("Beds"):
        beds = _safe_float(details["Beds"]) or 0.0
    if not baths and details.get("Total Bathrooms"):
        baths = _safe_float(details["Total Bathrooms"]) or 0.0
    if not sqft and details.get("Size"):
        sqft = _safe_float(details["Size"])
    if not list_price and details.get("List Price"):
        list_price = _safe_float(details["List Price"]) or 0.0

    # --- Annual Taxes ---
    annual_taxes = _safe_float(details.get("Annual Taxes"))

    # --- HOA (monthly) ---
    hoa_monthly = _safe_float(details.get("HOA Fee") or details.get("HOA") or details.get("Association Fee")) or 0.0

    # --- Investor financial fields from listing ---
    gross_income_annual = _safe_float(details.get("Gross Income"))

    # Prefer the actual OneHome rent roll for rent; fall back to gross income / 12.
    roll_total_monthly = sum(u["monthly_rent"] for u in rent_roll if u.get("monthly_rent"))
    if roll_total_monthly:
        estimated_rent_monthly = round(roll_total_monthly, 2)
    elif gross_income_annual:
        estimated_rent_monthly = round(gross_income_annual / 12, 2)
    else:
        estimated_rent_monthly = None

    insurance_annual_listed = _safe_float(details.get("Insurance Expense"))
    maintenance_annual_listed = _safe_float(details.get("Maintenance Expense"))
    noi_listed = _safe_float(details.get("Net Operating Income"))
    operating_expense_listed = _safe_float(details.get("Operating Expense"))
    electric_expense_listed = _safe_float(details.get("Electric Expense"))
    gross_income_annual_listed = gross_income_annual
    total_units = _safe_int(details.get("Total Units"))
    year_built = _safe_int(details.get("Year Built"))
    property_type = details.get("Property Type")
    price_per_sqft = _safe_float(details.get("Price per Sq Ft."))

    # --- Days on market ---
    raw_dom = await _get_text(page, "[data-qa*='dom'], [data-qa*='days']")
    days_on_market = _safe_int(raw_dom)

    # --- Photos ---
    try:
        img_els = await page.query_selector_all("img[src*='listing'], img[src*='property'], img[src*='photos'], img[class*='photo']")
        photo_urls = []
        for img in img_els[:10]:
            src = await img.get_attribute("src")
            if src:
                photo_urls.append(src)
    except Exception:
        photo_urls = []

    if list_price == 0.0:
        log.warning("Price not found for %s (page title: %s)", url, await page.title())

    return PropertyListing(
        url=url,
        address=address,
        list_price=list_price,
        beds=beds,
        baths=baths,
        sqft=sqft,
        annual_taxes=annual_taxes,
        hoa_monthly=hoa_monthly,
        estimated_rent_monthly=estimated_rent_monthly,
        days_on_market=days_on_market,
        photo_urls=photo_urls,
        insurance_annual_listed=insurance_annual_listed,
        maintenance_annual_listed=maintenance_annual_listed,
        noi_listed=noi_listed,
        gross_income_annual_listed=gross_income_annual_listed,
        operating_expense_listed=operating_expense_listed,
        electric_expense_listed=electric_expense_listed,
        total_units=total_units,
        year_built=year_built,
        property_type=property_type,
        price_per_sqft=price_per_sqft,
        rent_roll=rent_roll,
        listing_details=details,
    )


async def scrape_listing_with_retry(
    page: Page, url: str, email: str = "", password: str = "",
    retries: int = 2, base_delay: float = 2.0,
) -> PropertyListing:
    """scrape_listing with exponential backoff on transient failures.

    Retries on an exception, and also when a load returns no price (a common
    symptom of a page that didn't render). Backoff is base_delay * 2**attempt
    plus a little jitter. Re-raises the last exception if every attempt errors.
    """
    last_exc: Optional[Exception] = None
    listing: Optional[PropertyListing] = None
    for attempt in range(retries + 1):
        try:
            listing = await scrape_listing(page, url, email=email, password=password)
            if listing.list_price > 0 or attempt == retries:
                return listing
            log.warning("Attempt %d for %s returned no price; retrying", attempt + 1, url)
        except Exception as exc:
            last_exc = exc
            log.warning("Attempt %d failed for %s: %s", attempt + 1, url, exc)
        if attempt < retries:
            await asyncio.sleep(base_delay * (2 ** attempt) + random.uniform(0, 0.5))
    if last_exc is not None:
        raise last_exc
    return listing  # last attempt's listing (may lack a price, but no exception)


_AOTF_RE = re.compile(r"aotf~\d+~[A-Z]+")


def _to_list_view(search_url: str) -> str:
    """Normalize a OneHome saved-search URL to the /properties/list route.

    The map view only renders properties inside the current map viewport; the list
    view covers the whole result set. Query params (token, searchId) are preserved.
    """
    return re.sub(r"/properties/(map|gallery)\b", "/properties/list", search_url)


def _reconstruct_property_url(aotf_id: str, search_url: str) -> str:
    """Build a full property URL from an aotf id + the search URL's token/searchId.

    aotf_id is the full token captured by _AOTF_RE, e.g. 'aotf~123~HIGH'.
    """
    q = parse_qs(urlparse(search_url).query)
    token = q.get("token", [""])[0]
    search_id = q.get("searchId", [""])[0]
    url = f"https://portal.onehome.com/en-US/property/{aotf_id}"
    parts = []
    if token:
        parts.append(f"token={token}")
    if search_id:
        parts.append(f"searchId={search_id}")
    return url + ("?" + "&".join(parts) if parts else "")


_LISTING_ID_RE = re.compile(r'"id":"(aotf~\d+~[A-Z]+)"')


def _is_single_property_url(url: str) -> bool:
    return "/property/aotf~" in url


async def collect_search_property_urls(
    page: Page, search_url: str, limit: Optional[int] = None, max_scrolls: int = 60
) -> list[str]:
    """Walk a saved OneHome search and return every property URL in it.

    Primary path: the Angular app fetches results with a `GetSavedListings` GraphQL
    call whose request carries the full list of member listing ids. We capture that
    request (query + auth), then replay it in chunks of 25 (the server page size) to
    enumerate every listing's `aotf~…` id — no scrolling / virtual-list guesswork.
    Falls back to scroll-harvesting result-card anchors if the call isn't seen.

    Also accepts a single-property link or a `consumer-share/…` link: the former is
    returned as-is; the latter is opened and followed to wherever it redirects
    (a property → that one; a search view → collected normally).
    """
    # A direct property link isn't a search — just analyze that one.
    if _is_single_property_url(search_url):
        return [search_url]

    # A "share" link (what the OneHome Share button produces) must be resolved.
    if "/consumer-share/" in search_url:
        await _safe_goto(page, search_url)
        for _ in range(10):  # wait for the redirect away from /consumer-share/
            if "/consumer-share/" not in page.url:
                break
            await asyncio.sleep(0.5)
        resolved = page.url
        if _is_single_property_url(resolved):
            log.info("Share link resolved to a single property.")
            return [resolved]
        if "/properties/" in resolved:
            search_url = resolved  # a shared search view — collect from it
        else:
            log.warning("Share link did not resolve to a search or property: %s", resolved)
            return []

    list_url = _to_list_view(search_url)

    captured: dict = {}

    def _on_request(req):
        try:
            if "graphql" in req.url and req.post_data and "GetSavedListings" in req.post_data:
                captured["url"] = req.url
                captured["headers"] = dict(req.headers)
                captured["body"] = json.loads(req.post_data)
        except Exception:
            pass

    page.on("request", _on_request)
    try:
        await _safe_goto(page, list_url)
        await asyncio.sleep(4)
        # Give the results query a chance to fire.
        for _ in range(12):
            if captured.get("body"):
                break
            await asyncio.sleep(0.5)
    finally:
        page.remove_listener("request", _on_request)

    ordered_ids: list[str] = []
    seen: set[str] = set()

    body = captured.get("body")
    if body:
        all_ids = body.get("variables", {}).get("listingIds", []) or []
        headers = {k: v for k, v in captured.get("headers", {}).items()
                   if k.lower() in ("authorization", "content-type")}
        log.info("Search has %d saved listings; fetching via GraphQL replay", len(all_ids))
        for i in range(0, len(all_ids), 25):
            chunk = all_ids[i:i + 25]
            payload = json.loads(json.dumps(body))          # deep copy
            payload["variables"]["listingIds"] = chunk
            payload["variables"]["suppressEvent"] = True
            try:
                resp = await page.request.post(captured["url"], headers=headers, data=json.dumps(payload))
                text = await resp.text()
            except Exception as exc:
                log.warning("GraphQL replay chunk failed: %s", exc)
                continue
            for aotf_id in _LISTING_ID_RE.findall(text):
                if aotf_id not in seen:
                    seen.add(aotf_id)
                    ordered_ids.append(aotf_id)
            if limit is not None and len(ordered_ids) >= limit:
                break

    # Fallback: scroll-harvest result-card anchors if the GraphQL path yielded nothing.
    if not ordered_ids:
        log.info("Falling back to scroll-harvest of result cards")
        plateau = 0
        for step in range(max_scrolls):
            hrefs = await page.evaluate(
                """() => Array.from(document.querySelectorAll("a[href*='/property/aotf']"))
                          .map(a => a.getAttribute('href')).filter(Boolean)"""
            )
            before = len(seen)
            for href in hrefs:
                m = _AOTF_RE.search(href)
                if m and m.group(0) not in seen:
                    seen.add(m.group(0))
                    ordered_ids.append(m.group(0))
            if limit is not None and len(ordered_ids) >= limit:
                break
            plateau = plateau + 1 if len(seen) == before else 0
            if plateau >= 6:
                break
            await page.evaluate(
                """() => {
                    const v = document.querySelector('cdk-virtual-scroll-viewport')
                      || [...document.querySelectorAll('*')]
                           .filter(e => e.scrollHeight > e.clientHeight + 100)
                           .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                    if (v) v.scrollTop = v.scrollTop + v.clientHeight * 0.9;
                }"""
            )
            await asyncio.sleep(random.uniform(0.8, 1.3))

    urls = [_reconstruct_property_url(aotf_id, search_url) for aotf_id in ordered_ids]
    log.info("Collected %d property URLs from search", len(urls))
    return urls[:limit] if limit is not None else urls


# Backwards-compatible alias
async def paginate_search_results(page: Page, search_url: str, max_pages: int = 10) -> list[str]:
    return await collect_search_property_urls(page, search_url)


async def scrape_listings(
    urls: list[str],
    email: str,
    password: str,
    headless: bool = True,
    use_cache: bool = True,
) -> list[PropertyListing]:
    results: list[PropertyListing] = []

    # Separate cached from to-fetch
    to_fetch = []
    for url in urls:
        if use_cache:
            cached = _load_cache(url)
            if cached:
                log.info("Cache hit: %s", url)
                results.append(cached)
                continue
        to_fetch.append(url)

    if not to_fetch:
        return results

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

        for url in to_fetch:
            try:
                listing = await scrape_listing_with_retry(page, url, email=email, password=password)
                if use_cache:
                    _save_cache(listing)
                results.append(listing)
                log.info("Scraped: %s", listing.address)
            except Exception as exc:
                log.warning("Failed to scrape %s: %s", url, exc)

            await asyncio.sleep(random.uniform(1.5, 3.5))

        await browser.close()

    return results
