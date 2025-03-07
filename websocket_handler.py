# websocket_handler.py with enhanced debugging
import json
import logging
import base64
import asyncio
import boto3
import os
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Global variables for services
openai_service = None
evaluator_service = None
realtime_service = None
dynamodb_service = None
s3_service = None

# Global dictionary to store active WebSocket connections
active_connections = {}


def import_services():
    global openai_service, evaluator_service, realtime_service, dynamodb_service, s3_service
    try:
        # Add proper Python path
        import sys

        sys.path.append("/var/task")

        from app.services.openai_service import openai_service
        from app.services.evaluator import evaluator_service
        from app.services.realtime_service import realtime_service
        from app.services.dynamodb_service import dynamodb_service
        from app.services.s3_service import s3_service

        logger.error("Services imported successfully")
    except ImportError as e:
        logger.error(f"CRITICAL: Failed to import services: {str(e)}")
        logger.error(traceback.format_exc())
        raise


def lambda_handler(event, context):
    """
    Enhanced WebSocket handler with comprehensive error logging.
    """
    logger.error(f"WEBSOCKET EVENT: {json.dumps(event)}")

    try:
        # Import services with detailed logging
        import_services()
    except Exception as e:
        logger.error(f"CRITICAL: Service import failed: {str(e)}")
        logger.error(traceback.format_exc())
        return {"statusCode": 500, "body": "Service import catastrophically failed"}

    logger.error(f"Received WebSocket event: {json.dumps(event)}")

    # Extract connection details
    connection_id = event.get("requestContext", {}).get("connectionId")
    route_key = event.get("requestContext", {}).get("routeKey")
    domain_name = event.get("requestContext", {}).get("domainName")
    stage = event.get("requestContext", {}).get("stage")
    integration_id = "yul0k45"  # Add this from your route info
    logger.error(
        f"Connection ID: {connection_id}, Route: {route_key}, Domain: {domain_name}, Stage: {stage}"
    )

    # Create API Gateway Management client
    endpoint_url = f"https://{domain_name}/{stage}"
    logger.error(f"Using endpoint URL: {endpoint_url}")

    try:
        api_gateway_management = boto3.client(
            "apigatewaymanagementapi", endpoint_url=endpoint_url
        )
        logger.error("API Gateway Management client created successfully")
    except Exception as e:
        logger.error(f"Failed to create API Gateway client: {str(e)}")
        logger.error(traceback.format_exc())

    # Handle different routes with enhanced error handling
    try:
        if route_key == "$connect":
            return handle_connect(event, connection_id)
        elif route_key == "$disconnect":
            return handle_disconnect(event, connection_id)
        elif route_key == "$default":
            return handle_default_message(event, connection_id, api_gateway_management)
        else:
            logger.warning(f"Unknown route: {route_key}")
            return {"statusCode": 400, "body": "Unknown route"}
    except Exception as e:
        logger.error(f"UNHANDLED WebSocket event error: {str(e)}")
        logger.error(traceback.format_exc())
        return {"statusCode": 500, "body": f"Unhandled error: {str(e)}"}


def handle_connect(event, connection_id):
    """
    Enhanced connection handling with comprehensive logging.
    """
    logger.error(f"Handling WebSocket connect for connection {connection_id}")

    try:
        # Extract query parameters
        query_params = event.get("queryStringParameters", {}) or {}
        test_id = query_params.get("test_id")
        call_sid = query_params.get("call_sid")

        logger.error(f"Connect params - test_id: {test_id}, call_sid: {call_sid}")

        # Validate connection parameters
        if not test_id or not call_sid:
            logger.error("Missing test_id or call_sid in connection")
            return {
                "statusCode": 400,
                "body": "Invalid connection parameters: test_id and call_sid are required",
            }

        # Store connection information with enhanced tracking
        connection_data = {
            "connection_id": connection_id,
            "test_id": test_id,
            "call_sid": call_sid,
            "connected_at": datetime.now().isoformat(),
            "status": "connected",
            "audio_buffer": [],
            "connection_attempts": 1,
            "last_activity": datetime.now().isoformat(),
        }

        active_connections[connection_id] = connection_data

        # Load test data with detailed error handling
        try:
            # Attempt to load from active tests first
            test_data = None
            if (
                hasattr(evaluator_service, "active_tests")
                and test_id in evaluator_service.active_tests
            ):
                test_data = evaluator_service.active_tests[test_id]
                logger.error(f"Loaded test data from active tests for {test_id}")

            # Fallback to DynamoDB
            if not test_data:
                test_data = dynamodb_service.get_test(test_id)
                logger.error(f"Loaded test data from DynamoDB for {test_id}")

                # Update active tests if found
                if test_data and hasattr(evaluator_service, "active_tests"):
                    evaluator_service.active_tests[test_id] = test_data

            # Update connection with test data
            if test_data:
                connection_data["test_data"] = test_data
                logger.error(f"Successfully loaded test data for {test_id}")
            else:
                logger.warning(f"No test data found for test_id: {test_id}")

        except Exception as e:
            logger.error(f"Error loading test data: {str(e)}")
            logger.error(traceback.format_exc())

        return {"statusCode": 200, "body": "Connected successfully"}

    except Exception as e:
        logger.error(f"Comprehensive error in connection handling: {str(e)}")
        logger.error(traceback.format_exc())
        return {"statusCode": 500, "body": f"Connection error: {str(e)}"}


