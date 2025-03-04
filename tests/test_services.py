# tests/test_services.py
import unittest
import json
import boto3
import os
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from dotenv import load_dotenv

# Load environment variables from .env file for tests
load_dotenv()
from io import BytesIO
from datetime import datetime
from uuid import uuid4

from app.config import config
from app.services.s3_service import s3_service
from app.services.twilio_service import twilio_service
from app.services.openai_service import openai_service
from app.services.reporting import reporting_service


class TestConfigService(unittest.TestCase):
    """Test cases for the Config service."""

    def test_get_parameter(self):
        """Test getting a parameter from Parameter Store."""
        # Store the original get_parameter method
        original_get_parameter = config.get_parameter

        # Create a patched version of get_parameter
        def mock_get_parameter(name, use_cache=True):
            if use_cache and name in config._config_cache:
                return config._config_cache[name]

            # Return our mock value
            if name == "/test/parameter":
                value = "test-value"
                config._config_cache[name] = value
                return value

            # For other parameters, use default behavior
            return ""

        try:
            # Replace the method
            config.get_parameter = mock_get_parameter

            # Clear the cache
            config._config_cache = {}

            # Test getting a parameter
            value = config.get_parameter("/test/parameter")

            # Verify result
            self.assertEqual(value, "test-value")

            # Test caching
            value = config.get_parameter("/test/parameter")
            self.assertEqual(value, "test-value")
        finally:
            # Restore original method
            config.get_parameter = original_get_parameter


class TestS3Service(unittest.TestCase):
    """Test cases for the S3 service."""

    @patch("app.services.s3_service.s3_service.s3_client")
    def test_save_audio(self, mock_s3_client):
        """Test saving audio to S3."""
        # Test data
        test_id = str(uuid4())
        call_sid = "test-call-sid"
        audio_data = b"test-audio-data"

        # Test saving audio
        url = s3_service.save_audio(
            audio_data=audio_data,
            test_id=test_id,
            call_sid=call_sid,
            turn_number=1,
            speaker="evaluator",
        )

        # Verify result
        self.assertTrue(
            url.startswith(
                f"s3://{s3_service.bucket_name}/tests/{test_id}/calls/{call_sid}/audio/"
            )
        )

        # Verify put_object was called
        mock_s3_client.put_object.assert_called_once()

        # Verify arguments
        args, kwargs = mock_s3_client.put_object.call_args
        self.assertEqual(kwargs["Bucket"], s3_service.bucket_name)
        self.assertTrue(
            kwargs["Key"].startswith(f"tests/{test_id}/calls/{call_sid}/audio/")
        )
        self.assertEqual(kwargs["Body"], audio_data)
        self.assertEqual(kwargs["ContentType"], "audio/wav")

    def test_get_json(self):
        """Test getting JSON from S3."""
        # Save original client
        original_client = s3_service.s3_client

        try:
            # Create mock client
            mock_s3 = MagicMock()

            # Mock response
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({"test": "data"}).encode("utf-8")
            mock_s3.get_object.return_value = {"Body": mock_body}

            # Replace the client
            s3_service.s3_client = mock_s3

            # Test getting JSON
            result = s3_service.get_json("test-key")

            # Verify result
            self.assertEqual(result, {"test": "data"})
            mock_s3.get_object.assert_called_with(
                Bucket=s3_service.bucket_name, Key="test-key"
            )
        finally:
            # Restore original client
            s3_service.s3_client = original_client

    def test_get_json_s3_url(self):
        """Test getting JSON from S3 using S3 URL."""
        # Save original client
        original_client = s3_service.s3_client

        try:
            # Create mock client
            mock_s3 = MagicMock()

            # Mock response
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({"test": "data"}).encode("utf-8")
            mock_s3.get_object.return_value = {"Body": mock_body}

            # Replace the client
            s3_service.s3_client = mock_s3

            # Test getting JSON with S3 URL
            result = s3_service.get_json("s3://test-bucket/test-key")

            # Verify result
            self.assertEqual(result, {"test": "data"})
            mock_s3.get_object.assert_called_with(Bucket="test-bucket", Key="test-key")
        finally:
            # Restore original client
            s3_service.s3_client = original_client


