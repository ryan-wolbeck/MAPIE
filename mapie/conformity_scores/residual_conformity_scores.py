from .bounds import (  # noqa: F401
    AbsoluteConformityScore, GammaConformityScore, ResidualNormalisedScore
)

import warnings
warnings.warn(
    "Imports from mapie.conformity_scores.residual_conformity_scores " +
    "are deprecated. Please use from mapie.conformity_scores",
    DeprecationWarning
)
