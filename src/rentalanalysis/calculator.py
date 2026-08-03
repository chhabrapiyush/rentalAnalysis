from __future__ import annotations

import copy
import re
from typing import Optional

from .models import AnalysisConfig, AnalysisResult, PropertyListing, TargetConfig

# OneHome "* Expense" fields that already have dedicated handling in the model.
_HANDLED_EXPENSE_KEYS = {
    "insurance expense",
    "maintenance expense",
    "electric expense",
    "operating expense",   # this is the total, not a line item
}


# Values (in Type / Property Type / Sub Type / Zoning / Condition fields) that mark a
# listing as not an operating rental. MLS records are frequently mis-typed at the top
# level (e.g. a bare lot tagged "Residential Income"), so we also fall back to a
# structural check in detect_non_rentable().
_LAND_KEYWORDS = ("vacant land", "vacant lot", "raw land", "land", "acreage",
                  "farm", "agricultural", "unimproved", "to be built", "pre-construction",
                  "pre construction", "proposed construction")

# OneHome detail keys whose *values* we scan for the land keywords above.
_TYPE_DETAIL_KEYS = ("type", "property type", "property sub type", "additional property type",
                     "property subtype", "sub type", "structure type")


def detect_non_rentable(listing: PropertyListing) -> Optional[str]:
    """Return a human-readable reason if the listing is not an operating rental
    (land, vacant lot, or pre-construction), else None.

    Two independent signals — either one flags the listing:
      1. An explicit land/pre-construction keyword in any type/subtype field.
      2. Structural implausibility: no beds, no baths, no sqft, and no rent roll —
         a dwelling you can rent always has at least one of these. This catches the
         common case of a lot mis-typed as "Residential Income" (e.g. 806 Quincy).
    """
    details = listing.listing_details or {}

    # 1. Keyword scan across the top-level type and the detail type/subtype fields.
    candidates = [listing.property_type or ""]
    for label, value in details.items():
        if label.strip().rstrip(":").lower() in _TYPE_DETAIL_KEYS and value:
            candidates.append(str(value))
    haystack = " ".join(candidates).lower()
    for kw in _LAND_KEYWORDS:
        if kw in haystack:
            return f"Listing type indicates land / pre-construction ({kw!r}) — not an operating rental."

    # 2. Structural check — nothing habitable described.
    no_beds = not listing.beds
    no_baths = not listing.baths
    no_sqft = not listing.sqft
    no_roll = not listing.rent_roll
    if no_beds and no_baths and no_sqft and no_roll:
        return ("No habitable units in the listing data (0 bd / 0 ba, no sqft, no rent roll) "
                "— likely land or pre-construction; income metrics not meaningful.")
    return None


def _parse_currency(value: str) -> Optional[float]:
    if value is None:
        return None
    try:
        cleaned = re.sub(r"[^0-9.\-]", "", str(value))
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except (ValueError, TypeError):
        return None


def _collect_misc_listed_expenses(listing: PropertyListing) -> tuple[dict[str, float], float]:
    """Any OneHome '* Expense' field not already modelled → miscellaneous bucket."""
    items: dict[str, float] = {}
    for label, raw in (listing.listing_details or {}).items():
        key = label.strip().rstrip(":").lower()
        if key.endswith("expense") and key not in _HANDLED_EXPENSE_KEYS:
            amount = _parse_currency(raw)
            if amount:
                items[label.strip().rstrip(":")] = round(amount, 2)
    return items, round(sum(items.values()), 2)


def compute_monthly_payment(principal: float, annual_rate: float, term_years: int) -> float:
    n = term_years * 12
    if annual_rate == 0:
        return principal / n
    r = annual_rate / 12
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def compute_amortization_schedule(
    principal: float, annual_rate: float, term_years: int
) -> list[dict]:
    n = term_years * 12
    monthly = compute_monthly_payment(principal, annual_rate, term_years)
    r = annual_rate / 12
    balance = principal
    schedule = []
    for month in range(1, n + 1):
        interest = balance * r
        principal_paid = monthly - interest
        balance = max(0.0, balance - principal_paid)
        schedule.append(
            {
                "month": month,
                "payment": round(monthly, 2),
                "principal": round(principal_paid, 2),
                "interest": round(interest, 2),
                "balance": round(balance, 2),
            }
        )
    return schedule


