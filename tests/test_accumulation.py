"""
Tests for ``AccumulationManager``.

Pinned here, because each was established against a live tenant rather than
from the documentation alone:

- Accumulation profiles are looked up by ``profileName``; ``name`` is rejected
  by the server, so the name-to-ID lookup must send ``profileName``.
- The Create accumulation job body sends ``portfolioProperties.geocodeVersion``
  (the key the Risk Modeler UI sends), ``eventInfo.eventDate`` as a
  ``YYYY-MM-DD`` string, and ``additionalOutputOptions.includeLossByTreaty``.
- ``financial_perspectives``, ``event_date_behavior`` and ``event_date`` have no
  defaults and are validated before any request is made.
- Batch polling calls Get accumulation job per job ID. Search accumulation jobs
  ignores its ``filter``, so a ``jobId IN (...)`` search would poll the whole
  tenant's job list.
- A finished job's ``tasks[0].output.log.analysisId`` is the result's Risk Data
  ``analysisId``; while the job runs the field reads ``"0"``.
"""

import pytest

from irp_integration.exceptions import IRPAPIError, IRPJobError, IRPValidationError

# Invented names. Nothing here may name a real EDM, portfolio, analysis, profile
# or tenant: this file ships in the sdist, so a name used here is published to PyPI.
EDM_NAME = "example_edm"
PORTFOLIO_NAME = "example_portfolio"
JOB_NAME = "example_accumulation"
PROFILE_NAME = "example_profile"
PORTFOLIO_URI = "/platform/riskdata/v1/exposures/42/portfolios/7"
CURRENCY = {
    "asOfDate": "2025-01-01",
    "currency": "USD",
    "currencySchemeName": "Example Scheme",
    "currencyVersion": "RL25",
}


def profile(profile_id=11, name=PROFILE_NAME):
    """Build one accumulation profile summary."""
    return {"profileId": profile_id, "profileName": name, "analysisType": "geopolitical"}


def finished_job(job_id="901", analysis_id="5001", status="FINISHED"):
    """Build a Get accumulation job response with one task."""
    return {
        "jobId": job_id,
        "status": status,
        "progress": 100 if status == "FINISHED" else 1,
        "tasks": [{
            "taskId": "1",
            "output": {"summary": "", "errors": [], "log": {"profileId": "11", "analysisId": analysis_id}},
        }],
    }


def submit(manager, **overrides):
    """Call submit_portfolio_accumulation_job with a complete, valid argument set."""
    kwargs = dict(
        edm_name=EDM_NAME,
        portfolio_name=PORTFOLIO_NAME,
        job_name=JOB_NAME,
        profile_names=[PROFILE_NAME],
        financial_perspectives=["GU", "GR"],
        event_date_behavior="ignore",
        event_date="2026-01-15",
        treaty_names=[],
        tag_names=[],
        currency=CURRENCY,
        skip_duplicate_check=True,
    )
    kwargs.update(overrides)
    return manager.submit_portfolio_accumulation_job(**kwargs)


# --- Profiles -----------------------------------------------------------------

