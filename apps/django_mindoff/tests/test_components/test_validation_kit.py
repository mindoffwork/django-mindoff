import logging
import sys
import pytest
from ...components.validation_kit import (
    MindoffValidationError,
    ValidationError,
    mo_validation_kit,
)


class _CmpBoom:
    def __eq__(self, other):
        raise RuntimeError("eq failed")

    def __gt__(self, other):
        raise RuntimeError("gt failed")

    def __ge__(self, other):
        raise RuntimeError("ge failed")

    def __lt__(self, other):
        raise RuntimeError("lt failed")

    def __le__(self, other):
        raise RuntimeError("le failed")


class _BadIterable:
    def __iter__(self):
        raise RuntimeError("iter failed")


class TestImmediateValidator:

    @pytest.mark.parametrize(
        "fn, kwargs",
        [
            ("ensure_equal", {"left": 5, "right": 5}),
            ("ensure_not_equal", {"left": "x", "right": "y"}),
            ("ensure_same", {"left": "a", "right": "a"}),
            ("ensure_not_same", {"left": "a", "right": "b"}),
            ("ensure_greater", {"left": 10, "right": 5}),
            ("ensure_greater_equal", {"left": 5, "right": 5}),
            ("ensure_lesser", {"left": 7, "right": 10}),
            ("ensure_lesser_equal", {"left": 7, "right": 7}),
            ("ensure_in_range", {"value": 5, "min_value": 1, "max_value": 10}),
            ("ensure_not_in_range", {"value": 20, "min_value": 1, "max_value": 10}),
            ("ensure_almost_equal", {"left": 1.0, "right": 1.0000001}),
            ("ensure_not_almost_equal", {"left": 1.0, "right": 1.1}),
            ("ensure_falsey", {"value": []}),
            ("ensure_truthy", {"value": "x"}),
            ("ensure_type", {"value": 123, "typ": int}),
            ("ensure_not_type", {"value": "x", "typ": int}),
            ("ensure_subclass", {"cls": bool, "parent": int}),
            ("ensure_not_subclass", {"cls": str, "parent": dict}),
            ("ensure_in", {"value": 2, "container": [1, 2, 3]}),
            ("ensure_not_in", {"value": "z", "container": "hello"}),
            ("ensure_in", {"value": "a", "container": {"a": 1}}),
            ("ensure_count_equal", {"left": [1, 2], "right": [2, 1]}),
            ("ensure_count_not_equal", {"left": [1, 2], "right": [1, 3, 4]}),
            ("ensure_finite", {"value": 5}),
            ("ensure_regex", {"value": "abc123", "pattern": r"[a-z]+\d+"}),
            ("ensure_not_regex", {"value": "xyz", "pattern": r"\d+"}),
            ("ensure_path", {"path": sys.executable}),
            ("ensure_not_path", {"path": "nonexistent_file.tmp"}),
            ("ensure_equal", {"left": [1, 2, 3], "right": (1, 2, 3)}),
            ("ensure_falsey", {"value": 0}),
            ("ensure_truthy", {"value": True}),
            ("ensure_subclass", {"cls": str, "parent": object}),
            ("ensure_finite", {"value": 3.14}),
            ("ensure_regex", {"value": "hello", "pattern": r"hello"}),
            ("ensure", {"check": lambda: True}),
        ],
    )
    def test_validation_acceptance(self, fn, kwargs):
        """ACCEPTANCE: Validation helpers return True for valid inputs."""
        result = getattr(mo_validation_kit, fn)(**kwargs, is_exception=True)
        assert result == True

    @pytest.mark.parametrize(
        "fn, kwargs",
        [
            ("ensure_equal", {"left": 5, "right": 5}),
            ("ensure_greater", {"left": 10, "right": 5}),
            ("ensure_truthy", {"value": "x"}),
        ],
    )
    def test_validation_acceptance_json_smoke(self, fn, kwargs):
        """ACCEPTANCE: JSON mode also returns True for valid validation checks."""
        result = getattr(mo_validation_kit, fn)(**kwargs, is_exception=False)
        assert result == True

    @pytest.mark.parametrize(
        "fn, kwargs, is_exception, expected_exc, is_msg",
        [
            # â”€â”€ Native exception path (is_exception=True) â”€â”€
            (
                "ensure_equal",
                {"left": 3, "right": 5, "msg": "Custom Message 3 is not equal 5"},
                True,
                ValueError,
                True,
            ),
            ("ensure_not_equal", {"left": "x", "right": "x"}, True, ValueError, False),
            ("ensure_same", {"left": [1], "right": [1]}, True, ValueError, False),
            ("ensure_not_same", {"left": "a", "right": "a"}, True, ValueError, False),
            ("ensure_greater", {"left": 2, "right": 5}, True, ValueError, False),
            ("ensure_greater_equal", {"left": 4, "right": 5}, True, ValueError, False),
            ("ensure_lesser", {"left": 7, "right": 3}, True, ValueError, False),
            ("ensure_lesser_equal", {"left": 8, "right": 7}, True, ValueError, False),
            (
                "ensure_in_range",
                {"value": 0, "min_value": 1, "max_value": 10},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_in_range",
                {"value": 11, "min_value": 1, "max_value": 10},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_not_in_range",
                {"value": 5, "min_value": 1, "max_value": 10},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_almost_equal",
                {"left": 1.0, "right": 1.1, "tol": 1e-6},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_not_almost_equal",
                {"left": 1.0, "right": 1.0, "tol": 1e-6},
                True,
                ValueError,
                False,
            ),
            ("ensure_falsey", {"value": [1]}, True, ValueError, False),
            ("ensure_truthy", {"value": ""}, True, ValueError, False),
            ("ensure_type", {"value": "abc", "typ": int}, True, TypeError, False),
            ("ensure_not_type", {"value": 123, "typ": int}, True, TypeError, False),
            ("ensure_subclass", {"cls": int, "parent": str}, True, TypeError, False),
            (
                "ensure_not_subclass",
                {"cls": bool, "parent": int},
                True,
                TypeError,
                False,
            ),
            (
                "ensure_in",
                {"value": 99, "container": [1, 2, 3]},
                True,
                LookupError,
                False,
            ),
            ("ensure_in", {"value": "z", "container": {"a": 1}}, True, KeyError, False),
            (
                "ensure_not_in",
                {"value": 2, "container": [1, 2, 3]},
                True,
                LookupError,
                False,
            ),
            (
                "ensure_not_in",
                {"value": "a", "container": {"a": 1}},
                True,
                KeyError,
                False,
            ),
            (
                "ensure_count_equal",
                {"left": [1, 2], "right": [2, 2]},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_count_not_equal",
                {"left": [1, 1], "right": [1, 1]},
                True,
                ValueError,
                False,
            ),
            ("ensure_finite", {"value": float("inf")}, True, ValueError, False),
            ("ensure_finite", {"value": "not-a-number"}, True, TypeError, False),
            (
                "ensure_regex",
                {"value": "abc", "pattern": r"\d+"},
                True,
                ValueError,
                False,
            ),
            ("ensure_regex", {"value": 123, "pattern": r"\d+"}, True, TypeError, False),
            (
                "ensure_not_regex",
                {"value": "123", "pattern": r"\d+"},
                True,
                ValueError,
                False,
            ),
            (
                "ensure_not_regex",
                {"value": 123, "pattern": r"\d+"},
                True,
                TypeError,
                False,
            ),
            (
                "ensure_path",
                {"path": "nonexistent_file.tmp"},
                True,
                FileNotFoundError,
                False,
            ),
            ("ensure_not_path", {"path": sys.executable}, True, FileExistsError, False),
            ("ensure", {"check": False}, True, ValidationError, False),
            # â”€â”€ MindoffValidationError path (is_exception=False) â”€â”€
            # Representative sample: one per exception category + custom-msg path.
            # The wrapping is in shared _record_or_raise; every method above also
            # exercises it through the exception path.
            (
                "ensure_equal",
                {"left": 3, "right": 5, "msg": "Custom Message 3 is not equal 5"},
                False,
                ValueError,
                True,
            ),
            ("ensure_type", {"value": "abc", "typ": int}, False, TypeError, False),
            (
                "ensure_in",
                {"value": 99, "container": [1, 2, 3]},
                False,
                LookupError,
                False,
            ),
            (
                "ensure_in",
                {"value": "z", "container": {"a": 1}},
                False,
                KeyError,
                False,
            ),
            (
                "ensure_path",
                {"path": "nonexistent_file.tmp"},
                False,
                FileNotFoundError,
                False,
            ),
            (
                "ensure_not_path",
                {"path": sys.executable},
                False,
                FileExistsError,
                False,
            ),
        ],
    )
    def test_validation_rejection(self, fn, kwargs, is_exception, expected_exc, is_msg):
        """REJECTION: Invalid inputs raise expected errors in both exception and JSON modes."""
        if is_exception:
            with pytest.raises(expected_exc) as excinfo:
                getattr(mo_validation_kit, fn)(**kwargs, is_exception=is_exception)
            if is_msg:
                assert "Custom Message 3 is not equal 5" in str(excinfo.value)
        else:
            with pytest.raises(MindoffValidationError) as errinfo:
                getattr(mo_validation_kit, fn)(**kwargs, is_exception=is_exception)
            assert errinfo.value.data.get("type") == expected_exc.__name__
            if is_msg:
                assert "Custom Message 3 is not equal 5" in str(errinfo.value.message)

    @pytest.mark.parametrize(
        "fn, kwargs, expected_result",
        [
            # â”€â”€ Boundary â”€â”€
            ("ensure_equal", {"left": [], "right": []}, True),
            ("ensure_equal", {"left": [1], "right": (1,)}, True),
            ("ensure_greater_equal", {"left": 5, "right": 5}, True),
            ("ensure_lesser_equal", {"left": 5, "right": 5}, True),
            ("ensure_in_range", {"value": 1, "min_value": 1, "max_value": 10}, True),
            ("ensure_in_range", {"value": 10, "min_value": 1, "max_value": 10}, True),
            (
                "ensure_not_in_range",
                {"value": 0, "min_value": 1, "max_value": 10},
                True,
            ),
            (
                "ensure_not_in_range",
                {"value": 11, "min_value": 1, "max_value": 10},
                True,
            ),
            (
                "ensure_almost_equal",
                {"left": 1.0, "right": 1.0 + 1e-6, "tol": 1e-6},
                True,
            ),
            (
                "ensure_not_almost_equal",
                {"left": 1.0, "right": 1.0 + 2e-6, "tol": 1e-6},
                True,
            ),
            ("ensure_falsey", {"value": ""}, True),
            ("ensure_truthy", {"value": " "}, True),
            ("ensure_finite", {"value": float("nan")}, ValueError),
            # â”€â”€ Anomaly â”€â”€
            ("ensure_equal", {"left": None, "right": None}, True),
            ("ensure_equal", {"left": object(), "right": object()}, ValueError),
            ("ensure_equal", {"left": iter([1, 2]), "right": [1, 2]}, True),
            ("ensure_count_equal", {"left": (1, 2), "right": [1, 2]}, True),
            ("ensure_type", {"value": None, "typ": type(True)}, TypeError),
            ("ensure_type", {"value": [1, 2, 3], "typ": list}, True),
            ("ensure_subclass", {"cls": type, "parent": object}, True),
            ("ensure_not_subclass", {"cls": object, "parent": object}, TypeError),
            ("ensure_in", {"value": "a", "container": None}, TypeError),
            ("ensure_not_in", {"value": "a", "container": None}, TypeError),
            ("ensure_count_equal", {"left": [1, {}], "right": [1, {}]}, TypeError),
            ("ensure_count_not_equal", {"left": [{1}], "right": [{1}]}, TypeError),
            ("ensure_regex", {"value": "", "pattern": r".*"}, True),
            ("ensure_not_regex", {"value": "", "pattern": r"\d+"}, True),
            ("ensure_path", {"path": ""}, True),
            ("ensure_not_path", {"path": ""}, FileExistsError),
            ("ensure", {"check": None}, ValidationError),
            ("ensure", {"check": lambda: 1 / 0}, ValidationError),
        ],
    )
    def test_validation_boundary_anomaly(self, fn, kwargs, expected_result):
        """BOUNDARY: Edge and unusual inputs return expected pass or failure behavior."""
        if expected_result is not True:
            with pytest.raises(expected_result):
                getattr(mo_validation_kit, fn)(**kwargs, is_exception=True)
        else:
            result = getattr(mo_validation_kit, fn)(**kwargs, is_exception=True)
            assert result == expected_result

    @pytest.mark.parametrize(
        "fn, kwargs, expected_exc_type",
        [
            ("ensure_equal", {"left": object(), "right": object()}, ValueError),
            ("ensure_finite", {"value": float("nan")}, ValueError),
        ],
    )
    def test_boundary_anomaly_json_smoke(self, fn, kwargs, expected_exc_type):
        """ANOMALY: JSON mode preserves exception type for problematic edge inputs."""
        with pytest.raises(MindoffValidationError) as errinfo:
            getattr(mo_validation_kit, fn)(**kwargs, is_exception=False)
        assert errinfo.value.data["type"] == expected_exc_type.__name__

    @pytest.fixture(autouse=True)
    def _reset(self):
        mo_validation_kit.reset()


