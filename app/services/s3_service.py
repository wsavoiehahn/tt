# app/services/s3_service.py
import json
import logging
import boto3
from botocore.exceptions import ClientError
from typing import Dict, Any, List, Union, BinaryIO
from datetime import datetime
import os
import io
import wave
import audioop


logger = logging.getLogger(__name__)


class S3Service:
    """Service for interacting with AWS S3 for storage."""

    def __init__(self):
        from app.config import app_config

        self.region_name = app_config.AWS_DEFAULT_REGION
        self.bucket_name = app_config.FULL_S3_BUCKET_NAME
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
        Enhanced function to save audio data to S3 with improved error handling.

        Args:
            audio_data: Audio data as bytes or file-like object
            test_id: Test case ID
            call_sid: Call SID
            turn_number: Conversation turn number
            speaker: Speaker identifier (evaluator or agent)

        Returns:
            S3 URL for the saved audio
        """
        if not test_id:
            logger.error("Missing test_id when saving transcription")
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{turn_number}_{speaker}_{timestamp}.wav"
        key = f"tests/{test_id}/calls/{call_sid}/audio/{filename}"
        try:
            logger.debug(f"Saving audio to S3: bucket={self.bucket_name}, key={key}")

            pcm_audio = audioop.ulaw2lin(audio_data, 2)  # 2 bytes = 16 bits PCM
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file_format = "g711_ulaw"
                wav_file_sample_rate = 8000  # Sampling rate: 8000 Hz
                wav_sample_width = 2  # For G711 u-law, 16 bits PCM is standard

                wav_file.setnchannels(1)  # Mono channel
                wav_file.setsampwidth(wav_sample_width)
                wav_file.setframerate(wav_file_sample_rate)
                # Write audio frames
                wav_file.writeframes(pcm_audio)

            wav_buffer = wav_buffer.getvalue()

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=wav_buffer,
                ContentType="audio/wav",
            )
            # Return S3 URL
            s3_url = f"s3://{self.bucket_name}/{key}"
            logger.info(f"Audio saved to: {s3_url}")
            return s3_url
        except Exception as e:
            logger.error(f"Error saving audio to S3: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
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
        Save a transcription to S3 with improved error handling.

        Args:
            transcription: Text transcription
            test_id: Test case ID
            call_sid: Call SID
            turn_number: Conversation turn number
            speaker: Speaker identifier (evaluator or agent)

        Returns:
            S3 URL for the saved transcription
        """

        if not test_id:
            logger.error("Missing test_id when saving transcription")
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"tests/{test_id}/calls/{call_sid}/transcripts/{turn_number}_{speaker}_{timestamp}.txt"

        try:
            # Log what we're trying to do
            logger.debug(
                f"Saving transcription to S3: bucket={self.bucket_name}, key={key}"
            )

            # Make sure transcription is not empty
            if not transcription or len(transcription.strip()) == 0:
                logger.warning("Empty transcription provided, not saving to S3")
                return ""

            # Ensure the transcription is properly encoded
            transcription_bytes = transcription.encode("utf-8")

            # Put the object with explicit error handling
            try:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=transcription_bytes,
                    ContentType="text/plain",
                )
                logger.debug(
                    f"Successfully saved {len(transcription_bytes)} bytes of transcription data"
                )
            except Exception as upload_error:
                logger.error(f"S3 upload error: {str(upload_error)}")
                import traceback

                logger.error(f"S3 upload traceback: {traceback.format_exc()}")
                return ""

            # Return S3 URL
            s3_url = f"s3://{self.bucket_name}/{key}"
            logger.debug(f"Transcription saved to: {s3_url}")
            return s3_url
        except Exception as e:
            logger.error(f"Error saving transcription to S3: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
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

            logger.debug(f"Saved report {report_id} to S3 path: {key}")

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
            logger.debug(f"Test case saved to S3 for test {test_id}")
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
        Generate a presigned URL for an S3 object with improved error handling.

        Args:
            key: S3 object key or full S3 URL
            expiration: URL expiration time in seconds

        Returns:
            Presigned URL
        """
        try:
            # Handle full S3 URLs
            if key.startswith("s3://"):
                parts = key.replace("s3://", "").split("/", 1)
                bucket = parts[0]
                key = parts[1]
            else:
                bucket = self.bucket_name

            logger.debug(f"Generating presigned URL for: bucket={bucket}, key={key}")

            # First check if the object exists
            try:
                self.s3_client.head_object(Bucket=bucket, Key=key)
            except Exception as e:
                logger.error(f"S3 object does not exist: {str(e)}")
                logger.error(f"  Bucket: {bucket}")
                logger.error(f"  Key: {key}")
                return ""

            # Generate the URL
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                    "ResponseContentType": "audio/wav",
                    "ResponseContentDisposition": "inline",
                },
                ExpiresIn=expiration,
            )

            logger.debug(f"Generated presigned URL: {url[:100]}...")
            return url
        except Exception as e:
            logger.error(f"Error generating presigned URL: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
            return ""


# Create a singleton instance
s3_service = S3Service()
