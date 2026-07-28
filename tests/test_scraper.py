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


def test_reconstruct_property_url_carries_token_and_search():
    search = "https://portal.onehome.com/en-US/properties/map?token=TOK123&searchId=SID456"
    out = _reconstruct_property_url("aotf~999~HIGH", search)
    assert "aotf~999~HIGH" in out
    assert "token=TOK123" in out
    assert "searchId=SID456" in out