def test_profile_search_sends_filter_sort_limit_and_offset(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([response(200, json_body=[profile()])])

    results = manager.search_accumulation_profiles(filter='profileId = 11', sort="name ASC", limit=5, offset=10)

    assert results == [profile()]
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == "/platform/accumulation/v1/profiles"
    assert client.calls[0]["params"] == {"limit": 5, "offset": 10, "filter": "profileId = 11", "sort": "name ASC"}


def test_profile_by_name_filters_on_profileName(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([response(200, json_body=[profile()])])

    result = manager.get_accumulation_profile_by_name(PROFILE_NAME)

    assert result["profileId"] == 11
    assert client.calls[0]["params"]["filter"] == f'profileName = "{PROFILE_NAME}"'


@pytest.mark.parametrize("matches", [[], [profile(11), profile(12)]])
def test_profile_by_name_rejects_zero_or_several_matches(make_accumulation_manager, response, matches):
    manager, _, _ = make_accumulation_manager([response(200, json_body=matches)])

    with pytest.raises(IRPAPIError, match=PROFILE_NAME):
        manager.get_accumulation_profile_by_name(PROFILE_NAME)


def test_profile_by_id_sends_the_id_in_the_path(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([response(200, json_body={**profile(), "details": []})])

    result = manager.get_accumulation_profile_by_id(11)

    assert result["details"] == []
    assert client.calls[0]["path"] == "/platform/accumulation/v1/profiles/11"


# --- Submission ---------------------------------------------------------------

def test_submit_builds_the_portfolio_request_body(make_accumulation_manager, response):
    manager, client, edm_manager = make_accumulation_manager(
        responses=[
            response(200, json_body=[{"uri": PORTFOLIO_URI, "portfolioId": 7}]),
            response(200, json_body=[{"treatyId": 1, "treatyName": "t_one"}, {"treatyId": 2, "treatyName": "t_two"}]),
            response(200, json_body=[profile(11)]),
            response(201, headers={"location": "https://host/platform/accumulation/v1/jobs/901"}),
        ],
        edms=[{"exposureId": 42}],
    )

    job_id, body = submit(
        manager,
        treaty_names=["t_one", "t_two"],
        event_date_behavior="treatyAndPolicyAndLocation",
        financial_perspectives=["GU", "GR", "RL"],
    )

    assert job_id == 901
    assert body == {
        "resourceType": "portfolio",
        "resourceUri": PORTFOLIO_URI,
        "settings": {
            "name": JOB_NAME,
            "profileIds": [11],
            "financialPerspectives": ["GU", "GR", "RL"],
            "currency": CURRENCY,
            "eventInfo": {"eventDateBehavior": "treatyAndPolicyAndLocation", "eventDate": "2026-01-15"},
            "portfolioProperties": {"geocodeVersion": "", "treatyIds": [1, 2]},
            "additionalOutputOptions": {"includeLossByTreaty": True},
            "tagIds": [],
        },
    }
    assert edm_manager.filters == [f'exposureName="{EDM_NAME}"']
    assert client.calls[0]["path"] == "/platform/riskdata/v1/exposures/42/portfolios"
    assert client.calls[1]["params"]["filter"] == 'treatyName IN ("t_one", "t_two")'
    assert client.calls[2]["params"]["filter"] == f'profileName = "{PROFILE_NAME}"'
    assert client.calls[-1] == {
        "method": "POST",
        "path": "/platform/accumulation/v1/jobs",
        "params": None,
        "json": body,
    }


def test_submit_passes_geocode_version_and_include_loss_by_treaty_through(make_accumulation_manager, response):
    manager, _, _ = make_accumulation_manager(
        responses=[
            response(200, json_body=[{"uri": PORTFOLIO_URI}]),
            response(200, json_body=[profile(11)]),
            response(201, headers={"location": "/platform/accumulation/v1/jobs/902"}),
        ],
        edms=[{"exposureId": 42}],
    )

    _, body = submit(manager, geocode_version="25.0", include_loss_by_treaty=False)

    assert body["settings"]["portfolioProperties"] == {"geocodeVersion": "25.0", "treatyIds": []}
    assert body["settings"]["additionalOutputOptions"] == {"includeLossByTreaty": False}


def test_submit_resolves_every_profile_name_in_order(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager(
        responses=[
            response(200, json_body=[{"uri": PORTFOLIO_URI}]),
            response(200, json_body=[profile(11, "first")]),
            response(200, json_body=[profile(12, "second")]),
            response(201, headers={"location": "/platform/accumulation/v1/jobs/903"}),
        ],
        edms=[{"exposureId": 42}],
    )

    _, body = submit(manager, profile_names=["first", "second"])

    assert body["settings"]["profileIds"] == [11, 12]
    assert [c["params"]["filter"] for c in client.calls[1:3]] == ['profileName = "first"', 'profileName = "second"']


def test_submit_checks_for_an_existing_analysis_name_first(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager(
        responses=[response(200, json_body=[{"analysisId": 1, "analysisName": JOB_NAME}])],
        edms=[{"exposureId": 42}],
    )

    with pytest.raises(IRPAPIError, match=f"'{JOB_NAME}' already exists"):
        submit(manager, skip_duplicate_check=False)

    assert client.calls[0]["path"] == "/platform/riskdata/v1/analyses"
    assert client.calls[0]["params"]["filter"] == f'analysisName = "{JOB_NAME}" AND exposureName = "{EDM_NAME}"'


@pytest.mark.parametrize("overrides, message", [
    ({"financial_perspectives": []}, "financial_perspectives cannot be empty"),
    ({"financial_perspectives": ["GU", "XX"]}, r"financial_perspectives\[1\]"),
    ({"event_date_behavior": "always"}, "event_date_behavior must be one of"),
    ({"event_date_behavior": ""}, "event_date_behavior"),
    ({"event_date": "15/01/2026"}, "event_date must be a date in YYYY-MM-DD form"),
    ({"event_date": "2026-1-5"}, "event_date must be a date in YYYY-MM-DD form"),
    ({"event_date": 1788926400000}, "event_date must be a string"),
    ({"profile_names": []}, "profile_names cannot be empty"),
    ({"geocode_version": 25}, "geocode_version must be a string"),
])
def test_submit_validates_arguments_before_any_request(make_accumulation_manager, overrides, message):
    manager, client, _ = make_accumulation_manager(responses=[], edms=[{"exposureId": 42}])

    with pytest.raises(IRPValidationError, match=message):
        submit(manager, **overrides)

    assert client.calls == []


def test_submit_rejects_a_treaty_count_mismatch(make_accumulation_manager, response):
    manager, _, _ = make_accumulation_manager(
        responses=[
            response(200, json_body=[{"uri": PORTFOLIO_URI}]),
            response(200, json_body=[{"treatyId": 1, "treatyName": "t_one"}]),
        ],
        edms=[{"exposureId": 42}],
    )

    with pytest.raises(IRPAPIError, match="Expected 2 treaties, found 1"):
        submit(manager, treaty_names=["t_one", "missing"])


def test_batch_submit_requires_each_field_and_rejects_existing_names(make_accumulation_manager, response):
    manager, _, _ = make_accumulation_manager(
        responses=[response(200, json_body=[{"analysisId": 1}])],
    )
    entry = {
        "edm_name": EDM_NAME, "portfolio_name": PORTFOLIO_NAME, "job_name": JOB_NAME,
        "profile_names": [PROFILE_NAME], "financial_perspectives": ["GU"],
        "event_date_behavior": "ignore", "event_date": "2026-01-15",
    }

    with pytest.raises(IRPValidationError, match="missing required field 'event_date'"):
        manager.submit_portfolio_accumulation_jobs([{k: v for k, v in entry.items() if k != "event_date"}])

    with pytest.raises(IRPAPIError, match="already exists"):
        manager.submit_portfolio_accumulation_jobs([entry])


# --- Jobs ---------------------------------------------------------------------

def test_get_job_sends_the_id_in_the_accumulation_path(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([response(200, json_body=finished_job())])

    job = manager.get_accumulation_job(901)

    assert job["status"] == "FINISHED"
    assert client.calls[0]["path"] == "/platform/accumulation/v1/jobs/901"


def test_batch_poll_gets_each_job_by_id_and_returns_in_input_order(
    make_accumulation_manager, response, monkeypatch
):
    monkeypatch.setattr("irp_integration.accumulation.time.sleep", lambda seconds: None)
    manager, client, _ = make_accumulation_manager([
        response(200, json_body=finished_job("901", status="RUNNING")),
        response(200, json_body=finished_job("902")),
        response(200, json_body=finished_job("901")),
        response(200, json_body=finished_job("902")),
    ])

    jobs = manager.poll_accumulation_job_batch_to_completion([901, 902], interval=1, timeout=60)

    assert [job["jobId"] for job in jobs] == ["901", "902"]
    assert all(job["status"] == "FINISHED" for job in jobs)
    assert [call["path"] for call in client.calls] == [
        "/platform/accumulation/v1/jobs/901",
        "/platform/accumulation/v1/jobs/902",
        "/platform/accumulation/v1/jobs/901",
        "/platform/accumulation/v1/jobs/902",
    ]


def test_batch_poll_returns_on_failed_and_cancelled_too(make_accumulation_manager, response):
    manager, _, _ = make_accumulation_manager([
        response(200, json_body=finished_job("901", status="FAILED")),
        response(200, json_body=finished_job("902", status="CANCELLED")),
    ])

    jobs = manager.poll_accumulation_job_batch_to_completion([901, 902], interval=1, timeout=60)

    assert [job["status"] for job in jobs] == ["FAILED", "CANCELLED"]


def test_batch_poll_times_out_naming_the_pending_jobs(make_accumulation_manager, response, monkeypatch):
    monkeypatch.setattr("irp_integration.accumulation.time.sleep", lambda seconds: None)
    clock = iter([0, 100])
    monkeypatch.setattr("irp_integration.accumulation.time.time", lambda: next(clock))
    manager, _, _ = make_accumulation_manager([
        response(200, json_body=finished_job("901", status="RUNNING")),
        response(200, json_body=finished_job("902")),
    ])

    with pytest.raises(IRPJobError, match="still running: 901"):
        manager.poll_accumulation_job_batch_to_completion([901, 902], interval=1, timeout=10)


# --- Results ------------------------------------------------------------------

def test_extract_analysis_id_reads_the_task_log(make_accumulation_manager):
    manager, _, _ = make_accumulation_manager()

    assert manager.extract_analysis_id_from_accumulation_job(finished_job(analysis_id="5001")) == 5001


def test_extract_analysis_id_rejects_a_job_that_is_not_finished(make_accumulation_manager):
    manager, _, _ = make_accumulation_manager()

    with pytest.raises(IRPJobError, match="status RUNNING"):
        manager.extract_analysis_id_from_accumulation_job(finished_job(analysis_id="0", status="RUNNING"))


def test_extract_analysis_id_rejects_a_placeholder_id(make_accumulation_manager):
    manager, _, _ = make_accumulation_manager()

    with pytest.raises(IRPAPIError, match="analysisId 0"):
        manager.extract_analysis_id_from_accumulation_job(finished_job(analysis_id="0"))


def test_get_analysis_for_job_reads_the_job_then_the_analysis(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([
        response(200, json_body=finished_job(analysis_id="5001")),
        response(200, json_body={"analysisId": 5001, "engineType": "Accumulation"}),
    ])

    analysis = manager.get_analysis_for_accumulation_job(901)

    assert analysis["engineType"] == "Accumulation"
    assert [call["path"] for call in client.calls] == [
        "/platform/accumulation/v1/jobs/901",
        "/platform/riskdata/v1/analyses/5001",
    ]


def test_search_accumulation_analyses_adds_the_engine_type_filter(make_accumulation_manager, response):
    manager, client, _ = make_accumulation_manager([
        response(200, json_body=[]),
        response(200, json_body=[]),
    ])

    manager.search_accumulation_analyses()
    manager.search_accumulation_analyses(filter=f'exposureName = "{EDM_NAME}"', limit=10, offset=20)

    assert client.calls[0]["params"] == {"limit": 100, "offset": 0, "filter": 'engineType = "Accumulation"'}
    assert client.calls[1]["params"] == {
        "limit": 10,
        "offset": 20,
        "filter": f'engineType = "Accumulation" AND (exposureName = "{EDM_NAME}")',
    }


# --- Currency -----------------------------------------------------------------

def test_accumulation_currency_uses_the_latest_vintage_and_the_scheme_name(
    make_reference_data_manager, response
):
    manager, client = make_reference_data_manager([
        response(200, json_body={"items": [
            {"currencySchemeCode": "RMS", "effectiveDate": "2024-04-03T00:00:00.000Z", "vintage": "RL24"},
            {"currencySchemeCode": "RMS", "effectiveDate": "2025-05-28T00:00:00.000Z", "vintage": "RL25"},
        ]}),
        response(200, json_body={"items": [{"currencySchemeCode": "RMS", "currencySchemeName": "Example Scheme"}]}),
    ])

    currency = manager.get_accumulation_currency()

    assert currency == {
        "asOfDate": "2025-05-28",
        "currency": "USD",
        "currencySchemeName": "Example Scheme",
        "currencyVersion": "RL25",
    }
    assert client.calls[1]["params"]["where"] == 'currencySchemeCode="RMS"'


def test_accumulation_currency_falls_back_to_defaults_when_a_lookup_fails(
    make_reference_data_manager, response
):
    manager, _ = make_reference_data_manager([RuntimeError("connection reset")])

    currency = manager.get_accumulation_currency()

    assert set(currency) == {"asOfDate", "currency", "currencySchemeName", "currencyVersion"}
    assert currency["currency"] == "USD"
