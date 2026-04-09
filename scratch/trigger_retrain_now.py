"""Trigger immediate retrain with BTC returns fix applied."""
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from quant_v2.research.scheduled_retrain import retrain_and_promote

model_root = Path(os.getenv("BOT_MODEL_ROOT", "/app/models/production"))
registry_root = Path(os.getenv("BOT_MODEL_REGISTRY_ROOT", str(model_root / "registry")))

print("=" * 60)
print("TRIGGERING IMMEDIATE RETRAIN")
print("Fixes: weighted sentiment + BTC returns injection")
print("=" * 60)

version_id = retrain_and_promote(
    model_root=model_root,
    registry_root=registry_root,
    train_months=12,          # Full production retrain
    min_accuracy=0.525,
    extra_symbols=["ETHUSDT", "BNBUSDT"],
)

if version_id:
    print(f"\n✅ RETRAIN SUCCESS: promoted {version_id}")
else:
    print("\n❌ RETRAIN FAILED: model did not pass accuracy gate")