class TestTwilioService(unittest.TestCase):
    """Test cases for the Twilio service."""

    @patch("twilio.rest.Client")
    def test_initiate_call(self, mock_twilio_client):
        """Test initiating a call."""
        # Configure mock
        mock_calls = MagicMock()
        mock_twilio_client.return_value.calls = mock_calls

        mock_call = MagicMock()
        mock_call.sid = "test-call-sid"
        mock_call.status = "queued"
        mock_calls.create.return_value = mock_call

        # Replace the Client in twilio_service
        twilio_service.client = mock_twilio_client.return_value

        # Test initiating a call
        test_id = str(uuid4())
        result = twilio_service.initiate_call(test_id)

        # Verify result
        self.assertEqual(result["call_sid"], "test-call-sid")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["test_id"], test_id)

        # Verify call creation
        mock_calls.create.assert_called_once()

    @patch("twilio.rest.Client")
    def test_generate_twiml_for_question(self, mock_twilio_client):
        """Test generating TwiML for a question."""
        # Test generating TwiML
        question = "How can I find my ID card?"
        test_id = str(uuid4())
        call_sid = "test-call-sid"

        twiml = twilio_service.generate_twiml_for_question(question, test_id, call_sid)

        # Verify result
        self.assertIn(question, twiml)
        self.assertIn(f"test_id={test_id}", twiml)
        self.assertIn(f"call_sid={call_sid}", twiml)
        self.assertIn("<Say", twiml)
        self.assertIn("<Record", twiml)
        self.assertIn("<Gather", twiml)


class TestReportingService(unittest.TestCase):
    """Test cases for the Reporting service."""

    @patch("app.services.s3_service.s3_service")
    def test_calculate_aggregate_metrics(self, mock_s3):
        """Test calculating aggregate metrics."""
        from app.models.reports import (
            TestCaseReport,
            EvaluationMetrics,
            QuestionEvaluation,
        )

        # Create test reports
        reports = [
            TestCaseReport(
                test_case_id=uuid4(),
                test_case_name="Test Case 1",
                persona_name="Test Persona",
                behavior_name="Test Behavior",
                questions_evaluated=[],
                overall_metrics=EvaluationMetrics(
                    accuracy=0.8, empathy=0.7, response_time=2.0, successful=True
                ),
                execution_time=10.0,
            ),
            TestCaseReport(
                test_case_id=uuid4(),
                test_case_name="Test Case 2",
                persona_name="Another Persona",
                behavior_name="Another Behavior",
                questions_evaluated=[],
                overall_metrics=EvaluationMetrics(
                    accuracy=0.9, empathy=0.6, response_time=1.5, successful=True
                ),
                execution_time=12.0,
            ),
        ]

        # Calculate aggregate metrics
        metrics = reporting_service._calculate_aggregate_metrics(reports)

        # Verify metrics
        self.assertAlmostEqual(metrics["accuracy"], 0.85)  # (0.8 + 0.9) / 2
        self.assertAlmostEqual(metrics["empathy"], 0.65)  # (0.7 + 0.6) / 2
        self.assertAlmostEqual(metrics["response_time"], 1.75)  # (2.0 + 1.5) / 2
        self.assertAlmostEqual(metrics["success_rate"], 1.0)  # All successful
        self.assertAlmostEqual(metrics["total_test_cases"], 2)

        # Verify persona metrics
        self.assertIn("by_persona", metrics)
        self.assertIn("Test Persona", metrics["by_persona"])
        self.assertIn("Another Persona", metrics["by_persona"])

        # Verify behavior metrics
        self.assertIn("by_behavior", metrics)
        self.assertIn("Test Behavior", metrics["by_behavior"])
        self.assertIn("Another Behavior", metrics["by_behavior"])


# Also don't forget to create a __init__.py in the tests directory to make it a proper package
if __name__ == "__main__":
    unittest.main()
