"""Offline contract tests for ``AnalysisManager.describe_run``.

The analysis details, region rows, treaties and reference rows below are
trimmed to the fields ``describe_run`` reads and keep the shapes the Platform
returns. Reference values the responses did not cover are invented, and each is
marked where it appears: the model versions for ``RL25``/``RL23`` North
Atlantic windstorm and ``RL25`` North America earthquake, and the peril, model
region and model version of event-rate schemes 739 and 740.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from conftest import FakeClient, FakeResponse
from irp_integration.analysis import AnalysisManager
from irp_integration.exceptions import IRPAPIError

# Invented names and identifiers. Nothing here may name a real EDM, RDM,
# portfolio, analysis, treaty or tenant: this file ships in the sdist, so a name
# used here is a name published to PyPI.

# Analysis 101, an RL25 DLM ELT analysis of one own portfolio.
OWN_DLM_DETAIL = {
    "analysisId": 101,
    "analysisName": "example_ws_analysis",
    "exposureName": "example_edm",
    "engineType": "DLM",
    "engineVersion": "RL25",
    "analysisFramework": "ELT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Distributed",
    "isGroup": False,
    "groupType": "ANLS",
    "peril": "Windstorm",
    "perilCode": "WS",
    "subPeril": "Surge + Wind",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [
        {"id": 0, "code": "0", "name": "RMS 2025 Stochastic Event Rates"}
    ],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 5250, "code": "", "name": "example_ws_model_profile"},
    "additionalProperties": [
        {"key": "exposure", "properties": [
            {"id": 5, "name": "example_ws_portfolio", "value": "example_edm"}
        ]},
        {"key": "dataVersion", "properties": [
            {"id": 0, "name": "", "value": "22.0.0"}
        ]},
        {"key": "eventRateSchemeId", "properties": [
            {"id": 739, "name": "", "value": ""}
        ]},
    ],
}

OWN_DLM_REGION_ROW = {
    "region": "NA",
    "subRegion": "P1",
    "peril": "Windstorm",
    "eventRateSchemeId": 739,
    "framework": "ELT",
    "analysisId": 101,
    "modelProfileId": 5250,
    "petId": 0,
    "numSamples": 0,
    "periods": 0,
    "applyContractFlag": False,
    "engineVersion": "RL25",
}

# Both North Atlantic windstorm captures return 23 region rows identical apart
# from subRegion. The capture keeps one row and names the first six codes; the
# remaining codes are invented to reach the live row count of 23.
NA_WS_SUB_REGIONS = (
    "AL", "CT", "D1", "DC", "FL", "GA", "P1", "HI", "LA", "MA", "MD", "ME",
    "MS", "NC", "NH", "NJ", "NY", "PA", "RI", "SC", "TX", "VA", "D2",
)

OWN_DLM_TREATIES = [
    {
        "treatyId": 21,
        "treatyNumber": "PR1",
        "treatyName": "PR1",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 1000000.0,
        "attachmentPoint": 250000.0,
        "retentionAmount": 0.0,
        "riskLimit": 250000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 100.0,
        "effectiveDate": "2026-01-01T00:00:00.000Z",
        "expirationDate": "2026-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 101,
        "uri": "/platform/riskdata/v1/analyses/101/treaties/21",
    },
]

# Analysis 102, an HDv3.0 PLT analysis of one own portfolio.
OWN_HD_DETAIL = {
    "analysisId": 102,
    "analysisName": "example_eq_analysis",
    "exposureName": "example_edm",
    "engineType": "HD",
    "engineVersion": "HDv3.0",
    "analysisFramework": "PLT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Simulated",
    "isGroup": False,
    "peril": "Earthquake",
    "perilCode": "EQ",
    "subPeril": "Multi-SubPeril",
    "region": "New Zealand",
    "regionCode": "NZ",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 5328, "code": "", "name": "example_eq_model_profile"},
    "additionalProperties": [
        {"key": "dataVersion", "properties": [
            {"id": 0, "name": "", "value": "22.0.0"}
        ]},
        {"key": "exposure", "properties": [
            {"id": 1, "name": "example_eq_portfolio", "value": "example_edm"}
        ]},
    ],
}

OWN_HD_REGION_ROW = {
    "region": "NZ",
    "subRegion": "NZ",
    "peril": "Earthquake",
    "eventRateSchemeId": 0,
    "framework": "PLT",
    "analysisId": 102,
    "modelProfileId": 5328,
    "petId": 12,
    "numSamples": 1,
    "periods": 1978459,
    "applyContractFlag": False,
    "engineVersion": "HDv3.0",
}

# Analysis 103, an RL23 DLM ELT analysis imported from a broker RDM.
BROKER_DLM_DETAIL = {
    "analysisId": 103,
    "analysisName": "example_broker_analysis",
    "sourceRdmName": "example_rdm",
    "exposureName": "",
    "engineType": "DLM",
    "engineVersion": "RL23",
    "analysisFramework": "ELT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Distributed",
    "isGroup": False,
    "groupType": "ANLS",
    "peril": "Windstorm",
    "perilCode": "WS",
    "subPeril": "Surge Only",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 0, "code": "", "name": ""},
    "additionalProperties": [
        {"key": "exposure", "properties": [
            {"id": 3, "name": "", "value": "example_broker_edm"}
        ]},
        {"key": "dataVersion", "properties": [{"id": 0, "name": "", "value": ""}]},
    ],
}

BROKER_DLM_REGION_ROW = {
    "region": "NA",
    "subRegion": "HI",
    "peril": "Windstorm",
    "eventRateSchemeId": 577,
    "framework": "ELT",
    "analysisId": 103,
    "modelProfileId": 0,
    "petId": 0,
    "numSamples": 0,
    "periods": 0,
    "applyContractFlag": False,
    "engineVersion": "RL23",
}

BROKER_DLM_TREATIES = [
    {
        "treatyId": 22,
        "treatyNumber": "XPR_1_100_Fld",
        "treatyName": "XPR_1_100_Fld",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 10000000.0,
        "attachmentPoint": 5000000.0,
        "retentionAmount": 0.0,
        "riskLimit": 5000000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 100.0,
        "effectiveDate": "2000-01-01T00:00:00.000Z",
        "expirationDate": "2030-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 103,
        "uri": "/platform/riskdata/v1/analyses/103/treaties/22",
    },
    {
        "treatyId": 23,
        "treatyNumber": "XPR_1_95_Fld",
        "treatyName": "XPR_1_95_Fld",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 9000000.0,
        "attachmentPoint": 2000000.0,
        "retentionAmount": 0.0,
        "riskLimit": 3000000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 95.0,
        "effectiveDate": "2000-01-01T00:00:00.000Z",
        "expirationDate": "2030-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 103,
        "uri": "/platform/riskdata/v1/analyses/103/treaties/23",
    },
]

# Analysis 104, a broker group: isGroup is false and groupType is INGP, and
# engineType is Group.
BROKER_GROUP_DETAIL = {
    "analysisId": 104,
    "analysisName": "example_broker_group",
    "sourceRdmName": "example_rdm",
    "engineType": "Group",
    "engineVersion": "RL25",
    "analysisFramework": "ELT",
    "isGroup": False,
    "groupType": "INGP",
    "peril": "Windstorm",
    "perilCode": "WS",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "additionalProperties": [
        {"key": "propagateDetailedOutput", "properties": [
            {"id": 0, "name": "", "value": "Yes"}
        ]},
        {"key": "eventRateSchemes", "properties": [
            {"id": 0, "name": "", "value": {
                "regionCode": "NA",
                "perilCode": "WS",
                "framework": "ELT",
                "eventRateSchemeId": 578,
                "eventRateSchemeName": "RMS 2023 Stochastic Event Rates",
                "simulationSetId": 0,
                "simulationSetName": "",
                "simulationPeriods": 0,
            }}
        ]},
    ],
}

BROKER_GROUP_REGION_ROW = dict(
    BROKER_DLM_REGION_ROW,
    subRegion="DC",
    eventRateSchemeId=578,
    analysisId=104,
    engineVersion="RL25",
)

# Three of the 151 active event-rate scheme rows, plus scheme 739. The name of
# 739 is the one analysis 101 reports in eventRateSchemeNames; its peril,
# model region and model version are invented.
EVENT_RATE_SCHEME_ROWS = [
    {
        "eventRateSchemeId": 139,
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "eventRateSchemeName": "RMS 2020 Time-Independent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 175,
        "perilCode": "EQ",
        "modelRegionCode": "JPEQ",
        "modelVersionCode": "2.1",
        "eventRateSchemeName": "RMS V2.0 Time-Dependent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 138,
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "eventRateSchemeName": "RMS 2020 Time-Dependent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 739,
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
        "eventRateSchemeName": "RMS 2025 Stochastic Event Rates",
        "isActive": True,
    },
]

# PET ID 12 exists for model version 2.0 and 3.0 with a different petName.
PET_METADATA_ROWS = [
    {
        "id": 12,
        "petName": "RMS 2020 Time-Dependent Rates",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "numberOfPeriods": 1978459,
        "maxNumberOfSamples": 256,
    },
    {
        "id": 12,
        "petName": "RMS V2.0 Time-Dependent Rates",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "2.0",
        "numberOfPeriods": 1978459,
        "maxNumberOfSamples": 256,
    },
]

# HDv3.0/NZ/EQ is captured. The two RL model versions are invented: the capture
# did not read a North Atlantic windstorm mapping.
MODEL_VERSIONS = {
    ("HDv3.0", "NZ", "EQ"): "3.0",
    ("RL25", "NA", "WS"): "11.0",
    ("RL23", "NA", "WS"): "11.0",
}


class FakeReferenceDataManager:
    """Answer the reference reads ``describe_run`` makes from capture rows."""

    def __init__(self) -> None:
        self.event_rate_schemes = list(EVENT_RATE_SCHEME_ROWS)
        self.pet_metadata = list(PET_METADATA_ROWS)
        self.pet_metadata_calls: List[Dict[str, Any]] = []
        self.pet_metadata_returns_none = False

    def get_event_rate_schemes(self) -> Dict[str, Any]:
        """Return the active scheme rows in the Platform's envelope."""
        return {
            "items": list(self.event_rate_schemes),
            "totalCount": len(self.event_rate_schemes),
        }

    def get_model_version_by_engine_region_peril(
        self, engine_version: str, region_code: str, peril_code: str
    ) -> str:
        """Return the fixture model version for one engine, region and peril."""
        key = (engine_version, region_code, peril_code)
        if key not in MODEL_VERSIONS:
            raise IRPAPIError(f"No model version mapping found for {key}")
        return MODEL_VERSIONS[key]

    def get_pet_metadata_exact(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Return the one PET row matching every qualifier."""
        self.pet_metadata_calls.append(kwargs)
        if self.pet_metadata_returns_none:
            return None
        matches = [
            pet for pet in self.pet_metadata
            if pet["id"] == kwargs["pet_id"]
            and pet["modelVersionCode"] == kwargs["model_version"]
            and (
                kwargs["model_region_code"] is None
                or pet["modelRegionCode"] == kwargs["model_region_code"]
            )
        ]
        if not matches:
            raise IRPAPIError(
                f"No PET metadata found for PET ID {kwargs['pet_id']}"
            )
        if len(matches) > 1:
            raise IRPAPIError(
                f"Multiple PET metadata rows found for PET ID {kwargs['pet_id']}"
            )
        return matches[0]

    def get_all_simulation_sets(self) -> List[Dict[str, Any]]:
        """Fail the test: a PET ID does not name a ``SimulationSet`` row."""
        raise AssertionError(
            "describe_run must not read the SimulationSet reference table"
        )


def make_manager(detail, regions, treaties=()):
    """Build an analysis manager over one queued detail, region and treaty page."""
    client = FakeClient([
        FakeResponse(200, detail),
        FakeResponse(200, list(regions)),
        FakeResponse(200, list(treaties)),
    ])
    reference_data = FakeReferenceDataManager()
    irp = SimpleNamespace(client=client, reference_data=reference_data)
    return AnalysisManager(irp), reference_data


def rows(row, sub_regions=NA_WS_SUB_REGIONS):
    """Return one region row per sub-region code."""
    return [dict(row, subRegion=code) for code in sub_regions]


def test_own_dlm_reports_every_region_row_and_names_its_scheme():
    """Keep all 23 windstorm region rows and name event-rate scheme 739."""
    manager, _ = make_manager(OWN_DLM_DETAIL, rows(OWN_DLM_REGION_ROW))

    description = manager.describe_run(101)

    assert description.analysis_id == 101
    assert description.is_group is False
    assert len(description.regions) == 23
    assert {region.event_rate_scheme_id for region in description.regions} == {739}
    assert {region.framework for region in description.regions} == {"ELT"}
    assert {region.peril_code for region in description.regions} == {"WS"}
    assert {region.region_code for region in description.regions} == {"NA"}
    assert {region.model_version for region in description.regions} == {"11.0"}
    assert [region.sub_region for region in description.regions] == list(NA_WS_SUB_REGIONS)
    assert description.event_rate_scheme_names == {
        739: "RMS 2025 Stochastic Event Rates"
    }


def test_own_dlm_reports_its_treaty_with_the_name_grouping_drops():
    """Return treatyName alongside the treaty ID, number and terms."""
    manager, _ = make_manager(
        OWN_DLM_DETAIL, rows(OWN_DLM_REGION_ROW), OWN_DLM_TREATIES
    )

    treaties = manager.describe_run(101).treaties

    assert len(treaties) == 1
    assert treaties[0].treaty_id == 21
    assert treaties[0].treaty_number == "PR1"
    assert treaties[0].treaty_name == "PR1"
    assert treaties[0].terms["occurrenceLimit"] == 1000000.0


def test_own_hd_names_the_pet_row_for_the_resolved_model_version():
    """Name PET 12 from the 3.0 row, not the 2.0 row with the same ID."""
    manager, reference_data = make_manager(OWN_HD_DETAIL, [OWN_HD_REGION_ROW])

    description = manager.describe_run(102)

    assert len(description.regions) == 1
    region = description.regions[0]
    assert region.framework == "PLT"
    assert region.model_version == "3.0"
    assert region.pet_id == 12
    assert region.periods == 1978459
    assert region.pet_name == "RMS 2020 Time-Dependent Rates"
    assert reference_data.pet_metadata_calls == [
        {"pet_id": 12, "model_version": "3.0", "model_region_code": "NZEQ"}
    ]
    assert description.event_rate_scheme_names == {}


@pytest.mark.parametrize("unnamed", ["no qualified row", "no row returned"])
def test_unnamed_pet_id_keeps_the_id_and_periods(unnamed):
    """Report pet_name None when no PETMetadata row names the PET ID."""
    pet_id = 99999 if unnamed == "no qualified row" else 12
    manager, reference_data = make_manager(
        OWN_HD_DETAIL, [dict(OWN_HD_REGION_ROW, petId=pet_id)]
    )
    reference_data.pet_metadata_returns_none = unnamed == "no row returned"

    region = manager.describe_run(102).regions[0]

    assert region.pet_name is None
    assert region.pet_id == pet_id
    assert region.periods == 1978459


def test_broker_dlm_reports_both_treaty_names_and_terms():
    """Return the broker analysis's two treaties with names and normalized terms."""
    manager, _ = make_manager(
        BROKER_DLM_DETAIL, rows(BROKER_DLM_REGION_ROW), BROKER_DLM_TREATIES
    )

    description = manager.describe_run(103)

    assert [treaty.treaty_name for treaty in description.treaties] == [
        "XPR_1_100_Fld", "XPR_1_95_Fld"
    ]
    assert [treaty.treaty_id for treaty in description.treaties] == [22, 23]
    assert [treaty.treaty_number for treaty in description.treaties] == [
        "XPR_1_100_Fld", "XPR_1_95_Fld"
    ]
    placed = description.treaties[1].terms
    assert placed["currency"] == "USD"
    assert placed["percentagePlaced"] == 95.0
    assert placed["occurrenceLimit"] == 9000000.0
    assert placed["lobs"] == []
    assert placed["lossOccurrences"] == []


def test_broker_dlm_omits_an_event_rate_scheme_no_active_row_names():
    """Keep scheme 577 on every region row and leave it out of the names."""
    manager, _ = make_manager(BROKER_DLM_DETAIL, rows(BROKER_DLM_REGION_ROW))

    description = manager.describe_run(103)

    assert {region.event_rate_scheme_id for region in description.regions} == {577}
    assert description.event_rate_scheme_names == {}


def test_broker_group_type_reports_a_group():
    """Read groupType INGP as a group even though the detail reports isGroup false."""
    manager, _ = make_manager(BROKER_GROUP_DETAIL, [BROKER_GROUP_REGION_ROW])

    assert manager.describe_run(104).is_group is True


def test_cep_group_engine_type_reports_a_group():
    """Read engineType CEPGroup as a group: GetAnalysisResponse lists it beside Group."""
    detail = dict(BROKER_GROUP_DETAIL, engineType="CEPGroup", groupType="UNRECOGNIZED")
    manager, _ = make_manager(detail, [BROKER_GROUP_REGION_ROW])

    assert manager.describe_run(104).is_group is True


def test_is_group_flag_reports_a_group():
    """Read isGroup true as a group."""
    detail = dict(OWN_DLM_DETAIL, isGroup=True)
    manager, _ = make_manager(detail, rows(OWN_DLM_REGION_ROW))

    assert manager.describe_run(101).is_group is True


def test_missing_analysis_raises():
    """Raise when the analysis read fails rather than describing an empty run."""
    client = FakeClient([IRPAPIError("404 Client Error: Not Found")])
    irp = SimpleNamespace(client=client, reference_data=FakeReferenceDataManager())

    with pytest.raises(IRPAPIError, match="101"):
        AnalysisManager(irp).describe_run(101)


def test_empty_region_list_describes_no_regions():
    """Describe an analysis with no region rows without raising."""
    manager, _ = make_manager(OWN_DLM_DETAIL, [], OWN_DLM_TREATIES)

    description = manager.describe_run(101)

    assert description.regions == ()
    assert description.event_rate_scheme_names == {}
    assert len(description.treaties) == 1
