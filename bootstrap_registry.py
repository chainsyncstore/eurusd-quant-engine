"""One-time bootstrap: register the latest existing model in the v2 registry.

This allows the V2SignalManager to load a model immediately, even before the
scheduled retrain produces the first new-pipeline model.

Usage:
    python bootstrap_registry.py

On EC2 (inside Docker):
    docker exec quant_telegram python bootstrap_registry.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def bootstrap() -> bool:
    """Register the latest model directory in the v2 model registry."""

    from quant.telebot.model_selection import find_latest_model
    from quant_v2.model_registry import ModelRegistry

    # Resolve paths — same defaults as quant/telebot/main.py
    import os

    project_root = Path(__file__).resolve().parent
    
    # The .env may set Docker-internal paths (e.g. /app/models/production).
    # Detect and fall back to local project-relative paths when outside Docker.
    env_model_root = os.getenv("BOT_MODEL_ROOT", "")
    if env_model_root and Path(env_model_root).expanduser().exists():
        model_root = Path(env_model_root).expanduser()
    else:
        model_root = project_root / "models" / "production"

    env_registry_root = os.getenv("BOT_MODEL_REGISTRY_ROOT", "")
    if env_registry_root and Path(env_registry_root).expanduser().parent.exists():
        registry_root = Path(env_registry_root).expanduser()
    else:
        registry_root = model_root / "registry"

    logger.info("Model root: %s", model_root)
    logger.info("Registry root: %s", registry_root)

    # Find the latest model via filesystem discovery
    latest = find_latest_model(model_root)
    if latest is None:
        logger.error(
            "No model found under %s. Cannot bootstrap registry.", model_root
        )
        return False

    logger.info("Found latest model: %s", latest)

    # Check if registry already has an active version
    registry = ModelRegistry(registry_root)
    existing = registry.get_active_version()
    if existing is not None:
        logger.info(
            "Registry already has active version '%s'. Skipping bootstrap.",
            existing.version_id,
        )
        return True

    # Derive version_id from directory name
    version_id = latest.name  # e.g., "model_20260220_215919"
    if not version_id.startswith("model_"):
        version_id = f"model_{version_id}"

    logger.info("Registering version '%s' from %s ...", version_id, latest)

    try:
        registry.register_version(
            version_id=version_id,
            artifact_dir=latest,
            metrics={"source": "bootstrap", "note": "pre-upgrade legacy model"},
            tags={"source": "bootstrap"},
            description="Bootstrap registration of pre-existing model for immediate operation.",
        )
        registry.set_active_version(version_id)
        logger.info("✅ Registered and activated version '%s'", version_id)
    except Exception as e:
        logger.error("Failed to register model: %s", e)
        return False

    # Verify
    active = registry.get_active_version()
    if active is None:
        logger.error("Verification failed: no active version after registration.")
        return False

    logger.info("✅ Verified active version: %s → %s", active.version_id, active.artifact_dir)
    return True


if __name__ == "__main__":
    success = bootstrap()
    sys.exit(0 if success else 1)
