import pytest

from rentalanalysis.calculator import (
    analyze_property,
    apply_property_overrides,
    compute_amortization_schedule,
    compute_monthly_payment,
    detect_non_rentable,
    evaluate_deal,
)
from rentalanalysis.models import AnalysisConfig, LoanConfig, PropertyListing


def test_monthly_payment_known_value():
    # $300k at 7% for 30 years → ~$1,995.91
    result = compute_monthly_payment(300_000, 0.07, 30)
    assert abs(result - 1995.91) < 0.02


def test_monthly_payment_zero_rate():
    result = compute_monthly_payment(120_000, 0.0, 10)
    assert abs(result - 1000.0) < 0.01


def test_amortization_schedule_length():
    schedule = compute_amortization_schedule(300_000, 0.07, 30)
    assert len(schedule) == 360


def test_amortization_year1_interest_heavy():
    schedule = compute_amortization_schedule(300_000, 0.07, 30)
    first = schedule[0]
    # First month: interest should exceed principal
    assert first["interest"] > first["principal"]
    # Balance should be less than original
    assert first["balance"] < 300_000


def test_amortization_final_balance_near_zero():
    schedule = compute_amortization_schedule(300_000, 0.07, 30)
    assert schedule[-1]["balance"] < 1.0


def test_analyze_property_basic(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)

    # GRI = 2500 * 12 = 30,000
    assert result.gross_rental_income == pytest.approx(30_000, abs=0.01)

    # Vacancy = 5% → 1,500
    assert result.vacancy_loss == pytest.approx(1_500, abs=0.01)

    # EGI = 28,500
    assert result.effective_gross_income == pytest.approx(28_500, abs=0.01)

    # Loan = 400,000 * 0.80 = 320,000
    assert result.loan_amount == pytest.approx(320_000, abs=0.01)

    # Closing costs = 3% of 400,000 = 12,000
    assert result.closing_costs == pytest.approx(12_000, abs=0.01)
    # Total cash invested = 80,000 down + 12,000 closing = 92,000
    assert result.total_cash_invested == pytest.approx(92_000, abs=0.01)

    # NOI = EGI - operating expenses (excludes CapEx reserves)
    assert result.noi == pytest.approx(
        result.effective_gross_income - result.operating_expenses, abs=0.01
    )

    # CapEx reserve is pre-filled (5% of gross = 1,500) but OFF by default, so it is
    # NOT added to the total net operating expenses.
    assert result.capex_reserve == pytest.approx(1_500, abs=0.01)
    assert result.capex_on is False
    assert result.total_net_operating_expenses == pytest.approx(
        result.operating_expenses_used, abs=0.01
    )

    # Effective NOI = EGI - total net operating expenses (drives DSCR / debt sizing)
    assert result.effective_noi == pytest.approx(
        result.effective_gross_income - result.total_net_operating_expenses, abs=0.01
    )

    # Cash flow is leveraged off Effective NOI
    assert result.cash_flow_annual == pytest.approx(
        result.effective_noi - result.annual_debt_service, abs=0.01
    )
    assert result.cash_flow_monthly == pytest.approx(result.cash_flow_annual / 12, abs=0.01)


def test_analyze_property_expense_breakdown(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)

    # Taxes from listing = 6,000
    assert result.taxes_annual == pytest.approx(6_000, abs=0.01)

    # HOA = 100/mo * 12 = 1,200
    assert result.hoa_annual == pytest.approx(1_200, abs=0.01)

    # Mgmt = 10% of GRI = 3,000
    assert result.mgmt_fee_annual == pytest.approx(3_000, abs=0.01)

    # Maintenance = 10% of GRI = 3,000 (no listed value → the % assumption)
    assert result.maintenance_annual == pytest.approx(3_000, abs=0.01)

    # Insurance = per-unit basis (500 * 1 unit) since no listed insurance
    assert result.insurance_annual == pytest.approx(500, abs=0.01)

    # Itemized IRE utilities: trash 360, sewer 600, recycle 50 (1 unit), water 0
    assert result.trash_annual == pytest.approx(360, abs=0.01)
    assert result.sewer_annual == pytest.approx(600, abs=0.01)
    assert result.recycle_annual == pytest.approx(50, abs=0.01)
    assert result.water_annual == pytest.approx(0, abs=0.01)


def test_analyze_property_cap_rate(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)
    expected_cap_rate = result.noi / 400_000
    assert result.cap_rate == pytest.approx(expected_cap_rate, abs=1e-6)