def handle_disconnect(event, connection_id):
    """
    Handle WebSocket disconnection.
    Clean up connection resources.
    """
    try:
        logger.error(f"WebSocket disconnected: {connection_id}")

        # Remove from active connections
        connection_data = active_connections.pop(connection_id, None)

        if connection_data:
            test_id = connection_data.get("test_id")
            call_sid = connection_data.get("call_sid")

            # Clean up any ongoing test or call
            if test_id:
                try:
                    # Mark test as completed or failed
                    if test_id in evaluator_service.active_tests:
                        evaluator_service.active_tests[test_id][
                            "status"
                        ] = "disconnected"

                    # Update in DynamoDB
                    dynamodb_service.update_test_status(test_id, "disconnected")
                except Exception as e:
                    logger.error(f"Error updating test status: {str(e)}")

            # End the call if still active
            if call_sid:
                try:
                    from app.services.twilio_service import twilio_service

                    twilio_service.end_call(call_sid)
                except Exception as e:
                    logger.error(f"Error ending call: {str(e)}")

        return {"statusCode": 200, "body": "Disconnected"}

    except Exception as e:
        logger.error(f"Error in handle_disconnect: {str(e)}")
        return {"statusCode": 500, "body": f"Disconnection error: {str(e)}"}


def handle_default_message(event, connection_id, api_gateway_management):
    """
    Handle incoming messages on the WebSocket.
    Process audio streams and media events.
    """
    try:
        # Get connection data
        connection_data = active_connections.get(connection_id)
        if not connection_data:
            logger.error(f"No connection data found for {connection_id}")
            return {"statusCode": 400, "body": "Connection not found"}

        # Get message body
        body = event.get("body")
        if not body:
            return {"statusCode": 400, "body": "Empty message"}

        try:
            # Try parsing as JSON first
            message_data = json.loads(body)
            event_type = message_data.get("event")

            logger.error(f"Processing message event: {event_type}")

            # Process based on event type
            if event_type == "media":
                # Handle media payload (audio)
                handle_media_event(message_data, connection_data)
            elif event_type == "start":
                # Stream start event
                handle_stream_start(message_data, connection_data)
            elif event_type == "stop":
                # Stream stop event
                handle_stream_stop(message_data, connection_data)

        except json.JSONDecodeError:
            # If not JSON, treat as raw audio data
            handle_raw_audio(body, connection_data)

        return {"statusCode": 200, "body": "Processed"}

    except Exception as e:
        logger.error(f"Error in handle_default_message: {str(e)}")
        return {"statusCode": 500, "body": f"Message processing error: {str(e)}"}


def handle_media_event(message_data, connection_data):
    """
    Process media events (audio payloads) from Twilio.
    """
    try:
        logger.error(f"Processing media event: {json.dumps(message_data)}")
        payload = message_data.get("media", {}).get("payload")
        if not payload:
            logger.error("No payload in media event")
            return

        # Add to audio buffer
        connection_data.setdefault("audio_buffer", []).append(payload)
        logger.error(
            f"Added payload to buffer, size: {len(connection_data['audio_buffer'])}"
        )

        # Process buffer when it reaches a certain size
        if len(connection_data["audio_buffer"]) >= 5:
            process_audio_buffer(connection_data)
    except Exception as e:
        logger.error(f"Error processing media event: {str(e)}")
        logger.error(traceback.format_exc())

    except Exception as e:
        logger.error(f"Error processing media event: {str(e)}")


