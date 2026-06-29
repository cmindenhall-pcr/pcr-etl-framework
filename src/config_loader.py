import json
import os
from pathlib import Path

from src.project_paths import CONFIG_DIR


def load_pipeline_config() -> dict:
    """
    Load pipeline configuration from config/pipeline_config.json or an
    override supplied via PIPELINE_CONFIG_PATH.
    """
    config_override = os.getenv("PIPELINE_CONFIG_PATH")
    config_path = Path(config_override) if config_override else (CONFIG_DIR / "pipeline_config.json")

    if not config_path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
