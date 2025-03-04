# app/services/factory.py
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_service(service_type: str) -> Any:
    """
    Factory method to get appropriate service implementation
    based on local mode or AWS mode.
    """
    is_local_mode = os.environ.get("LOCAL_MODE", "false").lower() == "true"

    if service_type == "storage":
        if is_local_mode:
            from .local_storage import LocalStorageService

            logger.info("Using local storage service")
            return LocalStorageService()
        else:
            from .s3_service import s3_service

            logger.info("Using S3 storage service")
            return s3_service
    elif service_type == "twilio":
        if is_local_mode:
            from .mock_twilio_service import MockTwilioService

            logger.info("Using mock Twilio service")
            return MockTwilioService()
        else:
            from .twilio_service import twilio_service

            logger.info("Using real Twilio service")
            return twilio_service
    # Add other service types here

    raise ValueError(f"Unknown service type: {service_type}")
