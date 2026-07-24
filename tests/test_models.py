import pytest

from rentalanalysis.models import AnalysisConfig, LoanConfig, PropertyListing


def test_price_coercion_string():
    listing = PropertyListing(
        url="https://example.com",
        address="123 Test St",
        list_price="$450,000",
        beds=3,
        baths=2,
    )
    assert listing.list_price == pytest.approx(450_000.0)


def test_price_coercion_int():
    listing = PropertyListing(
        url="https://example.com",
        address="123 Test St",
        list_price=300000,
        beds=3,
        baths=2,
    )
    assert listing.list_price == pytest.approx(300_000.0)


def test_taxes_coercion_string():
    listing = PropertyListing(
        url="https://example.com",
        address="123 Test St",
        list_price=300_000,
        beds=3,
        baths=2,
        annual_taxes="$6,500",
    )
    assert listing.annual_taxes == pytest.approx(6_500.0)


def test_slug_filesystem_safe(sample_listing):
    slug = sample_listing.slug
    assert len(slug) <= 31
    assert "/" not in slug
    assert "#" not in slug


def test_analysis_config_defaults():
    config = AnalysisConfig()
    assert config.loan.down_payment_pct == pytest.approx(0.20)
    assert config.expenses.vacancy_rate == pytest.approx(0.05)
    assert config.property_overrides == {}


def test_analysis_config_from_yaml(tmp_path, sample_config):
    assert sample_config.loan.interest_rate == pytest.approx(0.07)
    assert sample_config.expenses.property_mgmt_pct == pytest.approx(0.10)
    assert "https://portal.onehome.com/listings/12345" in sample_config.property_overrides


def test_listing_optional_fields():
    listing = PropertyListing(
        url="https://example.com",
        address="Minimal Property",
        list_price=200_000,
        beds=2,
        baths=1,
    )
    assert listing.sqft is None
    assert listing.annual_taxes is None
    assert listing.estimated_rent_monthly is None
    assert listing.hoa_monthly == 0.0
    assert listing.photo_urls == []
