from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, computed_field, model_validator


def _coerce_price(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[$,\s]", "", v)
        return float(cleaned)
    raise ValueError(f"Cannot coerce {v!r} to float")


class UnitRent(BaseModel):
    """A single line from the OneHome rent roll (Property Details tab)."""
    unit: Optional[str] = None
    monthly_rent: Optional[float] = None
    beds: Optional[str] = None
    baths: Optional[str] = None
    description: Optional[str] = None


class PropertyListing(BaseModel):
    url: str
    address: str
    list_price: float
    beds: float
    baths: float
    sqft: Optional[float] = None
    annual_taxes: Optional[float] = None
    hoa_monthly: float = 0.0
    estimated_rent_monthly: Optional[float] = None
    days_on_market: Optional[int] = None
    photo_urls: list[str] = []

    # Additional fields scraped from OneHome Property Details tab
    insurance_annual_listed: Optional[float] = None      # "Insurance Expense"
    maintenance_annual_listed: Optional[float] = None    # "Maintenance Expense"
    noi_listed: Optional[float] = None                   # "Net Operating Income"
    gross_income_annual_listed: Optional[float] = None   # "Gross Income" (annual)
    operating_expense_listed: Optional[float] = None     # "Operating Expense" (annual)
    electric_expense_listed: Optional[float] = None      # "Electric Expense" (annual)
    total_units: Optional[int] = None                    # "Total Units"
    year_built: Optional[int] = None                     # "Year Built"
    property_type: Optional[str] = None                  # "Property Type"
    price_per_sqft: Optional[float] = None               # "Price per Sq Ft."
    rent_roll: list[UnitRent] = []                       # per-unit rents from OneHome
    listing_details: dict[str, str] = {}                 # raw OneHome dt/dd map (full transparency)

    @model_validator(mode="before")
    @classmethod
    def coerce_price_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            coerce_fields = (
                "list_price", "annual_taxes", "hoa_monthly", "estimated_rent_monthly",
                "insurance_annual_listed", "maintenance_annual_listed",
                "noi_listed", "gross_income_annual_listed", "operating_expense_listed",
                "electric_expense_listed", "price_per_sqft",
            )
            for field in coerce_fields:
                if field in data and data[field] is not None:
                    try:
                        data[field] = _coerce_price(data[field])
                    except (ValueError, TypeError):
                        pass
        return data

    @computed_field
    @property
    def slug(self) -> str:
        safe = re.sub(r"[^\w\s-]", "", self.address)
        safe = re.sub(r"[\s]+", "_", safe.strip())
        return safe[:31]  # Excel sheet name limit is 31 chars


class LoanConfig(BaseModel):
    down_payment_pct: float = 0.20
    interest_rate: float = 0.07
    loan_term_years: int = 30
    closing_costs: float = 5000.0


class ExpenseConfig(BaseModel):
    property_mgmt_pct: float = 0.10
    insurance_annual: float = 1500.0          # used only if no listed insurance & no per-unit basis
    insurance_per_unit: float = 500.0         # IRE default: 500 * units
    maintenance_reserve_pct: float = 0.05
    maintenance_on: bool = False              # count maintenance in expenses? (pre-filled either way)
    vacancy_rate: float = 0.05
    utilities_monthly: float = 0.0
    # IRE itemized / CapEx knobs (mirrors sample_proforma.xlsx)
    capex_reserve_pct: float = 0.05           # Replacement Reserves (% of gross income)
    capex_on: bool = False                    # count CapEx reserves in expenses? (pre-filled either way)
    leasing_fee_pct: float = 0.0              # off by default (sample toggle 'n')
    trash_annual: float = 360.0               # 30 * 12
    recycle_per_unit: float = 50.0
    sewer_annual: float = 600.0
    water_annual: float = 0.0                 # water toggle off by default


class TargetConfig(BaseModel):
    min_monthly_cash_flow: float = 200.0      # minimum $/mo
    min_cash_on_cash_pct: float = 0.12        # 12% annual CoC
    min_cap_rate_pct: float = 0.07            # 7% cap rate
    min_dscr: float = 1.20                    # 1.20x debt service coverage
    max_ltv: float = 0.80                     # 80% max LTV


class AnalysisConfig(BaseModel):
    loan: LoanConfig = LoanConfig()
    expenses: ExpenseConfig = ExpenseConfig()
    targets: TargetConfig = TargetConfig()
    # Fallback monthly rent (as % of price) for listings with no OneHome income data.
    fallback_rent_monthly_pct: float = 0.007
    property_overrides: dict[str, dict] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> "AnalysisConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)


class AnalysisResult(BaseModel):
    listing: PropertyListing

    # Data quality
    data_complete: bool = True                   # False when OneHome income data was missing
    data_notes: list[str] = []                   # human-readable caveats

    # Income
    gross_rental_income: float                   # conservative basis actually used
    rent_roll_annual: float = 0.0                # uncapped rent-roll total (reference)
    income_basis: str = "Calculated"            # how gross_rental_income was chosen
    effective_gross_income: float
    vacancy_loss: float

    # Expense breakdown
    taxes_annual: float
    insurance_annual: float
    insurance_source: str               # "OneHome" | "Assumed"
    mgmt_fee_annual: float
    maintenance_annual: float
    maintenance_source: str             # "OneHome" | "Assumed"
    hoa_annual: float
    utilities_annual: float
    # Itemized IRE utilities
    trash_annual: float = 0.0
    water_annual: float = 0.0
    sewer_annual: float = 0.0
    recycle_annual: float = 0.0
    leasing_fee_annual: float = 0.0
    # OneHome-listed expenses captured verbatim
    electric_annual: float = 0.0                 # "Electric Expense"
    misc_listed_expenses: float = 0.0            # any other "* Expense" fields from OneHome
    misc_expense_items: dict[str, float] = {}    # label -> annual $, for the Miscellaneous section
    operating_expenses: float           # C38 — itemised build-up (excludes CapEx reserves)
    operating_expenses_used: float = 0.0  # max(itemised, listed) — drives NOI
    opex_basis: str = "Calculated"       # how operating_expenses_used was chosen

    # CapEx & two-tier NOI
    capex_reserve: float = 0.0
    total_net_operating_expenses: float = 0.0   # operating_expenses_used + applied capex
    effective_noi: float = 0.0                  # EGI - total_net_operating_expenses (drives DSCR)

    # Whether maintenance / capex are counted (toggle default) — pre-filled either way
    maintenance_on: bool = False
    capex_on: bool = False

    # Income sourcing
    rent_source: str = "Assumed"                # "OneHome (rent roll)" | "OneHome (gross income)" | "Assumed"

    # NOI
    noi: float                                  # EGI - operating_expenses (drives cap rate / valuation)
    listed_noi: Optional[float] = None          # from listing, for comparison
    listed_gross_income: Optional[float] = None  # from listing

    # OpEx-vs-listing sanity check
    listed_operating_expenses: Optional[float] = None   # OneHome "Operating Expense" or (gross - NOI)
    listed_opex_source: Optional[str] = None            # "OneHome (Operating Expense)" | "OneHome (Gross - NOI)"
    opex_variance: Optional[float] = None               # operating_expenses - listed_operating_expenses
    opex_exceeds_listed: bool = False

    # Debt service
    loan_amount: float
    monthly_payment: float
    annual_debt_service: float

    # Cash flow & returns
    cash_flow_annual: float
    cash_flow_monthly: float
    cap_rate: float
    cash_on_cash: float
    grm: float
    dscr: float
    ltv: float
    total_cash_invested: float