def apply_property_overrides(
    listing: PropertyListing,
    config: AnalysisConfig,
) -> tuple[PropertyListing, AnalysisConfig]:
    override = None
    for key, val in config.property_overrides.items():
        if listing.url.startswith(key) or key in listing.url:
            override = val
            break

    if not override:
        return listing, config

    config_copy = copy.deepcopy(config)
    listing_data = listing.model_dump()

    listing_fields = set(PropertyListing.model_fields.keys())
    loan_override = override.pop("loan", None)
    expense_override = override.pop("expenses", None)

    for k, v in override.items():
        if k in listing_fields:
            listing_data[k] = v

    new_listing = PropertyListing.model_validate(listing_data)

    if loan_override:
        loan_data = config_copy.loan.model_dump()
        loan_data.update(loan_override)
        config_copy.loan = config_copy.loan.model_validate(loan_data)

    if expense_override:
        exp_data = config_copy.expenses.model_dump()
        exp_data.update(expense_override)
        config_copy.expenses = config_copy.expenses.model_validate(exp_data)

    return new_listing, config_copy


def analyze_property(
    listing: PropertyListing,
    config: AnalysisConfig,
    monthly_rent_override: Optional[float] = None,
    purchase_price_override: Optional[float] = None,
    use_fallback_rent: bool = False,
) -> AnalysisResult:
    purchase_price = purchase_price_override if purchase_price_override is not None else listing.list_price
    monthly_rent = monthly_rent_override if monthly_rent_override is not None else listing.estimated_rent_monthly

    data_complete = True
    data_notes: list[str] = []
    fallback_pct = config.fallback_rent_monthly_pct

    # Land / vacant-lot / pre-construction marker. Metrics are still computed (so the
    # sheet is populated) but the deal is excluded from grading and flagged everywhere.
    non_rentable_reason = detect_non_rentable(listing)
    non_rentable = non_rentable_reason is not None
    if non_rentable:
        data_notes.append(f"⚫ NON-RENTABLE: {non_rentable_reason}")

    if monthly_rent is None:
        # Batch runs fall back to an assumed rent so no listing is dropped; single
        # calls stay strict and raise so the caller can supply an explicit rent.
        if use_fallback_rent and purchase_price > 0 and fallback_pct > 0:
            monthly_rent = purchase_price * fallback_pct
            data_complete = False
            data_notes.append(
                f"No OneHome income data — rent assumed at {fallback_pct:.2%} of price/mo."
            )
        else:
            raise ValueError(
                f"No rent estimate for {listing.address!r}. "
                "Provide --rent on the CLI or set estimated_rent_monthly in property_overrides."
            )

    # Where did the rent come from? Overrides are user assumptions; otherwise prefer
    # the OneHome rent roll, then the OneHome gross income figure.
    if not data_complete:
        rent_source = "Assumed (fallback)"
    elif monthly_rent_override is not None:
        rent_source = "Assumed"
    elif listing.rent_roll and any(u.monthly_rent for u in listing.rent_roll):
        rent_source = "OneHome (rent roll)"
    elif listing.gross_income_annual_listed:
        rent_source = "OneHome (gross income)"
    else:
        rent_source = "Assumed"

    loan = config.loan
    exp = config.expenses

    loan_amount = purchase_price * (1 - loan.down_payment_pct)
    down_payment = purchase_price * loan.down_payment_pct
    closing_costs = purchase_price * loan.closing_costs_pct
    total_cash_invested = down_payment + closing_costs
    ltv = loan_amount / purchase_price if purchase_price else 0.0

    monthly_payment = compute_monthly_payment(loan_amount, loan.interest_rate, loan.loan_term_years)
    annual_debt_service = monthly_payment * 12

    # Conservative income basis: if OneHome lists a lower Gross Income than the rent roll
    # implies, use the lower (listed) figure. Never overstate income.
    rent_roll_annual = monthly_rent * 12
    if (monthly_rent_override is None and listing.gross_income_annual_listed is not None
            and listing.gross_income_annual_listed < rent_roll_annual):
        gross_rental_income = listing.gross_income_annual_listed
        income_basis = "Listed (lower — conservative)"
    else:
        gross_rental_income = rent_roll_annual
        income_basis = "Calculated" if monthly_rent_override is not None else rent_source

    vacancy_loss = gross_rental_income * exp.vacancy_rate
    effective_gross_income = gross_rental_income - vacancy_loss

    units = listing.total_units or 1
    taxes_annual = listing.annual_taxes or 0.0

    # Insurance: listed → per-unit basis → flat fallback
    if listing.insurance_annual_listed:
        insurance_annual = listing.insurance_annual_listed
        insurance_source = "OneHome"
    elif exp.insurance_per_unit:
        insurance_annual = exp.insurance_per_unit * units
        insurance_source = "Assumed"
    else:
        insurance_annual = exp.insurance_annual
        insurance_source = "Assumed"

    # Maintenance: the greater of the listing's figure and the % assumption
    # (conservative — never understate maintenance). Always computed as the pre-fill
    # value; only counted in expenses when the toggle is on.
    maint_candidate = gross_rental_income * exp.maintenance_reserve_pct
    listed_maint = listing.maintenance_annual_listed or 0.0
    maintenance_annual = max(listed_maint, maint_candidate)
    maintenance_source = "OneHome" if listed_maint >= maint_candidate and listed_maint > 0 else "Assumed"
    maint_applied = maintenance_annual if exp.maintenance_on else 0.0

    mgmt_fee_annual = gross_rental_income * exp.property_mgmt_pct
    hoa_annual = listing.hoa_monthly * 12
    utilities_annual = exp.utilities_monthly * 12

    # Itemized IRE utilities
    trash_annual = exp.trash_annual
    water_annual = exp.water_annual
    sewer_annual = exp.sewer_annual
    recycle_annual = exp.recycle_per_unit * units
    leasing_fee_annual = gross_rental_income * exp.leasing_fee_pct

    # OneHome-listed expenses captured verbatim so nothing on the listing is dropped.
    electric_annual = listing.electric_expense_listed or 0.0
    misc_expense_items, misc_listed_expenses = _collect_misc_listed_expenses(listing)

    # Total Operating Expenses (sample C38 — excludes CapEx reserves)
    operating_expenses = (
        taxes_annual
        + insurance_annual
        + mgmt_fee_annual
        + maint_applied
        + hoa_annual
        + utilities_annual
        + trash_annual
        + water_annual
        + sewer_annual
        + recycle_annual
        + leasing_fee_annual
        + electric_annual
        + misc_listed_expenses
    )

    # OpEx-vs-listing sanity check. Prefer OneHome's own "Operating Expense" figure;
    # fall back to the implied value (gross income - listed NOI).
    listed_operating_expenses: Optional[float] = None
    listed_opex_source: Optional[str] = None
    opex_variance: Optional[float] = None
    opex_exceeds_listed = False
    if listing.operating_expense_listed is not None:
        listed_operating_expenses = listing.operating_expense_listed
        listed_opex_source = "OneHome (Operating Expense)"
    elif listing.gross_income_annual_listed and listing.noi_listed is not None:
        listed_operating_expenses = listing.gross_income_annual_listed - listing.noi_listed
        listed_opex_source = "OneHome (Gross - NOI)"
    if listed_operating_expenses is not None:
        opex_variance = operating_expenses - listed_operating_expenses
        opex_exceeds_listed = operating_expenses > listed_operating_expenses

    # Conservative expense basis: if the listing reports a higher operating expense than
    # our itemised build-up, use the greater (listed). Never understate expenses.
    if listed_operating_expenses is not None and listed_operating_expenses > operating_expenses:
        operating_expenses_used = listed_operating_expenses
        opex_basis = "Listed (higher — conservative)"
    else:
        operating_expenses_used = operating_expenses
        opex_basis = "Calculated"

    # CapEx reserves and Total Net Operating Expenses (sample C39/C40).
    # Always computed as the pre-fill value; only added when the toggle is on.
    capex_reserve = gross_rental_income * exp.capex_reserve_pct
    capex_applied = capex_reserve if exp.capex_on else 0.0
    total_net_operating_expenses = operating_expenses_used + capex_applied

    # Two-tier NOI (sample J41 vs C42) — driven by the conservative expense basis
    noi = effective_gross_income - operating_expenses_used             # cap rate / valuation
    effective_noi = effective_gross_income - total_net_operating_expenses  # DSCR / debt sizing

    cash_flow_annual = effective_noi - annual_debt_service            # leveraged (sample J49)
    cash_flow_monthly = cash_flow_annual / 12

    cap_rate = noi / purchase_price if purchase_price else 0.0
    cash_on_cash = cash_flow_annual / total_cash_invested if total_cash_invested else 0.0
    grm = purchase_price / gross_rental_income if gross_rental_income else 0.0
    dscr = effective_noi / annual_debt_service if annual_debt_service else 0.0

    return AnalysisResult(
        listing=listing,
        data_complete=data_complete,
        data_notes=data_notes,
        non_rentable=non_rentable,
        non_rentable_reason=non_rentable_reason,
        gross_rental_income=round(gross_rental_income, 2),
        rent_roll_annual=round(rent_roll_annual, 2),
        income_basis=income_basis,
        effective_gross_income=round(effective_gross_income, 2),
        vacancy_loss=round(vacancy_loss, 2),
        taxes_annual=round(taxes_annual, 2),
        insurance_annual=round(insurance_annual, 2),
        insurance_source=insurance_source,
        mgmt_fee_annual=round(mgmt_fee_annual, 2),
        maintenance_annual=round(maintenance_annual, 2),
        maintenance_source=maintenance_source,
        hoa_annual=round(hoa_annual, 2),
        utilities_annual=round(utilities_annual, 2),
        trash_annual=round(trash_annual, 2),
        water_annual=round(water_annual, 2),
        sewer_annual=round(sewer_annual, 2),
        recycle_annual=round(recycle_annual, 2),
        leasing_fee_annual=round(leasing_fee_annual, 2),
        electric_annual=round(electric_annual, 2),
        misc_listed_expenses=misc_listed_expenses,
        misc_expense_items=misc_expense_items,
        operating_expenses=round(operating_expenses, 2),
        operating_expenses_used=round(operating_expenses_used, 2),
        opex_basis=opex_basis,
        maintenance_on=exp.maintenance_on,
        capex_on=exp.capex_on,
        capex_reserve=round(capex_reserve, 2),
        total_net_operating_expenses=round(total_net_operating_expenses, 2),
        effective_noi=round(effective_noi, 2),
        rent_source=rent_source,
        noi=round(noi, 2),
        listed_noi=listing.noi_listed,
        listed_gross_income=listing.gross_income_annual_listed,
        listed_operating_expenses=round(listed_operating_expenses, 2) if listed_operating_expenses is not None else None,
        listed_opex_source=listed_opex_source,
        opex_variance=round(opex_variance, 2) if opex_variance is not None else None,
        opex_exceeds_listed=opex_exceeds_listed,
        loan_amount=round(loan_amount, 2),
        monthly_payment=round(monthly_payment, 2),
        annual_debt_service=round(annual_debt_service, 2),
        closing_costs=round(closing_costs, 2),
        cash_flow_annual=round(cash_flow_annual, 2),
        cash_flow_monthly=round(cash_flow_monthly, 2),
        cap_rate=round(cap_rate, 6),
        cash_on_cash=round(cash_on_cash, 6),
        grm=round(grm, 4),
        dscr=round(dscr, 4),
        ltv=round(ltv, 6),
        total_cash_invested=round(total_cash_invested, 2),
    )