def test_analyze_property_dscr(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)
    # DSCR uses Effective NOI (includes CapEx reserves), matching the IRE proforma
    expected_dscr = result.effective_noi / result.annual_debt_service
    assert result.dscr == pytest.approx(expected_dscr, abs=1e-4)


def test_analyze_property_rent_override(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config, monthly_rent_override=3000)
    assert result.gross_rental_income == pytest.approx(36_000, abs=0.01)


def test_analyze_property_price_override(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config, purchase_price_override=350_000)
    assert result.loan_amount == pytest.approx(280_000, abs=0.01)


def test_analyze_property_no_rent_raises(sample_config):
    listing = PropertyListing(
        url="https://example.com/1",
        address="No Rent Ave",
        list_price=300_000,
        beds=3,
        baths=2,
        estimated_rent_monthly=None,
    )
    with pytest.raises(ValueError, match="No rent estimate"):
        analyze_property(listing, sample_config)


def test_apply_property_overrides(sample_listing, sample_config):
    new_listing, new_config = apply_property_overrides(sample_listing, sample_config)
    # Override sets estimated_rent_monthly=2800, annual_taxes=6200
    assert new_listing.estimated_rent_monthly == pytest.approx(2_800, abs=0.01)
    assert new_listing.annual_taxes == pytest.approx(6_200, abs=0.01)
    # Original config should not be mutated
    assert sample_config.expenses.insurance_annual == pytest.approx(1_500, abs=0.01)


def test_apply_property_overrides_no_match(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"url": "https://other.com/999"})
    new_listing, new_config = apply_property_overrides(listing, sample_config)
    assert new_listing.estimated_rent_monthly == sample_listing.estimated_rent_monthly


def test_insurance_scales_with_units(sample_listing, sample_config):
    # 3 units → insurance = 500 * 3 = 1,500; recycle = 50 * 3 = 150
    listing = sample_listing.model_copy(update={"total_units": 3})
    result = analyze_property(listing, sample_config)
    assert result.insurance_annual == pytest.approx(1_500, abs=0.01)
    assert result.recycle_annual == pytest.approx(150, abs=0.01)


def test_listed_insurance_preferred_over_per_unit(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"total_units": 5, "insurance_annual_listed": 1069})
    result = analyze_property(listing, sample_config)
    assert result.insurance_annual == pytest.approx(1_069, abs=0.01)
    assert result.insurance_source == "OneHome"


def test_opex_check_flags_when_calc_exceeds_listed(sample_listing, sample_config):
    # Listing claims a very high NOI → low implied OpEx → our calc should exceed it
    listing = sample_listing.model_copy(
        update={"gross_income_annual_listed": 30_000, "noi_listed": 28_000}
    )
    result = analyze_property(listing, sample_config)
    # Listed implied OpEx = 30,000 - 28,000 = 2,000
    assert result.listed_operating_expenses == pytest.approx(2_000, abs=0.01)
    assert result.opex_variance == pytest.approx(result.operating_expenses - 2_000, abs=0.01)
    assert result.opex_exceeds_listed is True


def test_opex_check_not_flagged_when_listed_expenses_high(sample_listing, sample_config):
    # Listing claims a low NOI → high implied OpEx → our calc should NOT exceed it
    listing = sample_listing.model_copy(
        update={"gross_income_annual_listed": 30_000, "noi_listed": 5_000}
    )
    result = analyze_property(listing, sample_config)
    # Listed implied OpEx = 25,000, well above our calculated OpEx
    assert result.listed_operating_expenses == pytest.approx(25_000, abs=0.01)
    assert result.opex_exceeds_listed is False


def test_opex_check_absent_without_listing_data(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)
    assert result.listed_operating_expenses is None
    assert result.opex_variance is None
    assert result.opex_exceeds_listed is False


def test_opex_prefers_onehome_operating_expense(sample_listing, sample_config):
    # When OneHome gives an explicit "Operating Expense", use it over Gross − NOI
    listing = sample_listing.model_copy(update={
        "gross_income_annual_listed": 57_000,
        "noi_listed": 40_180,          # implied OpEx would be 16,820
        "operating_expense_listed": 17_570,  # but OneHome states 17,570 directly
    })
    result = analyze_property(listing, sample_config)
    assert result.listed_operating_expenses == pytest.approx(17_570, abs=0.01)
    assert result.listed_opex_source == "OneHome (Operating Expense)"


def test_opex_falls_back_to_gross_minus_noi(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={
        "gross_income_annual_listed": 57_000,
        "noi_listed": 40_180,
    })
    result = analyze_property(listing, sample_config)
    assert result.listed_operating_expenses == pytest.approx(16_820, abs=0.01)
    assert result.listed_opex_source == "OneHome (Gross - NOI)"


