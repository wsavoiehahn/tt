# websocket_handler.py
import json
import logging
import base64
import asyncio
import boto3
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Global dictionary to store active WebSocket connections
active_connections = {}

# DynamoDB client for test storage
dynamodb = boto3.resource("dynamodb")
test_table = dynamodb.Table("ai-call-center-evaluator-dev-tests")

# Import services when used in Lambda context
try:
    from app.services.openai_service import openai_service
    from app.services.evaluator import evaluator_service
    from app.services.s3_service import s3_service
    from app.services.dynamodb_service import dynamodb_service
    from app.services.realtime_service import realtime_service
except ImportError:
    logger.warning(
        "Could not import app services - will attempt to import when handling requests"
    )
    realtime_service = None


def lambda_handler(event, context):
    """
    WebSocket handler for Twilio Media Streams.
    This function processes WebSocket events for real-time audio streaming
    between Twilio and OpenAI.
    """
    logger.info(f"WebSocket event received: {json.dumps(event)}")

    # Get connection info
    connection_id = event.get("requestContext", {}).get("connectionId")
    route_key = event.get("requestContext", {}).get("routeKey")
    domain_name = event.get("requestContext", {}).get("domainName")
    stage = event.get("requestContext", {}).get("stage")

    # Create API Gateway Management client for sending responses back
    api_gateway_management = boto3.client(
        "apigatewaymanagementapi", endpoint_url=f"https://{domain_name}/{stage}"
    )

    logger.info(f"Connection ID: {connection_id}, Route: {route_key}")

    # Handle different WebSocket routes
    if route_key == "$connect":
        return handle_connect(event, connection_id)
    elif route_key == "$disconnect":
        return handle_disconnect(event, connection_id)
    elif route_key == "$default":
        return handle_default_message(event, connection_id, api_gateway_management)
    else:
        return {"statusCode": 400, "body": "Unknown route"}


def handle_connect(event, connection_id):
    """Handle new WebSocket connection."""
    try:
        # Extract query parameters
        query_params = event.get("queryStringParameters", {}) or {}
        test_id = query_params.get("test_id")
        call_sid = query_params.get("call_sid")

        logger.info(f"WebSocket connect with test_id: {test_id}, call_sid: {call_sid}")

        # Store connection information
        active_connections[connection_id] = {
            "connection_id": connection_id,
            "test_id": test_id,
            "call_sid": call_sid,
            "connected_at": datetime.now().isoformat(),
            "status": "connected",
            "openai_stream": None,
        }

        # Try to load test data from DynamoDB
        if test_id:
            try:
                # Initialize services if needed
                global dynamodb_service, evaluator_service, realtime_service
                if dynamodb_service is None:
                    from app.services.dynamodb_service import dynamodb_service
                if evaluator_service is None:
                    from app.services.evaluator import evaluator_service
                if realtime_service is None:
                    from app.services.realtime_service import realtime_service

                # Try to get test from memory first
                test_data = None
                if (
                    hasattr(evaluator_service, "active_tests")
                    and test_id in evaluator_service.active_tests
                ):
                    test_data = evaluator_service.active_tests[test_id]

                # If not in memory, try DynamoDB
                if not test_data:
                    test_data = dynamodb_service.get_test(test_id)
                    # If found, add to active tests in memory
                    if test_data and hasattr(evaluator_service, "active_tests"):
                        evaluator_service.active_tests[test_id] = test_data

                # Store test data in connection
                if test_data:
                    active_connections[connection_id]["test_data"] = test_data
                else:
                    logger.warning(f"No test data found for test_id: {test_id}")
            except Exception as e:
                logger.error(f"Error loading test data: {str(e)}")
                import traceback

                logger.error(f"Traceback: {traceback.format_exc()}")

        return {"statusCode": 200, "body": "Connected"}
    except Exception as e:
        logger.error(f"Error handling connect: {str(e)}")
        return {"statusCode": 500, "body": f"Error: {str(e)}"}


def handle_disconnect(event, connection_id):
    """Handle WebSocket disconnection."""
    try:
        logger.info(f"WebSocket disconnected: {connection_id}")

        # Get connection data
        connection_data = active_connections.get(connection_id)
        if connection_data:
            # Clean up OpenAI stream if exists
            openai_stream = connection_data.get("openai_stream")
            if openai_stream:
                asyncio.run(close_openai_stream(openai_stream))

            # Remove from active connections
            active_connections.pop(connection_id, None)

        return {"statusCode": 200, "body": "Disconnected"}
    except Exception as e:
        logger.error(f"Error handling disconnect: {str(e)}")
        return {"statusCode": 500, "body": f"Error: {str(e)}"}


async def close_openai_stream(openai_stream):
    """Close OpenAI stream connection."""
    try:
        if hasattr(openai_stream, "close") and callable(openai_stream.close):
            await openai_stream.close()
    except Exception as e:
        logger.error(f"Error closing OpenAI stream: {str(e)}")


