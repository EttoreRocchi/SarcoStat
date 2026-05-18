"""Core numerical primitives for SarcoStat.

This subpackage exposes circular-statistics utilities (mean vector length,
Rayleigh and V tests, bootstrap CIs, segment-sequence analysis) and
single-sarcomere morphometry (geometry, alpha angle, shape classification,
Z-line alignment). It depends only on numpy and pingouin; it has no GUI,
plotting, or I/O dependencies.
"""

from sarcostat import __version__

from ._interpretation import (
    DEFAULT_EFFECT_BANDS,
    EffectSizeBand,
    RayleighInterpreter,
    VTestInterpreter,
    interpret_rayleigh_test,
    interpret_v_test,
)
from ._serialization import Serializable, dump_json, to_json_string
from .circular_stats import (
    CircularStatisticsReport,
    SegmentData,
    SequenceMetrics,
    analyze_segment_sequence,
    bootstrap_circular_mean_ci,
    bootstrap_mvl_ci,
    calculate_circular_mean,
    calculate_circular_std,
    calculate_circular_variance,
    calculate_concentration_parameter,
    calculate_linearity_ratio,
    calculate_mean_vector_length,
    compute_circular_statistics_report,
    perform_rayleigh_test,
    perform_v_test,
)
from .sarcomere_morphometry import (
    DEFAULT_SHAPE_TOLERANCE,
    SarcomereChainMetrics,
    SarcomereGeometry,
    ShapeClassification,
    ZLineAlignment,
    analyze_sarcomere_chain,
    calculate_alpha_angle,
    calculate_h_parameter,
    calculate_sarcomere_geometry,
    calculate_z_staircase_alignment,
    classify_sarcomere_shape,
    quick_sarcomere_analysis,
)

__all__ = [
    "__version__",
    "DEFAULT_EFFECT_BANDS",
    "DEFAULT_SHAPE_TOLERANCE",
    "EffectSizeBand",
    "RayleighInterpreter",
    "VTestInterpreter",
    "interpret_rayleigh_test",
    "interpret_v_test",
    "Serializable",
    "dump_json",
    "to_json_string",
    "CircularStatisticsReport",
    "SegmentData",
    "SequenceMetrics",
    "analyze_segment_sequence",
    "bootstrap_circular_mean_ci",
    "bootstrap_mvl_ci",
    "calculate_circular_mean",
    "calculate_circular_std",
    "calculate_circular_variance",
    "calculate_concentration_parameter",
    "calculate_linearity_ratio",
    "calculate_mean_vector_length",
    "compute_circular_statistics_report",
    "perform_rayleigh_test",
    "perform_v_test",
    "SarcomereChainMetrics",
    "SarcomereGeometry",
    "ShapeClassification",
    "ZLineAlignment",
    "analyze_sarcomere_chain",
    "calculate_alpha_angle",
    "calculate_h_parameter",
    "calculate_sarcomere_geometry",
    "calculate_z_staircase_alignment",
    "classify_sarcomere_shape",
    "quick_sarcomere_analysis",
]
