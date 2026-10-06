from __future__ import annotations

from pathlib import Path

PHASE3_PHASE_ID = "v2-phase-03"
PHASE3_ARTIFACT_SCHEMA_VERSION = "spy-v2-phase3-research-artifacts-v1"
PHASE3_EXPERIMENT_ID_VERSION = "spy-v2-phase3-experiment-id-v1"
PHASE3_FOLD_ID_VERSION = "spy-v2-phase3-fold-id-v1"
PHASE3_FOLD_MANIFEST_ID_VERSION = "spy-v2-phase3-fold-manifest-id-v1"

FEATURE_WARMUP_ROWS = 20
ENTRY_OFFSET_SESSIONS = 1
EXIT_OFFSET_SESSIONS = 6
MANDATORY_GAP_SESSIONS = 5
BOUNDARY_EXCLUSION_SESSIONS = 6

MINIMUM_INITIAL_TRAINING_ROWS = 756
DEFAULT_ASSESSMENT_WINDOW_ROWS = 126
DEFAULT_STEP_ROWS = 63
MINIMUM_FINAL_ASSESSMENT_ROWS = 63

WALK_FORWARD_FOLD_POLICY_ID = "phase3-expanding-window-756-train-126-assess-63-step-6-purge-v1"
PHASE3_CLASSIFICATION_SELECTION_RULE_ID = (
    "phase3-median-roc-log-loss-brier-worst-quartile-simplicity-v1"
)
DIAGNOSTIC_THRESHOLD_POLICY_ID = "phase3-fixed-diagnostic-threshold-0.5-v1"
NO_CALIBRATION_POLICY_ID = "phase3-no-calibration-v1"

RESEARCH_ARTIFACT_ROOT = Path("artifacts/research")
