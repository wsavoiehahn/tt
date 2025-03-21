# app/config.py
import os
import json
import boto3
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from pydantic import BaseModel, Field
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Load environment variables from .env file
load_dotenv()


class AppConfig(BaseModel):
    CLIENT_ID: str
    ENV_TIER: str  # set to "local" if running locally!
    AWS_DEFAULT_REGION: str
    LOCAL_MODE: bool = False
    LOCAL_STORAGE_PATH: str = "./storage"
    OPENAI_API_KEY: str
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    URL: str
    TWILIO_PHONE_NUMBER: str
    TARGET_PHONE_NUMBER: str
    KNOWLEDGE_BASE_PATH: str
    PERSONAS_PATH: str
    S3_BUCKET_NAME: str  # base bucket name from AWS params or env
    PORT: int
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str

    KNOWLEDGE_BASE: Dict[str, Any] = Field(default_factory=dict)
    PERSONAS: Dict[str, Any] = Field(default_factory=dict)

    # dynamically construct the full bucket name
    @property
    def FULL_S3_BUCKET_NAME(self):
        return f"{self.CLIENT_ID}-{self.ENV_TIER}-{self.S3_BUCKET_NAME}"

    def is_local_mode(self):
        return self.LOCAL_MODE

    def _load_local_file(self, key: str) -> Dict[str, Any]:
        file_path = f"{self.LOCAL_STORAGE_PATH}/{key}"
        with open(file_path, "r") as f:
            return json.load(f)

    def load_json_file(self, key: str) -> Dict[str, Any]:
        if self.is_local_mode():
            logger.info("Loading JSON from local storage")
            return self._load_local_file(key)

        s3_client = boto3.client("s3", region_name=self.AWS_DEFAULT_REGION)
        try:
            response = s3_client.get_object(Bucket=self.FULL_S3_BUCKET_NAME, Key=key)
            json_data = json.loads(response["Body"].read().decode("utf-8"))
        except Exception as e:
            logging.error(
                f"Error loading knowledge base from S3: {str(e)}. Improper location"
            )
        return json_data

    @classmethod
    def load(cls):
        local_mode = os.getenv("LOCAL_MODE", "false").lower() == "true"

        required_vars = [
            "OPENAI_API_KEY",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "URL",
            "TWILIO_PHONE_NUMBER",
            "TARGET_PHONE_NUMBER",
            "KNOWLEDGE_BASE_PATH",
            "PERSONAS_PATH",
            "S3_BUCKET_NAME",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "PORT",
        ]

        if local_mode:
            missing = [var for var in required_vars if var not in os.environ]
            if missing:
                raise ValueError(f"Missing vars in LOCAL_MODE: {missing}")
            config = {var: os.getenv(var) for var in required_vars}
        else:
            CLIENT_ID = os.environ["CLIENT_ID"]
            ENV_TIER = os.environ["ENV_TIER"]
            AWS_REGION = os.environ["AWS_DEFAULT_REGION"]

            ssm = boto3.client("ssm", region_name=AWS_REGION)
            param_paths = [f"{CLIENT_ID}/{ENV_TIER}/{var}" for var in required_vars]

            try:
                response = ssm.get_parameters(Names=param_paths, WithDecryption=True)
                aws_params = {
                    param["Name"]: param["Value"] for param in response["Parameters"]
                }

                missing = set(param_paths) - set(aws_params.keys())
                if missing:
                    raise ValueError(f"Missing AWS parameters: {missing}")

                config = {
                    var: aws_params[f"{CLIENT_ID}/{ENV_TIER}/{var}"]
                    for var in required_vars
                }
            except ClientError as e:
                raise RuntimeError(f"AWS Error: {e}")

        config.update(
            {
                "CLIENT_ID": os.environ["CLIENT_ID"],
                "ENV_TIER": os.environ["ENV_TIER"],
                "AWS_DEFAULT_REGION": os.environ["AWS_DEFAULT_REGION"],
                "LOCAL_MODE": local_mode,
                "LOCAL_STORAGE_PATH": os.getenv("LOCAL_STORAGE_PATH", "./storage"),
                "PORT": int(config["PORT"]),
            }
        )

        instance = cls(**config)
        # Load dictionaries after instance creation

        instance.KNOWLEDGE_BASE = instance.load_json_file(instance.KNOWLEDGE_BASE_PATH)
        instance.PERSONAS = instance.load_json_file(instance.PERSONAS_PATH)

        return instance

    def get_persona_traits(self, persona_name):
        """
        Returns the traits for the given persona name.
        If the persona is not found, returns None.
        """
        return next(
            (
                persona["traits"]
                for persona in self.PERSONAS["personas"]
                if persona["name"] == persona_name
            ),
            None,
        )

    def get_behavior_characteristics(self, behavior_name):
        """
        Returns the characteristics for the given behavior name.
        If the behavior is not found, returns None.
        """
        return next(
            (
                behavior["characteristics"]
                for behavior in self.PERSONAS["behaviors"]
                if behavior["name"] == behavior_name
            ),
            None,
        )


# Singleton instance initialized here
app_config = AppConfig.load()
