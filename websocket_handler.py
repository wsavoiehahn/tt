import json
import logging
import os
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


def lambda_handler(event, context):
    """Handle WebSocket connections and messages."""
    logger.debug(f"WebSocket event received: {json.dumps(event)}")

    # Get connection info
    connection_id = event.get("requestContext", {}).get("connectionId")
    route_key = event.get("requestContext", {}).get("routeKey")

    logger.debug(f"Connection ID: {connection_id}, Route: {route_key}")

    # Handle different WebSocket routes
    if route_key == "$connect":
        # Extract query parameters
        query_params = event.get("queryStringParameters", {}) or {}
        test_id = query_params.get("test_id")
        call_sid = query_params.get("call_sid")

        logger.debug(f"WebSocket connect with test_id: {test_id}, call_sid: {call_sid}")

        return {"statusCode": 200, "body": "Connected"}

    elif route_key == "$disconnect":
        logger.debug("WebSocket disconnected")
        return {"statusCode": 200, "body": "Disconnected"}

    elif route_key == "$default":
        # Handle media data
        try:
            body = event.get("body")
            if body:
                # Could be JSON or binary data
                try:
                    data = json.loads(body)
                    logger.debug(f"Received JSON data: {json.dumps(data)}")
                except:
                    logger.debug(f"Received non-JSON data, length: {len(body)}")

            # Send a response (optional)
            send_message(event, "Message received")

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

        return {"statusCode": 200, "body": "Message received"}

    return {"statusCode": 404, "body": "Unknown route"}


def send_message(event, message):
    """Send a message back to the connected client."""
    try:
        domain = event["requestContext"]["domainName"]
        stage = event["requestContext"]["stage"]
        connection_id = event["requestContext"]["connectionId"]

        # Create API Gateway management client
        import boto3

        client = boto3.client(
            "apigatewaymanagementapi", endpoint_url=f"https://{domain}/{stage}"
        )

        # Send message
        client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps({"message": message}).encode("utf-8"),
        )

        logger.debug(f"Message sent to {connection_id}")
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
