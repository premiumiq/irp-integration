"""
Accumulation analysis operations.

Accumulation profiles, accumulation job submission and polling, and the link
from a finished accumulation job to its analysis result.

Accumulation profiles:
    An accumulation profile holds the peril, geocode version, scope, damage
    factors and filters an accumulation job runs with. The Accumulation API
    only reads profiles (``/platform/accumulation/v1/profiles``); profiles are
    created and edited in ExposureIQ. ``search_accumulation_profiles`` filters
    on ``profileName`` and ``profileId``; ``name`` and ``id`` are rejected with
    ``400 Unsupported field``.

Accumulation jobs:
    ``submit_portfolio_accumulation_job`` posts to
    ``/platform/accumulation/v1/jobs`` with ``resourceType`` ``portfolio`` and
    returns the ``jobId`` from the ``Location`` header. The job is served by
    ``/platform/accumulation/v1/jobs/{jobId}``, not by
    ``/platform/riskdata/v1/jobs``, which answers ``404 Invalid workflow id``
    for it. Poll with ``poll_accumulation_job_to_completion`` or
    ``poll_accumulation_job_batch_to_completion``. Both return on ``FINISHED``,
    ``FAILED`` or ``CANCELLED``; check ``status`` (see ``client.py``).

    Search accumulation jobs documents a ``filter`` parameter, but every filter
    tried (``jobId =``, ``jobId IN``, ``name =``, ``status =``, ``userName =``)
    returned the same unfiltered list. ``poll_accumulation_job_batch_to_completion``
    therefore calls Get accumulation job once per job ID each round instead of
    searching with ``jobId IN (...)`` the way ``AnalysisManager`` does.

Results:
    A finished accumulation job's ``tasks[0].output.log.analysisId`` is the
    Risk Data API ``analysisId`` of the result, so no search by name is needed.
    The result is an ordinary analysis with ``engineType`` ``Accumulation`` and
    ``modelProfile.id`` equal to the accumulation ``profileId``; read it with
    ``get_analysis_for_accumulation_job`` or ``AnalysisManager.get_analysis_by_id``.
    Exposed-limit rows are not retrievable through the Accumulation API; the
    Export API and the Risk Data report job are the documented routes and are
    not implemented here.
"""

import logging
import time
from typing import Dict, List, Any, Optional, Tuple, TYPE_CHECKING

from .constants import (
    ACCUMULATION_ENGINE_TYPE, ACCUMULATION_EVENT_DATE_BEHAVIORS,
    CREATE_ACCUMULATION_JOB, GET_ACCUMULATION_JOB, GET_ACCUMULATION_PROFILE,
    PERSPECTIVE_CODES, SEARCH_ACCUMULATION_JOBS, SEARCH_ACCUMULATION_PROFILES,
    WORKFLOW_COMPLETED_STATUSES, WORKFLOW_FINISHED_STATUS,
)
from .exceptions import IRPAPIError, IRPJobError, IRPValidationError
from .utils import extract_id_from_location_header, paginate_search
from .validators import (
    validate_iso_date_string, validate_list_not_empty, validate_non_empty_string,
    validate_positive_int,
)

if TYPE_CHECKING:
    from . import IRPClient
    from .analysis import AnalysisManager
    from .edm import EDMManager
    from .portfolio import PortfolioManager
    from .reference_data import ReferenceDataManager
    from .treaty import TreatyManager

logger = logging.getLogger(__name__)