def test_rent_source_from_rent_roll(sample_listing, sample_config):
    from rentalanalysis.models import UnitRent
    listing = sample_listing.model_copy(update={
        "rent_roll": [UnitRent(unit="1", monthly_rent=1500), UnitRent(unit="2", monthly_rent=1000)],
    })
    result = analyze_property(listing, sample_config)
    assert result.rent_source == "OneHome (rent roll)"


def test_rent_source_from_gross_income(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"gross_income_annual_listed": 30_000})
    result = analyze_property(listing, sample_config)
    assert result.rent_source == "OneHome (gross income)"


def test_rent_source_assumed_on_override(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"gross_income_annual_listed": 30_000})
    result = analyze_property(listing, sample_config, monthly_rent_override=2000)
    assert result.rent_source == "Assumed"


def test_income_capped_at_lower_listed_gross(sample_listing, sample_config):
    from rentalanalysis.models import UnitRent
    # Rent roll implies 60,600/yr but the listing states 57,000 — use the lower (listed)
    listing = sample_listing.model_copy(update={
        "rent_roll": [UnitRent(unit="1", monthly_rent=5050)],
        "estimated_rent_monthly": 5050,       # 60,600/yr
        "gross_income_annual_listed": 57_000,
    })
    result = analyze_property(listing, sample_config)
    assert result.rent_roll_annual == pytest.approx(60_600, abs=0.01)
    assert result.gross_rental_income == pytest.approx(57_000, abs=0.01)
    assert "Listed" in result.income_basis


def test_income_uses_rent_roll_when_listed_is_higher(sample_listing, sample_config):
    from rentalanalysis.models import UnitRent
    listing = sample_listing.model_copy(update={
        "rent_roll": [UnitRent(unit="1", monthly_rent=4000)],
        "estimated_rent_monthly": 4000,       # 48,000/yr
        "gross_income_annual_listed": 57_000,  # higher — do not inflate to it
    })
    result = analyze_property(listing, sample_config)
    assert result.gross_rental_income == pytest.approx(48_000, abs=0.01)


def test_expense_uses_greater_of_listed_or_calculated(sample_listing, sample_config):
    # Listing states a higher operating expense than our itemized build-up → use listed
    listing = sample_listing.model_copy(update={"operating_expense_listed": 25_000})
    result = analyze_property(listing, sample_config)
    assert result.operating_expenses < 25_000            # itemized is lower
    assert result.operating_expenses_used == pytest.approx(25_000, abs=0.01)
    assert "Listed" in result.opex_basis
    # NOI is driven by the higher (used) expense
    assert result.noi == pytest.approx(result.effective_gross_income - 25_000, abs=0.01)


def test_expense_uses_calculated_when_higher(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"operating_expense_listed": 1_000})
    result = analyze_property(listing, sample_config)
    assert result.operating_expenses_used == pytest.approx(result.operating_expenses, abs=0.01)
    assert result.opex_basis == "Calculated"


def test_no_rent_raises_without_fallback(sample_config):
    listing = PropertyListing(
        url="https://example.com/x", address="No Income Ln", list_price=300_000,
        beds=3, baths=2, estimated_rent_monthly=None,
    )
    with pytest.raises(ValueError, match="No rent estimate"):
        analyze_property(listing, sample_config)


def test_fallback_rent_used_and_flagged(sample_config):
    listing = PropertyListing(
        url="https://example.com/x", address="No Income Ln", list_price=300_000,
        beds=3, baths=2, estimated_rent_monthly=None,
    )
    result = analyze_property(listing, sample_config, use_fallback_rent=True)
    # 0.7% of 300k = 2,100/mo → 25,200/yr
    assert result.data_complete is False
    assert result.gross_rental_income == pytest.approx(25_200, abs=0.01)
    assert result.rent_source == "Assumed (fallback)"
    assert result.data_notes


def test_fallback_not_used_when_rent_present(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config, use_fallback_rent=True)
    assert result.data_complete is True


def test_non_rentable_detected_by_land_keyword():
    listing = PropertyListing(
        url="https://x/land", address="Empty Lot Rd", list_price=100_000,
        beds=3, baths=2, sqft=1200, estimated_rent_monthly=1500,
        property_type="Vacant Land",
    )
    reason = detect_non_rentable(listing)
    assert reason is not None and "land" in reason.lower()


