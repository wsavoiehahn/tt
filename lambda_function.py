# lambda_function.py (updated version)
import logging
import os
import traceback
import json
from pathlib import Path
from mangum import Mangum
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from app.services.evaluator import evaluator_service

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Import main FastAPI app if available
try:
    from app.main import app

    logger.info("Successfully imported app from app.main")
except Exception as e:
    logger.error("Failed to import app from app.main")
    logger.error(f"Error: {e}")
    logger.error(traceback.format_exc())

    # Create a new app if import failed
    app = FastAPI()
    logger.info("Created new FastAPI app as fallback")

# Import custom WebSocket handlers
try:
    from app.websocket_handlers import handle_media_stream

    logger.info("Successfully imported WebSocket handlers")
except Exception as e:
    logger.error("Failed to import WebSocket handlers")
    logger.error(f"Error: {e}")
    logger.error(traceback.format_exc())


# Add WebSocket endpoint for media streaming
@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """WebSocket endpoint for media streaming between Twilio and OpenAI"""
    await handle_media_stream(websocket)


@app.api_route("/webhooks/call-started", methods=["GET", "POST"])
async def handle_call_started(request: Request):
    """Handle call started webhook from Twilio."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    test_id = request.query_params.get("test_id")

    logger.error(f"Call started - SID: {call_sid}, Test ID: {test_id}")

    # Create TwiML response
    response = VoiceResponse()

    if test_id and test_id in evaluator_service.active_tests:
        # Real test found - create proper response
        response.say("Starting real-time AI evaluation call.")
        response.pause(length=1)

        # Create Connect and Stream elements
        connect = Connect()

        # Determine the WebSocket URL
        host = request.headers.get("host", request.url.hostname)
        protocol = "wss"  # Always use secure WebSockets in production
        stream_url = (
            f"{protocol}://{host}/media-stream?test_id={test_id}&call_sid={call_sid}"
        )

        logger.error(f"Using WebSocket URL: {stream_url}")

        # Create Stream properly
        stream = Stream(name="media_stream", url=stream_url)

        # Add parameters
        stream.parameter(name="format", value="audio")
        stream.parameter(name="rate", value="8000")

        # Add stream to connect
        connect.append(stream)

        # Add connect to response
        response.append(connect)
    else:
        # Test not found - return simple response
        response.say("Starting simplified evaluation call.")
        response.pause(length=1)
        response.say(f"No test found with the provided ID: {test_id}")
        response.pause(length=2)
        response.say("Thank you for your time. This concludes our test.")

    return HTMLResponse(content=str(response), media_type="application/xml")


# Add a simple root endpoint
@app.get("/", response_class=JSONResponse)
async def index_page():
    """Root endpoint showing API status"""
    return {"message": "AI Call Center Evaluator API is running", "status": "healthy"}


# Create Mangum handler for Lambda
handler = Mangum(app)


def lambda_handler(event, context):
    """AWS Lambda handler that supports both WebSockets and HTTP API requests"""
    try:
        # Enhanced logging for debugging
        logger.error(f"Event type: {type(event)}")
        logger.error(f"Event content: {json.dumps(event, default=str)}")

        # Extract request details
        request_context = event.get("requestContext", {})
        event_type = request_context.get("eventType")
        route_key = request_context.get("routeKey")
        connection_id = request_context.get("connectionId")
        domain_name = request_context.get("domainName")
        stage = request_context.get("stage")

        logger.error(f"Event type: {event_type}, Route key: {route_key}")
        logger.error(
            f"Connection ID: {connection_id}, Domain: {domain_name}, Stage: {stage}"
        )

        # Extract query parameters if any
        query_params = event.get("queryStringParameters", {}) or {}
        logger.error(f"Query parameters: {query_params}")

        # Handle WebSocket connections
        if event_type == "CONNECT":
            logger.error("WebSocket CONNECT event received")
            # Accept all connections for now for testing
            return {"statusCode": 200, "body": "WebSocket connected"}

        elif event_type == "DISCONNECT":
            logger.error("WebSocket DISCONNECT event received")
            return {"statusCode": 200, "body": "WebSocket disconnected"}

        elif event_type == "MESSAGE":
            logger.error(f"WebSocket MESSAGE event received")
            message_body = event.get("body", "{}")
            logger.error(f"Message body: {message_body}")
            return {"statusCode": 200, "body": "Message received"}

        # Route-specific handlers
        elif route_key == "$default" and "media-stream" in event.get(
            "requestContext", {}
        ).get("resourcePath", ""):
            logger.error(
                "Handling media-stream WebSocket connection via $default route"
            )
            # Accept all media-stream connections for now for testing
            return {"statusCode": 200, "body": "Media stream connected"}

        elif route_key == "media-stream" or route_key == "$connect/media-stream":
            logger.error("Handling explicit media-stream WebSocket connection")
            # Accept all media-stream connections for now for testing
            return {"statusCode": 200, "body": "Media stream connected"}

        # Handle HTTP requests using Mangum (FastAPI)
        else:
            logger.error(f"Handling HTTP request with route: {route_key}")
            return handler(event, context)

    except Exception as e:
        logger.error(f"Unhandled exception in Lambda handler: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal Server Error", "details": str(e)}),
        }


# Add static files directory and templates if not exists
def setup_directories():
    """Create necessary directories for static files and templates"""
    # Create static directory
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)

    static_css_dir = static_dir / "css"
    static_css_dir.mkdir(exist_ok=True)

    static_js_dir = static_dir / "js"
    static_js_dir.mkdir(exist_ok=True)

    # Create templates directory
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)


# Setup directories when running in Lambda
if os.environ.get("AWS_EXECUTION_ENV"):
    setup_directories()

# For local development
if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    # Load environment variables from .env file
    load_dotenv()

    # Set up directories for local development
    setup_directories()

    # Print local development info
    print("\n=== Local Development Mode ===")
    print("Make sure you've set up your .env file with the necessary configuration")
    print("Dashboard will be available at: http://localhost:8000/dashboard")
    print("API endpoints will be available at: http://localhost:8000/api/...")
    print("===========================\n")

    # Run the FastAPI app
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
