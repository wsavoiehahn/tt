# app/main.py
import logging
import os
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from .routers import tests, reports, twilio_webhooks
from .config import config
from .services.evaluator import evaluator_service
from .services.s3_service import s3_service
from .services.reporting import reporting_service

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create FastAPI app
LOG_EVENT_TYPES = [
    "response.content.done",
    "rate_limits.updated",
    "response.done",
    "input_audio_buffer.committed",
    "input_audio_buffer.speech_stopped",
    "input_audio_buffer.speech_started",
    "session_created",
]

app = FastAPI(
    title="AI Call Center Evaluator",
    description="Evaluate AI call center agent performance across various personas and behaviors",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup static files and templates directories
base_dir = Path(__file__).resolve().parent.parent
static_dir = base_dir / "static"
templates_dir = base_dir / "templates"


# Mount static files if the directory exists
if static_dir.exists():
    try:
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        logger.info(f"Static files mounted from {static_dir}")
    except Exception as e:
        logger.warning(f"Could not mount static files: {str(e)}")
else:
    logger.warning(f"Static directory not found at {static_dir}")

# Set up templates if the directory exists
if templates_dir.exists():
    try:
        templates = Jinja2Templates(directory=str(templates_dir))
        logger.info(f"Templates directory configured at {templates_dir}")
    except Exception as e:
        logger.warning(f"Could not configure templates directory: {str(e)}")
        templates = None
else:
    logger.warning(f"Templates directory not found at {templates_dir}")
    templates = None


# Initialize config and services on startup
@app.on_event("startup")
async def startup_event():
    """Initialize services on application startup."""
    try:
        is_local_mode = os.environ.get("LOCAL_MODE", "false").lower() == "true"
        if is_local_mode:
            logger.info("Starting in LOCAL MODE")
        # Load configuration
        secrets = config.load_secrets()
        logger.info("Configuration loaded successfully")

        # Load knowledge base and personas data
        evaluator_service.knowledge_base = config.load_knowledge_base()
        evaluator_service.personas_data = config.load_personas()
        logger.info("Knowledge base and personas data loaded successfully")

        s3_service.ensure_bucket_exists()
        logger.info(f"Storage initialized")

        # Initialize DynamoDB table
        from .services.dynamodb_service import dynamodb_service

        dynamodb_service.ensure_table_exists()
        logger.info("DynamoDB table initialized")

        # Log application startup
        logger.info("AI Call Center Evaluator application started successfully")
    except Exception as e:
        logger.error(f"Error during application startup: {str(e)}")


# Include routers
app.include_router(tests.router)
app.include_router(reports.router)
app.include_router(twilio_webhooks.router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to dashboard or show a welcome page."""
    # Redirect to dashboard
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the dashboard page."""
    if templates:
        try:
            return templates.TemplateResponse("dashboard.html", {"request": request})
        except Exception as e:
            logger.error(f"Error rendering dashboard template: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Error rendering dashboard template"
            )
    else:
        # Fallback to simple HTML response
        return HTMLResponse(
            content="""
            <html>
                <head>
                    <title>AI Call Center Evaluator Dashboard</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            margin: 40px;
                            line-height: 1.6;
                            text-align: center;
                        }
                        h1 {
                            color: #333;
                        }
                        .error {
                            color: #dc3545;
                            background-color: #f8d7da;
                            border: 1px solid #f5c6cb;
                            border-radius: 4px;
                            padding: 15px;
                            margin: 20px 0;
                        }
                        a {
                            display: inline-block;
                            background-color: #007bff;
                            color: white;
                            padding: 10px 20px;
                            text-decoration: none;
                            border-radius: 4px;
                            margin-top: 20px;
                        }
                    </style>
                </head>
                <body>
                    <h1>AI Call Center Evaluator Dashboard</h1>
                    <div class="error">
                        <p>Dashboard template could not be loaded.</p>
                        <p>Please check that the templates directory exists and contains dashboard.html.</p>
                    </div>
                    <a href="/api/tests">View Tests API</a>
                    <a href="/api/reports">View Reports API</a>
                </body>
            </html>
        """
        )


@app.get("/dashboard/reports/{report_id}", response_class=HTMLResponse)
async def report_details(request: Request, report_id: str):
    """Render the report details page."""
    if templates:
        try:
            return templates.TemplateResponse(
                "report_details.html", {"request": request, "report_id": report_id}
            )
        except Exception as e:
            logger.error(f"Error rendering report details template: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Error rendering report details template"
            )
    else:
        # Fallback to simple HTML response
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Report Details</title>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            margin: 40px;
                            line-height: 1.6;
                            text-align: center;
                        }}
                        h1 {{
                            color: #333;
                        }}
                        .error {{
                            color: #dc3545;
                            background-color: #f8d7da;
                            border: 1px solid #f5c6cb;
                            border-radius: 4px;
                            padding: 15px;
                            margin: 20px 0;
                        }}
                        a {{
                            display: inline-block;
                            background-color: #007bff;
                            color: white;
                            padding: 10px 20px;
                            text-decoration: none;
                            border-radius: 4px;
                            margin-top: 20px;
                        }}
                    </style>
                </head>
                <body>
                    <h1>Report Details: {report_id}</h1>
                    <div class="error">
                        <p>Report details template could not be loaded.</p>
                        <p>Please check that the templates directory exists and contains report_details.html.</p>
                    </div>
                    <a href="/dashboard">Back to Dashboard</a>
                    <a href="/api/reports/{report_id}">View JSON Report</a>
                </body>
            </html>
        """
        )


@app.get("/api/system-info")
async def system_info():
    """Get system information and configuration."""
    try:
        # Collect system info
        info = {
            "version": "1.0.0",
            "environment": os.environ.get("ENV", "development"),
            "aws_region": config.region_name,
            "s3_bucket": s3_service.bucket_name,
            "twilio_configured": bool(config.get_parameter("/twilio/account_sid")),
            "openai_configured": bool(config.get_parameter("/openai/api_key")),
            "knowledge_base_items": len(
                evaluator_service.knowledge_base.get("faqs", [])
            ),
            "personas_count": len(evaluator_service.personas_data.get("personas", [])),
            "behaviors_count": len(
                evaluator_service.personas_data.get("behaviors", [])
            ),
            "active_tests": len(evaluator_service.active_tests),
        }
        return info
    except Exception as e:
        logger.error(f"Error getting system info: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error getting system info: {str(e)}"
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "1.0.0"}


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception for request {request.url}: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": str(exc)},
    )


# Add a function to ensure S3 bucket exists to the S3 service
if not hasattr(s3_service, "ensure_bucket_exists"):

    def ensure_bucket_exists(self):
        """Ensure that the S3 bucket exists, create it if it doesn't."""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"S3 bucket '{self.bucket_name}' already exists")
        except:
            logger.info(f"Creating S3 bucket '{self.bucket_name}'")
            try:
                self.s3_client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.region_name},
                )
                logger.info(f"S3 bucket '{self.bucket_name}' created successfully")
            except Exception as e:
                logger.error(f"Error creating S3 bucket: {str(e)}")
                # Fall back to using a temporary directory
                logger.warning("Falling back to local file storage")

    # Add the method to the service
    setattr(s3_service.__class__, "ensure_bucket_exists", ensure_bucket_exists)
