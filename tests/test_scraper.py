from rentalanalysis.scraper import (
    _to_list_view, _reconstruct_property_url, _is_single_property_url,
    _safe_float, _safe_int,
)


def test_safe_float_extracts_from_messy_strings():
    assert _safe_float("$1,850/mo") == 1850.0
    assert _safe_float("$261.90") == 261.90
    assert _safe_float("57,000") == 57000.0
    assert _safe_float("N/A") is None
    assert _safe_float(None) is None
    assert _safe_float("Brick") is None


def test_safe_int_handles_unit_suffix():
    assert _safe_int("2 units") == 2
    assert _safe_int("5") == 5
    assert _safe_int("--") is None
    assert _safe_int(None) is None


def test_detects_single_property_url():
    assert _is_single_property_url("https://portal.onehome.com/en-US/property/aotf~123~HIGH?token=x")
    assert not _is_single_property_url("https://portal.onehome.com/en-US/properties/map?token=x")
    assert not _is_single_property_url("https://portal.onehome.com/en-US/consumer-share/abc123")


def test_map_url_normalized_to_list():
    url = "https://portal.onehome.com/en-US/properties/map?token=abc&searchId=xyz"
    assert _to_list_view(url) == "https://portal.onehome.com/en-US/properties/list?token=abc&searchId=xyz"


def test_gallery_url_normalized_to_list():
    url = "https://portal.onehome.com/en-US/properties/gallery?token=abc&searchId=xyz"
    assert "/properties/list?" in _to_list_view(url)


def test_list_url_unchanged():
    url = "https://portal.onehome.com/en-US/properties/list?token=abc&searchId=xyz"
    assert _to_list_view(url) == url


def _stub_listing(price):
    from rentalanalysis.models import PropertyListing
    return PropertyListing(url="u", address="A", list_price=price, beds=1, baths=1)


def test_retry_recovers_after_transient_failure(monkeypatch):
    import asyncio
    from rentalanalysis import scraper
    calls = {"n": 0}

    async def flaky(page, url, email="", password=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient blip")
        return _stub_listing(100000)

    monkeypatch.setattr(scraper, "scrape_listing", flaky)
    result = asyncio.run(scraper.scrape_listing_with_retry(None, "u", base_delay=0))
    assert result.list_price == 100000
    assert calls["n"] == 2  # failed once, retried, succeeded


def test_retry_reraises_after_exhausting(monkeypatch):
    import asyncio
    import pytest as _pytest
    from rentalanalysis import scraper

    async def always_fail(page, url, email="", password=""):
        raise RuntimeError("down")

    monkeypatch.setattr(scraper, "scrape_listing", always_fail)
    with _pytest.raises(RuntimeError):
        asyncio.run(scraper.scrape_listing_with_retry(None, "u", retries=1, base_delay=0))


def test_retry_on_zero_price_returns_last_attempt(monkeypatch):
    import asyncio
    from rentalanalysis import scraper
    calls = {"n": 0}

    async def zero_price(page, url, email="", password=""):
        calls["n"] += 1
        return _stub_listing(0)

    monkeypatch.setattr(scraper, "scrape_listing", zero_price)
    result = asyncio.run(scraper.scrape_listing_with_retry(None, "u", retries=2, base_delay=0))
    assert result.list_price == 0
    assert calls["n"] == 3  # all attempts used before giving up


def test_reconstruct_property_url_carries_token_and_search():
    search = "https://portal.onehome.com/en-US/properties/map?token=TOK123&searchId=SID456"
    out = _reconstruct_property_url("aotf~999~HIGH", search)
    assert "aotf~999~HIGH" in out
    assert "token=TOK123" in out
    assert "searchId=SID456" in out
