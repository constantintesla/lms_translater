"""Service settings. Everything is overridable via environment variables; no secrets live in files."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DUB_DATA_DIR", ROOT / "data"))
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "dub.sqlite"

# Translation: any OpenAI-compatible server (LM Studio by default). Empty model = first one the server lists.
LLM_URL = os.environ.get("DUB_LLM_URL", "http://localhost:1234/v1")
LLM_MODEL = os.environ.get("DUB_LLM_MODEL", "")
LLM_TIMEOUT = int(os.environ.get("DUB_LLM_TIMEOUT", "900"))

# Speech models
ASR_MODEL = os.environ.get("DUB_ASR_MODEL", "large-v3")
CHECK_MODEL = os.environ.get("DUB_CHECK_MODEL", "medium")      # round-trip check of generated speech
MODELS_DIR = Path(os.environ.get("DUB_MODELS_DIR", DATA_DIR / "models"))
F5_CKPT = os.environ.get("DUB_F5_CKPT", str(MODELS_DIR / "f5ru" / "model_v2.safetensors"))   # fetched by `python -m dubservice.setup_models`
F5_VOCAB = os.environ.get("DUB_F5_VOCAB", str(MODELS_DIR / "f5ru" / "vocab.txt"))

# Dubbing behaviour (tuned on the pilot lecture; see README)
TARGET_LANG = "ru"
CHARS_PER_SEC = float(os.environ.get("DUB_CHARS_PER_SEC", "12"))   # expected Russian speech rate
CANDIDATES = int(os.environ.get("DUB_CANDIDATES", "3"))            # best-of-N per phrase
MAX_ATEMPO = float(os.environ.get("DUB_MAX_ATEMPO", "1.3"))
MAX_F5_SPEED = float(os.environ.get("DUB_MAX_F5_SPEED", "1.5"))

# Teachbase (read from the environment only)
TB_PUBLIC_KEY = os.environ.get("TB_PUBLIC_KEY", "")
TB_SECRET_KEY = os.environ.get("TB_SECRET_KEY", "")

# HTTP
HOST = os.environ.get("DUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("DUB_PORT", "8000"))
API_TOKEN = os.environ.get("DUB_API_TOKEN", "")                    # optional bearer token