def handle_default_message(event, connection_id, api_gateway_management):
    """Handle incoming messages on the WebSocket."""
    try:
        # Get connection data
        connection_data = active_connections.get(connection_id)
        if not connection_data:
            logger.error(f"No connection data found for {connection_id}")
            return {"statusCode": 400, "body": "Connection not found"}

        # Get message data
        body = event.get("body")
        if not body:
            return {"statusCode": 400, "body": "Empty message"}

        # Parse message
        try:
            message_data = json.loads(body)
            event_type = message_data.get("event")

            # Process based on event type
            if event_type == "start":
                handle_stream_start(message_data, connection_data)
            elif event_type == "media":
                handle_media_data(message_data, connection_data, api_gateway_management)
            elif event_type == "stop":
                handle_stream_stop(message_data, connection_data)
            elif event_type == "mark":
                handle_mark_event(message_data, connection_data)
            else:
                logger.warning(f"Unknown event type: {event_type}")
        except json.JSONDecodeError:
            # Handle binary data (likely PCM audio)
            handle_binary_data(body, connection_data, api_gateway_management)

        return {"statusCode": 200, "body": "Processed"}
    except Exception as e:
        logger.error(f"Error handling message: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"statusCode": 500, "body": f"Error: {str(e)}"}


def handle_stream_start(message_data, connection_data):
    """Handle stream start event from Twilio."""
    logger.info(f"Stream start: {message_data}")

    # Extract stream SID
    stream_sid = message_data.get("start", {}).get("streamSid")
    if stream_sid:
        connection_data["stream_sid"] = stream_sid
        connection_data["status"] = "streaming"

        # Initialize OpenAI connection
        try:
            global realtime_service
            if realtime_service is None:
                from app.services.realtime_service import realtime_service

            # Start OpenAI session if test data is available
            test_data = connection_data.get("test_data")
            if test_data:
                test_id = connection_data.get("test_id")
                call_sid = connection_data.get("call_sid")

                # Extract test info
                test_case = test_data.get("test_case", {})
                persona_name = test_case.get("config", {}).get("persona_name")
                behavior_name = test_case.get("config", {}).get("behavior_name")

                # Initialize OpenAI connection in the background
                import threading

                threading.Thread(
                    target=asyncio.run,
                    args=(
                        initialize_openai_session(
                            connection_data,
                            test_id,
                            call_sid,
                            persona_name,
                            behavior_name,
                        ),
                    ),
                ).start()
        except Exception as e:
            logger.error(f"Error initializing OpenAI session: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")


def handle_media_data(message_data, connection_data, api_gateway_management):
    """Handle media data from Twilio."""
    # Extract and process audio
    media_data = message_data.get("media", {})
    payload = media_data.get("payload")

    if payload:
        # Store in connection data buffer
        if "audio_buffer" not in connection_data:
            connection_data["audio_buffer"] = []

        connection_data["audio_buffer"].append(payload)

        # Process buffer if it reaches threshold
        if len(connection_data["audio_buffer"]) >= 5:
            # Send to OpenAI in the background
            process_audio_buffer(connection_data, api_gateway_management)

            # Clear buffer
            connection_data["audio_buffer"] = []


def process_audio_buffer(connection_data, api_gateway_management):
    """Process audio buffer and send to OpenAI."""
    # Get OpenAI session
    openai_session = connection_data.get("openai_session")
    if not openai_session:
        logger.warning("No OpenAI session available")
        return

    # Concatenate audio data
    audio_buffer = connection_data.get("audio_buffer", [])
    if not audio_buffer:
        return

    # Concatenate payloads
    concatenated_payload = "".join(audio_buffer)

    # Send to OpenAI in the background
    import threading

    threading.Thread(
        target=asyncio.run,
        args=(
            send_audio_to_openai(
                concatenated_payload, connection_data, api_gateway_management
            ),
        ),
    ).start()


async def send_audio_to_openai(audio_payload, connection_data, api_gateway_management):
    """Send audio to OpenAI and process the response."""
    try:
        global realtime_service
        if realtime_service is None:
            from app.services.realtime_service import realtime_service

        # Get connection details
        connection_id = connection_data.get("connection_id")
        stream_sid = connection_data.get("stream_sid")

        # Send audio to OpenAI
        await realtime_service.process_audio_chunk(
            connection_data.get("call_sid"), audio_payload
        )

        # Check if there's a response from OpenAI
        openai_session = connection_data.get("openai_session")
        if openai_session and openai_session.get("last_response_audio"):
            # Get audio response
            audio_response = openai_session.pop("last_response_audio")

            # Send back to Twilio
            try:
                media_message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {
                        "payload": base64.b64encode(audio_response).decode("utf-8")
                    },
                }
                api_gateway_management.post_to_connection(
                    ConnectionId=connection_id, Data=json.dumps(media_message)
                )
            except Exception as e:
                logger.error(f"Error sending media response: {str(e)}")
    except Exception as e:
        logger.error(f"Error sending audio to OpenAI: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")


