from __future__ import annotations

import inspect
import warnings
from abc import ABCMeta, abstractmethod
from typing import Any, Callable, Dict, Optional, Tuple, Union, cast

import numpy as np
from sklearn.base import BaseEstimator
from sklearn.model_selection import (BaseCrossValidator, BaseShuffleSplit,
                                     PredefinedSplit, ShuffleSplit)
from sklearn.utils.validation import _num_samples, check_is_fitted

from mapie._typing import ArrayLike, NDArray
from mapie.calibrators import BaseCalibrator
from mapie.calibrators.ccp import CCPCalibrator
from mapie.conformity_scores import ConformityScore
from mapie.utils import _sample_non_null_weight, fit_estimator


class SplitCP(BaseEstimator, metaclass=ABCMeta):
    """
    Base abstract class for Split Conformal Prediction

    Parameters
    ----------
    predictor: Union[RegressorMixin, ClassifierMixin]
        Any regressor or classifier from scikit-learn API.
        (i.e. with ``fit`` and ``predict`` methods).

        By default ``"None"``.

    calibrator: Optional[Calibrator]
        A ``Calibrator`` instance used to estimate the conformity scores.

        By default ``None``.

    cv: Optional[Union[int, str, ShuffleSplit, PredefinedSplit]]
        The splitting strategy for computing conformity scores.
        Choose among:

        - Any splitter (``ShuffleSplit`` or ``PredefinedSplit``)
        with ``n_splits=1``.
        - ``"prefit"``, assumes that ``predictor`` has been fitted already.
          All data provided in the ``calibrate`` method is then used
          for the calibration.
          The user has to take care manually that data used for model fitting
          and calibration (the data given in the ``calibrate`` method)
          are disjoint.
        - ``"split"`` or ``None``: divide the data into training and
          calibration subsets (using the default ``calib_size``=0.3).
          The splitter used is the following:
            ``sklearn.model_selection.ShuffleSplit`` with ``n_splits=1``.

        By default ``None``.

    conformity_score: Optional[ConformityScore]
        ConformityScore instance.
        It defines the link between the observed values, the predicted ones
        and the conformity scores.

        - Can be any ``ConformityScore`` class
        - ``None`` is associated with a default value defined by the subclass

        By default ``None``.

    alpha: Optional[float]
        Between ``0.0`` and ``1.0``, represents the risk level of the
        confidence interval.
        Lower ``alpha`` produce larger (more conservative) prediction
        intervals.
        ``alpha`` is the complement of the target coverage level.

        By default ``None``

    random_state: Optional[int]
        Integer used to set the numpy seed, to get reproducible calibration
        results.
        If ``None``, the prediction intervals will be stochastics, and will
        change if you refit the calibration (even if no arguments have change).

        WARNING: If ``random_state`` is not ``None``, ``np.random.seed`` will
        be changed, which will reset the seed for all the other random
        number generators. It may have an impact on the rest of your code.

        By default ``None``.
    """

    default_sym_ = True
    fit_attributes = ["predictor_"]
    calib_attributes = ["calibrator_"]

    cv: Optional[
            Union[str, BaseCrossValidator, BaseShuffleSplit]
        ]
    alpha: Optional[float]

    @abstractmethod
    def __init__(
        self,
        predictor: Optional[BaseEstimator] = None,
        calibrator: Optional[CCPCalibrator] = None,
        cv: Optional[
            Union[str, BaseCrossValidator, BaseShuffleSplit]
        ] = None,
        alpha: Optional[float] = None,
        conformity_score: Optional[ConformityScore] = None,
        random_state: Optional[int] = None,
    ) -> None:
        """
        Initialisation
        """

    @abstractmethod
    def _check_fit_parameters(self) -> BaseEstimator:
        """
        Check and replace default value of ``predictor`` and ``cv`` arguments.
        """

    @abstractmethod
    def _check_calibrate_parameters(self) -> Tuple[
        ConformityScore, BaseCalibrator
    ]:
        """
        Check and replace default ``conformity_score``, ``alpha`` and
        ``calibrator`` arguments.
        """

    def _check_cv(
        self,
        cv: Optional[Union[str, BaseCrossValidator, BaseShuffleSplit]] = None,
        test_size: Optional[Union[int, float]] = None,
    ) -> Union[str, BaseCrossValidator, BaseShuffleSplit]:
        """
        Check if ``cv`` is ``None``, ``"prefit"``, ``"split"``,
        or ``BaseShuffleSplit``/``BaseCrossValidator`` with ``n_splits``=1.
        Return a ``ShuffleSplit`` instance ``n_splits``=1
        if ``None`` or ``"split"``.
        Else raise error.

        Parameters
        ----------
        cv: Optional[Union[str, BaseCrossValidator, BaseShuffleSplit]]
            Cross-validator to check, by default ``None``.

        test_size: float
            If float, should be between 0.0 and 1.0 and represent the
            proportion of the dataset to include in the test split.
            If cv is not ``"split"``, ``test_size`` is ignored.

            By default ``None``.

        Returns
        -------
        Union[str, PredefinedSplit, ShuffleSplit]
            The cast `cv` parameter.

        Raises
        ------
        ValueError
            If the cross-validator is not valid.
        """
        if cv is None or cv == "split":
            return ShuffleSplit(
                n_splits=1, test_size=test_size, random_state=self.random_state
            )
        elif (isinstance(cv, (PredefinedSplit, ShuffleSplit))
              and cv.get_n_splits() == 1):
            return cv
        elif cv == "prefit":
            return cv
        else:
            raise ValueError(
                "Invalid cv argument.  Allowed values are None, 'prefit', "
                "'split' or a ShuffleSplit/PredefinedSplit object with "
                "``n_splits=1``."
            )

    def _check_alpha(self, alpha: Optional[float] = None) -> None:
        """
        Check alpha

        Parameters
        ----------
        alpha: Optional[float]
            Can be a float between 0 and 1, represent the uncertainty
            of the confidence interval. Lower alpha produce
            larger (more conservative) prediction intervals.
            alpha is the complement of the target coverage level.

        Raises
        ------
        ValueError
            If alpha is not ``None`` or a float between 0 and 1.
        """
        if alpha is None:
            return
        if isinstance(alpha, float):
            alpha = alpha
        else:
            raise ValueError(
                "Invalid alpha. Allowed values are float."
            )

        if alpha < 0 or alpha > 1:
            raise ValueError("Invalid alpha. "
                             "Allowed values are between 0 and 1.")

    def _get_method_arguments(
        self, method: Callable, local_vars: Dict[str, Any],
        kwargs: Optional[Dict],
    ) -> Dict:
        """
        Return a dictionnary with ``calibrator_.fit`` arguments

        Parameters
        ----------
        method: Callable
            method for which to check the signature

        local_vars : Dict[str, Any]
            Dictionnary of available variables

        kwargs : Optional[Dict]
            Other arguments

        exclude_args : Optional[List[str]]
            Arguments to exclude

        Returns
        -------
        Dict
            dictinnary of arguments
        """
        self_attrs = {k: v for k, v in self.__dict__.items()}
        sig = inspect.signature(method)

        method_kwargs: Dict[str, Any] = {}
        for param in sig.parameters.values():
            # We ignore the arguments like *args and **kwargs of the method
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
                param_name = param.name
                if kwargs is not None and param_name in kwargs:
                    method_kwargs[param_name] = kwargs[param_name]
                elif param_name in self_attrs:
                    method_kwargs[param_name] = self_attrs[param_name]
                elif param_name in local_vars:
                    method_kwargs[param_name] = local_vars[param_name]

        return method_kwargs

    def fit_predictor(
        self,
        X: ArrayLike,
        y: ArrayLike,
        sample_weight: Optional[ArrayLike] = None,
        groups: Optional[ArrayLike] = None,
        **fit_params,
    ) -> SplitCP:
        """
        Fit the predictor if ``cv`` argument is not ``"prefit"``

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Training data.

        y: ArrayLike of shape (n_samples,)
            Training labels.

        sample_weight: Optional[ArrayLike] of shape (n_samples,)
            Sample weights for fitting the out-of-fold models.
            If ``None``, then samples are equally weighted.
            If some weights are null,
            their corresponding observations are removed
            before the fitting process and hence have no residuals.
            If weights are non-uniform, residuals are still uniformly weighted.

            By default ``None``.

        groups: Optional[ArrayLike] of shape (n_samples,)
            Group labels for the samples used while splitting the dataset into
            train/test set.

            By default ``None``.

        **fit_params: dict
            Additional fit parameters for the predictor.

        Returns
        -------
        SplitCP
            self
        """
        predictor = self._check_fit_parameters()

        if self.cv != 'prefit':
            self.cv = cast(BaseCrossValidator, self.cv)

            train_index, _ = list(self.cv.split(X, y, groups))[0]

            (
                X_train, y_train, _, sample_weight_train, _
            ) = _sample_non_null_weight(X, y, sample_weight, train_index)

            self.predictor_ = fit_estimator(
                predictor, X_train, y_train,
                sample_weight=sample_weight_train, **fit_params
            )
        else:
            self.predictor_ = predictor
        return self

    def fit_calibrator(
        self,
        X: ArrayLike,
        y: ArrayLike,
        sample_weight: Optional[ArrayLike] = None,
        groups: Optional[ArrayLike] = None,
        **calib_kwargs,
    ) -> SplitCP:
        """
        Fit the calibrator with (``X``, ``y`` and ``z``)
        and the new value ``alpha`` value, if not ``None``

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Training data.

        y: ArrayLike of shape (n_samples,)
            Training labels.

        sample_weight: Optional[ArrayLike] of shape (n_samples,)
            Sample weights of the data, used as weights in the
            calibration process.

            By default ``None``.

        groups: Optional[ArrayLike] of shape (n_samples,)
            Group labels for the samples used while splitting the dataset into
            train/test set.

            By default ``None``.

        calib_kwargs: Dict
            Other argument, used in sklear.optimize.minimize

        Returns
        -------
        SplitCP
            self
        """
        self._check_fit_parameters()
        self.conformity_score_, calibrator = self._check_calibrate_parameters()
        check_is_fitted(self, self.fit_attributes)

        if self.alpha is None:
            warnings.warn("No calibration is done, because alpha is None.")
            return self

        # Get training and calibration sets
        if self.cv != 'prefit':
            self.cv = cast(BaseCrossValidator, self.cv)

            train_index, calib_index = list(self.cv.split(X, y, groups))[0]
        else:
            train_index, calib_index = (np.array([], dtype=int),
                                        np.arange(_num_samples(X)))

        z = cast(Optional[ArrayLike], calib_kwargs.get("z", None))
        (
            X_train, y_train, z_train, sample_weight_train, train_index
        ) = _sample_non_null_weight(X, y, sample_weight, train_index, z)
        (
            X_calib, y_calib, z_calib, sample_weight_calib, calib_index
        ) = _sample_non_null_weight(X, y, sample_weight, calib_index, z)

        # Compute conformity scores
        y_pred_calib = self.predict_score(X_calib)

        conformity_scores_calib = self.conformity_score_.get_conformity_scores(
            X_calib, y_calib, y_pred_calib
        )

        calib_arguments = self._get_method_arguments(
            calibrator.fit,
            dict(zip([
                "X", "y", "sample_weight", "groups",
                "y_pred_calib", "conformity_scores_calib",
                "X_train", "y_train", "z_train",
                "sample_weight_train", "train_index",
                "X_calib", "y_calib", "z_calib",
                "sample_weight_calib", "calib_index",
             ],
             [
                X, y, sample_weight, groups,
                y_pred_calib, conformity_scores_calib,
                X_train, y_train, z_train, sample_weight_train, train_index,
                X_calib, y_calib, z_calib, sample_weight_calib, calib_index,
            ])),
            calib_kwargs
        )

        self.calibrator_ = calibrator.fit(
            **calib_arguments,
            **(calib_kwargs if calib_kwargs is not None else {})
        )

        return self

    def fit(
        self,
        X: ArrayLike,
        y: ArrayLike,
        sample_weight: Optional[ArrayLike] = None,
        groups: Optional[ArrayLike] = None,
        fit_kwargs: Optional[Dict] = None,
        calib_kwargs: Optional[Dict] = None
    ) -> SplitCP:
        """
        Fit the predictor (if ``cv`` is not ``"prefit"``)
        and fit the calibration.

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Training data.

        y: ArrayLike of shape (n_samples,)
            Training labels.

        z: Optional[ArrayLike] of shape (n_calib_samples, n_exog_features)
            Exogenous variables

            By default ``None``

        alpha: Optional[float]
            Between ``0.0`` and ``1.0``, represents the risk level of the
            confidence interval.
            Lower ``alpha`` produce larger (more conservative) prediction
            intervals.
            ``alpha`` is the complement of the target coverage level.

            If ``None``, the calibration will be done using the ``alpha``value
            set in the initialisation. Else, the new value will overwrite the
            old one.

            By default ``None``

        sample_weight: Optional[ArrayLike] of shape (n_samples,)
            Sample weights for fitting the out-of-fold models and the
            conformalisation process.
            If ``None``, then samples are equally weighted.
            If some weights are null,
            their corresponding observations are removed
            before the fitting process and hence have no residuals.
            If weights are non-uniform, residuals are still uniformly weighted.

            By default ``None``.

        groups: Optional[ArrayLike] of shape (n_samples,)
            Group labels for the samples used while splitting the dataset into
            train/test set.

            By default ``None``.

        fit_params: dict
            Additional fit parameters for the predictor, used as kwargs.

        calib_params: dict
            Additional fit parameters for the calibrator, used as kwargs.

        Returns
        -------
        SplitCP
            self
        """
        self.fit_predictor(X, y, sample_weight, groups,
                           **(fit_kwargs if fit_kwargs is not None else {}))
        self.fit_calibrator(X, y, sample_weight, groups,
                            **(calib_kwargs
                               if calib_kwargs is not None else {}))
        return self

    def predict(
        self,
        X: ArrayLike,
        **kwargs,
    ) -> Union[NDArray, Tuple[NDArray, NDArray]]:
        """
        Predict target on new samples with confidence intervals.

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Test data.

        Returns
        -------
        Union[NDArray, Tuple[NDArray, NDArray]]
            - Predictions : NDArray of shape (n_samples,)
            if ``alpha`` is ``None``.
            - Predictions and confidence intervals
            if ``alpha`` is not ``None``.
        """
        check_is_fitted(self, self.fit_attributes)
        y_pred = self.predict_score(X)

        if self.alpha is None:
            return y_pred

        check_is_fitted(self, self.calib_attributes)

        # Fit the calibrator
        bounds_arguments = self._get_method_arguments(
            self.calibrator_.predict, {}, kwargs,
        )

        y_bounds = self.predict_bounds(X, y_pred, **bounds_arguments)

        return self.predict_best(y_pred), y_bounds

    @abstractmethod
    def predict_score(
        self, X: ArrayLike
    ) -> NDArray:
        """
        Compute the predictor prediction, used to compute the
        conformity scores.

        Parameters
        ----------
        X: ArrayLike
            Observed values.

        Returns
        -------
        NDArray
            Scores (usually ``y_pred`` in regression and ``y_pred_proba``
            in classification)
        """

    @abstractmethod
    def predict_bounds(
        self,
        X: ArrayLike,
        y_pred: NDArray,
        **predict_kwargs,
    ) -> NDArray:
        """
        Compute the bounds, using the fitted ``_calibrator``.

        Parameters
        ----------
        X: ArrayLike
            Observed values.

        y_pred: 2D NDArray
            Predicted scores (target)

        z: Optional[ArrayLike]
            Exogenous variables

        Returns
        -------
        NDArray
            Bounds (or prediction set in classification)
        """

    @abstractmethod
    def predict_best(self, y_pred: NDArray) -> NDArray:
        """
        Compute the best prediction, in an array of shape (n_samples, )

        Parameters
        ----------
        y_pred: NDArray
            Prediction scores (can be the prediction, the probas, ...)

        z: Optional[ArrayLike]
            Exogenous variables

        Returns
        -------
        NDArray
            predictions
        """
