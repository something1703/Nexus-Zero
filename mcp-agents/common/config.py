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

# Gemini model name (NOT the API key — key is in credential_store)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Server Configuration
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")
