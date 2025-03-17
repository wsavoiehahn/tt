# app/routers/tests.py
import logging
import asyncio
from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Depends,
    Query,
    Path,
    Request,
)
from typing import List, Dict, Any, Optional
from uuid import UUID

from ..models.test_cases import TestCase, TestSuite
from ..models.reports import TestCaseReport
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.reporting import reporting_service


router = APIRouter(
    prefix="/api/tests",
    tags=["tests"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)


@router.post("/", response_model=Dict[str, Any])
async def create_test(test_case: TestCase, background_tasks: BackgroundTasks):
    """Create and execute a test case."""
    logger.info(f"Creating test case: {test_case.name}")

    # Save the test case
    s3_service.save_test_case(test_case.dict(), str(test_case.id))

    # Execute the test case in the background
    background_tasks.add_task(evaluator_service.execute_test_case, test_case)

    return {
        "message": "Test case created and scheduled for execution",
        "test_id": str(test_case.id),
    }


@router.get("/debug/{test_id}", response_model=Dict[str, Any])
async def debug_test(test_id: str):
    """
    Debug endpoint to inspect test data.
    """
    try:
        logger.info(f"Debug request for test {test_id}")

        # If not in memory, check DynamoDB
        from ..services.dynamodb_service import dynamodb_service

        test_data = dynamodb_service.get_test(test_id)
        if test_data:
            logger.info(f"Test {test_id} found in DynamoDB")

            # Copy and sanitize data for response
            response_data = {
                "status": test_data.get("status", "unknown"),
                "call_sid": test_data.get("call_sid", "unknown"),
                "start_time": test_data.get("start_time"),
                "end_time": test_data.get("end_time"),
                "conversation_count": len(test_data.get("conversation", [])),
                "conversation_summary": [
                    {
                        "speaker": turn.get("speaker", "unknown"),
                        "text": (
                            turn.get("text", "")[:100] + "..."
                            if turn.get("text")
                            else ""
                        ),
                        "has_audio": "audio_url" in turn,
                        "has_transcript": "transcription_url" in turn,
                        "timestamp": turn.get("timestamp"),
                    }
                    for turn in test_data.get("conversation", [])
                ],
                "report_id": test_data.get("report_id"),
            }

            return response_data

        # Check S3 for test data
        from ..services.s3_service import s3_service

        test_data = s3_service.get_json(f"tests/{test_id}/config.json")
        if test_data:
            logger.info(f"Test {test_id} found in S3")
            return {"source": "s3", "test_data": test_data}

        return {"error": f"Test {test_id} not found"}
    except Exception as e:
        logger.error(f"Error in debug endpoint: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return {"error": str(e)}


@router.post("/suite", response_model=Dict[str, Any])
async def create_test_suite(test_suite: TestSuite, background_tasks: BackgroundTasks):
    """Create and execute a test suite (multiple test cases)."""
    logger.info(
        f"Creating test suite: {test_suite.name} with {len(test_suite.test_cases)} test cases"
    )

    # Save the test suite
    suite_id = str(test_suite.id)
    s3_key = f"test-suites/{suite_id}.json"
    s3_service.s3_client.put_object(
        Bucket=s3_service.bucket_name,
        Key=s3_key,
        Body=s3_service.json.dumps(test_suite.dict(), default=str).encode("utf-8"),
        ContentType="application/json",
    )

    # Execute each test case in the background
    for test_case in test_suite.test_cases:
        background_tasks.add_task(evaluator_service.execute_test_case, test_case)

    return {
        "message": f"Test suite created with {len(test_suite.test_cases)} test cases",
        "test_suite_id": suite_id,
        "test_case_ids": [str(tc.id) for tc in test_suite.test_cases],
    }


@router.get("/{test_id}/status", response_model=Dict[str, Any])
async def get_test_status(test_id: UUID):
    """Get the status of a test case execution."""
    logger.info(f"Getting status for test ID: {test_id}")
    test_id_str = str(test_id)

    # Check if the test is active
    if test_id_str in evaluator_service.active_tests:
        test_data = evaluator_service.active_tests[test_id_str]
        return {
            "test_id": test_id_str,
            "status": test_data["status"],
            "progress": test_data.get("current_question_index", 0),
            "total_questions": len(test_data["test_case"]["config"]["questions"]),
        }

    # Check if the test is completed (has a report)
    from ..services.reporting import reporting_service

    reports = reporting_service.list_reports(limit=100)

    for report_meta in reports:
        report_data = reporting_service.get_report(report_meta["report_id"])
        if report_data and report_data.get("test_case_id") == test_id_str:
            return {
                "test_id": test_id_str,
                "status": "completed",
                "report_id": report_meta["report_id"],
            }

    # Test not found
    raise HTTPException(status_code=404, detail=f"Test case {test_id} not found")


# Add this to app/routers/tests.py


@router.delete("/{test_id}", response_model=Dict[str, Any])
async def delete_test(test_id: UUID):
    """Delete a test case and its associated resources."""
    logger.info(f"Deleting test case with ID: {test_id}")
    test_id_str = str(test_id)

    # Check if test exists (either active or has resources in S3)
    test_exists = False

    # Check if it's an active test
    if test_id_str in evaluator_service.active_tests:
        test_exists = True

        # If test is in progress, we might want to prevent deletion
        status = evaluator_service.active_tests[test_id_str].get("status")
        if status == "in_progress":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete a test that is currently in progress. Wait for it to complete or fail.",
            )

    # Check if test resources exist in S3
    try:
        s3_prefix = f"tests/{test_id_str}/"
        response = s3_service.s3_client.list_objects_v2(
            Bucket=s3_service.bucket_name, Prefix=s3_prefix, MaxKeys=1
        )
        if response.get("KeyCount", 0) > 0:
            test_exists = True
    except Exception as e:
        logger.warning(f"Error checking S3 for test resources: {str(e)}")

    if not test_exists:
        raise HTTPException(status_code=404, detail=f"Test case {test_id} not found")

    # Delete resources from S3
    deleted_objects_count = 0
    try:
        # List all objects with the test ID prefix
        paginator = s3_service.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=s3_service.bucket_name, Prefix=s3_prefix)

        for page in pages:
            if "Contents" not in page:
                continue

            # Delete objects in batches
            objects_to_delete = [{"Key": obj["Key"]} for obj in page["Contents"]]
            if objects_to_delete:
                s3_service.s3_client.delete_objects(
                    Bucket=s3_service.bucket_name, Delete={"Objects": objects_to_delete}
                )
                deleted_objects_count += len(objects_to_delete)
    except Exception as e:
        logger.error(f"Error deleting test resources from S3: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting test resources: {str(e)}"
        )

    # Remove from active tests if present
    if test_id_str in evaluator_service.active_tests:
        del evaluator_service.active_tests[test_id_str]

    # Find and delete any associated reports
    deleted_reports = []
    try:
        # List all reports in any date subfolder
        report_prefix = "reports/"
        paginator = s3_service.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=s3_service.bucket_name, Prefix=report_prefix)

        for page in pages:
            if "Contents" not in page:
                continue

            # Check each report file to see if it's associated with this test
            for obj in page["Contents"]:
                if obj["Key"].endswith(".json"):
                    try:
                        # Get the report data
                        report_data = s3_service.get_json(obj["Key"])

                        # Check if it's for our test
                        if (
                            report_data
                            and report_data.get("test_case_id") == test_id_str
                        ):
                            # Delete the report
                            s3_service.s3_client.delete_object(
                                Bucket=s3_service.bucket_name, Key=obj["Key"]
                            )

                            # Extract report ID from filename
                            report_id = obj["Key"].split("/")[-1].replace(".json", "")
                            deleted_reports.append(report_id)

                            # Remove from cache if exists
                            if report_id in reporting_service.cached_reports:
                                del reporting_service.cached_reports[report_id]

                            logger.info(f"Deleted associated report: {obj['Key']}")
                    except Exception as e:
                        logger.warning(
                            f"Error processing report {obj['Key']}: {str(e)}"
                        )
    except Exception as e:
        logger.error(f"Error cleaning up associated reports: {str(e)}")

    return {
        "message": f"Test case {test_id} deleted successfully",
        "test_id": test_id_str,
        "deleted_objects_count": deleted_objects_count,
        "deleted_reports": deleted_reports,
    }


