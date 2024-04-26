from __future__ import annotations

import warnings
from typing import Any, Iterable, Optional, Tuple, Union, cast

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.model_selection import BaseCrossValidator, ShuffleSplit
from sklearn.preprocessing import LabelEncoder, label_binarize
from sklearn.utils import _safe_indexing, check_random_state
from sklearn.utils.multiclass import (check_classification_targets,
                                      type_of_target)
from sklearn.utils.validation import (_check_y, _num_samples, check_is_fitted,
                                      indexable)

from ._machine_precision import EPSILON
from ._typing import ArrayLike, NDArray
from .estimator.estimator_draft import EnsembleClassifier
from .metrics import classification_mean_width_score
from .utils import (check_alpha, check_alpha_and_n_samples, check_cv,
                    check_estimator_classification, check_n_features_in,
                    check_n_jobs, check_null_weight, check_verbose,
                    compute_quantiles, fix_number_of_classes)


class MapieClassifier(BaseEstimator, ClassifierMixin):
    """
    Prediction sets for classification.

    This class implements several conformal prediction strategies for
    estimating prediction sets for classification. Instead of giving a
    single predicted label, the idea is to give a set of predicted labels
    (or prediction sets) which come with mathematically guaranteed coverages.

    Parameters
    ----------
    estimator: Optional[ClassifierMixin]
        Any classifier with scikit-learn API
        (i.e. with fit, predict, and predict_proba methods), by default None.
        If ``None``, estimator defaults to a ``LogisticRegression`` instance.

    method: Optional[str]
        Method to choose for prediction interval estimates.
        Choose among:

        - ``"naive"``, sum of the probabilities until the 1-alpha thresold.

        - ``"lac"`` (formerly called ``"score"``), Least Ambiguous set-valued
          Classifier. It is based on the the scores
          (i.e. 1 minus the softmax score of the true label)
          on the calibration set. See [1] for more details.

        - ``"aps"`` (formerly called "cumulated_score"), Adaptive Prediction
          Sets method. It is based on the sum of the softmax outputs of the
          labels until the true label is reached, on the calibration set.
          See [2] for more details.

        - ``"raps"``, Regularized Adaptive Prediction Sets method. It uses the
          same technique as ``"aps"`` method but with a penalty term
          to reduce the size of prediction sets. See [3] for more
          details. For now, this method only works with ``"prefit"`` and
          ``"split"`` strategies.

        - ``"top_k"``, based on the sorted index of the probability of the true
          label in the softmax outputs, on the calibration set. In case two
          probabilities are equal, both are taken, thus, the size of some
          prediction sets may be different from the others. See [3] for
          more details.

        By default ``"lac"``.

    cv: Optional[str]
        The cross-validation strategy for computing scores.
        It directly drives the distinction between jackknife and cv variants.
        Choose among:

        - ``None``, to use the default 5-fold cross-validation
        - integer, to specify the number of folds.
          If equal to -1, equivalent to
          ``sklearn.model_selection.LeaveOneOut()``.
        - CV splitter: any ``sklearn.model_selection.BaseCrossValidator``
          Main variants are:
          - ``sklearn.model_selection.LeaveOneOut`` (jackknife),
          - ``sklearn.model_selection.KFold`` (cross-validation)
        - ``"split"``, does not involve cross-validation but a division
          of the data into training and calibration subsets. The splitter
          used is the following: ``sklearn.model_selection.ShuffleSplit``.
        - ``"prefit"``, assumes that ``estimator`` has been fitted already.
          All data provided in the ``fit`` method is then used
          to calibrate the predictions through the score computation.
          At prediction time, quantiles of these scores are used to estimate
          prediction sets.

        By default ``None``.

    test_size: Optional[Union[int, float]]
        If float, should be between 0.0 and 1.0 and represent the proportion
        of the dataset to include in the test split. If int, represents the
        absolute number of test samples. If None, it will be set to 0.1.

        If cv is not ``"split"``, ``test_size`` is ignored.

        By default ``None``.

    n_jobs: Optional[int]
        Number of jobs for parallel processing using joblib
        via the "locky" backend.
        At this moment, parallel processing is disabled.
        If ``-1`` all CPUs are used.
        If ``1`` is given, no parallel computing code is used at all,
        which is useful for debugging.
        For n_jobs below ``-1``, ``(n_cpus + 1 + n_jobs)`` are used.
        None is a marker for `unset` that will be interpreted as ``n_jobs=1``
        (sequential execution).

        By default ``None``.

    random_state: Optional[Union[int, RandomState]]
        Pseudo random number generator state used for random uniform sampling
        for evaluation quantiles and prediction sets.
        Pass an int for reproducible output across multiple function calls.

        By default ``None``.

    verbose: int, optional
        The verbosity level, used with joblib for multiprocessing.
        At this moment, parallel processing is disabled.
        The frequency of the messages increases with the verbosity level.
        If it more than ``10``, all iterations are reported.
        Above ``50``, the output is sent to stdout.

        By default ``0``.

    Attributes
    ----------
    valid_methods: List[str]
        List of all valid methods.

    single_estimator_: sklearn.ClassifierMixin
        Estimator fitted on the whole training set.

    n_features_in_: int
        Number of features passed to the fit method.

    conformity_scores_: ArrayLike of shape (n_samples_train)
        The conformity scores used to calibrate the prediction sets.

    quantiles_: ArrayLike of shape (n_alpha)
        The quantiles estimated from ``conformity_scores_`` and alpha values.

    References
    ----------
    [1] Mauricio Sadinle, Jing Lei, and Larry Wasserman.
    "Least Ambiguous Set-Valued Classifiers with Bounded Error Levels.",
    Journal of the American Statistical Association, 114, 2019.

    [2] Yaniv Romano, Matteo Sesia and Emmanuel J. Candès.
    "Classification with Valid and Adaptive Coverage."
    NeurIPS 202 (spotlight) 2020.

    [3] Anastasios Nikolas Angelopoulos, Stephen Bates, Michael Jordan
    and Jitendra Malik.
    "Uncertainty Sets for Image Classifiers using Conformal Prediction."
    International Conference on Learning Representations 2021.

    Examples
    --------
    >>> import numpy as np
    >>> from sklearn.naive_bayes import GaussianNB
    >>> from mapie.classification import MapieClassifier
    >>> X_toy = np.arange(9).reshape(-1, 1)
    >>> y_toy = np.stack([0, 0, 1, 0, 1, 2, 1, 2, 2])
    >>> clf = GaussianNB().fit(X_toy, y_toy)
    >>> mapie = MapieClassifier(estimator=clf, cv="prefit").fit(X_toy, y_toy)
    >>> _, y_pi_mapie = mapie.predict(X_toy, alpha=0.2)
    >>> print(y_pi_mapie[:, :, 0])
    [[ True False False]
     [ True False False]
     [ True  True False]
     [ True  True False]
     [False  True False]
     [False  True  True]
     [False  True  True]
     [False False  True]
     [False False  True]]
    """

    raps_valid_cv_ = ["prefit", "split"]
    valid_methods_ = [
        "naive", "score", "lac", "cumulated_score", "aps", "top_k", "raps"
    ]
    fit_attributes = [
        "n_features_in_",
        "conformity_scores_",
        "classes_",
        "label_encoder_"
    ]

    def __init__(
        self,
        estimator: Optional[ClassifierMixin] = None,
        method: str = "lac",
        cv: Optional[Union[int, str, BaseCrossValidator]] = None,
        test_size: Optional[Union[int, float]] = None,
        n_jobs: Optional[int] = None,
        random_state: Optional[Union[int, np.random.RandomState]] = None,
        verbose: int = 0
    ) -> None:
        print()
        print("use of _init")
        self.estimator = estimator
        print("estimator", estimator)
        self.method = method
        print("method:", method)
        self.cv = cv
        print("cv:", cv)
        self.test_size = test_size
        print("test_size:", test_size)
        self.n_jobs = n_jobs
        print("n_jobs:", n_jobs)
        self.random_state = random_state
        print("random_state:", random_state)
        self.verbose = verbose
        print("verbose:", verbose)
        print()

    def _check_parameters(self) -> None:
        """
        Perform several checks on input parameters.

        Raises
        ------
        ValueError
            If parameters are not valid.
        """
        print()
        print("use of _check_parameters")
        if self.method not in self.valid_methods_:
            raise ValueError(
                "Invalid method. "
                f"Allowed values are {self.valid_methods_}."
            )
        check_n_jobs(self.n_jobs)
        check_verbose(self.verbose)
        check_random_state(self.random_state)
        self._check_depreciated()
        self._check_raps()

    def _check_depreciated(self) -> None:
        """
        Check if the chosen method is outdated.

        Raises
        ------
        Warning
            If method is ``"score"`` (not ``"lac"``) or
            if method is ``"cumulated_score"`` (not ``"aps"``).
        """
        print()
        print("use of _check_depreciated")
        if self.method == "score":
            warnings.warn(
                "WARNING: Deprecated method. "
                + "The method \"score\" is outdated. "
                + "Prefer to use \"lac\" instead to keep "
                + "the same behavior in the next release.",
                DeprecationWarning
            )
        if self.method == "cumulated_score":
            warnings.warn(
                "WARNING: Deprecated method. "
                + "The method \"cumulated_score\" is outdated. "
                + "Prefer to use \"aps\" instead to keep "
                + "the same behavior in the next release.",
                DeprecationWarning
            )

    def _check_target(self, y: ArrayLike) -> None:
        """
        Check that if the type of target is binary,
        (then the method have to be ``"lac"``), or multi-class.

        Parameters
        ----------
        y: NDArray of shape (n_samples,)
            Training labels.

        Raises
        ------
        ValueError
            If type of target is binary and method is not ``"lac"``
            or ``"score"`` or if type of target is not multi-class.
        """
        print()
        print("use of _check_target")
        check_classification_targets(y)
        if type_of_target(y) == "binary" and \
                self.method not in ["score", "lac"]:
            raise ValueError(
                "Invalid method for binary target. "
                "Your target is not of type multiclass and "
                "allowed values for binary type are "
                f"{['score', 'lac']}."
            )
         
    def _check_raps(self):
        """
        Check that if the method used is ``"raps"``, then
        the cross validation strategy is ``"prefit"``.

        Raises
        ------
        ValueError
            If ``method`` is ``"raps"`` and ``cv`` is not ``"prefit"``.
        """
        print()
        print("use of _check_raps")
        if (self.method == "raps") and (
            (self.cv not in self.raps_valid_cv_)
            or isinstance(self.cv, ShuffleSplit)
        ):
            raise ValueError(
                "RAPS method can only be used "
                f"with cv in {self.raps_valid_cv_}."
            )

    def _check_include_last_label(
        self,
        include_last_label: Optional[Union[bool, str]]
    ) -> Optional[Union[bool, str]]:
        """
        Check if ``include_last_label`` is a boolean or a string.
        Else raise error.

        Parameters
        ----------
        include_last_label: Optional[Union[bool, str]]
            Whether or not to include last label in
            prediction sets for the ``"aps"`` method. Choose among:

            - ``False``, does not include label whose cumulated score is just
            over the quantile.
            - ``True``, includes label whose cumulated score is just over the
            quantile, unless there is only one label in the prediction set.
            - ``"randomized"``, randomly includes label whose cumulated score
            is just over the quantile based on the comparison of a uniform
            number and the difference between the cumulated score of the last
            label and the quantile.

        Returns
        -------
        Optional[Union[bool, str]]

        Raises
        ------
        ValueError
            "Invalid include_last_label argument. "
            "Should be a boolean or 'randomized'."
        """
        print()
        print("use of _check_include_last_label")
        if (
            (not isinstance(include_last_label, bool)) and
            (not include_last_label == "randomized")
        ):
            raise ValueError(
                "Invalid include_last_label argument. "
                "Should be a boolean or 'randomized'."
            )
        else:
            return include_last_label

    def _check_proba_normalized(
        self,
        y_pred_proba: ArrayLike,
        axis: int = 1
    ) -> NDArray:
        """
        Check if, for all the observations, the sum of
        the probabilities is equal to one.

        Parameters
        ----------
        y_pred_proba: ArrayLike of shape
            (n_samples, n_classes) or
            (n_samples, n_train_samples, n_classes)
            Softmax output of a model.

        Returns
        -------
        ArrayLike of shape (n_samples, n_classes)
            Softmax output of a model if the scores all sum
            to one.

        Raises
        ------
            ValueError
            If the sum of the scores is not equal to one.
        """
        print()
        print("use of _check_proba_normalized")
        np.testing.assert_allclose(
            np.sum(y_pred_proba, axis=axis),
            1,
            err_msg="The sum of the scores is not equal to one.",
            rtol=1e-5
        )
        y_pred_proba = cast(NDArray, y_pred_proba).astype(np.float64)
        return y_pred_proba

    def _get_last_index_included(
        self,
        y_pred_proba_cumsum: NDArray,
        threshold: NDArray,
        include_last_label: Optional[Union[bool, str]]
    ) -> NDArray:
        """
        Return the index of the last included sorted probability
        depending if we included the first label over the quantile
        or not.

        Parameters
        ----------
        y_pred_proba_cumsum: NDArray of shape (n_samples, n_classes)
            Cumsumed probabilities in the original order.

        threshold: NDArray of shape (n_alpha,) or shape (n_samples_train,)
            Threshold to compare with y_proba_last_cumsum, can be either:

            - the quantiles associated with alpha values when
              ``cv`` == "prefit", ``cv`` == "split"
              or ``agg_scores`` is "mean"
            - the conformity score from training samples otherwise
              (i.e., when ``cv`` is a CV splitter and
              ``agg_scores`` is "crossval")

        include_last_label: Union[bool, str]
            Whether or not include the last label. If 'randomized',
            the last label is included.

        Returns
        -------
        NDArray of shape (n_samples, n_alpha)
            Index of the last included sorted probability.
        """
        print()
        print("use of _get_last_index_included")
        if (
            (include_last_label) or
            (include_last_label == 'randomized')
        ):
            y_pred_index_last = (
                    np.ma.masked_less(
                        y_pred_proba_cumsum
                        - threshold[np.newaxis, :],
                        -EPSILON
                    ).argmin(axis=1)
            )
        elif (include_last_label is False):
            max_threshold = np.maximum(
                threshold[np.newaxis, :],
                np.min(y_pred_proba_cumsum, axis=1)
            )
            y_pred_index_last = np.argmax(
                np.ma.masked_greater(
                    y_pred_proba_cumsum - max_threshold[:, np.newaxis, :],
                    EPSILON
                ), axis=1
            )
        else:
            raise ValueError(
                "Invalid include_last_label argument. "
                "Should be a boolean or 'randomized'."
            )
        return y_pred_index_last[:, np.newaxis, :]

    def _add_random_tie_breaking(
        self,
        prediction_sets: NDArray,
        y_pred_index_last: NDArray,
        y_pred_proba_cumsum: NDArray,
        y_pred_proba_last: NDArray,
        threshold: NDArray,
        lambda_star: Union[NDArray, float, None],
        k_star: Union[NDArray, None]
    ) -> NDArray:
        """
        Randomly remove last label from prediction set based on the
        comparison between a random number and the difference between
        cumulated score of the last included label and the quantile.

        Parameters
        ----------
        prediction_sets: NDArray of shape
            (n_samples, n_classes, n_threshold)
            Prediction set for each observation and each alpha.

        y_pred_index_last: NDArray of shape (n_samples, threshold)
            Index of the last included label.

        y_pred_proba_cumsum: NDArray of shape (n_samples, n_classes)
            Cumsumed probability of the model in the original order.

        y_pred_proba_last: NDArray of shape (n_samples, 1, threshold)
            Last included probability.

        threshold: NDArray of shape (n_alpha,) or shape (n_samples_train,)
            Threshold to compare with y_proba_last_cumsum, can be either:

            - the quantiles associated with alpha values when
              ``cv`` == "prefit", ``cv`` == "split" or
              ``agg_scores`` is "mean"
            - the conformity score from training samples otherwise
              (i.e., when ``cv`` is a CV splitter and
              ``agg_scores`` is "crossval")

        lambda_star: Union[NDArray, float, None] of shape (n_alpha):
            Optimal value of the regulizer lambda.

        k_star: Union[NDArray, None] of shape (n_alpha):
            Optimal value of the regulizer k.

        Returns
        -------
        NDArray of shape (n_samples, n_classes, n_alpha)
            Updated version of prediction_sets with randomly removed
            labels.
        """
        print()
        print("use of _add_random_tie_breaking")
        # get cumsumed probabilities up to last retained label
        y_proba_last_cumsumed = np.squeeze(
            np.take_along_axis(
                y_pred_proba_cumsum,
                y_pred_index_last,
                axis=1
            ), axis=1
        )

        if self.method in ["cumulated_score", "aps"]:
            # compute V parameter from Romano+(2020)
            vs = (
                (y_proba_last_cumsumed - threshold.reshape(1, -1)) /
                y_pred_proba_last[:, 0, :]
            )
        else:
            # compute V parameter from Angelopoulos+(2020)
            L = np.sum(prediction_sets, axis=1)
            vs = (
                (y_proba_last_cumsumed - threshold.reshape(1, -1)) /
                (
                    y_pred_proba_last[:, 0, :] -
                    lambda_star * np.maximum(0, L - k_star) +
                    lambda_star * (L > k_star)
                )
            )

        # get random numbers for each observation and alpha value
        random_state = check_random_state(self.random_state)
        us = random_state.uniform(size=(prediction_sets.shape[0], 1))
        # remove last label from comparison between uniform number and V
        vs_less_than_us = np.less_equal(vs - us, EPSILON)
        np.put_along_axis(
            prediction_sets,
            y_pred_index_last,
            vs_less_than_us[:, np.newaxis, :],
            axis=1
        )
        return prediction_sets

    def _predict_oof_model(
        self,
        estimator: ClassifierMixin,
        X: ArrayLike,
    ) -> NDArray:
        """
        Predict probabilities of a test set from a fitted estimator.

        Parameters
        ----------
        estimator: ClassifierMixin
            Fitted estimator.

        X: ArrayLike
            Test set.

        Returns
        -------
        ArrayLike
            Predicted probabilities.
        """
        print()
        print("use of _predict_oof_model")
        y_pred_proba = estimator.predict_proba(X)
        # we enforce y_pred_proba to contain all labels included in y
        if len(estimator.classes_) != self.n_classes_:
            y_pred_proba = fix_number_of_classes(
                self.n_classes_,
                estimator.classes_,
                y_pred_proba
            )
        y_pred_proba = self._check_proba_normalized(y_pred_proba)
        return y_pred_proba

    def _get_true_label_cumsum_proba(
        self,
        y: ArrayLike,
        y_pred_proba: NDArray
    ) -> Tuple[NDArray, NDArray]:
        """
        Compute the cumsumed probability of the true label.

        Parameters
        ----------
        y: NDArray of shape (n_samples, )
            Array with the labels.
        y_pred_proba: NDArray of shape (n_samples, n_classes)
            Predictions of the model.

        Returns
        -------
        Tuple[NDArray, NDArray] of shapes
        (n_samples, 1) and (n_samples, ). The first element
        is the cumsum probability of the true label. The second
        is the sorted position of the true label.
        """
        print()
        print("use of _get_true_label_cumsum_proba")
        y_true = label_binarize(
            y=y, classes=self.classes_
        )
        index_sorted = np.fliplr(np.argsort(y_pred_proba, axis=1))
        y_pred_proba_sorted = np.take_along_axis(
            y_pred_proba, index_sorted, axis=1
        )
        y_true_sorted = np.take_along_axis(y_true, index_sorted, axis=1)
        y_pred_proba_sorted_cumsum = np.cumsum(y_pred_proba_sorted, axis=1)
        cutoff = np.argmax(y_true_sorted, axis=1)
        true_label_cumsum_proba = np.take_along_axis(
            y_pred_proba_sorted_cumsum, cutoff.reshape(-1, 1), axis=1
        )
        return true_label_cumsum_proba, cutoff + 1

    def _regularize_conformity_score(
        self,
        k_star: NDArray,
        lambda_: Union[NDArray, float],
        conf_score: NDArray,
        cutoff: NDArray
    ) -> NDArray:
        """
        Regularize the conformity scores with the ``"raps"``
        method. See algo. 2 in [3].

        Parameters
        ----------
        k_star: NDArray of shape (n_alphas, )
            Optimal value of k (called k_reg in the paper). There
            is one value per alpha.

        lambda_: Union[NDArray, float] of shape (n_alphas, )
            One value of lambda for each alpha.

        conf_score: NDArray of shape (n_samples, 1)
            Conformity scores.

        cutoff: NDArray of shape (n_samples, 1)
            Position of the true label.

        Returns
        -------
        NDArray of shape (n_samples, 1, n_alphas)
            Regularized conformity scores. The regularization
            depends on the value of alpha.
        """
        print()
        print("use of _regularize_conformity_score")
        conf_score = np.repeat(
            conf_score[:, :, np.newaxis], len(k_star), axis=2
        )
        cutoff = np.repeat(
            cutoff[:, np.newaxis], len(k_star), axis=1
        )
        conf_score += np.maximum(
            np.expand_dims(
                lambda_ * (cutoff - k_star),
                axis=1
            ),
            0
        )
        return conf_score

    def _get_true_label_position(
        self,
        y_pred_proba: NDArray,
        y: NDArray
    ) -> NDArray:
        """
        Return the sorted position of the true label in the
        prediction

        Parameters
        ----------
        y_pred_proba: NDArray of shape (n_samples, n_calsses)
            Model prediction.

        y: NDArray of shape (n_samples)
            Labels.

        Returns
        -------
        NDArray of shape (n_samples, 1)
            Position of the true label in the prediction.
        """
        print()
        print("use of _get_true_label_position")
        index = np.argsort(
                np.fliplr(np.argsort(y_pred_proba, axis=1))
            )
        position = np.take_along_axis(
            index,
            y.reshape(-1, 1),
            axis=1
        )
        return position

    def _get_last_included_proba(
        self,
        y_pred_proba: NDArray,
        thresholds: NDArray,
        include_last_label: Union[bool, str, None],
        lambda_: Union[NDArray, float, None],
        k_star: Union[NDArray, Any]
    ) -> Tuple[NDArray, NDArray, NDArray]:
        """
        Function that returns the smallest score
        among those which are included in the prediciton set.

        Parameters
        ----------
        y_pred_proba: NDArray of shape (n_samples, n_classes)
            Predictions of the model.

        thresholds: NDArray of shape (n_alphas, )
            Quantiles that have been computed from the conformity
            scores.

        include_last_label: Union[bool, str, None]
            Whether to include or not the label whose score
            exceeds the threshold.

        lambda_: Union[NDArray, float, None] of shape (n_alphas)
            Values of lambda for the regularization.

        k_star: Union[NDArray, Any]
            Values of k for the regularization.

        Returns
        -------
        Tuple[ArrayLike, ArrayLike, ArrayLike]
            Arrays of shape (n_samples, n_classes, n_alphas),
            (n_samples, 1, n_alphas) and (n_samples, 1, n_alphas).
            They are respectively the cumsumed scores in the original
            order which can be different according to the value of alpha
            with the RAPS method, the index of the last included score
            and the value of the last included score.
        """
        print()
        print("use of _get_last_included_proba")
        index_sorted = np.flip(
            np.argsort(y_pred_proba, axis=1), axis=1
        )
        # sort probabilities by decreasing order
        y_pred_proba_sorted = np.take_along_axis(
            y_pred_proba, index_sorted, axis=1
        )
        # get sorted cumulated score
        y_pred_proba_sorted_cumsum = np.cumsum(
            y_pred_proba_sorted, axis=1
        )

        if self.method == "raps":
            y_pred_proba_sorted_cumsum += lambda_ * np.maximum(
                0,
                np.cumsum(
                    np.ones(y_pred_proba_sorted_cumsum.shape),
                    axis=1
                ) - k_star
            )
        # get cumulated score at their original position
        y_pred_proba_cumsum = np.take_along_axis(
            y_pred_proba_sorted_cumsum,
            np.argsort(index_sorted, axis=1),
            axis=1
        )
        # get index of the last included label
        y_pred_index_last = self._get_last_index_included(
            y_pred_proba_cumsum,
            thresholds,
            include_last_label
        )
        # get the probability of the last included label
        y_pred_proba_last = np.take_along_axis(
            y_pred_proba,
            y_pred_index_last,
            axis=1
        )

        zeros_scores_proba_last = (y_pred_proba_last <= EPSILON)

        # If the last included proba is zero, change it to the
        # smallest non-zero value to avoid inluding them in the
        # prediction sets.
        if np.sum(zeros_scores_proba_last) > 0:
            y_pred_proba_last[zeros_scores_proba_last] = np.expand_dims(
                np.min(
                    np.ma.masked_less(
                        y_pred_proba,
                        EPSILON
                    ).filled(fill_value=np.inf),
                    axis=1
                ), axis=1
            )[zeros_scores_proba_last]
        return y_pred_proba_cumsum, y_pred_index_last, y_pred_proba_last

    def _update_size_and_lambda(
        self,
        best_sizes: NDArray,
        alpha_np: NDArray,
        y_ps: NDArray,
        lambda_: Union[NDArray, float],
        lambda_star: NDArray
    ) -> Tuple[NDArray, NDArray]:
        """Update the values of the optimal lambda if the
        average size of the prediction sets decreases with
        this new value of lambda.

        Parameters
        ----------
        best_sizes: NDArray of shape (n_alphas, )
            Smallest average prediciton set size before testing
            for the new value of lambda_

        alpha_np: NDArray of shape (n_alphas)
            Level of confidences.

        y_ps: NDArray of shape (n_samples, n_classes, n_alphas)
            Prediction sets computed with the RAPS method and the
            new value of lambda_

        lambda_: NDArray of shape (n_alphas, )
            New value of lambda_star to test

        lambda_star: NDArray of shape (n_alphas, )
            Actual optimal lambda values for each alpha.

        Returns
        -------
        Tuple[NDArray, NDArray]
            Arrays of shape (n_alphas, ) and (n_alpha, ) which
            respectively represent the updated values of lambda_star
            and the new best sizes.
        """
        print()
        print('use of _update_size_and_lambda')
        sizes = [
            classification_mean_width_score(
                y_ps[:, :, i]
            ) for i in range(len(alpha_np))
        ]

        sizes_improve = (sizes < best_sizes - EPSILON)
        lambda_star = (
            sizes_improve * lambda_ + (1 - sizes_improve) * lambda_star
        )
        best_sizes = sizes_improve * sizes + (1 - sizes_improve) * best_sizes
        return lambda_star, best_sizes

    def _find_lambda_star(
        self,
        y_pred_proba_raps: NDArray,
        alpha_np: NDArray,
        include_last_label: Union[bool, str, None],
        k_star: NDArray
    ) -> Union[NDArray, float]:
        """Find the optimal value of lambda for each alpha.

        Parameters
        ----------
        y_pred_proba_raps: NDArray of shape (n_samples, n_labels, n_alphas)
            Predictions of the model repeated on the last axis as many times
            as the number of alphas

        alpha_np: NDArray of shape (n_alphas, )
            Levels of confidences.

        include_last_label: bool
            Whether to include or not last label in
            the prediction sets

        k_star: NDArray of shape (n_alphas, )
            Values of k for the regularization.

        Returns
        -------
        ArrayLike of shape (n_alphas, )
            Optimal values of lambda.
        """
        print()
        print("use of _find_lambda_star")
        lambda_star = np.zeros(len(alpha_np))
        best_sizes = np.full(len(alpha_np), np.finfo(np.float64).max)

        for lambda_ in [.001, .01, .1, .2, .5]:  # values given in paper[3]
            true_label_cumsum_proba, cutoff = (
                self._get_true_label_cumsum_proba(
                    self.y_raps_no_enc,
                    y_pred_proba_raps[:, :, 0],
                )
            )

            true_label_cumsum_proba_reg = self._regularize_conformity_score(
                k_star,
                lambda_,
                true_label_cumsum_proba,
                cutoff
            )

            quantiles_ = compute_quantiles(
                true_label_cumsum_proba_reg,
                alpha_np
            )

            _, _, y_pred_proba_last = self._get_last_included_proba(
                y_pred_proba_raps,
                quantiles_,
                include_last_label,
                lambda_,
                k_star
            )

            y_ps = np.greater_equal(
                    y_pred_proba_raps - y_pred_proba_last, -EPSILON
            )
            lambda_star, best_sizes = self._update_size_and_lambda(
                best_sizes, alpha_np, y_ps, lambda_, lambda_star
            )
        if len(lambda_star) == 1:
            lambda_star = lambda_star[0]
        return lambda_star

    def _get_classes_info(
            self, estimator: ClassifierMixin, y: NDArray
    ) -> Tuple[int, NDArray]:
        """
        Compute the number of classes and the classes values
        according to either the pre-trained model or to the
        values in y.

        Parameters
        ----------
        estimator: ClassifierMixin
            Estimator pre-fitted or not.

        y: NDArray
            Values to predict.

        Returns
        -------
        Tuple[int, NDArray]
            The number of unique classes and their unique
            values.

        Raises
        ------
        ValueError
            If `cv="prefit"` and that classes in `y` are not included into
            `estimator.classes_`.

        Warning
            If number of calibration labels is lower than number of labels
            for training (in prefit setting)
        """
        print()
        print("use of _get_classes_info")
        n_unique_y_labels = len(np.unique(y))
        if self.cv == "prefit":
            classes = estimator.classes_
            n_classes = len(np.unique(classes))
            if not set(np.unique(y)).issubset(classes):
                raise ValueError(
                    "Values in y do not matched values in estimator.classes_."
                    + " Check that you are not adding any new label"
                )
            if n_classes > n_unique_y_labels:
                warnings.warn(
                    "WARNING: your calibration dataset has less labels"
                    + " than your training dataset (training"
                    + f" has {n_classes} unique labels while"
                    + f" calibration have {n_unique_y_labels} unique labels"
                )

        else:
            n_classes = n_unique_y_labels
            classes = np.unique(y)

        return n_classes, classes

    def _check_fit_parameter(self, X, y, sample_weight, groups):
        print()
        print("use of _check_fit_parameters")
        self._check_parameters()
        cv = check_cv(
            self.cv, test_size=self.test_size, random_state=self.random_state
        )
        X, y = indexable(X, y)
        y = _check_y(y)

        sample_weight = cast(Optional[NDArray], sample_weight)
        groups = cast(Optional[NDArray], groups)
        sample_weight, X, y = check_null_weight(sample_weight, X, y)

        y = cast(NDArray, y)

        estimator = check_estimator_classification(
            X,
            y,
            cv,
            self.estimator
        )
        self.n_features_in_ = check_n_features_in(X, cv, estimator)

        n_samples = _num_samples(y)

        self.n_classes_, self.classes_ = self._get_classes_info(
            estimator, y
        )
        enc = LabelEncoder()
        enc.fit(self.classes_)
        y_enc = enc.transform(y)

        self.label_encoder_ = enc
        self._check_target(y)
        
        return (
            estimator, cv, X, y, y_enc,
            sample_weight, groups,
            n_samples,
        )

    def _split_raps_data(self, X, y_enc, sample_weight, groups, size_raps):
        print()
        print("use of _split_raps_data")
        raps_split = ShuffleSplit(
                1, test_size=size_raps, random_state=self.random_state
            )
        train_raps_index, val_raps_index = next(raps_split.split(X))
        X, self.X_raps, y_enc, self.y_raps = \
            _safe_indexing(X, train_raps_index), \
            _safe_indexing(X, val_raps_index), \
            _safe_indexing(y_enc, train_raps_index), \
            _safe_indexing(y_enc, val_raps_index)
        self.y_raps_no_enc = self.label_encoder_.inverse_transform(
            self.y_raps
        )
        y = self.label_encoder_.inverse_transform(y_enc)
        y_enc = cast(NDArray, y_enc)
        n_samples = _num_samples(y_enc)
        if sample_weight is not None:
            sample_weight = sample_weight[train_raps_index]
            sample_weight = cast(NDArray, sample_weight)
        if groups is not None:
            groups = groups[train_raps_index]
            groups = cast(NDArray, groups)

        return X, y_enc, y, n_samples, sample_weight, groups

    def fit(
        self,
        X: ArrayLike,
        y: ArrayLike,
        sample_weight: Optional[ArrayLike] = None,
        size_raps: Optional[float] = .2,
        groups: Optional[ArrayLike] = None,
        **fit_params,
    ) -> MapieClassifier:
        """
        Fit the base estimator or use the fitted base estimator.

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Training data.

        y: NDArray of shape (n_samples,)
            Training labels.

        sample_weight: Optional[ArrayLike] of shape (n_samples,)
            Sample weights for fitting the out-of-fold models.
            If None, then samples are equally weighted.
            If some weights are null,
            their corresponding observations are removed
            before the fitting process and hence have no prediction sets.

            By default ``None``.

        size_raps: Optional[float]
            Percentage of the data to be used for choosing lambda_star and
            k_star for the RAPS method.

            By default ``.2``.

        groups: Optional[ArrayLike] of shape (n_samples,)
            Group labels for the samples used while splitting the dataset into
            train/test set.

            By default ``None``.

        **fit_params : dict
            Additional fit parameters.

        Returns
        -------
        MapieClassifier
            The model itself.
        """
        print()
        print("USE OF FIT")
        # Checks
        (
            estimator, cv, X, y, y_enc,
            sample_weight, groups,
            n_samples
        ) = self._check_fit_parameter(
            X, y, sample_weight, groups
        )
        print()
        print((
            "estimator:",estimator, "cv:",cv, "X_train:", X, "y_train:", y, "y_enc:" ,y_enc,
            "sample_weight:",sample_weight, "groups:",groups,
            "n_samples",n_samples))


        self.k_ = np.empty_like(y, dtype=int)
        print()
        print("self.k_", self.k_, "shape_of k_ :", self.k_.shape,"type :" , type(self.k_),  "unique_values :", list(set(self.k_)))
        self.n_samples_ = _num_samples(X)
        print()
        print("self.n_samples",self.n_samples_)

        if self.method == "raps":
            (
                X, y_enc, y, n_samples,
                sample_weight, groups
            ) = self._split_raps_data(
                X, y_enc, sample_weight,
                groups, size_raps
            )

        # Work
        self.estimator_ = EnsembleClassifier(
            estimator,
            self.n_classes_,
            cv,
            self.n_jobs,
            self.random_state,
            self.test_size,
            self.verbose
        )

        self.estimator_.fit(X, y, y_enc, sample_weight, groups, **fit_params)
        
        y_pred_proba, y, y_enc = self.estimator_.predict_proba_calib(
            X, y, y_enc, groups
        )

        # RAPS: compute y_pred and position on the RAPS validation dataset
        if self.method == "raps":
            self.y_pred_proba_raps = self.estimator_.single_estimator_.predict_proba(
                self.X_raps
            )
            self.position_raps = self._get_true_label_position(
                self.y_pred_proba_raps,
                self.y_raps
            )

        # Conformity scores
        if self.method == "naive":
            self.conformity_scores_ = np.empty(
                y_pred_proba.shape,
                dtype="float"
            )
        elif self.method in ["score", "lac"]:
            self.conformity_scores_ = np.take_along_axis(
                1 - y_pred_proba, y_enc.reshape(-1, 1), axis=1
            )
        elif self.method in ["cumulated_score", "aps", "raps"]:
            self.conformity_scores_, self.cutoff = (
                self._get_true_label_cumsum_proba(
                    y,
                    y_pred_proba
                )
            )
            y_proba_true = np.take_along_axis(
                y_pred_proba, y_enc.reshape(-1, 1), axis=1
            )
            random_state = check_random_state(self.random_state)
            u = random_state.uniform(size=len(y_pred_proba)).reshape(-1, 1)
            self.conformity_scores_ -= u * y_proba_true
        elif self.method == "top_k":
            # Here we reorder the labels by decreasing probability
            # and get the position of each label from decreasing
            # probability
            self.conformity_scores_ = self._get_true_label_position(
                y_pred_proba,
                y_enc
            )
        else:
            raise ValueError(
                "Invalid method. "
                f"Allowed values are {self.valid_methods_}."
            )

        return self

    def predict(
        self,
        X: ArrayLike,
        alpha: Optional[Union[float, Iterable[float]]] = None,
        include_last_label: Optional[Union[bool, str]] = True,
        agg_scores: Optional[str] = "mean"
    ) -> Union[NDArray, Tuple[NDArray, NDArray]]:
        """
        Prediction prediction sets on new samples based on target confidence
        interval.
        Prediction sets for a given ``alpha`` are deduced from:

        - quantiles of softmax scores (``"lac"`` method)
        - quantiles of cumulated scores (``"aps"`` method)

        Parameters
        ----------
        X: ArrayLike of shape (n_samples, n_features)
            Test data.

        alpha: Optional[Union[float, Iterable[float]]]
            Can be a float, a list of floats, or a ``ArrayLike`` of floats.
            Between 0 and 1, represent the uncertainty of the confidence
            interval.
            Lower ``alpha`` produce larger (more conservative) prediction
            sets.
            ``alpha`` is the complement of the target coverage level.

            By default ``None``.

        include_last_label: Optional[Union[bool, str]]
            Whether or not to include last label in
            prediction sets for the "aps" method. Choose among:

            - False, does not include label whose cumulated score is just over
              the quantile.
            - True, includes label whose cumulated score is just over the
              quantile, unless there is only one label in the prediction set.
            - "randomized", randomly includes label whose cumulated score is
              just over the quantile based on the comparison of a uniform
              number and the difference between the cumulated score of
              the last label and the quantile.

            When set to ``True`` or ``False``, it may result in a coverage
            higher than ``1 - alpha`` (because contrary to the "randomized"
            setting, none of this methods create empty prediction sets). See
            [2] and [3] for more details.

            By default ``True``.

        agg_scores: Optional[str]

            How to aggregate the scores output by the estimators on test data
            if a cross-validation strategy is used. Choose among:

            - "mean", take the mean of scores.
            - "crossval", compare the scores between all training data and each
              test point for each label to estimate if the label must be
              included in the prediction set. Follows algorithm 2 of
              Romano+2020.

            By default "mean".

        Returns
        -------
        Union[NDArray, Tuple[NDArray, NDArray]]

        - NDArray of shape (n_samples,) if alpha is None.

        - Tuple[NDArray, NDArray] of shapes
        (n_samples,) and (n_samples, n_classes, n_alpha) if alpha is not None.
        """
        print()
        print("use of predict")
        if self.method == "top_k":
            agg_scores = "mean"
        # Checks
        cv = check_cv(
            self.cv, test_size=self.test_size, random_state=self.random_state
        )
        include_last_label = self._check_include_last_label(include_last_label)
        alpha = cast(Optional[NDArray], check_alpha(alpha))
        check_is_fitted(self, self.fit_attributes)
        lambda_star, k_star = None, None
        # Estimate prediction sets
        y_pred = self.estimator_.single_estimator_.predict(X)

        if alpha is None:
            return y_pred

        n = len(self.conformity_scores_)

        # Estimate of probabilities from estimator(s)
        # In all cases: len(y_pred_proba.shape) == 3
        # with  (n_test, n_classes, n_alpha or n_train_samples)
        alpha_np = cast(NDArray, alpha)
        check_alpha_and_n_samples(alpha_np, n)
        y_pred_proba = self.estimator_.predict(
            X, agg_scores
        )
        y_pred_proba = self._check_proba_normalized(y_pred_proba, axis=1)
        if (cv == "prefit") or (agg_scores in ["mean"]):
            y_pred_proba = np.repeat(
                y_pred_proba[:, :, np.newaxis], len(alpha_np), axis=2
            )

        # Choice of the quantile
        check_alpha_and_n_samples(alpha_np, n)

        if self.method == "naive":
            self.quantiles_ = 1 - alpha_np
        else:
            if (cv == "prefit") or (agg_scores in ["mean"]):
                if self.method == "raps":
                    check_alpha_and_n_samples(alpha_np, len(self.X_raps))
                    k_star = compute_quantiles(
                        self.position_raps,
                        alpha_np
                    ) + 1
                    y_pred_proba_raps = np.repeat(
                        self.y_pred_proba_raps[:, :, np.newaxis],
                        len(alpha_np),
                        axis=2
                    )
                    lambda_star = self._find_lambda_star(
                        y_pred_proba_raps,
                        alpha_np,
                        include_last_label,
                        k_star
                    )
                    self.conformity_scores_regularized = (
                        self._regularize_conformity_score(
                                    k_star,
                                    lambda_star,
                                    self.conformity_scores_,
                                    self.cutoff
                        )
                    )
                    self.quantiles_ = compute_quantiles(
                        self.conformity_scores_regularized,
                        alpha_np
                    )
                else:
                    self.quantiles_ = compute_quantiles(
                        self.conformity_scores_,
                        alpha_np
                    )
            else:
                self.quantiles_ = (n + 1) * (1 - alpha_np)

        # Build prediction sets
        if self.method in ["score", "lac"]:
            if (cv == "prefit") or (agg_scores == "mean"):
                prediction_sets = np.greater_equal(
                    y_pred_proba - (1 - self.quantiles_), -EPSILON
                )
            else:
                y_pred_included = np.less_equal(
                    (1 - y_pred_proba) - self.conformity_scores_.ravel(),
                    EPSILON
                ).sum(axis=2)
                prediction_sets = np.stack(
                    [
                        np.greater_equal(
                            y_pred_included - _alpha * (n - 1), -EPSILON
                        )
                        for _alpha in alpha_np
                    ], axis=2
                )

        elif self.method in ["naive", "cumulated_score", "aps", "raps"]:
            # specify which thresholds will be used
            if (cv == "prefit") or (agg_scores in ["mean"]):
                thresholds = self.quantiles_
            else:
                thresholds = self.conformity_scores_.ravel()
            # sort labels by decreasing probability
            y_pred_proba_cumsum, y_pred_index_last, y_pred_proba_last = (
                self._get_last_included_proba(
                    y_pred_proba,
                    thresholds,
                    include_last_label,
                    lambda_star,
                    k_star,
                )
            )
            # get the prediction set by taking all probabilities
            # above the last one
            if (cv == "prefit") or (agg_scores in ["mean"]):
                y_pred_included = np.greater_equal(
                    y_pred_proba - y_pred_proba_last, -EPSILON
                )
            else:
                y_pred_included = np.less_equal(
                    y_pred_proba - y_pred_proba_last, EPSILON
                )
            # remove last label randomly
            if include_last_label == "randomized":
                y_pred_included = self._add_random_tie_breaking(
                    y_pred_included,
                    y_pred_index_last,
                    y_pred_proba_cumsum,
                    y_pred_proba_last,
                    thresholds,
                    lambda_star,
                    k_star
                )
            if (cv == "prefit") or (agg_scores in ["mean"]):
                prediction_sets = y_pred_included
            else:
                # compute the number of times the inequality is verified
                prediction_sets_summed = y_pred_included.sum(axis=2)
                prediction_sets = np.less_equal(
                    prediction_sets_summed[:, :, np.newaxis]
                    - self.quantiles_[np.newaxis, np.newaxis, :],
                    EPSILON
                )
        elif self.method == "top_k":
            y_pred_proba = y_pred_proba[:, :, 0]
            index_sorted = np.fliplr(np.argsort(y_pred_proba, axis=1))
            y_pred_index_last = np.stack(
                [
                    index_sorted[:, quantile]
                    for quantile in self.quantiles_
                ], axis=1
            )
            y_pred_proba_last = np.stack(
                [
                    np.take_along_axis(
                        y_pred_proba,
                        y_pred_index_last[:, iq].reshape(-1, 1),
                        axis=1
                    )
                    for iq, _ in enumerate(self.quantiles_)
                ], axis=2
            )
            prediction_sets = np.greater_equal(
                y_pred_proba[:, :, np.newaxis]
                - y_pred_proba_last,
                -EPSILON
            )
        else:
            raise ValueError(
                "Invalid method. "
                f"Allowed values are {self.valid_methods_}."
            )
        return y_pred, prediction_sets
