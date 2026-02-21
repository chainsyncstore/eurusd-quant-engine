"""
GMM-based regime detection.

Fits a Gaussian Mixture Model on volatility/momentum features
to identify distinct market regimes. Fitted on training data only;
applied forward without refit during test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
import joblib
from pathlib import Path

from quant.config import get_research_config

logger = logging.getLogger(__name__)

# Base regime features (always available from OHLCV)
_BASE_REGIME_FEATURES = [
    "atr_14",
    "rolling_std_20",
    "roc_5",
    "ema_slope_5",
    "vol_ratio",
]

# Additional regime features for crypto mode
_CRYPTO_REGIME_FEATURES = [
    "funding_rate_zscore",
    "oi_zscore",
    "taker_buy_ratio_ma8",
]


def _get_regime_features(df: pd.DataFrame) -> list[str]:
    """Select regime input features based on available columns."""
    features = list(_BASE_REGIME_FEATURES)
    for f in _CRYPTO_REGIME_FEATURES:
        if f in df.columns:
            features.append(f)
    return features


# Backward-compatible alias
REGIME_INPUT_FEATURES = _BASE_REGIME_FEATURES


@dataclass
class RegimeModel:
    """Container for a fitted regime model."""

    gmm: GaussianMixture
    scaler: StandardScaler
    n_regimes: int
    input_features: list = field(default_factory=lambda: list(_BASE_REGIME_FEATURES))


def fit(df: pd.DataFrame, n_regimes: Optional[int] = None) -> RegimeModel:
    """
    Fit a GMM regime model on training data.

    Automatically includes crypto-specific features when available.

    Args:
        df: DataFrame with feature columns (output of feature pipeline).
        n_regimes: Number of regimes (default from config).

    Returns:
        Fitted RegimeModel.
    """
    cfg = get_research_config()
    n = n_regimes or cfg.n_regimes

    input_features = _get_regime_features(df)
    X = df[input_features].values

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Fit GMM
    gmm = GaussianMixture(
        n_components=n,
        covariance_type="full",
        n_init=5,
        max_iter=200,
        random_state=42,
    )
    gmm.fit(X_scaled)

    logger.info(
        "Fitted GMM with %d regimes on %d samples (BIC: %.1f, features: %s)",
        n,
        len(X_scaled),
        gmm.bic(X_scaled),
        input_features,
    )

    return RegimeModel(gmm=gmm, scaler=scaler, n_regimes=n, input_features=input_features)


def predict(model: RegimeModel, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """
    Predict regime labels and probabilities.

    Args:
        model: Fitted RegimeModel.
        df: DataFrame with regime input feature columns.

    Returns:
        (regime_labels, regime_probabilities) — labels are int array,
        probabilities is (n_samples, n_regimes) array.
    """
    missing = [f for f in model.input_features if f not in df.columns]
    if missing:
        logger.warning("Missing regime features: %s — filling with scaler mean (neutral)", missing)
        df = df.copy()
        for f in missing:
            idx = model.input_features.index(f)
            df[f] = model.scaler.mean_[idx]

    X = df[model.input_features].values
    X_scaled = model.scaler.transform(X)

    labels = model.gmm.predict(X_scaled)
    probas = model.gmm.predict_proba(X_scaled)

    return labels, probas


def add_regime_columns(
    df: pd.DataFrame, model: RegimeModel
) -> pd.DataFrame:
    """
    Add regime label and max probability columns to DataFrame.

    Args:
        df: Feature DataFrame.
        model: Fitted RegimeModel.

    Returns:
        DataFrame with 'regime' and 'regime_prob' columns.
    """
    labels, probas = predict(model, df)
    out = df.copy()
    out["regime"] = labels
    out["regime_prob"] = probas.max(axis=1)
    return out


def save_model(model: RegimeModel, path: Path) -> None:
    """Serialize regime model to disk."""
    joblib.dump(model, path)
    logger.info("Regime model saved: %s", path)


def load_model(path: Path) -> RegimeModel:
    """Deserialize regime model from disk."""
    model = joblib.load(path)
    logger.info("Regime model loaded: %s", path)
    return model