@router.get("/", response_model=List[Dict[str, Any]])
async def list_tests(limit: int = Query(50, ge=1, le=500)):
    """List all test cases."""
    logger.info(f"Listing test cases (limit: {limit})")

    # List test case files from S3
    try:
        response = s3_service.s3_client.list_objects_v2(
            Bucket=s3_service.bucket_name, Prefix="tests/", MaxKeys=limit
        )

        tests = []
        for obj in response.get("Contents", []):
            if obj["Key"].endswith("config.json"):
                # Extract test ID from key
                key_parts = obj["Key"].split("/")
                if len(key_parts) >= 2:
                    test_id = key_parts[1]

                    # Get test case data
                    test_data = s3_service.get_json(obj["Key"])
                    if test_data:
                        tests.append(
                            {
                                "test_id": test_id,
                                "name": test_data.get("name", "Unknown"),
                                "description": test_data.get("description", ""),
                                "persona": test_data.get("config", {}).get(
                                    "persona_name", "Unknown"
                                ),
                                "behavior": test_data.get("config", {}).get(
                                    "behavior_name", "Unknown"
                                ),
                                "created_at": test_data.get("created_at", ""),
                                "status": (
                                    "completed"
                                    if test_id not in evaluator_service.active_tests
                                    else "in_progress"
                                ),
                            }
                        )

        # Add active tests that might not be in S3 yet
        for test_id, test_data in evaluator_service.active_tests.items():
            # Check if this test is already in our list
            if not any(t["test_id"] == test_id for t in tests):
                test_case = test_data.get("test_case", {})
                tests.append(
                    {
                        "test_id": test_id,
                        "name": test_case.get("name", "Unknown"),
                        "description": test_case.get("description", ""),
                        "persona": test_case.get("config", {}).get(
                            "persona_name", "Unknown"
                        ),
                        "behavior": test_case.get("config", {}).get(
                            "behavior_name", "Unknown"
                        ),
                        "created_at": test_case.get("created_at", ""),
                        "status": test_data.get("status", "in_progress"),
                    }
                )

        return tests

    except Exception as e:
        logger.error(f"Error listing tests: {e}")
        return []
