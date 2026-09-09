"""
Analysis management operations.

Handles portfolio analysis submission, job tracking, and result retrieval.
"""

import json
import logging
import time
from typing import Dict, List, Any, Optional, Tuple, TYPE_CHECKING
from .analysis_validation import (
    analysis_type_for_software_version,
    validate_event_rate_scheme_settings,
)
from .constants import (
    CREATE_ANALYSIS_JOB, DELETE_ANALYSIS,
    GET_ANALYSIS_JOB, GET_ANALYSIS_RESULT,
    SEARCH_ANALYSIS_JOBS, SEARCH_ANALYSIS_RESULTS,
    WORKFLOW_COMPLETED_STATUSES, WORKFLOW_IN_PROGRESS_STATUSES,
    GET_ANALYSIS_ELT, GET_ANALYSIS_EP, GET_ANALYSIS_STATS, GET_ANALYSIS_PLT,
    GET_ANALYSIS_REGIONS, GET_ANALYSIS_TREATIES, PERSPECTIVE_CODES,
    CREATE_EXPORT_JOB
)
from .exceptions import IRPAPIError, IRPJobError, IRPReferenceDataError, IRPValidationError
from .validators import validate_non_empty_string, validate_positive_int, validate_list_not_empty
from .utils import extract_id_from_location_header, paginate_search

if TYPE_CHECKING:
    from . import IRPClient
    from .reference_data import ReferenceDataManager
    from .treaty import TreatyManager
    from .edm import EDMManager
    from .portfolio import PortfolioManager

logger = logging.getLogger(__name__)

