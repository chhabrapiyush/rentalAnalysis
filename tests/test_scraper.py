from rentalanalysis.scraper import _to_list_view, _reconstruct_property_url, _is_single_property_url


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