def process_audio_buffer(connection_data):
    """
    Process accumulated audio buffer.
    Send to OpenAI real-time service.
    """
    try:
        # Concatenate audio buffer
        audio_buffer = connection_data.get("audio_buffer", [])
        if not audio_buffer:
            return

        concatenated_payload = "".join(audio_buffer)
        connection_data["audio_buffer"] = []  # Clear buffer

        # Send to real-time service
        call_sid = connection_data.get("call_sid")
        if call_sid:
            # Use async task to send audio
            asyncio.create_task(
                realtime_service.process_audio_chunk(call_sid, concatenated_payload)
            )

    except Exception as e:
        logger.error(f"Error in process_audio_buffer: {str(e)}")


def handle_stream_start(message_data, connection_data):
    """
    Process stream start event from Twilio.
    Initialize OpenAI session.
    """
    try:
        stream_sid = message_data.get("start", {}).get("streamSid")
        logger.error(f"Stream started: {stream_sid}")

        # Extract test and call details
        test_id = connection_data.get("test_id")
        call_sid = connection_data.get("call_sid")
        test_data = connection_data.get("test_data", {})

        # Ensure required details exist
        if not test_id or not call_sid:
            logger.error("Missing test_id or call_sid")
            return

        # Initialize OpenAI session
        asyncio.create_task(initialize_openai_session(call_sid, test_id, test_data))

    except Exception as e:
        logger.error(f"Error in stream start: {str(e)}")


def handle_stream_stop(message_data, connection_data):
    """
    Process stream stop event.
    Clean up and finalize the test.
    """
    try:
        test_id = connection_data.get("test_id")
        call_sid = connection_data.get("call_sid")

        logger.error(f"Stream stopped for test {test_id}, call {call_sid}")

        # End the OpenAI session
        asyncio.create_task(realtime_service.end_session(call_sid))

        # Process test completion
        if test_id:
            asyncio.create_task(finalize_test_completion(test_id, call_sid))

    except Exception as e:
        logger.error(f"Error in stream stop: {str(e)}")


def handle_raw_audio(audio_data, connection_data):
    """
    Process raw audio data received from Twilio.
    """
    try:
        # Add to audio buffer
        connection_data.setdefault("audio_buffer", []).append(audio_data)

        # Process buffer when it reaches a certain size
        if len(connection_data["audio_buffer"]) >= 5:
            process_audio_buffer(connection_data)

    except Exception as e:
        logger.error(f"Error processing raw audio: {str(e)}")


async def initialize_openai_session(call_sid, test_id, test_data):
    """
    Initialize OpenAI session for the conversation.
    """
    try:
        # Extract persona and behavior details
        persona_name = test_data.get("config", {}).get(
            "persona_name", "Default Persona"
        )
        behavior_name = test_data.get("config", {}).get(
            "behavior_name", "Default Behavior"
        )

        # Get persona and behavior
        persona = evaluator_service.get_persona(persona_name)
        behavior = evaluator_service.get_behavior(behavior_name)

        # If no persona/behavior found, create default ones
        if not persona:
            from app.models.personas import Persona

            persona = Persona(
                name=persona_name, traits=["polite", "professional", "helpful"]
            )

        if not behavior:
            from app.models.personas import Behavior

            behavior = Behavior(
                name=behavior_name,
                characteristics=["asks clear questions", "listens attentively"],
            )

        # Initialize session
        session = await realtime_service.initialize_session(
            call_sid=call_sid,
            test_id=test_id,
            persona=persona,
            behavior=behavior,
            knowledge_base=evaluator_service.knowledge_base or {},
        )

        return session

    except Exception as e:
        logger.error(f"Error initializing OpenAI session: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        return None


async def finalize_test_completion(test_id, call_sid):
    """
    Finalize the test after call completion.
    """
    try:
        # Get conversation from real-time service
        conversation = realtime_service.get_conversation(call_sid)

        # Generate report
        report = await evaluator_service.generate_report_from_conversation(
            test_id, conversation
        )

        # Save report to S3
        if report:
            s3_service.save_report(report.dict(), str(report.id))

        # Update test status
        if test_id in evaluator_service.active_tests:
            evaluator_service.active_tests[test_id]["status"] = "completed"
            evaluator_service.active_tests[test_id]["report_id"] = str(report.id)

        # Update in DynamoDB
        dynamodb_service.update_test_status(test_id, "completed")
        dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

        logger.error(f"Test {test_id} completed and report generated")

    except Exception as e:
        logger.error(f"Error finalizing test completion: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