def test_non_rentable_detected_by_structural_signal():
    # The 806 Quincy pattern: mis-typed "Residential Income" but 0 bd / 0 ba / no sqft.
    listing = PropertyListing(
        url="https://x/quincy", address="806 Quincy St #802", list_price=149_999,
        beds=0, baths=0, sqft=None, estimated_rent_monthly=5000,
        property_type="Residential Income", total_units=2,
    )
    assert detect_non_rentable(listing) is not None


def test_normal_listing_not_flagged_non_rentable(sample_listing):
    assert detect_non_rentable(sample_listing) is None


def test_non_rentable_result_excluded_from_grading(sample_config):
    listing = PropertyListing(
        url="https://x/quincy", address="806 Quincy St #802", list_price=149_999,
        beds=0, baths=0, sqft=None, estimated_rent_monthly=5000,
        property_type="Residential Income", total_units=2,
    )
    result = analyze_property(listing, sample_config)
    assert result.non_rentable is True
    assert result.non_rentable_reason
    # Even with an absurdly high CoC, the verdict is EXCLUDED, not GO.
    assert evaluate_deal(result, sample_config.targets)["verdict"] == "EXCLUDED"


def test_maintenance_uses_greater_of_listed_or_10pct(sample_listing, sample_config):
    # Listed maintenance ($800) is below 10% of gross ($3,000) → use 10%
    low = sample_listing.model_copy(update={"maintenance_annual_listed": 800})
    r_low = analyze_property(low, sample_config)
    assert r_low.maintenance_annual == pytest.approx(3_000, abs=0.01)

    # Listed maintenance ($5,000) exceeds 10% → keep the listed (greater) value
    high = sample_listing.model_copy(update={"maintenance_annual_listed": 5_000})
    r_high = analyze_property(high, sample_config)
    assert r_high.maintenance_annual == pytest.approx(5_000, abs=0.01)
    assert r_high.maintenance_source == "OneHome"


def test_maintenance_and_capex_off_by_default(sample_listing, sample_config):
    result = analyze_property(sample_listing, sample_config)
    # Pre-filled values exist...
    assert result.maintenance_annual == pytest.approx(3_000, abs=0.01)
    assert result.capex_reserve == pytest.approx(1_500, abs=0.01)
    # ...but neither is counted: operating expenses exclude the 3,000 maintenance,
    # and total net = operating expenses used (no capex added).
    assert result.maintenance_on is False and result.capex_on is False
    with_maint = result.operating_expenses + result.maintenance_annual
    # Sanity: adding maintenance back would raise the total by exactly 3,000
    assert with_maint - result.operating_expenses == pytest.approx(3_000, abs=0.01)
    assert result.total_net_operating_expenses == pytest.approx(result.operating_expenses_used, abs=0.01)


def test_maintenance_and_capex_on_when_enabled(sample_listing, sample_config):
    cfg = sample_config.model_copy(deep=True)
    cfg.expenses.maintenance_on = True
    cfg.expenses.capex_on = True
    off = analyze_property(sample_listing, sample_config)
    on = analyze_property(sample_listing, cfg)
    # Maintenance (3,000) now included in operating expenses
    assert on.operating_expenses == pytest.approx(off.operating_expenses + 3_000, abs=0.01)
    # CapEx (1,500) now included in total net operating expenses
    assert on.total_net_operating_expenses == pytest.approx(
        on.operating_expenses_used + on.capex_reserve, abs=0.01
    )
    # Turning them on makes the deal look worse (lower NOI, cap rate)
    assert on.noi < off.noi
    assert on.effective_noi < off.effective_noi


def test_electric_expense_added_to_opex(sample_listing, sample_config):
    listing = sample_listing.model_copy(update={"electric_expense_listed": 350})
    base = analyze_property(sample_listing, sample_config)
    result = analyze_property(listing, sample_config)
    assert result.electric_annual == pytest.approx(350, abs=0.01)
    # Operating expenses should rise by exactly the electric amount
    assert result.operating_expenses == pytest.approx(base.operating_expenses + 350, abs=0.01)


def test_misc_listed_expenses_from_details(sample_listing, sample_config):
    # An unmodelled "* Expense" field on the listing lands in the miscellaneous bucket
    listing = sample_listing.model_copy(update={
        "listing_details": {"Gas Expense": "$1,200", "Zoning": "R-Mh", "Electric Expense": "$350"},
        "electric_expense_listed": 350,
    })
    result = analyze_property(listing, sample_config)
    # Gas Expense is misc; Electric is handled separately (not double-counted); Zoning ignored
    assert result.misc_expense_items == {"Gas Expense": 1200.0}
    assert result.misc_listed_expenses == pytest.approx(1200, abs=0.01)
