# app/routers/reports.py
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from typing import List, Dict, Any, Optional

from app.services.reporting import reporting_service
from app.services.s3_service import s3_service

router = APIRouter(
    prefix="/api/reports",
    tags=["reports"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


@router.get("/", response_model=List[Dict[str, Any]])
async def list_reports(
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)
):
    """
    List available reports.

    Args:
        limit: Maximum number of reports to return
        offset: Number of reports to skip

    Returns:
        List of report metadata
    """
    logger.debug(f"Listing reports (limit: {limit}, offset: {offset})")
    reports = reporting_service.list_reports(limit=limit)

    # Create a new list with deduplicated reports based on report_id
    unique_reports = {}
    for report in reports:
        if "report_id" in report:
            report_id = report["report_id"]

            # Only process this report if we haven't seen this ID before
            if report_id not in unique_reports:
                # If needed, fetch additional data and enhance the report
                if "persona_name" not in report and "data" not in report:
                    report_data = reporting_service.get_report(report_id)
                    if report_data:
                        # Include important fields directly at the top level
                        report["persona_name"] = report_data.get("persona_name")
                        report["behavior_name"] = report_data.get("behavior_name")
                        report["test_case_name"] = report_data.get("test_case_name")

                unique_reports[report_id] = report

    # Convert back to a list
    return list(unique_reports.values())


@router.get("/s3-presigned-url", response_model=Dict[str, str])
async def get_s3_presigned_url(
    bucket: str = Query(..., description="S3 bucket name"),
    key: str = Query(..., description="S3 object key"),
    expiration: int = Query(
        3600, ge=1, le=86400, description="URL expiration time in seconds"
    ),
):
    """
    Generate a presigned URL for an S3 object.

    This endpoint is used to get temporary access to audio files stored in S3.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        expiration: URL expiration time in seconds

    Returns:
        Dictionary with the presigned URL
    """
    logger.debug(f"Generating presigned URL for s3://{bucket}/{key}")

    url = s3_service.generate_presigned_url(f"s3://{bucket}/{key}", expiration)

    if not url:
        raise HTTPException(status_code=404, detail="Failed to generate presigned URL")

    return {"url": url}


@router.get("/presigned-audio-url", response_model=Dict[str, str])
async def get_presigned_audio_url(
    s3_url: str = Query(..., description="S3 URL of the audio recording")
):
    """
    Generate a presigned URL for an audio recording stored in S3.

    Args:
        s3_url: Full S3 URL (s3://bucket/key)

    Returns:
        Dict with presigned URL and content type
    """
    logger.debug(f"Generating presigned URL for: {s3_url}")

    try:
        from app.services.s3_service import s3_service

        # Validate S3 URL format
        if not s3_url.startswith("s3://"):
            logger.error(f"Invalid S3 URL format: {s3_url}")
            raise HTTPException(
                status_code=400, detail="Invalid S3 URL format, must start with s3://"
            )

        # Generate presigned URL
        presigned_url = s3_service.generate_presigned_url(
            s3_url, expiration=3600  # URL valid for 1 hour
        )

        if not presigned_url:
            logger.error(f"Failed to generate presigned URL for {s3_url}")
            raise HTTPException(
                status_code=404,
                detail="Failed to generate presigned URL or object not found",
            )

        # Determine content type based on file extension
        content_type = "audio/wav"  # Default for WAV
        if s3_url.endswith(".mp3"):
            content_type = "audio/mpeg"
        elif s3_url.endswith(".ogg"):
            content_type = "audio/ogg"

        return {"url": presigned_url, "contentType": content_type}

    except Exception as e:
        logger.error(f"Error generating presigned URL: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500, detail=f"Error generating presigned URL: {str(e)}"
        )


@router.get("/{report_id}", response_model=Dict[str, Any])
async def get_report(report_id: str):
    """
    Get a report by ID.

    Args:
        report_id: Report ID

    Returns:
        Report data
    """
    logger.info(f"Getting report: {report_id}")
    report = reporting_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


@router.get("/{report_id}/html", response_class=HTMLResponse)
async def get_report_html(report_id: str):
    """
    Get an HTML version of a report.

    Args:
        report_id: Report ID

    Returns:
        HTML report
    """
    logger.info(f"Getting HTML report: {report_id}")
    html = reporting_service.generate_html_report(report_id)
    return html


@router.post("/aggregate", response_model=Dict[str, Any])
async def create_aggregate_report(
    report_ids: List[str], name: str, description: Optional[str] = None
):
    """
    Create an aggregate report from multiple test case reports.

    Args:
        report_ids: List of report IDs to aggregate
        name: Name of the aggregate report
        description: Optional description of the aggregate report

    Returns:
        Metadata for the created aggregate report
    """
    logger.info(f"Creating aggregate report '{name}' from {len(report_ids)} reports")

    try:
        aggregate_report = reporting_service.generate_aggregate_report(
            report_ids, name, description
        )

        return {
            "message": "Aggregate report created",
            "report_id": str(aggregate_report.id),
            "name": aggregate_report.name,
            "num_reports": len(report_ids),
        }
    except Exception as e:
        logger.error(f"Error creating aggregate report: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating aggregate report: {str(e)}"
        )


@router.delete("/{report_id}", response_model=Dict[str, Any])
async def delete_report(report_id: str):
    """
    Delete a report.

    Args:
        report_id: Report ID

    Returns:
        Status message
    """
    logger.info(f"Deleting report: {report_id}")

    # Check if report exists
    report = reporting_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    try:
        # List reports to find the exact S3 key
        reports = s3_service.list_reports(limit=1000)
        matching_reports = [r for r in reports if r["report_id"] == report_id]

        if not matching_reports:
            raise HTTPException(
                status_code=404, detail=f"Report {report_id} not found in S3"
            )

        # Get the S3 key
        s3_key = matching_reports[0]["s3_key"]

        # Delete the report from S3
        s3_service.s3_client.delete_object(Bucket=s3_service.bucket_name, Key=s3_key)

        # Remove from cache if exists
        if report_id in reporting_service.cached_reports:
            del reporting_service.cached_reports[report_id]

        return {"message": f"Report {report_id} deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting report {report_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting report: {str(e)}")


@router.get("/audio/{test_id}/{call_sid}/{filename}", response_class=FileResponse)
async def get_audio_file(test_id: str, call_sid: str, filename: str):
    """
    Get an audio file directly from S3 and serve it to the client.

    Args:
        test_id: Test ID
        call_sid: Call SID
        filename: Audio filename

    Returns:
        Audio file as streaming response
    """
    try:
        # Construct the S3 key
        s3_key = f"tests/{test_id}/calls/{call_sid}/audio/{filename}"

        logger.info(f"Attempting to retrieve audio file: {s3_key}")

        # Get the file from S3
        from app.services.s3_service import s3_service

        try:
            # Check if the file exists
            s3_service.s3_client.head_object(Bucket=s3_service.bucket_name, Key=s3_key)
        except Exception as e:
            logger.error(f"Audio file does not exist: {str(e)}")
            raise HTTPException(status_code=404, detail="Audio file not found")

        # Generate a presigned URL with a short expiration
        presigned_url = s3_service.generate_presigned_url(s3_key, expiration=300)

        if not presigned_url:
            logger.error(f"Failed to generate presigned URL for {s3_key}")
            raise HTTPException(status_code=500, detail="Failed to generate audio URL")

        # Redirect to the presigned URL
        return RedirectResponse(url=presigned_url)

    except Exception as e:
        logger.error(f"Error retrieving audio file: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving audio file: {str(e)}"
        )


@router.get("/metrics/summary", response_model=Dict[str, Any])
async def get_metrics_summary():
    """
    Get a summary of metrics across all reports.

    Returns:
        Summary metrics
    """
    logger.info("Getting metrics summary")

    try:
        # Get all reports
        reports = reporting_service.list_reports(limit=1000)

        if not reports:
            return {"total_reports": 0, "accuracy": 0, "empathy": 0, "response_time": 0}

        # Calculate summary metrics
        total_accuracy = 0
        total_empathy = 0
        total_response_time = 0
        successful_reports = 0

        for report_meta in reports:
            report = reporting_service.get_report(report_meta["report_id"])
            if report and "overall_metrics" in report:
                metrics = report["overall_metrics"]
                if metrics.get("successful", False):
                    total_accuracy += metrics.get("accuracy", 0)
                    total_empathy += metrics.get("empathy", 0)
                    total_response_time += metrics.get("response_time", 0)
                    successful_reports += 1

        # Calculate averages
        avg_accuracy = (
            total_accuracy / successful_reports if successful_reports > 0 else 0
        )
        avg_empathy = (
            total_empathy / successful_reports if successful_reports > 0 else 0
        )
        avg_response_time = (
            total_response_time / successful_reports if successful_reports > 0 else 0
        )

        return {
            "total_reports": len(reports),
            "successful_reports": successful_reports,
            "accuracy": avg_accuracy,
            "empathy": avg_empathy,
            "response_time": avg_response_time,
        }
    except Exception as e:
        logger.error(f"Error getting metrics summary: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error getting metrics summary: {str(e)}"
        )
