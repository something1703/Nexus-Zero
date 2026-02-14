import os


# GCP Configuration
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nexus-zero-sre")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

# Cloud SQL Configuration
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "5432")
INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME",
    "nexus-zero-sre:us-central1:nexus-zero-db"
)
CLOUD_SQL_SOCKET_DIR = os.environ.get("CLOUD_SQL_SOCKET_DIR", "/cloudsql")

# GitHub Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

# Gemini Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Slack Configuration
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#nexus-zero-alerts")

# Server Configuration
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")