def evaluate_deal(result: AnalysisResult, targets: TargetConfig) -> dict:
    """Return pass/fail for each target metric and an overall verdict.

    Non-rentable listings (land / pre-construction) short-circuit to EXCLUDED — their
    income metrics are not meaningful, so a GO/NO-GO grade would be misleading.
    """
    checks = {
        "monthly_cash_flow": (result.cash_flow_monthly, targets.min_monthly_cash_flow, ">="),
        "annual_cash_on_cash": (result.cash_on_cash, targets.min_cash_on_cash_pct, ">="),
        "cap_rate": (result.cap_rate, targets.min_cap_rate_pct, ">="),
        "dscr": (result.dscr, targets.min_dscr, ">="),
        "ltv": (result.ltv, targets.max_ltv, "<="),
    }
    passed = {}
    for key, (actual, target, op) in checks.items():
        passed[key] = actual >= target if op == ">=" else actual <= target

    if getattr(result, "non_rentable", False):
        return {"checks": passed, "verdict": "EXCLUDED",
                "pass_count": sum(passed.values()), "total": len(passed)}

    # Verdict on the two primary gates (cash flow + cash-on-cash):
    #   GO = both pass, NO-GO = neither passes, BORDERLINE = exactly one passes.
    # Kept in sync with the Excel dashboard verdict formula in excel_export.py.
    primary = (passed["monthly_cash_flow"], passed["annual_cash_on_cash"])
    if all(primary):
        verdict = "GO"
    elif not any(primary):
        verdict = "NO-GO"
    else:
        verdict = "BORDERLINE"

    return {"checks": passed, "verdict": verdict, "pass_count": sum(passed.values()), "total": len(passed)}