class AccumulationManager:
    """Manager for accumulation profile reads and accumulation job operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize accumulation manager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client

    @property
    def reference_data_manager(self) -> "ReferenceDataManager":
        """Return the owning client's reference data manager."""
        return self._irp.reference_data

    @property
    def treaty_manager(self) -> "TreatyManager":
        """Return the owning client's treaty manager."""
        return self._irp.treaty

    @property
    def edm_manager(self) -> "EDMManager":
        """Return the owning client's EDM manager."""
        return self._irp.edm

    @property
    def portfolio_manager(self) -> "PortfolioManager":
        """Return the owning client's portfolio manager."""
        return self._irp.portfolio

    @property
    def analysis_manager(self) -> "AnalysisManager":
        """Return the owning client's analysis manager."""
        return self._irp.analysis


    # --- Accumulation profiles -------------------------------------------------

    def search_accumulation_profiles(
        self,
        filter: str = "",
        sort: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search accumulation profiles.

        Filterable properties observed live: ``profileName`` and ``profileId``.
        ``name`` and ``id`` are rejected with ``400 Unsupported field``. Sort
        accepts the documented ``name``, ``id``, ``type``, ``createdBy``,
        ``dateCreated`` and ``dateUpdated``.

        Args:
            filter: Optional filter string, e.g. ``profileName = "US EQ"``
            sort: Optional sort, e.g. ``name ASC``
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of accumulation profile summary dicts (``profileId``,
            ``profileName``, ``analysisType``, ``isActive``, ``geocodeVersion``,
            ``filterPredicateCount``, ``tags``, ...)

        Raises:
            IRPAPIError: If the request fails
        """
        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter
        if sort:
            params['sort'] = sort

        try:
            response = self.client.request('GET', SEARCH_ACCUMULATION_PROFILES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to search accumulation profiles: {e}")


    def search_accumulation_profiles_paginated(self, filter: str = "") -> List[Dict[str, Any]]:
        """
        Search all accumulation profiles with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``.

        Args:
            filter: Optional filter string

        Returns:
            Complete list of all matching accumulation profiles across all pages

        Raises:
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        return paginate_search(
            lambda limit, offset: self.search_accumulation_profiles(
                filter=filter,
                limit=limit,
                offset=offset
            ),
            "Accumulation profile search"
        )


    def get_accumulation_profile_by_id(self, profile_id: int) -> Dict[str, Any]:
        """
        Retrieve one accumulation profile, including its details.

        The response adds ``details`` (damage factors with their filters and
        ``lossTypeDamageFactors``, ``minLossThreshold``,
        ``workersCompProfileSettings``) and ``globalProfileFilters`` to the
        summary fields returned by ``search_accumulation_profiles``.

        Args:
            profile_id: Accumulation profile ID

        Returns:
            Dict containing the accumulation profile

        Raises:
            IRPValidationError: If profile_id is invalid
            IRPAPIError: If the request fails
        """
        validate_positive_int(profile_id, "profile_id")
        try:
            response = self.client.request('GET', GET_ACCUMULATION_PROFILE.format(profileId=profile_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get accumulation profile {profile_id}: {e}")


    def get_accumulation_profile_by_name(self, profile_name: str) -> Dict[str, Any]:
        """
        Retrieve an accumulation profile summary by its ``profileName``.

        Profile names are not unique across a tenant, so a name that matches
        more than one profile is an error rather than a silent first pick.

        Args:
            profile_name: Accumulation profile name

        Returns:
            Dict containing the accumulation profile summary

        Raises:
            IRPValidationError: If profile_name is empty
            IRPAPIError: If the search fails, or zero or more than one profile
                carries the name
        """
        validate_non_empty_string(profile_name, "profile_name")
        profiles = self.search_accumulation_profiles(filter=f'profileName = "{profile_name}"')
        if len(profiles) == 0:
            raise IRPAPIError(f"Accumulation profile '{profile_name}' not found")
        if len(profiles) > 1:
            raise IRPAPIError(f"Multiple accumulation profiles found with name '{profile_name}'")
        return profiles[0]


    # --- Accumulation jobs ----------------------------------------------------

    def submit_portfolio_accumulation_jobs(
        self,
        accumulation_data_list: List[Dict[str, Any]]
    ) -> List[int]:
        """
        Submit multiple portfolio accumulation jobs.

        Args:
            accumulation_data_list: List of accumulation job data dicts, each containing:
                - edm_name: str
                - portfolio_name: str
                - job_name: str
                - profile_names: List[str]
                - financial_perspectives: List[str]
                - event_date_behavior: str
                - event_date: str
                - treaty_names: List[str], optional (defaults to [])
                - tag_names: List[str], optional (defaults to [])
                - currency: Dict[str, str], optional
                - geocode_version: str, optional (defaults to "")
                - include_loss_by_treaty: bool, optional (defaults to True)

        Returns:
            List of job IDs

        Raises:
            IRPValidationError: If accumulation_data_list is empty or an entry is invalid
            IRPAPIError: If a submission fails or an analysis name already exists
        """
        validate_list_not_empty(accumulation_data_list, "accumulation_data_list")

        required = (
            "edm_name", "portfolio_name", "job_name", "profile_names",
            "financial_perspectives", "event_date_behavior", "event_date",
        )
        for index, entry in enumerate(accumulation_data_list):
            entry_name = f"accumulation_data_list[{index}]"
            if not isinstance(entry, dict):
                raise IRPValidationError(
                    f"{entry_name} must be a dictionary, got {type(entry).__name__}"
                )
            for field in required:
                if field not in entry:
                    raise IRPValidationError(f"{entry_name} is missing required field '{field}'")

        # Pre-validate that no analysis names already exist
        for entry in accumulation_data_list:
            existing = self.analysis_manager.search_analyses(
                filter=f'analysisName = "{entry["job_name"]}" AND exposureName = "{entry["edm_name"]}"'
            )
            if len(existing) > 0:
                raise IRPAPIError(
                    f"Analysis with name '{entry['job_name']}' already exists for EDM '{entry['edm_name']}'"
                )

        job_ids = []
        for entry in accumulation_data_list:
            job_id, _ = self.submit_portfolio_accumulation_job(
                edm_name=entry['edm_name'],
                portfolio_name=entry['portfolio_name'],
                job_name=entry['job_name'],
                profile_names=entry['profile_names'],
                financial_perspectives=entry['financial_perspectives'],
                event_date_behavior=entry['event_date_behavior'],
                event_date=entry['event_date'],
                treaty_names=entry.get('treaty_names', []),
                tag_names=entry.get('tag_names', []),
                currency=entry.get('currency'),
                geocode_version=entry.get('geocode_version', ""),
                include_loss_by_treaty=entry.get('include_loss_by_treaty', True),
                skip_duplicate_check=True
            )
            job_ids.append(job_id)

        return job_ids


    def submit_portfolio_accumulation_job(
        self,
        edm_name: str,
        portfolio_name: str,
        job_name: str,
        profile_names: List[str],
        financial_perspectives: List[str],
        event_date_behavior: str,
        event_date: str,
        treaty_names: List[str],
        tag_names: List[str],
        currency: Optional[Dict[str, str]] = None,
        geocode_version: str = "",
        include_loss_by_treaty: bool = True,
        skip_duplicate_check: bool = False
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Submit a portfolio accumulation job (submits but doesn't wait).

        Posts a ``resourceType`` ``portfolio`` request to Create accumulation
        job. Every accumulation result the job produces is named ``job_name``;
        with several profiles the results share the name and differ by
        ``modelProfile.id``.

        Args:
            edm_name: Name of the EDM (exposure database)
            portfolio_name: Name of the portfolio to analyze
            job_name: Name for the job and its analysis result (must be unique
                within the EDM)
            profile_names: Accumulation profile names; each must resolve to
                exactly one profile
            financial_perspectives: Financial perspective codes to calculate,
                e.g. ``["GU", "GR", "RL"]``. No default: the API requires the
                list and the Risk Modeler UI always includes ``GU``
            event_date_behavior: One of ``ACCUMULATION_EVENT_DATE_BEHAVIORS``
                (``ignore``, ``location``, ``policy``, ``policyAndLocation``,
                ``treaty``, ``treatyAndLocation``, ``treatyAndPolicy``,
                ``treatyAndPolicyAndLocation``). Decides which effective dates
                ``event_date`` is checked against. No default
            event_date: Event date in ``YYYY-MM-DD`` form. Sent as-is; the API
                accepted this form live. The Risk Modeler UI sends the same
                field as epoch milliseconds
            treaty_names: Treaty names to apply. An empty list sends
                ``treatyIds`` as ``[]``
            tag_names: Tag names to apply. An empty list sends ``tagIds`` as ``[]``
            currency: Optional ``settings.currency`` object with keys
                ``asOfDate``, ``currency``, ``currencySchemeName`` and
                ``currencyVersion``. Defaults to
                ``ReferenceDataManager.get_accumulation_currency()``
            geocode_version: ``portfolioProperties.geocodeVersion``. Defaults
                to ``""``, which is what the Risk Modeler UI sends even for a
                geocoded portfolio. The documented key is ``geoCodingVersion``;
                ``geocodeVersion`` is the key the UI sends and the API accepted
            include_loss_by_treaty: ``additionalOutputOptions.includeLossByTreaty``.
                Defaults to ``True``, matching the Risk Modeler UI
            skip_duplicate_check: Skip checking whether an analysis named
                ``job_name`` already exists in the EDM (for batch submission)

        Returns:
            Tuple of (job_id, request_body) where request_body is the HTTP request payload

        Raises:
            IRPValidationError: If a string argument is empty, ``profile_names``
                or ``financial_perspectives`` is empty, a perspective code is not
                in ``PERSPECTIVE_CODES``, ``event_date_behavior`` is not in
                ``ACCUMULATION_EVENT_DATE_BEHAVIORS``, or ``event_date`` is not
                ``YYYY-MM-DD``
            IRPAPIError: If the analysis name exists, the EDM, portfolio, a
                treaty, a profile or a tag cannot be resolved, or the request fails
        """
        validate_non_empty_string(edm_name, "edm_name")
        validate_non_empty_string(portfolio_name, "portfolio_name")
        validate_non_empty_string(job_name, "job_name")
        validate_list_not_empty(profile_names, "profile_names")
        validate_list_not_empty(financial_perspectives, "financial_perspectives")
        for index, code in enumerate(financial_perspectives):
            if code not in PERSPECTIVE_CODES:
                raise IRPValidationError(
                    f"financial_perspectives[{index}] must be one of {PERSPECTIVE_CODES}, got: {code}"
                )
        validate_non_empty_string(event_date_behavior, "event_date_behavior")
        if event_date_behavior not in ACCUMULATION_EVENT_DATE_BEHAVIORS:
            raise IRPValidationError(
                f"event_date_behavior must be one of {ACCUMULATION_EVENT_DATE_BEHAVIORS}, "
                f"got: {event_date_behavior}"
            )
        validate_iso_date_string(event_date, "event_date")
        if not isinstance(geocode_version, str):
            raise IRPValidationError(
                f"geocode_version must be a string, got {type(geocode_version).__name__}"
            )

        logger.info("Submitting accumulation job '%s' for '%s'/'%s'", job_name, edm_name, portfolio_name)

        if not skip_duplicate_check:
            existing = self.analysis_manager.search_analyses(
                filter=f'analysisName = "{job_name}" AND exposureName = "{edm_name}"'
            )
            if len(existing) > 0:
                raise IRPAPIError(f"Analysis with name '{job_name}' already exists for EDM '{edm_name}'")

        # Look up EDM to get exposure_id
        edms = self.edm_manager.search_edms(filter=f'exposureName="{edm_name}"')
        if len(edms) != 1:
            raise IRPAPIError(f"Expected 1 EDM with name {edm_name}, found {len(edms)}")
        try:
            exposure_id = edms[0]['exposureId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(f"Failed to extract exposure ID for EDM '{edm_name}': {e}") from e

        # Look up portfolio to get portfolio_uri
        portfolios = self.portfolio_manager.search_portfolios(
            exposure_id=exposure_id,
            filter=f'portfolioName="{portfolio_name}"'
        )
        if len(portfolios) != 1:
            raise IRPAPIError(f"Expected 1 portfolio with name {portfolio_name}, found {len(portfolios)}")
        try:
            portfolio_uri = portfolios[0]['uri']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract portfolio URI for portfolio '{portfolio_name}': {e}"
            ) from e

        # Look up treaties by name
        treaty_ids: List[int] = []
        if treaty_names:
            quoted = ", ".join(f'"{name}"' for name in treaty_names)
            try:
                treaties = self.treaty_manager.search_treaties(
                    exposure_id=exposure_id,
                    filter=f"treatyName IN ({quoted})"
                )
            except Exception as e:
                raise IRPAPIError(f"Failed to search treaties with names {treaty_names}: {e}")
            if len(treaties) != len(treaty_names):
                raise IRPAPIError(f"Expected {len(treaty_names)} treaties, found {len(treaties)}")
            try:
                treaty_ids = [treaty['treatyId'] for treaty in treaties]
            except (KeyError, TypeError) as e:
                raise IRPAPIError(f"Failed to extract treaty IDs from treaty search response: {e}") from e

        # Look up accumulation profiles by name
        profile_ids: List[int] = []
        for profile_name in profile_names:
            profile = self.get_accumulation_profile_by_name(profile_name)
            try:
                profile_ids.append(int(profile['profileId']))
            except (KeyError, TypeError, ValueError) as e:
                raise IRPAPIError(
                    f"Failed to extract profile ID for accumulation profile '{profile_name}': {e}"
                ) from e

        # Look up tag IDs
        tag_ids: List[int] = []
        if tag_names:
            try:
                tag_ids = self.reference_data_manager.get_tag_ids_from_tag_names(tag_names)
            except IRPAPIError as e:
                raise IRPAPIError(f"Failed to get tag ids for tag names {tag_names}: {e}")

        if currency is None:
            currency = self.reference_data_manager.get_accumulation_currency()

        data = {
            "resourceType": "portfolio",
            "resourceUri": portfolio_uri,
            "settings": {
                "name": job_name,
                "profileIds": profile_ids,
                "financialPerspectives": financial_perspectives,
                "currency": currency,
                "eventInfo": {
                    "eventDateBehavior": event_date_behavior,
                    "eventDate": event_date
                },
                "portfolioProperties": {
                    "geocodeVersion": geocode_version,
                    "treatyIds": treaty_ids
                },
                "additionalOutputOptions": {
                    "includeLossByTreaty": include_loss_by_treaty
                },
                "tagIds": tag_ids
            }
        }

        try:
            response = self.client.request('POST', CREATE_ACCUMULATION_JOB, json=data)
            job_id = extract_id_from_location_header(response, "accumulation job submission")
            logger.info("Accumulation job submitted — job ID: %s", job_id)
            return int(job_id), data
        except Exception as e:
            raise IRPAPIError(
                f"Failed to submit accumulation job '{job_name}' for portfolio {portfolio_name}: {e}"
            )


    def get_accumulation_job(self, job_id: int) -> Dict[str, Any]:
        """
        Retrieve accumulation job status by job ID.

        The response is the Platform job shape: ``jobId``, ``status``,
        ``progress``, ``name``, ``type``, ``details.resources`` and ``tasks``.
        ``type`` is reported as ``PublicLiveEdmAccumulationEngine``.

        Args:
            job_id: Job ID

        Returns:
            Dict containing job status details

        Raises:
            IRPValidationError: If job_id is invalid
            IRPAPIError: If the request fails
        """
        validate_positive_int(job_id, "job_id")
        try:
            response = self.client.request('GET', GET_ACCUMULATION_JOB.format(jobId=job_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get accumulation job status for job ID {job_id}: {e}")


    def search_accumulation_jobs(
        self,
        filter: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search accumulation jobs.

        ``filter`` is sent when given because Search accumulation jobs documents
        it, but live the server returned the same unfiltered list for
        ``jobId =``, ``jobId IN``, ``name =``, ``status =`` and ``userName =``.
        Do not rely on it to narrow the result; ``limit`` and ``offset`` are
        honoured.

        Args:
            filter: Optional filter string (default: "")
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of accumulation job summary dicts, newest first

        Raises:
            IRPAPIError: If the request fails
        """
        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter

        try:
            response = self.client.request('GET', SEARCH_ACCUMULATION_JOBS, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to search accumulation jobs: {e}")


    def poll_accumulation_job_to_completion(
        self,
        job_id: int,
        interval: int = 10,
        timeout: int = 600000
    ) -> Dict[str, Any]:
        """
        Poll an accumulation job until it reaches a terminal status or times out.

        Returns on ``FINISHED``, ``FAILED`` or ``CANCELLED``; check ``status``.

        Args:
            job_id: Job ID
            interval: Polling interval in seconds (default: 10)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            Final job status details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If the job does not reach a terminal status within ``timeout``
            IRPAPIError: If polling fails or the response lacks ``status``/``progress``
        """
        validate_positive_int(job_id, "job_id")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling accumulation job ID %s", job_id)
            job_data = self.get_accumulation_job(job_id)
            try:
                status = job_data['status']
                progress = job_data['progress']
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Missing 'status' or 'progress' in job response for job ID {job_id}: {e}"
                ) from e
            logger.info("Job %s status: %s; progress: %s", job_id, status, progress)
            if status in WORKFLOW_COMPLETED_STATUSES:
                return job_data

            if time.time() - start > timeout:
                logger.error("Accumulation job %s timed out after %s seconds. Last status: %s", job_id, timeout, status)
                raise IRPJobError(
                    f"Accumulation job ID {job_id} did not complete within {timeout} seconds. Last status: {status}"
                )
            time.sleep(interval)


    def poll_accumulation_job_batch_to_completion(
        self,
        job_ids: List[int],
        interval: int = 20,
        timeout: int = 600000
    ) -> List[Dict[str, Any]]:
        """
        Poll multiple accumulation jobs until all reach a terminal status or time out.

        Calls Get accumulation job once per job ID each round. Search
        accumulation jobs ignores its ``filter`` (see ``search_accumulation_jobs``),
        so a ``jobId IN (...)`` search cannot narrow the list to these jobs.

        Args:
            job_ids: List of job IDs
            interval: Polling interval in seconds (default: 20)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            Final job status details for every job, in ``job_ids`` order. Each
            entry is ``FINISHED``, ``FAILED`` or ``CANCELLED``; check ``status``

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If any job has not reached a terminal status within ``timeout``
            IRPAPIError: If polling fails
        """
        validate_list_not_empty(job_ids, "job_ids")
        for job_id in job_ids:
            validate_positive_int(job_id, "job_ids")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling batch accumulation job IDs: %s", ",".join(str(j) for j in job_ids))
            jobs = [self.get_accumulation_job(job_id) for job_id in job_ids]

            pending = [
                str(job.get('jobId', job_id)) for job_id, job in zip(job_ids, jobs)
                if job.get('status') not in WORKFLOW_COMPLETED_STATUSES
            ]
            if not pending:
                return jobs

            if time.time() - start > timeout:
                logger.error("Batch accumulation jobs timed out after %s seconds; still running: %s", timeout, pending)
                raise IRPJobError(
                    f"Batch accumulation jobs did not complete within {timeout} seconds; "
                    f"still running: {', '.join(pending)}"
                )
            time.sleep(interval)


    # --- Results ----------------------------------------------------------------

    def extract_analysis_id_from_accumulation_job(self, job: Dict[str, Any]) -> int:
        """
        Read the result ``analysisId`` out of a finished accumulation job.

        The ID is ``tasks[0].output.log.analysisId`` and is the Risk Data API
        ``analysisId`` (``/platform/riskdata/v1/analyses/{analysisId}``). While
        the job is running the field reads ``"0"``, so a job that is not
        ``FINISHED`` is rejected rather than returning a placeholder.

        Args:
            job: Job dict as returned by ``get_accumulation_job`` or the poll methods

        Returns:
            Analysis ID of the accumulation result

        Raises:
            IRPJobError: If the job ``status`` is not ``FINISHED``
            IRPAPIError: If the job carries no positive ``analysisId``
        """
        job_id = job.get('jobId') if isinstance(job, dict) else None
        status = job.get('status') if isinstance(job, dict) else None
        if status != WORKFLOW_FINISHED_STATUS:
            raise IRPJobError(
                f"Accumulation job {job_id} has status {status}; an analysis ID is only "
                f"available once the job is {WORKFLOW_FINISHED_STATUS}"
            )
        try:
            analysis_id = int(job['tasks'][0]['output']['log']['analysisId'])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise IRPAPIError(
                f"Accumulation job {job_id} carries no tasks[0].output.log.analysisId: {e}"
            ) from e
        if analysis_id <= 0:
            raise IRPAPIError(
                f"Accumulation job {job_id} reports analysisId {analysis_id}, which is not a result"
            )
        return analysis_id


    def get_analysis_for_accumulation_job(self, job_id: int) -> Dict[str, Any]:
        """
        Retrieve the analysis result produced by a finished accumulation job.

        Reads the job, takes ``tasks[0].output.log.analysisId``, and returns
        ``AnalysisManager.get_analysis_by_id`` for it. The analysis carries
        ``engineType`` ``Accumulation``, ``modelProfile.id`` equal to the
        accumulation ``profileId``, ``variationId``, ``eventInfo`` and
        ``appAnalysisId``.

        Args:
            job_id: Accumulation job ID

        Returns:
            Dict containing the analysis result metadata

        Raises:
            IRPValidationError: If job_id is invalid
            IRPJobError: If the job is not ``FINISHED``
            IRPAPIError: If the job or analysis cannot be read
        """
        job = self.get_accumulation_job(job_id)
        analysis_id = self.extract_analysis_id_from_accumulation_job(job)
        return self.analysis_manager.get_analysis_by_id(analysis_id)


    def search_accumulation_analyses(
        self,
        filter: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search analysis results whose ``engineType`` is ``Accumulation``.

        ``filter`` is combined with ``engineType = "Accumulation"`` using ``AND``.

        Args:
            filter: Optional additional filter, e.g. ``exposureName = "my_edm"``
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of analysis result dicts

        Raises:
            IRPAPIError: If the search fails
        """
        combined = f'engineType = "{ACCUMULATION_ENGINE_TYPE}"'
        if filter:
            combined = f'{combined} AND ({filter})'
        return self.analysis_manager.search_analyses(filter=combined, limit=limit, offset=offset)


    def search_accumulation_analyses_paginated(self, filter: str = "") -> List[Dict[str, Any]]:
        """
        Search all accumulation analysis results with automatic pagination.

        Args:
            filter: Optional additional filter, combined with
                ``engineType = "Accumulation"`` using ``AND``

        Returns:
            Complete list of all matching analysis results across all pages

        Raises:
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        return paginate_search(
            lambda limit, offset: self.search_accumulation_analyses(
                filter=filter,
                limit=limit,
                offset=offset
            ),
            "Accumulation analysis results search"
        )
