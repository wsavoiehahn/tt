# app/services/twilio_service.py
import os
import logging
import requests
from typing import Dict, Any, Optional, List
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from app.config import app_config

import time

logger = logging.getLogger(__name__)


class TwilioService:
    """Service for interacting with Twilio's API for call handling."""

    def __init__(self):
        self.account_sid = app_config.TWILIO_ACCOUNT_SID
        self.auth_token = app_config.TWILIO_AUTH_TOKEN
        self.client = Client(self.account_sid, self.auth_token)
        self.ai_service_number = app_config.TARGET_PHONE_NUMBER
        self.callback_url = f"https://{app_config.URL}"
        # Track active calls
        self.active_calls = {}

        # Log initialization
        logger.info(
            f"TwilioService initialized with account_sid: {self.account_sid[:5]}***, target number: {self.ai_service_number}, callback URL: {self.callback_url}"
        )

    def initiate_call(self, test_id: str) -> Dict[str, Any]:
        """
        Initiate a call to the AI service agent with the test questions.
        """
        try:
            if not test_id:
                logger.error("Attempted to initiate call with missing test_id")
                return {"error": "Missing test_id", "status": "failed"}

            logger.info(f"Initiating call for test_id: {test_id}")

            # Get outbound number
            from_number = self.get_outbound_number()
            logger.info(f"Using outbound number: {from_number}")
            logger.debug(f"Using callback URL: {self.callback_url}")

            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service
            from ..services.dynamodb_service import dynamodb_service

            test_data = evaluator_service.active_tests.get(test_id, {})
            test_case = test_data.get("test_case", {})
            config = test_case.get("config", {})
            target_phone_number = (
                config.get("target_phone_number") or self.ai_service_number
            )

            # Create a simple TwiML for call initiation
            response = VoiceResponse()
            response.say("Speak Now.")

            # Log TwiML content
            logger.debug(f"Generated TwiML: {str(response)}")

            # Set up call parameters - ensure test_id is passed in multiple places
            status_callback_url = (
                f"{self.callback_url}/webhooks/call-status?test_id={test_id}"
            )
            websocket_url = f"wss://{app_config.URL}"
            logger.info(f"Status callback URL: {status_callback_url}")

            # Critical part: Ensure test_id is properly passed to the WebSocket
            connect = Connect()
            stream = Stream(url=f"{websocket_url}/media-stream")

            # Explicitly pass test_id as a parameter
            stream.parameter(name="test_id", value=test_id)
            logger.info(f"Added test_id parameter to Twilio stream: {test_id}")

            connect.append(stream)
            response.append(connect)

            # Create the call with all parameters
            call = self.client.calls.create(
                to=target_phone_number,
                from_=from_number,
                twiml=str(response),
                status_callback=status_callback_url,
                status_callback_event=[
                    "initiated",
                    "ringing",
                    "answered",
                    "completed",
                ],
                status_callback_method="POST",
                method="POST",
                machine_detection="Enable",
                machine_detection_timeout=30,
                machine_detection_speech_threshold=2500,
                machine_detection_speech_end_threshold=1000,
                machine_detection_silence_timeout=5000,
                record=True,
            )

            logger.info(
                f"Call created successfully with SID: {call.sid} for test_id: {test_id}"
            )

            # Store call information with additional logging
            self.active_calls[call.sid] = {
                "test_id": test_id,
                "status": "initiated",
                "start_time": time.time(),
                "to": self.ai_service_number,
                "from": from_number,
            }

            # Update test data with call information
            try:
                if test_id not in evaluator_service.active_tests:
                    logger.warning(f"Test {test_id} not in active_tests, initializing")
                    evaluator_service.active_tests[test_id] = {
                        "status": "waiting_for_call",
                        "start_time": time.time(),
                        "test_case": {},  # This will be populated later
                        "execution_details": [],
                    }

                evaluator_service.active_tests[test_id]["call_sid"] = call.sid
                evaluator_service.active_tests[test_id]["call_status"] = call.status

                # Update in DynamoDB
                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )
                logger.info(
                    f"Updated test {test_id} in DynamoDB with call_sid: {call.sid}"
                )
            except Exception as update_error:
                logger.error(f"Error updating test with call SID: {str(update_error)}")

            return {
                "call_sid": call.sid,
                "status": call.status,
                "test_id": test_id,
                "to": self.ai_service_number,
            }

        except Exception as e:
            logger.error(f"ERROR in initiate_call: {str(e)}")
            # Log detailed exception information including traceback
            import traceback

            logger.error(f"ERROR TRACEBACK: {traceback.format_exc()}")
            return {"error": str(e), "test_id": test_id, "status": "failed"}

    def get_outbound_number(self) -> str:
        """Get an available Twilio number for outbound calling."""
        # Get first available phone number from account
        incoming_phone_numbers = self.client.incoming_phone_numbers.list(limit=1)
        if incoming_phone_numbers:
            return incoming_phone_numbers[0].phone_number
        else:
            raise ValueError("No phone numbers available")

    def get_call_status(self, call_sid: str) -> Dict[str, Any]:
        """Get status of a call by SID."""
        try:
            call = self.client.calls(call_sid).fetch()

            # Update local tracking
            if call_sid in self.active_calls:
                self.active_calls[call_sid]["status"] = call.status

            return {
                "call_sid": call_sid,
                "status": call.status,
                "duration": call.duration,
                "direction": call.direction,
                "answered_by": call.answered_by,
            }
        except Exception as e:
            logger.error(f"Error getting call status: {str(e)}")
            return {"call_sid": call_sid, "error": str(e), "status": "unknown"}

    def end_call(self, call_sid: str) -> Dict[str, Any]:
        """End an active call."""
        try:
            call = self.client.calls(call_sid).update(status="completed")

            # Update local tracking
            if call_sid in self.active_calls:
                self.active_calls[call_sid]["status"] = "completed"
                self.active_calls[call_sid]["end_time"] = time.time()

            return {"call_sid": call_sid, "status": call.status}
        except Exception as e:
            logger.error(f"Error ending call: {str(e)}")
            return {"call_sid": call_sid, "error": str(e), "status": "error"}


# Create a singleton instance
twilio_service = TwilioService()
