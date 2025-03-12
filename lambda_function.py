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

    logger.debug("Successfully imported app from app.main")
except Exception as e:
    logger.error("Failed to import app from app.main")
    logger.error(f"Error: {e}")
    logger.error(traceback.format_exc())

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
        logger.debug(f"Event type: {type(event)}")
        logger.debug(f"Event content: {json.dumps(event, default=str)}")

        # Extract request details
        request_context = event.get("requestContext", {})
        event_type = request_context.get("eventType")
        route_key = request_context.get("routeKey")
        connection_id = request_context.get("connectionId")
        domain_name = request_context.get("domainName")
        stage = request_context.get("stage")

        logger.info(f"Event type: {event_type}, Route key: {route_key}")
        logger.info(
            f"Connection ID: {connection_id}, Domain: {domain_name}, Stage: {stage}"
        )

        # Extract query parameters if any
        query_params = event.get("queryStringParameters", {}) or {}
        logger.info(f"Query parameters: {query_params}")

        # Handle WebSocket connections
        if event_type == "CONNECT":
            logger.info("WebSocket CONNECT event received")
            # Accept all connections for now for testing
            return {"statusCode": 200, "body": "WebSocket connected"}

        elif event_type == "DISCONNECT":
            logger.info("WebSocket DISCONNECT event received")
            return {"statusCode": 200, "body": "WebSocket disconnected"}

        elif event_type == "MESSAGE":
            logger.info(f"WebSocket MESSAGE event received")
            message_body = event.get("body", "{}")
            logger.info(f"Message body: {message_body}")
            return {"statusCode": 200, "body": "Message received"}

        # Route-specific handlers
        elif route_key == "$default" and "media-stream" in event.get(
            "requestContext", {}
        ).get("resourcePath", ""):
            logger.info("Handling media-stream WebSocket connection via $default route")
            # Accept all media-stream connections for now for testing
            return {"statusCode": 200, "body": "Media stream connected"}

        elif route_key == "media-stream" or route_key == "$connect/media-stream":
            logger.error("Handling explicit media-stream WebSocket connection")
            # Accept all media-stream connections for now for testing
            return {"statusCode": 200, "body": "Media stream connected"}

        # Handle HTTP requests using Mangum (FastAPI)
        else:
            logger.info(f"Handling HTTP request with route: {route_key}")
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
