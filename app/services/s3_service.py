# app/services/s3_service.py
import json
import logging
import boto3
from botocore.exceptions import ClientError
from io import BytesIO, StringIO
from typing import Dict, Any, List, Optional, Union, BinaryIO
import uuid
from datetime import datetime

from ..config import config

logger = logging.getLogger(__name__)


class S3Service:
    """Service for interacting with AWS S3 for storage."""

    def __init__(self):
        self.region_name = config.region_name
        self.bucket_name = config.get_parameter("/ai-evaluator/s3_bucket_name")
        self.s3_client = boto3.client("s3", region_name=self.region_name)

    def save_audio(
        self,
        audio_data: Union[bytes, BinaryIO],
        test_id: str,
        call_sid: str,
        turn_number: int,
        speaker: str,
    ) -> str:
        """
        Save audio data to S3.

        Args:
            audio_data: Audio data as bytes or file-like object
            test_id: Test case ID
            call_sid: Call SID
            turn_number: Conversation turn number
            speaker: Speaker identifier (evaluator or agent)

        Returns:
            S3 URL for the saved audio
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"tests/{test_id}/calls/{call_sid}/audio/{turn_number}_{speaker}_{timestamp}.wav"

        try:
            if isinstance(audio_data, bytes):
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=audio_data,
                    ContentType="audio/wav",
                )
            else:
                self.s3_client.upload_fileobj(
                    audio_data,
                    self.bucket_name,
                    key,
                    ExtraArgs={"ContentType": "audio/wav"},
                )

            return f"s3://{self.bucket_name}/{key}"
        except ClientError as e:
            logger.error(f"Error saving audio to S3: {str(e)}")
            return ""

    def save_recording(self, recording_url: str, test_id: str, call_sid: str) -> str:
        """
        Save a Twilio recording to S3.

        Args:
            recording_url: URL of the recording to download
            test_id: Test case ID
            call_sid: Call SID

        Returns:
            S3 URL for the saved recording
        """
        import requests

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"tests/{test_id}/calls/{call_sid}/recordings/{timestamp}.mp3"

        try:
            # Download the recording
            auth = (
                config.get_parameter("/ai-evaluator/twilio_account_sid"),
                config.get_parameter("/ai-evaluator/twilio_auth_token"),
            )
            response = requests.get(recording_url, auth=auth)

            if response.status_code == 200:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=response.content,
                    ContentType="audio/mp3",
                )

                return f"s3://{self.bucket_name}/{key}"
            else:
                logger.error(f"Failed to download recording: {response.status_code}")
                return ""
        except (ClientError, requests.RequestException) as e:
            logger.error(f"Error saving recording to S3: {str(e)}")
            return ""

    def save_transcription(
        self,
        transcription: str,
        test_id: str,
        call_sid: str,
        turn_number: int,
        speaker: str,
    ) -> str:
        """
        Save a transcription to S3.

        Args:
            transcription: Text transcription
            test_id: Test case ID
            call_sid: Call SID
            turn_number: Conversation turn number
            speaker: Speaker identifier (evaluator or agent)

        Returns:
            S3 URL for the saved transcription
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"tests/{test_id}/calls/{call_sid}/transcripts/{turn_number}_{speaker}_{timestamp}.txt"

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=transcription.encode("utf-8"),
                ContentType="text/plain",
            )

            return f"s3://{self.bucket_name}/{key}"
        except ClientError as e:
            logger.error(f"Error saving transcription to S3: {str(e)}")
            return ""

    def save_report(self, report_data: Dict[str, Any], report_id: str) -> str:
        """
        Save a test report to S3 with consistent path structure.

        Args:
            report_data: Report data as dictionary
            report_id: Report ID

        Returns:
            S3 URL for the saved report
        """
        # Always use current date for folder structure
        timestamp = datetime.now().strftime("%Y%m%d")
        key = f"reports/{timestamp}/{report_id}.json"

        try:
            # Save to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=json.dumps(report_data, default=str).encode("utf-8"),
                ContentType="application/json",
            )

            # Log the exact path for debugging
            logger.info(f"Saved report {report_id} to S3 path: {key}")

            return f"s3://{self.bucket_name}/{key}"
        except ClientError as e:
            logger.error(f"Error saving report to S3: {str(e)}")
            return ""

    def save_test_case(self, test_case_data: Dict[str, Any], test_id: str) -> str:
        """
        Save a test case configuration to S3.

        Args:
            test_case_data: Test case data as dictionary
            test_id: Test case ID

        Returns:
            S3 URL for the saved test case
        """
        key = f"tests/{test_id}/config.json"

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=json.dumps(test_case_data, default=str).encode("utf-8"),
                ContentType="application/json",
            )

            return f"s3://{self.bucket_name}/{key}"
        except ClientError as e:
            logger.error(f"Error saving test case to S3: {str(e)}")
            return ""

    def get_object(self, key: str) -> bytes:
        """
        Get an object from S3.

        Args:
            key: S3 object key or full S3 URL

        Returns:
            Object contents as bytes
        """
        # Handle full S3 URLs
        if key.startswith("s3://"):
            parts = key.replace("s3://", "").split("/", 1)
            bucket = parts[0]
            key = parts[1]
        else:
            bucket = self.bucket_name

        try:
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError as e:
            logger.error(
                f"Error getting object from S3: {str(e)} - Bucket: {bucket}, Key: {key}"
            )
            return b""

    def get_json(self, key: str) -> Dict[str, Any]:
        """
        Get a JSON object from S3.

        Args:
            key: S3 object key or full S3 URL

        Returns:
            Parsed JSON as dictionary
        """
        content = self.get_object(key)
        if content:
            try:
                return json.loads(content.decode("utf-8"))
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing JSON from S3: {str(e)}")
                return {}
        return {}

    def list_reports(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        List available reports with improved folder structure support.

        Args:
            limit: Maximum number of reports to list

        Returns:
            List of report metadata
        """
        try:
            # Use a more general prefix to include date folders
            reports = []

            # First list all date folders
            date_folders = set()
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix="reports/", Delimiter="/"
            )

            # Add the base reports folder
            date_folders.add("reports/")

            # Add all date subfolders
            for prefix in response.get("CommonPrefixes", []):
                date_folders.add(prefix.get("Prefix"))

            # For each date folder, list report files
            for folder in date_folders:
                try:
                    folder_response = self.s3_client.list_objects_v2(
                        Bucket=self.bucket_name,
                        Prefix=folder,
                        MaxKeys=limit - len(reports),  # Respect the overall limit
                    )

                    for obj in folder_response.get("Contents", []):
                        if obj["Key"].endswith(".json"):
                            try:
                                # Extract report ID from filename
                                filename = obj["Key"].split("/")[-1]
                                report_id = filename.replace(".json", "")

                                # Verify the object can be retrieved
                                report_data = self.get_json(obj["Key"])

                                # Only add if we can successfully retrieve the report
                                if report_data:
                                    reports.append(
                                        {
                                            "report_id": report_id,
                                            "date": obj["LastModified"],
                                            "size": obj["Size"],
                                            "s3_key": obj["Key"],
                                            "s3_url": f"s3://{self.bucket_name}/{obj['Key']}",
                                            "data": report_data,  # Include the full report data
                                        }
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"Skipping report due to error: {obj['Key']} - {str(e)}"
                                )

                    # Stop if we've reached the limit
                    if len(reports) >= limit:
                        break
                except Exception as folder_error:
                    logger.warning(
                        f"Error listing reports in folder {folder}: {str(folder_error)}"
                    )
                    continue

            # Sort by date (most recent first)
            reports.sort(key=lambda x: x.get("date"), reverse=True)

            return reports[:limit]
        except ClientError as e:
            logger.error(f"Error listing reports: {str(e)}")
            return []

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> str:
        """
        Generate a presigned URL for an S3 object.

        Args:
            key: S3 object key or full S3 URL
            expiration: URL expiration time in seconds

        Returns:
            Presigned URL
        """
        # Handle full S3 URLs
        if key.startswith("s3://"):
            parts = key.replace("s3://", "").split("/", 1)
            bucket = parts[0]
            key = parts[1]
        else:
            bucket = self.bucket_name

        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {str(e)}")
            return ""


# Create a singleton instance
s3_service = S3Service()
