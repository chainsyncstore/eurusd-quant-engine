"""Multi-horizon model ensemble with weighted probability combination."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from quant_v2.models.trainer import TrainedModel, load_model
from quant_v2.models.predictor import predict_proba_with_uncertainty

logger = logging.getLogger(__name__)

# Decay weights: shorter horizon gets more weight
DEFAULT_HORIZON_WEIGHTS = {2: 0.45, 4: 0.35, 8: 0.20}


class HorizonEnsemble:
    """Combine multiple horizon models into a single probability + uncertainty."""

    def __init__(
        self,
        models: dict[int, TrainedModel],
        weights: dict[int, float] | None = None,
    ) -> None:
        self.models = models
        self.weights = weights or DEFAULT_HORIZON_WEIGHTS
        # Normalize weights to sum to 1.0
        total = sum(self.weights.get(h, 0.0) for h in self.models)
        if total > 0:
            self.weights = {h: self.weights.get(h, 0.0) / total for h in self.models}

    @classmethod
    def from_directory(cls, artifact_dir: Path) -> "HorizonEnsemble | None":
        """Load all horizon models from a registry artifact directory."""
        models: dict[int, TrainedModel] = {}
        for horizon in (2, 4, 8):
            for suffix in (f"model_{horizon}m.pkl", f"model_{horizon}m.joblib"):
                path = artifact_dir / suffix
                if path.exists():
                    try:
                        models[horizon] = load_model(path)
                    except Exception as e:
                        logger.warning("Failed to load horizon=%d model: %s", horizon, e)
                    break
        if not models:
            return None
        return cls(models)

    def predict(self, X: pd.DataFrame) -> tuple[float, float]:
        """Return weighted ensemble (probability, uncertainty) for one row.

        Falls back gracefully if some horizon models are missing features.
        """
        probas: list[float] = []
        uncertainties: list[float] = []
        weights_used: list[float] = []

        for horizon, model in self.models.items():
            try:
                # Align features: fill missing with 0.0
                missing = set(model.feature_names) - set(X.columns)
                X_aligned = X.copy()
                for col in missing:
                    X_aligned[col] = 0.0
                X_ordered = X_aligned[model.feature_names]

                p, u = predict_proba_with_uncertainty(model, X_ordered)
                probas.append(float(p[0]))
                uncertainties.append(float(u[0]))
                weights_used.append(self.weights.get(horizon, 0.0))
            except Exception as e:
                logger.warning("Horizon=%d prediction failed: %s", horizon, e)
                continue

        if not probas:
            return 0.5, 1.0  # total uncertainty if all models failed

        w = np.array(weights_used)
        w = w / w.sum()
        ensemble_proba = float(np.dot(w, probas))
        ensemble_uncertainty = float(np.dot(w, uncertainties))

        # Agreement bonus: if all models agree on direction, reduce uncertainty
        directions = [1 if p > 0.5 else 0 for p in probas]
        if len(set(directions)) == 1 and len(directions) > 1:
            ensemble_uncertainty *= 0.80  # 20% uncertainty reduction for agreement

        return (
            float(np.clip(ensemble_proba, 0.0, 1.0)),
            float(np.clip(ensemble_uncertainty, 0.0, 1.0)),
        )

    @property
    def horizon_count(self) -> int:
        return len(self.models)