class AnalysisManager:
    """Manager for analysis operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize analysis manager.

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


    def get_analysis_by_id(self, analysis_id: int) -> Dict[str, Any]:
        """
        Retrieve analysis by ID.

        Args:
            analysis_id: Analysis ID

        Returns:
            Dict containing analysis details

        Raises:
            IRPValidationError: If analysis_id is invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        try:
            response = self.client.request('GET', GET_ANALYSIS_RESULT.format(analysisId=analysis_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get analysis {analysis_id}: {e}")


    def submit_portfolio_analysis_jobs(self, analysis_data_list: List[Dict[str, Any]]) -> List[int]:
        """
        Submit multiple portfolio analysis jobs.

        Args:
            analysis_data_list: List of analysis job data dicts, each containing:
                - edm_name: str
                - portfolio_name: str
                - job_name: str
                - analysis_profile_name: str
                - output_profile_name: str
                - event_rate_scheme_name: str
                - treaty_names: List[str], optional (defaults to [])
                - tag_names: List[str], optional (defaults to [])

        Returns:
            List of job IDs

        Raises:
            IRPValidationError: If analysis_data_list is empty or invalid
            IRPAPIError: If analysis submission fails or duplicate analysis names exist
        """
        validate_list_not_empty(analysis_data_list, "analysis_data_list")

        # Pre-validate that no analysis names already exist
        analysis_names = list(a['job_name'] for a in analysis_data_list)
        for name in analysis_names:
            analysis_response = self.search_analyses(filter=f"analysisName = \"{name}\"")
            if len(analysis_response) > 0:
                raise IRPAPIError(f"Analysis with this name already exists: {name}")

        job_ids = []
        for analysis_data in analysis_data_list:
            try:
                # Returns tuple of (job_id, request_body) - we only need job_id here
                job_id, _ = self.submit_portfolio_analysis_job(
                    edm_name=analysis_data['edm_name'],
                    portfolio_name=analysis_data['portfolio_name'],
                    job_name=analysis_data['job_name'],
                    analysis_profile_name=analysis_data['analysis_profile_name'],
                    output_profile_name=analysis_data['output_profile_name'],
                    event_rate_scheme_name=analysis_data['event_rate_scheme_name'],
                    treaty_names=analysis_data.get('treaty_names', []),
                    tag_names=analysis_data.get('tag_names', []),
                    skip_duplicate_check=True  # Already validated above
                )
                job_ids.append(job_id)
            except KeyError as e:
                raise IRPAPIError(f"Missing analysis job data: {e}") from e

        return job_ids

    def submit_portfolio_analysis_job(
        self,
        edm_name: str,
        portfolio_name: str,
        job_name: str,
        analysis_profile_name: str,
        output_profile_name: str,
        event_rate_scheme_name: str,
        treaty_names: List[str],
        tag_names: List[str],
        currency: Optional[Dict[str, str]] = None,
        skip_duplicate_check: bool = False,
        franchise_deductible: bool = False,
        min_loss_threshold: float = 1.0,
        treat_construction_occupancy_as_unknown: bool = True,
        num_max_loss_event: int = 1
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Submit portfolio analysis job (submits but doesn't wait).

        Args:
            edm_name: Name of the EDM (exposure database)
            portfolio_name: Name of the portfolio to analyze
            job_name: Name for analysis job (must be unique)
            analysis_profile_name: Model profile name
            output_profile_name: Output profile name
            event_rate_scheme_name: Event rate scheme name (required for DLM, optional for HD)
            treaty_names: List of treaty names to apply. An empty list submits the
                analysis with no treaties applied (treatyIds is sent as [])
            tag_names: List of tag names to apply. An empty list submits the analysis
                with no tags applied (tagIds is sent as [])
            currency: Optional currency configuration
            skip_duplicate_check: Skip checking if analysis name already exists (for batch operations)
            franchise_deductible: Whether to apply franchise deductible (default: False)
            min_loss_threshold: Minimum loss threshold value (default: 0)
            treat_construction_occupancy_as_unknown: Treat construction/occupancy as unknown (default: True)
            num_max_loss_event: Number of max loss events to include (default: 1)

        Returns:
            Tuple of (job_id, request_body) where request_body is the HTTP request payload

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If request fails or EDM/portfolio not found
            IRPReferenceDataError: If a profile, tag, or event rate scheme cannot
                be resolved; if the model profile is DLM and no event rate scheme
                name was given; or if the event rate scheme's perilCode and
                modelRegionCode do not match the model profile's
        """
        validate_non_empty_string(edm_name, "edm_name")
        validate_non_empty_string(portfolio_name, "portfolio_name")
        validate_non_empty_string(job_name, "job_name")
        validate_non_empty_string(analysis_profile_name, "analysis_profile_name")
        validate_non_empty_string(output_profile_name, "output_profile_name")
        # event_rate_scheme_name validation deferred - required for DLM but optional for HD

        logger.info("Submitting analysis job '%s' for '%s'/'%s'", job_name, edm_name, portfolio_name)

        # Check if analysis name already exists (unless skipped for batch operations)
        if not skip_duplicate_check:
            analysis_response = self.search_analyses(filter=f"analysisName = \"{job_name}\" AND exposureName = \"{edm_name}\"")
            if len(analysis_response) > 0:
                raise IRPAPIError(f"Analysis with name '{job_name}' already exists for EDM '{edm_name}'")

        # Look up EDM to get exposure_id
        edms = self.edm_manager.search_edms(filter=f"exposureName=\"{edm_name}\"")
        if len(edms) != 1:
            raise IRPAPIError(f"Expected 1 EDM with name {edm_name}, found {len(edms)}")
        try:
            exposure_id = edms[0]['exposureId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract exposure ID for EDM '{edm_name}': {e}"
            ) from e

        # Look up portfolio to get portfolio_uri
        portfolios = self.portfolio_manager.search_portfolios(
            exposure_id=exposure_id,
            filter=f"portfolioName=\"{portfolio_name}\""
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
        if treaty_names:
            try:
                quoted = ", ".join(json.dumps(s) for s in treaty_names)
                filter_statement = f"treatyName IN ({quoted})"
                treaties_response = self.treaty_manager.search_treaties(
                    exposure_id=exposure_id,
                    filter=filter_statement
                )
            except Exception as e:
                raise IRPAPIError(f"Failed to search treaties with names {treaty_names}: {e}")

            if len(treaties_response) != len(treaty_names):
                raise IRPAPIError(f"Expected {len(treaty_names)} treaties, found {len(treaties_response)}")
            try:
                treaty_ids = [treaty['treatyId'] for treaty in treaties_response]
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Failed to extract treaty IDs from treaty search response: {e}"
                ) from e
        else:
            treaty_ids = []

        # Look up reference data - model profile first to determine job type
        model_profile_response = self.reference_data_manager.get_model_profile_by_name(analysis_profile_name)
        output_profile_response = self.reference_data_manager.get_output_profile_by_name(output_profile_name)

        if model_profile_response.get('count', 0) == 0:
            raise IRPReferenceDataError(f"Analysis profile '{analysis_profile_name}' not found")
        if len(output_profile_response) == 0:
            raise IRPReferenceDataError(f"Output profile '{output_profile_name}' not found")

        try:
            model_profile = model_profile_response['items'][0]
            model_profile_id = model_profile['id']
            # Extract perilCode and modelRegionCode for event rate scheme lookup
            model_peril_code = model_profile.get('perilCode')
            model_region_code = model_profile.get('modelRegionCode')
            software_version_code = model_profile['softwareVersionCode']
            job_type = analysis_type_for_software_version(software_version_code)
        except (KeyError, IndexError, TypeError) as e:
            raise IRPReferenceDataError(
                f"Failed to extract model profile ID for '{analysis_profile_name}': {e}"
            ) from e

        try:
            output_profile_id = output_profile_response[0]['id']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPReferenceDataError(
                f"Failed to extract output profile ID for '{output_profile_name}': {e}"
            ) from e

        # Use perilCode and modelRegionCode from model profile to filter the correct event rate scheme
        event_rate_scheme_id = None
        scheme_peril_code = None
        scheme_model_region_code = None
        if event_rate_scheme_name:
            event_rate_scheme_response = self.reference_data_manager.get_event_rate_scheme_by_name(
                event_rate_scheme_name,
                peril_code=model_peril_code,
                model_region_code=model_region_code
            )
            if event_rate_scheme_response.get('count', 0) == 0:
                filter_info = f" (perilCode={model_peril_code}, modelRegionCode={model_region_code})" if model_peril_code or model_region_code else ""
                raise IRPReferenceDataError(f"Event rate scheme '{event_rate_scheme_name}'{filter_info} not found")
            try:
                event_rate_scheme = event_rate_scheme_response['items'][0]
                event_rate_scheme_id = event_rate_scheme['eventRateSchemeId']
                scheme_peril_code = event_rate_scheme.get('perilCode')
                scheme_model_region_code = event_rate_scheme.get('modelRegionCode')
            except (KeyError, IndexError, TypeError) as e:
                raise IRPReferenceDataError(
                    f"Failed to extract event rate scheme ID for '{event_rate_scheme_name}': {e}"
                ) from e

        # Event rate scheme is required for DLM analyses but optional for HD
        validation_error = validate_event_rate_scheme_settings(
            software_version_code,
            scheme_provided=bool(event_rate_scheme_name),
            profile_peril_code=model_peril_code,
            profile_model_region_code=model_region_code,
            scheme_peril_code=scheme_peril_code,
            scheme_model_region_code=scheme_model_region_code,
        )
        if validation_error:
            raise IRPReferenceDataError(validation_error)

        # Look up tag IDs
        if tag_names:
            try:
                tag_ids = self.reference_data_manager.get_tag_ids_from_tag_names(tag_names)
            except IRPAPIError as e:
                raise IRPAPIError(f"Failed to get tag ids for tag names {tag_names}: {e}")
        else:
            tag_ids = []

        if currency is None:
            currency = self.reference_data_manager.get_analysis_currency()

        settings = {
            "name": job_name,
            "modelProfileId": model_profile_id,
            "outputProfileId": output_profile_id,
            "treatyIds": treaty_ids,
            "tagIds": tag_ids,
            "currency": currency,
            "franchiseDeductible": franchise_deductible,
            "minLossThreshold": min_loss_threshold,
            "treatConstructionOccupancyAsUnknown": treat_construction_occupancy_as_unknown,
            "numMaxLossEvent": num_max_loss_event
        }

        # Only include eventRateSchemeId for DLM analyses
        if event_rate_scheme_id is not None:
            settings["eventRateSchemeId"] = event_rate_scheme_id

        data = {
            "resourceUri": portfolio_uri,
            "resourceType": "portfolio",
            "type": job_type,
            "settings": settings
        }

        try:
            response = self.client.request('POST', CREATE_ANALYSIS_JOB, json=data)
            job_id = extract_id_from_location_header(response, "analysis job submission")
            logger.info("Analysis job submitted — job ID: %s", job_id)
            return int(job_id), data
        except Exception as e:
            raise IRPAPIError(f"Failed to submit analysis job '{job_name}' for portfolio {portfolio_name}: {e}")


    def get_analysis_job(self, job_id: int) -> Dict[str, Any]:
        """
        Retrieve analysis job status by job ID.

        Args:
            job_id: Job ID

        Returns:
            Dict containing job status details

        Raises:
            IRPValidationError: If job_id is invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(job_id, "job_id")

        try:
            response = self.client.request('GET', GET_ANALYSIS_JOB.format(jobId=job_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get analysis job status for job ID {job_id}: {e}")


    def poll_analysis_job_to_completion(
            self,
            job_id: int,
            interval: int = 10,
            timeout: int = 600000
    ) -> Dict[str, Any]:
        """
        Poll analysis job until completion or timeout.

        Args:
            job_id: Job ID
            interval: Polling interval in seconds (default: 10)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            Final job status details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If job times out
            IRPAPIError: If polling fails
        """
        validate_positive_int(job_id, "job_id")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling analysis job ID %s", job_id)
            job_data = self.get_analysis_job(job_id)
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
                logger.error("Analysis job %s timed out after %s seconds. Last status: %s", job_id, timeout, status)
                raise IRPJobError(
                    f"Analysis job ID {job_id} did not complete within {timeout} seconds. Last status: {status}"
                )
            time.sleep(interval)


    def search_analysis_jobs(self, filter: str = "", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Search analysis jobs with optional filtering.

        Args:
            filter: Optional filter string (default: "")
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of analysis job dicts

        Raises:
            IRPAPIError: If search fails
        """
        params: Dict[str, Any] = {
            'limit': limit,
            'offset': offset
        }
        if filter:
            params['filter'] = filter

        try:
            response = self.client.request('GET', SEARCH_ANALYSIS_JOBS, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to search analysis jobs : {e}")


    def poll_analysis_job_batch_to_completion(
            self,
            job_ids: List[int],
            interval: int = 20,
            timeout: int = 600000
    ) -> List[Dict[str, Any]]:
        """
        Poll multiple analysis jobs until all complete or timeout.

        Args:
            job_ids: List of job IDs
            interval: Polling interval in seconds (default: 20)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            List of final job status details for all jobs

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If jobs time out
            IRPAPIError: If polling fails
        """
        validate_list_not_empty(job_ids, "job_ids")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling batch analysis job IDs: %s", ",".join(str(item) for item in job_ids))

            # Fetch all workflows across all pages
            all_jobs = []
            offset = 0
            limit = 100
            while True:
                quoted = ", ".join(json.dumps(str(s)) for s in job_ids)
                filter_statement = f"jobId IN ({quoted})"
                analysis_response = self.search_analysis_jobs(
                    filter=filter_statement,
                    limit=limit,
                    offset=offset
                )
                all_jobs.extend(analysis_response)

                # Check if we've fetched all workflows
                if len(all_jobs) >= len(job_ids):
                    break

                # Move to next page
                offset += limit

            # Check if all workflows are completed
            all_completed = True
            for job in all_jobs:
                status = job.get('status', '')
                if status in WORKFLOW_IN_PROGRESS_STATUSES:
                    all_completed = False
                    break

            if all_completed:
                return all_jobs

            if time.time() - start > timeout:
                logger.error("Batch analysis jobs timed out after %s seconds", timeout)
                raise IRPJobError(
                    f"Batch analysis jobs did not complete within {timeout} seconds"
                )
            time.sleep(interval)


    def search_analyses(self, filter: str = "", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Search analysis results with optional filtering.

        Args:
            filter: Optional filter string (default: "")
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of analysis result dicts

        Raises:
            IRPAPIError: If search fails
        """
        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter

        try:
            response = self.client.request('GET', SEARCH_ANALYSIS_RESULTS, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to search analysis results : {e}")

    def search_analyses_paginated(self, filter: str = "") -> List[Dict[str, Any]]:
        """
        Search all analysis results with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``.

        Args:
            filter: Optional filter string (default: "")

        Returns:
            Complete list of all matching analysis results across all pages

        Raises:
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        return paginate_search(
            lambda limit, offset: self.search_analyses(
                filter=filter,
                limit=limit,
                offset=offset
            ),
            "Analysis results search"
        )

    def get_analysis_by_name(self, analysis_name: str, edm_name: str) -> Dict[str, Any]:
        """
        Get an analysis by name and EDM name.

        Args:
            analysis_name: Name of the analysis
            edm_name: Name of the EDM (exposure database)

        Returns:
            Dict containing analysis details

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If analysis not found or multiple matches
        """
        validate_non_empty_string(analysis_name, "analysis_name")
        validate_non_empty_string(edm_name, "edm_name")

        filter_str = f'analysisName = "{analysis_name}" AND exposureName = "{edm_name}"'
        analyses = self.search_analyses(filter=filter_str)

        if len(analyses) == 0:
            raise IRPAPIError(f"Analysis '{analysis_name}' not found for EDM '{edm_name}'")
        if len(analyses) > 1:
            raise IRPAPIError(f"Multiple analyses found with name '{analysis_name}' for EDM '{edm_name}'")

        return analyses[0]

    def delete_analysis(self, analysis_id: int) -> None:
        """
        Delete an analysis by ID.

        Args:
            analysis_id: Analysis ID to delete

        Raises:
            IRPValidationError: If analysis_id is invalid
            IRPAPIError: If deletion fails
        """
        validate_positive_int(analysis_id, "analysis_id")

        try:
            self.client.request('DELETE', DELETE_ANALYSIS.format(analysisId=analysis_id))
            logger.info("Deleted analysis ID: %s", analysis_id)
        except Exception as e:
            raise IRPAPIError(f"Failed to delete analysis : {e}")

    def get_analysis_by_app_analysis_id(self, app_analysis_id: int) -> Dict[str, Any]:
        """
        Retrieve analysis by appAnalysisId (the ID used in the application/UI).

        Args:
            app_analysis_id: Application analysis ID (e.g., 35810)

        Returns:
            Dict containing analysisId and exposureResourceId

        Raises:
            IRPValidationError: If app_analysis_id is invalid
            IRPAPIError: If request fails or analysis not found
        """
        validate_positive_int(app_analysis_id, "app_analysis_id")

        try:
            filter_str = f"appAnalysisId={app_analysis_id}"
            results = self.search_analyses(filter=filter_str)
            if not results:
                raise IRPAPIError(f"No analysis found with appAnalysisId={app_analysis_id}")

            analysis = results[0]
            return {
                'analysisId': analysis.get('analysisId'),
                'exposureResourceId': analysis.get('exposureResourceId'),
                'analysisName': analysis.get('analysisName'),
                'engineType': analysis.get('engineType'),  # 'HD' or 'DLM'
                'uri': analysis.get('uri'),
                'raw': analysis
            }
        except IRPAPIError:
            raise
        except Exception as e:
            raise IRPAPIError(f"Failed to get analysis by appAnalysisId {app_analysis_id}: {e}")

    def _validate_perspective_code(self, perspective_code: str) -> None:
        """Validate perspective code is one of the allowed values."""
        if perspective_code not in PERSPECTIVE_CODES:
            raise IRPValidationError(
                f"Invalid perspective_code '{perspective_code}'. Not in the "
                "list of valid perspective codes; see PERSPECTIVE_CODES in "
                "irp_integration/constants.py"
            )

    def get_elt(
        self,
        analysis_id: int,
        perspective_code: str,
        exposure_resource_id: int,
        filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve Event Loss Table (ELT) for an analysis.

        Args:
            analysis_id: Analysis ID
            perspective_code: Risk Modeler financial perspective code
                (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
                in constants.py for the full set.
            exposure_resource_id: Exposure resource ID (portfolio ID from analysis)
            filter: Optional filter string (e.g., "eventId IN (1, 2, 3)" or "eventId = 123")
            limit: Optional maximum number of records to return
            offset: Optional number of records to skip (for pagination)

        Returns:
            List of ELT records containing eventId, positionValue, stdDevI, stdDevC, etc.

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        self._validate_perspective_code(perspective_code)

        params = {
            'perspectiveCode': perspective_code,
            'exposureResourceType': 'PORTFOLIO',
            'exposureResourceId': exposure_resource_id
        }

        if filter is not None:
            params['filter'] = filter
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset

        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_ELT.format(analysisId=analysis_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get ELT for analysis {analysis_id}: {e}")

    def get_ep(
        self,
        analysis_id: int,
        perspective_code: str,
        exposure_resource_id: int
    ) -> List[Dict[str, Any]]:
        """
        Retrieve EP (Exceedance Probability) metrics for an analysis.

        Args:
            analysis_id: Analysis ID
            perspective_code: Risk Modeler financial perspective code
                (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
                in constants.py for the full set.
            exposure_resource_id: Exposure resource ID (portfolio ID from analysis)

        Returns:
            List of EP curve data (OEP, AEP, CEP, TCE curves)

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        self._validate_perspective_code(perspective_code)

        params = {
            'perspectiveCode': perspective_code,
            'exposureResourceType': 'PORTFOLIO',
            'exposureResourceId': exposure_resource_id
        }

        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_EP.format(analysisId=analysis_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get EP metrics for analysis {analysis_id}: {e}")

    def get_stats(
        self,
        analysis_id: int,
        perspective_code: str,
        exposure_resource_id: int
    ) -> List[Dict[str, Any]]:
        """
        Retrieve statistics for an analysis.

        Args:
            analysis_id: Analysis ID
            perspective_code: Risk Modeler financial perspective code
                (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
                in constants.py for the full set.
            exposure_resource_id: Exposure resource ID (portfolio ID from analysis)

        Returns:
            List of statistical metrics

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        self._validate_perspective_code(perspective_code)

        params = {
            'perspectiveCode': perspective_code,
            'exposureResourceType': 'PORTFOLIO',
            'exposureResourceId': exposure_resource_id
        }

        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_STATS.format(analysisId=analysis_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get statistics for analysis {analysis_id}: {e}")

    def get_plt(
        self,
        analysis_id: int,
        perspective_code: str,
        exposure_resource_id: int,
        filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve Period Loss Table (PLT) for an analysis.

        Note: PLT is only available for HD (High Definition) analyses.

        Args:
            analysis_id: Analysis ID
            perspective_code: Risk Modeler financial perspective code
                (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
                in constants.py for the full set.
            exposure_resource_id: Exposure resource ID (portfolio ID from analysis)
            filter: Optional filter string (e.g., "eventId IN (1, 2, 3)" or "eventId = 123")
            limit: Optional maximum number of records to return (default: 100000)
            offset: Optional number of records to skip (for pagination)

        Returns:
            List of PLT records containing event dates, loss dates, and loss amounts

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        self._validate_perspective_code(perspective_code)

        params = {
            'perspectiveCode': perspective_code,
            'exposureResourceType': 'PORTFOLIO',
            'exposureResourceId': exposure_resource_id,
            'limit': limit if limit is not None else 100000
        }

        if filter is not None:
            params['filter'] = filter
        if offset is not None:
            params['offset'] = offset

        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_PLT.format(analysisId=analysis_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get PLT for analysis {analysis_id}: {e}")

    def get_regions(
        self,
        analysis_id: int
    ) -> List[Dict[str, Any]]:
        """
        Retrieve region/peril breakdown for an analysis or group.

        This is used to build the regionPerilSimulationSet for grouping requests.
        Each region entry contains framework, peril display name, region code, and
        simulation identifiers (eventRateSchemeId for ELT, petId for PLT).

        Args:
            analysis_id: Analysis or group ID

        Returns:
            List of region dicts containing:
                - region: Region code (e.g., "NA")
                - subRegion: Sub-region code (e.g., "I2")
                - peril: Peril display name (e.g., "Earthquake", "Windstorm"); the
                  analysis detail carries the code in ``perilCode``
                - eventRateSchemeId: Event rate scheme ID (for ELT framework)
                - framework: Framework type ("ELT" or "PLT")
                - analysisId: The analysis ID
                - modelProfileId: Model profile ID
                - petId: PET ID (for PLT/HD framework)
                - numSamples: Number of samples
                - periods: Number of periods
                - applyContractFlag: Contract application flag
                - engineVersion: Engine version (e.g., "RL23", "HDv2.0")

        Raises:
            IRPValidationError: If analysis_id is invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(analysis_id, "analysis_id")

        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_REGIONS.format(analysisId=analysis_id)
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get regions for analysis {analysis_id}: {e}")

    def search_analysis_treaties(
        self,
        analysis_id: int,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search the treaties applied to an analysis.

        Args:
            analysis_id: Analysis ID
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of treaty dictionaries

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If API request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        try:
            response = self.client.request(
                'GET',
                GET_ANALYSIS_TREATIES.format(analysisId=analysis_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(f"Failed to get treaties for analysis {analysis_id}: {e}")

    def search_analysis_treaties_paginated(self, analysis_id: int) -> List[Dict[str, Any]]:
        """
        Search all treaties applied to an analysis with automatic pagination.

        Fetches all pages of results, paging via ``paginate_search``.

        Args:
            analysis_id: Analysis ID

        Returns:
            Complete list of all treaties across all pages. Each treaty contains
            treatyId, treatyNumber, treatyName, cedant, producer, treatyType
            (CATA, QUOT, SURP, WORK, CORP, STOP, NCAT), currency, attachmentBasis
            (L or R), attachmentLevel (PORT, ACCT, POL, LOC), premium,
            occurrenceLimit, attachmentPoint, riskLimit, retentionAmount,
            percentagePlaced, effectiveDate, expirationDate, percentageRetention,
            percentageRiShare, percentageCovered, priority,
            numberOfReinstatements, reinstatementCharge, maolAmount, isValid,
            userId1, userId2, aggregateDeductible, aggregateLimit, uri, lobs,
            lossOccurrences, analysisId, and tagIds.

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(analysis_id, "analysis_id")

        return paginate_search(
            lambda limit, offset: self.search_analysis_treaties(
                analysis_id=analysis_id,
                limit=limit,
                offset=offset
            ),
            f"Treaty search for analysis ID {analysis_id}"
        )

    def submit_analysis_export_job(
        self,
        analysis_id: int,
        loss_details: List[Dict[str, Any]],
        file_extension: str = "PARQUET"
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Submit an analysis results export job.

        Args:
            analysis_id: ID of the analysis to export
            loss_details: List of loss detail configurations, each containing:
                - metricType: str (e.g., "LOSS_TABLES")
                - outputLevels: List[str] (e.g., ["Portfolio"])
                - perspectiveCodes: List[str] (e.g., ["GU", "GR"])
            file_extension: Export file format (default: "PARQUET")

        Returns:
            Tuple of (job_id, request_body)

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If the analysis doesn't exist or request fails
        """
        validate_positive_int(analysis_id, "analysis_id")
        validate_list_not_empty(loss_details, "loss_details")

        resource_uris = []

        # Validate analysis exists
        results = self.search_analyses(filter=f"analysisId={analysis_id}")
        if not results:
            results = self.search_analyses(filter=f"appAnalysisId={analysis_id}")
        if not results:
            raise IRPAPIError(f"Analysis with ID {analysis_id} not found")
        resource_uris.append(results[0]['uri'])

        data = {
            "exportType": "RESULTS",
            "resourceUris": resource_uris,
            "resourceType": "analyses",
            "settings": {
                "fileExtension": file_extension,
                "lossDetails": loss_details
            }
        }

        try:
            response = self.client.request('POST', CREATE_EXPORT_JOB, json=data)
            job_id = extract_id_from_location_header(response, "analysis export job")
            logger.info("Analysis export job submitted — job ID: %s", job_id)
            return int(job_id), data
        except IRPAPIError:
            raise
        except Exception as e:
            raise IRPAPIError(f"Failed to submit analysis export job: {e}")