class TestAggregatedValidator:

    @pytest.mark.parametrize("is_exception", [True, False])
    def test_multiple_failures(self, caplog, capsys, is_exception):
        """REJECTION: Aggregate mode returns or raises all collected failures correctly."""
        mo_validation_kit.ensure_equal(
            left=1, right=2, is_aggregate=True, is_exception=is_exception
        )
        mo_validation_kit.ensure_in(
            value=99,
            container=[1, 2],
            is_aggregate=True,
            is_exception=is_exception,
            msg="Custom Message 99 not in 1,2",
        )
        if is_exception:
            with pytest.raises(ValidationError):
                mo_validation_kit.finalize(return_mode="exception")
        else:
            result = mo_validation_kit.finalize(return_mode="list")
            assert len(result) == 2
            assert result[1]["message"] == "Custom Message 99 not in 1,2"
            for err_obj in result:
                assert (
                    "ValueError" in err_obj["type"] or "LookupError" in err_obj["type"]
                )
            captured = capsys.readouterr()
            assert "Traceback" not in captured.out
            with caplog.at_level(logging.ERROR):
                assert not any("Traceback" in rec.message for rec in caplog.records)

    @pytest.fixture(autouse=True)
    def _reset(self):
        mo_validation_kit.reset()


class TestCustomCodeValidator:
    VALID_CODES = [
        "UNEXPECTED_ERR",
        "NOT_AUTHENTICATED",
        "PERMISSION_DENIED",
        "INVALID_PAYLOAD",
        "INVALID_METHOD",
    ]

    @pytest.mark.parametrize(
        "fn, kwargs, code, is_exception, expected_result",
        [
            # ---- UNEXPECTED_ERR with Exception ----
            ("ensure_equal", {"left": 5, "right": 5}, "UNEXPECTED_ERR", True, True),
            (
                "ensure_not_equal",
                {"left": "x", "right": "y"},
                "UNEXPECTED_ERR",
                True,
                True,
            ),
            ("ensure_greater", {"left": 10, "right": 5}, "UNEXPECTED_ERR", True, True),
            ("ensure_type", {"value": 123, "typ": int}, "UNEXPECTED_ERR", True, True),
            (
                "ensure_in",
                {"value": 2, "container": [1, 2, 3]},
                "UNEXPECTED_ERR",
                True,
                True,
            ),
            ("ensure_finite", {"value": 5}, "UNEXPECTED_ERR", True, True),
            # ---- NOT_AUTHENTICATED with Exception ----
            ("ensure_equal", {"left": 5, "right": 5}, "NOT_AUTHENTICATED", True, True),
            (
                "ensure_not_equal",
                {"left": "x", "right": "y"},
                "NOT_AUTHENTICATED",
                True,
                True,
            ),
            (
                "ensure_greater",
                {"left": 10, "right": 5},
                "NOT_AUTHENTICATED",
                True,
                True,
            ),
            (
                "ensure_type",
                {"value": 123, "typ": int},
                "NOT_AUTHENTICATED",
                True,
                True,
            ),
            (
                "ensure_in",
                {"value": 2, "container": [1, 2, 3]},
                "NOT_AUTHENTICATED",
                True,
                True,
            ),
            # ---- PERMISSION_DENIED with Exception ----
            ("ensure_equal", {"left": 5, "right": 5}, "PERMISSION_DENIED", True, True),
            ("ensure_truthy", {"value": "x"}, "PERMISSION_DENIED", True, True),
            (
                "ensure_subclass",
                {"cls": bool, "parent": int},
                "PERMISSION_DENIED",
                True,
                True,
            ),
            (
                "ensure_not_in",
                {"value": "z", "container": "hello"},
                "PERMISSION_DENIED",
                True,
                True,
            ),
            # ---- INVALID_PAYLOAD with Exception ----
            ("ensure_equal", {"left": 5, "right": 5}, "INVALID_PAYLOAD", True, True),
            (
                "ensure_regex",
                {"value": "abc123", "pattern": r"[a-z]+\d+"},
                "INVALID_PAYLOAD",
                True,
                True,
            ),
            (
                "ensure_not_regex",
                {"value": "xyz", "pattern": r"\d+"},
                "INVALID_PAYLOAD",
                True,
                True,
            ),
            # ---- INVALID_METHOD with Exception ----
            ("ensure_equal", {"left": 5, "right": 5}, "INVALID_METHOD", True, True),
            (
                "ensure_count_equal",
                {"left": [1, 2], "right": [2, 1]},
                "INVALID_METHOD",
                True,
                True,
            ),
            # ---- UNEXPECTED_ERR with JSON Response ----
            ("ensure_equal", {"left": 5, "right": 5}, "UNEXPECTED_ERR", False, True),
            (
                "ensure_not_equal",
                {"left": "x", "right": "y"},
                "UNEXPECTED_ERR",
                False,
                True,
            ),
            ("ensure_greater", {"left": 10, "right": 5}, "UNEXPECTED_ERR", False, True),
            ("ensure_type", {"value": 123, "typ": int}, "UNEXPECTED_ERR", False, True),
            # ---- NOT_AUTHENTICATED with JSON Response ----
            ("ensure_equal", {"left": 5, "right": 5}, "NOT_AUTHENTICATED", False, True),
            ("ensure_truthy", {"value": "x"}, "NOT_AUTHENTICATED", False, True),
            # ---- PERMISSION_DENIED with JSON Response ----
            ("ensure_equal", {"left": 5, "right": 5}, "PERMISSION_DENIED", False, True),
            (
                "ensure_in",
                {"value": 2, "container": [1, 2, 3]},
                "PERMISSION_DENIED",
                False,
                True,
            ),
            # ---- INVALID_PAYLOAD with JSON Response ----
            ("ensure_equal", {"left": 5, "right": 5}, "INVALID_PAYLOAD", False, True),
            (
                "ensure_regex",
                {"value": "abc123", "pattern": r"[a-z]+\d+"},
                "INVALID_PAYLOAD",
                False,
                True,
            ),
            # ---- INVALID_METHOD with JSON Response ----
            ("ensure_equal", {"left": 5, "right": 5}, "INVALID_METHOD", False, True),
            (
                "ensure_count_equal",
                {"left": [1, 2], "right": [2, 1]},
                "INVALID_METHOD",
                False,
                True,
            ),
        ],
    )
    def test_custom_code_acceptance(
        self, fn, kwargs, code, is_exception, expected_result
    ):
        """ACCEPTANCE: Valid checks accept custom error codes without changing success flow."""
        result = getattr(mo_validation_kit, fn)(
            **kwargs, code=code, is_exception=is_exception
        )
        assert result == expected_result

    @pytest.mark.parametrize(
        "fn, kwargs, code, is_exception, expected_exc",
        [
            # â”€â”€ Original entries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # UNEXPECTED_ERR Failures
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "UNEXPECTED_ERR",
                True,
                ValueError,
            ),
            (
                "ensure_not_equal",
                {"left": "x", "right": "x"},
                "UNEXPECTED_ERR",
                True,
                ValueError,
            ),
            (
                "ensure_greater",
                {"left": 2, "right": 5},
                "UNEXPECTED_ERR",
                True,
                ValueError,
            ),
            (
                "ensure_type",
                {"value": "abc", "typ": int},
                "UNEXPECTED_ERR",
                True,
                TypeError,
            ),
            (
                "ensure_in",
                {"value": 99, "container": [1, 2, 3]},
                "UNEXPECTED_ERR",
                True,
                LookupError,
            ),
            # NOT_AUTHENTICATED Failures
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "NOT_AUTHENTICATED",
                True,
                ValueError,
            ),
            ("ensure_truthy", {"value": ""}, "NOT_AUTHENTICATED", True, ValueError),
            (
                "ensure_not_in",
                {"value": 2, "container": [1, 2, 3]},
                "NOT_AUTHENTICATED",
                True,
                LookupError,
            ),
            # PERMISSION_DENIED Failures
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "PERMISSION_DENIED",
                True,
                ValueError,
            ),
            (
                "ensure_subclass",
                {"cls": int, "parent": str},
                "PERMISSION_DENIED",
                True,
                TypeError,
            ),
            (
                "ensure_finite",
                {"value": float("inf")},
                "PERMISSION_DENIED",
                True,
                ValueError,
            ),
            # INVALID_PAYLOAD Failures
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "INVALID_PAYLOAD",
                True,
                ValueError,
            ),
            (
                "ensure_regex",
                {"value": "abc", "pattern": r"\d+"},
                "INVALID_PAYLOAD",
                True,
                ValueError,
            ),
            (
                "ensure_not_regex",
                {"value": "123", "pattern": r"\d+"},
                "INVALID_PAYLOAD",
                True,
                ValueError,
            ),
            # INVALID_METHOD Failures
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "INVALID_METHOD",
                True,
                ValueError,
            ),
            (
                "ensure_count_equal",
                {"left": [1, 2], "right": [2, 2]},
                "INVALID_METHOD",
                True,
                ValueError,
            ),
            # JSON Response Failures (original)
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "UNEXPECTED_ERR",
                False,
                ValueError,
            ),
            (
                "ensure_not_equal",
                {"left": "x", "right": "x"},
                "UNEXPECTED_ERR",
                False,
                ValueError,
            ),
            (
                "ensure_greater",
                {"left": 2, "right": 5},
                "UNEXPECTED_ERR",
                False,
                ValueError,
            ),
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "NOT_AUTHENTICATED",
                False,
                ValueError,
            ),
            ("ensure_truthy", {"value": ""}, "NOT_AUTHENTICATED", False, ValueError),
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "PERMISSION_DENIED",
                False,
                ValueError,
            ),
            (
                "ensure_subclass",
                {"cls": int, "parent": str},
                "PERMISSION_DENIED",
                False,
                TypeError,
            ),
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_regex",
                {"value": "abc", "pattern": r"\d+"},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_equal",
                {"left": 3, "right": 5},
                "INVALID_METHOD",
                False,
                ValueError,
            ),
            (
                "ensure_count_equal",
                {"left": [1, 2], "right": [2, 2]},
                "INVALID_METHOD",
                False,
                ValueError,
            ),
            # â”€â”€ Absorbed from test_each_validation_method_with_custom_code_failure â”€â”€
            # These 14 fns had no prior coverage of "fail + code= â†’ .code preserved".
            (
                "ensure_not_same",
                {"left": "a", "right": "a"},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_same",
                {"left": [1], "right": [1]},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_greater_equal",
                {"left": 4, "right": 5},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_lesser",
                {"left": 7, "right": 3},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_lesser_equal",
                {"left": 8, "right": 7},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_in_range",
                {"value": 0, "min_value": 1, "max_value": 10},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_not_in_range",
                {"value": 5, "min_value": 1, "max_value": 10},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_almost_equal",
                {"left": 1.0, "right": 1.1, "tol": 1e-6},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            (
                "ensure_not_almost_equal",
                {"left": 1.0, "right": 1.0, "tol": 1e-6},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            ("ensure_falsey", {"value": [1]}, "INVALID_PAYLOAD", False, ValueError),
            (
                "ensure_not_type",
                {"value": 123, "typ": int},
                "INVALID_PAYLOAD",
                False,
                TypeError,
            ),
            (
                "ensure_not_subclass",
                {"cls": bool, "parent": int},
                "INVALID_PAYLOAD",
                False,
                TypeError,
            ),
            (
                "ensure_count_not_equal",
                {"left": [1, 1], "right": [1, 1]},
                "INVALID_PAYLOAD",
                False,
                ValueError,
            ),
            ("ensure", {"check": False}, "INVALID_PAYLOAD", False, ValidationError),
        ],
    )
    def test_custom_code_rejection(self, fn, kwargs, code, is_exception, expected_exc):
        """REJECTION: Failed checks keep custom codes in raised or wrapped errors."""
        if is_exception:
            with pytest.raises(expected_exc):
                getattr(mo_validation_kit, fn)(
                    **kwargs, code=code, is_exception=is_exception
                )
        else:
            with pytest.raises(MindoffValidationError) as errinfo:
                getattr(mo_validation_kit, fn)(
                    **kwargs, code=code, is_exception=is_exception
                )
            assert errinfo.value.data.get("type") == expected_exc.__name__
            assert errinfo.value.code == code

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_custom_code_reflected_in_error_data(self, code):
        """REJECTION: MindoffValidationError exposes the provided custom code."""
        with pytest.raises(MindoffValidationError) as errinfo:
            mo_validation_kit.ensure_equal(
                left=1, right=2, code=code, is_exception=False
            )
        assert errinfo.value.code == code

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_custom_code_in_aggregate_exception(self, code):
        """REJECTION: Aggregate exception mode preserves custom code during finalize."""
        mo_validation_kit.ensure_equal(
            left=1, right=2, is_aggregate=True, is_exception=True, code=code
        )
        with pytest.raises(ValidationError):
            mo_validation_kit.finalize(return_mode="exception")

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_custom_code_in_aggregate_json_response(self, code):
        """REJECTION: Aggregate list mode returns custom code for each failure."""
        mo_validation_kit.ensure_equal(
            left=1, right=2, is_aggregate=True, is_exception=False, code=code
        )
        result = mo_validation_kit.finalize(return_mode="list")
        assert len(result) == 1
        assert result[0]["code"] == code

    @pytest.mark.parametrize(
        "code1, code2, code3",
        [
            ("UNEXPECTED_ERR", "NOT_AUTHENTICATED", "PERMISSION_DENIED"),
            ("INVALID_PAYLOAD", "INVALID_METHOD", "UNEXPECTED_ERR"),
            ("NOT_AUTHENTICATED", "INVALID_PAYLOAD", "INVALID_METHOD"),
        ],
    )
    def test_multiple_custom_codes_in_aggregate(self, code1, code2, code3):
        """BOUNDARY: Aggregate failures keep each custom code in original order."""
        mo_validation_kit.ensure_equal(
            left=1, right=2, is_aggregate=True, is_exception=False, code=code1
        )
        mo_validation_kit.ensure_not_equal(
            left="x", right="x", is_aggregate=True, is_exception=False, code=code2
        )
        mo_validation_kit.ensure_greater(
            left=2, right=5, is_aggregate=True, is_exception=False, code=code3
        )
        result = mo_validation_kit.finalize(return_mode="list")
        assert len(result) == 3
        assert result[0]["code"] == code1
        assert result[1]["code"] == code2
        assert result[2]["code"] == code3

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_custom_code_with_custom_message(self, code):
        """REJECTION: Custom message and custom code are both retained on failure."""
        custom_msg = "Custom validation message"
        with pytest.raises(MindoffValidationError) as errinfo:
            mo_validation_kit.ensure_equal(
                left=1,
                right=2,
                msg=custom_msg,
                code=code,
                is_exception=False,
            )
        assert errinfo.value.code == code
        assert errinfo.value.message == custom_msg

    def test_default_code_is_validation_err(self):
        """BOUNDARY: Default error code is VALIDATION_ERR when no code is supplied."""
        with pytest.raises(MindoffValidationError) as errinfo:
            mo_validation_kit.ensure_equal(left=1, right=2, is_exception=False)
        assert errinfo.value.code == "VALIDATION_ERR"

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_custom_code_in_aggregate_mixed_success_failure(self, code):
        """BOUNDARY: Mixed aggregate results include only failed entries with custom code."""
        mo_validation_kit.ensure_equal(
            left=5, right=5, is_aggregate=True, is_exception=False, code=code
        )
        mo_validation_kit.ensure_equal(
            left=1, right=2, is_aggregate=True, is_exception=False, code=code
        )
        mo_validation_kit.ensure_truthy(
            value="x", is_aggregate=True, is_exception=False, code=code
        )
        result = mo_validation_kit.finalize(return_mode="list")
        assert len(result) == 1
        assert result[0]["code"] == code

    @pytest.fixture(autouse=True)
    def _reset(self):
        mo_validation_kit.reset()