def handle_stream_stop(message_data, connection_data):
    """Handle stream stop event from Twilio."""
    logger.info(f"Stream stop: {message_data}")

    # Update connection status
    connection_data["status"] = "stopped"

    # Cleanup OpenAI connection
    openai_stream = connection_data.get("openai_stream")
    if openai_stream:
        asyncio.run(close_openai_stream(openai_stream))

    # Process test completion
    test_id = connection_data.get("test_id")
    call_sid = connection_data.get("call_sid")

    if test_id and call_sid:
        try:
            global evaluator_service
            if evaluator_service is None:
                from app.services.evaluator import evaluator_service

            # Process completed call
            asyncio.run(
                evaluator_service.process_completed_call(test_id, call_sid, "completed")
            )
        except Exception as e:
            logger.error(f"Error processing completed call: {str(e)}")


def handle_mark_event(message_data, connection_data):
    """Handle mark event from Twilio."""
    logger.info(f"Mark event: {message_data}")

    # Extract mark name
    mark_name = message_data.get("mark", {}).get("name")
    if mark_name:
        # Process specific marks
        if mark_name == "question_complete":
            # Advance to next question
            test_id = connection_data.get("test_id")
            if test_id:
                try:
                    global evaluator_service
                    if evaluator_service is None:
                        from app.services.evaluator import evaluator_service

                    # Increment current question index
                    if test_id in evaluator_service.active_tests:
                        current_index = evaluator_service.active_tests[test_id].get(
                            "current_question_index", 0
                        )
                        evaluator_service.active_tests[test_id][
                            "current_question_index"
                        ] = (current_index + 1)
                except Exception as e:
                    logger.error(f"Error advancing question: {str(e)}")


def handle_binary_data(data, connection_data, api_gateway_management):
    """Handle binary data (likely raw audio)."""
    logger.info("Received binary data")
    # For binary data, process similarly to media data
    if data:
        # Store in connection data buffer
        if "audio_buffer" not in connection_data:
            connection_data["audio_buffer"] = []

        connection_data["audio_buffer"].append(data)

        # Process buffer if it reaches threshold
        if len(connection_data["audio_buffer"]) >= 5:
            # Send to OpenAI in the background
            process_audio_buffer(connection_data, api_gateway_management)

            # Clear buffer
            connection_data["audio_buffer"] = []


async def initialize_openai_session(
    connection_data, test_id, call_sid, persona_name, behavior_name
):
    """
    Initialize an OpenAI session for real-time conversation.
    """
    try:
        global realtime_service, evaluator_service
        if realtime_service is None:
            from app.services.realtime_service import realtime_service
        if evaluator_service is None:
            from app.services.evaluator import evaluator_service

        # Get knowledge base
        knowledge_base = evaluator_service.knowledge_base or {}

        # Get persona and behavior
        persona = None
        behavior = None

        if hasattr(evaluator_service, "get_persona") and callable(
            evaluator_service.get_persona
        ):
            persona = evaluator_service.get_persona(persona_name)

        if hasattr(evaluator_service, "get_behavior") and callable(
            evaluator_service.get_behavior
        ):
            behavior = evaluator_service.get_behavior(behavior_name)

        # If no persona/behavior found, create default ones
        if not persona:
            from app.models.personas import Persona

            persona = Persona(
                name=persona_name or "Default Persona",
                traits=["polite", "professional", "helpful"],
            )

        if not behavior:
            from app.models.personas import Behavior

            behavior = Behavior(
                name=behavior_name or "Default Behavior",
                characteristics=["asks questions clearly", "listens attentively"],
            )

        # Initialize OpenAI session
        openai_session = await realtime_service.initialize_session(
            call_sid=call_sid,
            test_id=test_id,
            persona=persona,
            behavior=behavior,
            knowledge_base=knowledge_base,
        )

        # Store in connection data
        connection_data["openai_session"] = openai_session
        connection_data["openai_initialized"] = True

        logger.info(f"OpenAI session initialized for call {call_sid}, test {test_id}")

        # Send welcome message
        test_case = connection_data.get("test_data", {}).get("test_case", {})
        special_instructions = test_case.get("config", {}).get("special_instructions")

        welcome_message = (
            "Hello, I'm your AI assistant for this call. How can I help you today?"
        )
        if special_instructions:
            welcome_message = (
                f"Special instructions: {special_instructions}. " + welcome_message
            )

        await realtime_service.send_message(call_sid, welcome_message)

        # Record in conversation
        evaluator_service.record_conversation_turn(
            test_id=test_id,
            call_sid=call_sid,
            speaker="evaluator",
            text=welcome_message,
        )

        return openai_session
    except Exception as e:
        logger.error(f"Error initializing OpenAI session: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        return None
