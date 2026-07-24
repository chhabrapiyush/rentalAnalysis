import json
from pathlib import Path

import pytest

from rentalanalysis.models import AnalysisConfig, PropertyListing

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_listing() -> PropertyListing:
    data = json.loads((FIXTURES / "sample_property.json").read_text())
    return PropertyListing.model_validate(data)


@pytest.fixture
def sample_config() -> AnalysisConfig:
    return AnalysisConfig.from_yaml(FIXTURES / "sample_config.yaml")