class TestValidationCoverageGaps:

    def test_mindoff_validation_error_rejects_invalid_data_type(self):
        """REJECTION: MindoffValidationError rejects unsupported data payload types."""
        with pytest.raises(TypeError, match="'data' must be dict or list"):
            raise MindoffValidationError(data="not-dict-or-list")

    def test_ensure_equal_comparison_exception_path(self):
        """ANOMALY: Comparison failures are converted into TypeError by ensure_equal."""
        with pytest.raises(TypeError, match="Comparison failed"):
            mo_validation_kit.ensure_equal(left=_CmpBoom(), right=1, is_exception=True)

    @pytest.mark.parametrize(
        "fn, kwargs",
        [
            ("ensure_subclass", {"cls": 1, "parent": int}),
            ("ensure_not_subclass", {"cls": 1, "parent": int}),
            ("ensure_greater", {"left": _CmpBoom(), "right": 1}),
            ("ensure_greater_equal", {"left": _CmpBoom(), "right": 1}),
            ("ensure_lesser", {"left": _CmpBoom(), "right": 1}),
            ("ensure_lesser_equal", {"left": _CmpBoom(), "right": 1}),
            (
                "ensure_in_range",
                {"value": _CmpBoom(), "min_value": _CmpBoom(), "max_value": _CmpBoom()},
            ),
            (
                "ensure_not_in_range",
                {"value": _CmpBoom(), "min_value": _CmpBoom(), "max_value": _CmpBoom()},
            ),
            ("ensure_almost_equal", {"left": "x", "right": 1.0}),
            ("ensure_not_almost_equal", {"left": "x", "right": 1.0}),
        ],
    )
    def test_comparison_helpers_exception_paths(self, fn, kwargs):
        """ANOMALY: Comparison helpers raise TypeError when operators fail internally."""
        with pytest.raises(TypeError):
            getattr(mo_validation_kit, fn)(**kwargs, is_exception=True)

    @pytest.mark.parametrize(
        "fn",
        ["ensure_count_equal", "ensure_count_not_equal"],
    )
    def test_count_helpers_generic_exception_path(self, fn):
        """ANOMALY: Count helpers raise TypeError for iterables that fail during iteration."""
        with pytest.raises(TypeError):
            getattr(mo_validation_kit, fn)(
                left=_BadIterable(),
                right=_BadIterable(),
                is_exception=True,
            )

    @pytest.mark.parametrize(
        "mode, expected",
        [("list", []), ("error", None), ("exception", None)],
    )
    def test_finalize_no_errors_all_return_modes(self, mode, expected):
        """BOUNDARY: Finalize returns empty outputs when no aggregate errors exist."""
        assert mo_validation_kit.finalize(return_mode=mode) == expected

    def test_finalize_default_error_mode_raises_mindoff_validation_error(self):
        """REJECTION: Default finalize mode raises MindoffValidationError for collected failures."""
        mo_validation_kit.ensure_equal(
            left=1,
            right=2,
            is_aggregate=True,
            is_exception=False,
            code="INVALID_PAYLOAD",
        )
        with pytest.raises(MindoffValidationError) as errinfo:
            mo_validation_kit.finalize(
                code="INVALID_PAYLOAD", message="Aggregate failed"
            )

        assert errinfo.value.code == "INVALID_PAYLOAD"
        assert errinfo.value.message == "Aggregate failed"
        assert isinstance(errinfo.value.data, list)
        assert len(errinfo.value.data) == 1

    @pytest.fixture(autouse=True)
    def _reset(self):
        mo_validation_kit.reset()
