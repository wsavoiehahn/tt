# lambda_function.py
import logging
import os
import traceback
import json
from pathlib import Path
from mangum import Mangum


# Import FastAPI app
from app_func import app

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Capture and log any import errors
try:
    from app.main import app
except Exception as e:
    logger.error("Failed to import app from app.main")
    logger.error(f"Error: {e}")
    logger.error(traceback.format_exc())
    raise

# Create Mangum handler for Lambda
handler = Mangum(app)


def lambda_handler(event, context):
    logger.info("Lambda handler called with event: %s", json.dumps(event))
    try:
        return handler(event, context)
    except Exception as e:
        logger.error("Unhandled exception in Lambda handler")
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal Server Error", "details": str(e)}),
        }


# Add static files directory and templates if not exists
def setup_directories():
    # Create static directory if it doesn't exist
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)

    static_css_dir = static_dir / "css"
    static_css_dir.mkdir(exist_ok=True)

    static_js_dir = static_dir / "js"
    static_js_dir.mkdir(exist_ok=True)

    # Create templates directory if it doesn't exist
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)

    # Create default dashboard template if it doesn't exist
    dashboard_template = templates_dir / "dashboard.html"
    if not dashboard_template.exists():
        with open("app/templates/dashboard.html", "r") as src:
            with open(dashboard_template, "w") as dst:
                dst.write(src.read())

    # Create default report details template if it doesn't exist
    report_template = templates_dir / "report_details.html"
    if not report_template.exists():
        with open("app/templates/report_details.html", "r") as src:
            with open(report_template, "w") as dst:
                dst.write(src.read())


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
