from __future__ import annotations
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, Literal
from .helper_kit import mo_helper_kit


# ----------------
# Classes
# ----------------
class MindoffValidationError(Exception):
    """Structured validation exception with response metadata (`message`, `code`, `category`, `data`)."""

    def __init__(
        self,
        *,
        message: str = "Validation Failed",
        code: str = "VALIDATION_ERR",
        category: str = "danger",
        data: Optional[Dict[str, Any]] = None,
    ):
        if data is not None and not isinstance(data, (dict, list)):
            raise TypeError(f"'data' must be dict or list, got {type(data).__name__}")
        self.message = message
        self.code = code
        self.category = category
        self.data = data or {}
        super().__init__(message)


class ValidationError(Exception):
    """Fallback exception for validation failures."""


class MindoffValidator:
    """
    Central validation engine for Mindoff kits with immediate and aggregate modes.

    This validator provides a broad set of `ensure_*` guards for type, value,
    collection, regex, path, and custom checks. Each check can either raise
    immediately, accumulate structured errors for later finalization, or return
    success directly, enabling consistent validation behavior across all kits.
    """

    def __init__(self) -> None:
        self._errors: List[_ErrorItem] = []

    # 1. Equality & Comparison
    def ensure_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two values are equal.

        Usage:

        ```python
        mo_validation_kit.ensure_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_equal(2 + 2, 4, is_exception=True)
        ```
        """
        try:
            if (
                hasattr(left, "__iter__")
                and hasattr(right, "__iter__")
                and not isinstance(left, (str, bytes, dict))
                and not isinstance(right, (str, bytes, dict))
            ):
                ok = list(left) == list(right)
            else:
                ok = left == right
            exc = ValueError
            default_message = f"Expected equal: {left!r} vs {right!r}"
        except Exception:
            ok = False
            exc = TypeError
            default_message = f"Comparison failed: {left!r} vs {right!r}"

        message = msg or default_message
        return self._record_or_raise(
            ok=ok,
            fn="ensure_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two values are not equal.

        Usage:

        ```python
        mo_validation_kit.ensure_not_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_equal("draft", "published", is_exception=True)
        ```
        """
        ok = left != right
        message = msg or f"Expected {left!r} != {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_equal",
            exc_type=ValueError,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_same(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two references point to the same object.

        Usage:

        ```python
        mo_validation_kit.ensure_same(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_same(request.user, owner, is_exception=True)
        ```
        """
        ok = left is right
        message = msg or f"Expected {left!r} is {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_is",
            exc_type=ValueError,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_same(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two references do not point to the same object.

        Usage:

        ```python
        mo_validation_kit.ensure_not_same(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_same(current_user, banned_user, is_exception=True)
        ```
        """
        ok = left is not right
        message = msg or f"Expected {left!r} is not {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_is",
            exc_type=ValueError,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_greater(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `left` is greater than `right`.

        Usage:

        ```python
        mo_validation_kit.ensure_greater(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_greater(order_total, 0, is_exception=True)
        ```
        """
        try:
            ok = left > right
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} > {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_greater",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_greater_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `left` is greater than or equal to `right`.

        Usage:

        ```python
        mo_validation_kit.ensure_greater_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_greater_equal(stock_qty, 0, is_exception=True)
        ```
        """
        try:
            ok = left >= right
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} >= {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_greater_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_lesser(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `left` is less than `right`.

        Usage:

        ```python
        mo_validation_kit.ensure_lesser(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_lesser(discount, subtotal, is_exception=True)
        ```
        """
        try:
            ok = left < right
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} < {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_lesser",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_lesser_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `left` is less than or equal to `right`.

        Usage:

        ```python
        mo_validation_kit.ensure_lesser_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_lesser_equal(page_size, 100, is_exception=True)
        ```
        """
        try:
            ok = left <= right
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} <= {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_lesser_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_in_range(
        self,
        value: float,
        min_value: float,
        max_value: float,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value is inside an inclusive numeric range.

        Usage:

        ```python
        mo_validation_kit.ensure_in_range(
            value: float,
            min_value: float,
            max_value: float,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`float`): Value being validated.
        - `min_value` (`float`): Minimum allowed value.
        - `max_value` (`float`): Maximum allowed value.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_in_range(rating, 1, 5, is_exception=True)
        ```
        """
        try:
            ok = min_value <= value <= max_value
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {value!r} in range [{min_value}, {max_value}]"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_in_range",
            exc_type=exc,
            message=message,
            context={"value": value, "min_value": min_value, "max_value": max_value},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_in_range(
        self,
        value: float,
        min_value: float,
        max_value: float,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value is outside an inclusive numeric range.

        Usage:

        ```python
        mo_validation_kit.ensure_not_in_range(
            value: float,
            min_value: float,
            max_value: float,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`float`): Value being validated.
        - `min_value` (`float`): Minimum allowed value.
        - `max_value` (`float`): Maximum allowed value.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_in_range(age, 0, 12, is_exception=True)
        ```
        """
        try:
            ok = not (min_value <= value <= max_value)
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {value!r} not in range [{min_value}, {max_value}]"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_in_range",
            exc_type=exc,
            message=message,
            context={"value": value, "min_value": min_value, "max_value": max_value},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_almost_equal(
        self,
        left: float,
        right: float,
        tol: float = 1e-6,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two floating-point values are approximately equal within tolerance.

        Usage:

        ```python
        mo_validation_kit.ensure_almost_equal(
            left: float,
            right: float,
            tol: float = 1e-6,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`float`): First value to compare.
        - `right` (`float`): Second value to compare.
        - `tol` (`float, default=1e-6`): Allowed tolerance for floating-point comparisons.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_almost_equal(total, expected, tol=1e-6, is_exception=True)
        ```
        """
        try:
            ok = math.isclose(left, right, abs_tol=tol)
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} â‰ˆ {right!r} (tol={tol})"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_almost_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right, "tol": tol},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_almost_equal(
        self,
        left: float,
        right: float,
        tol: float = 1e-6,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two floating-point values are not approximately equal within tolerance.

        Usage:

        ```python
        mo_validation_kit.ensure_not_almost_equal(
            left: float,
            right: float,
            tol: float = 1e-6,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`float`): First value to compare.
        - `right` (`float`): Second value to compare.
        - `tol` (`float, default=1e-6`): Allowed tolerance for floating-point comparisons.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_almost_equal(score, 0.0, tol=1e-9, is_exception=True)
        ```
        """
        try:
            ok = not math.isclose(left, right, abs_tol=tol)
            exc = ValueError
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Expected {left!r} not â‰ˆ {right!r} (tol={tol})"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_almost_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right, "tol": tol},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 2. Truthiness

    def ensure_falsey(
        self,
        value: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value evaluates to `False`.

        Usage:

        ```python
        mo_validation_kit.ensure_falsey(
            value: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_falsey(payload.get("debug"), is_exception=True)
        ```
        """
        ok = not bool(value)
        message = msg or f"Condition failed: expected truthy, got {value!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_falsey",
            exc_type=ValueError,
            message=message,
            context={"condition": value},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_truthy(
        self,
        value: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value evaluates to `True`.

        Usage:

        ```python
        mo_validation_kit.ensure_truthy(
            value: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_truthy(payload.get("customer_id"), is_exception=True)
        ```
        """
        ok = bool(value)
        message = msg or f"Condition failed: expected falsy, got {value!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_truthy",
            exc_type=ValueError,
            message=message,
            context={"condition": value},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 3. Types & Classes

    def ensure_type(
        self,
        value: Any,
        typ: type,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value is an instance of the expected type.

        Usage:

        ```python
        mo_validation_kit.ensure_type(
            value: Any,
            typ: type,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `typ` (`type`): Expected Python type.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_type(payload, dict, is_exception=True)
        ```
        """
        ok = isinstance(value, typ)
        message = (
            msg
            or f"Expected type {getattr(typ, '__name__', typ)!r}, got {type(value).__name__!r}"
        )
        return self._record_or_raise(
            ok=ok,
            fn="ensure_type",
            exc_type=TypeError,
            message=message,
            context={"value": value, "typ": getattr(typ, "__name__", str(typ))},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_type(
        self,
        value: Any,
        typ: type,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a value is not an instance of the provided type.

        Usage:

        ```python
        mo_validation_kit.ensure_not_type(
            value: Any,
            typ: type,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `typ` (`type`): Expected Python type.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_type(payload, list, is_exception=True)
        ```
        """
        ok = not isinstance(value, typ)
        message = msg or f"Expected not type {getattr(typ, '__name__', typ)!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_type",
            exc_type=TypeError,
            message=message,
            context={"value": value, "typ": getattr(typ, "__name__", str(typ))},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_subclass(
        self,
        cls: type,
        parent: type,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `cls` is a subclass of `parent`.

        Usage:

        ```python
        mo_validation_kit.ensure_subclass(
            cls: type,
            parent: type,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `cls` (`type`): Class to validate.
        - `parent` (`type`): Expected parent class.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_subclass(CustomError, Exception, is_exception=True)
        ```
        """
        try:
            ok = issubclass(cls, parent)
            exc = TypeError
        except Exception:
            ok = False
            exc = TypeError
        message = (
            msg
            or f"{getattr(cls, '__name__', cls)!r} is not subclass of {getattr(parent, '__name__', parent)!r}"
        )
        return self._record_or_raise(
            ok=ok,
            fn="ensure_subclass",
            exc_type=exc,
            message=message,
            context={
                "cls": getattr(cls, "__name__", str(cls)),
                "parent": getattr(parent, "__name__", str(parent)),
            },
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_subclass(
        self,
        cls: type,
        parent: type,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `cls` is not a subclass of `parent`.

        Usage:

        ```python
        mo_validation_kit.ensure_not_subclass(
            cls: type,
            parent: type,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `cls` (`type`): Class to validate.
        - `parent` (`type`): Expected parent class.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_subclass(int, Exception, is_exception=True)
        ```
        """
        try:
            ok = not issubclass(cls, parent)
            exc = TypeError
        except Exception:
            ok = False
            exc = TypeError
        message = (
            msg
            or f"{getattr(cls, '__name__', cls)!r} is a subclass of {getattr(parent, '__name__', parent)!r}"
        )
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_subclass",
            exc_type=exc,
            message=message,
            context={
                "cls": getattr(cls, "__name__", str(cls)),
                "parent": getattr(parent, "__name__", str(parent)),
            },
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 4. Containers & Collections

    def ensure_in(
        self,
        value: Any,
        container: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `value` exists in `container`.

        Usage:

        ```python
        mo_validation_kit.ensure_in(
            value: Any,
            container: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `container` (`Any`): Container used for membership checks.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_in(status, {"queued", "running", "done"}, is_exception=True)
        ```
        """
        exc = KeyError if isinstance(container, dict) else LookupError
        try:
            ok = value in container
        except Exception:
            ok = False
            exc = TypeError
            msg = msg or "Container is not iterable or does not support membership test"
        message = msg or f"Expected {value!r} in {container!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_in",
            exc_type=exc,
            message=message,
            context={"value": value, "container": container},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_in(
        self,
        value: Any,
        container: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that `value` does not exist in `container`.

        Usage:

        ```python
        mo_validation_kit.ensure_not_in(
            value: Any,
            container: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `container` (`Any`): Container used for membership checks.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_in(role, {"banned", "blocked"}, is_exception=True)
        ```
        """
        exc = KeyError if isinstance(container, dict) else LookupError
        try:
            ok = value not in container
        except Exception:
            ok = False
            exc = TypeError
            msg = msg or "Container is not iterable or does not support membership test"
        message = msg or f"Expected {value!r} not in {container!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_in",
            exc_type=exc,
            message=message,
            context={"value": value, "container": container},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_count_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two iterables have equal element counts.

        Usage:

        ```python
        mo_validation_kit.ensure_count_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_count_equal(["a", "b"], ["b", "a"], is_exception=True)
        ```
        """
        try:
            from collections import Counter

            ok = Counter(left) == Counter(right)
            exc = ValueError
        except TypeError:
            ok = False
            exc = TypeError
            msg = msg or "Elements must be hashable for ensure_count_equal"
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Counts not equal: {left!r} vs {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_count_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_count_not_equal(
        self,
        left: Any,
        right: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that two iterables do not have equal element counts.

        Usage:

        ```python
        mo_validation_kit.ensure_count_not_equal(
            left: Any,
            right: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `left` (`Any`): First value to compare.
        - `right` (`Any`): Second value to compare.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_count_not_equal(["a", "a"], ["a", "b"], is_exception=True)
        ```
        """
        try:
            from collections import Counter

            ok = Counter(left) != Counter(right)
            exc = ValueError
        except TypeError:
            ok = False
            exc = TypeError
            msg = msg or "Elements must be hashable for ensure_count_not_equal"
        except Exception:
            ok = False
            exc = TypeError
        message = msg or f"Counts unexpectedly equal: {left!r} vs {right!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_count_not_equal",
            exc_type=exc,
            message=message,
            context={"left": left, "right": right},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 5. Numeric / Regex / File

    def ensure_finite(
        self,
        value: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a numeric value is finite.

        Usage:

        ```python
        mo_validation_kit.ensure_finite(
            value: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_finite(latency_ms, is_exception=True)
        ```
        """
        if not isinstance(value, (int, float)):
            return self._record_or_raise(
                ok=False,
                fn="ensure_finite",
                exc_type=TypeError,
                message=msg or f"Expected number, got {type(value).__name__}",
                context={"value": value},
                is_exception=is_exception,
                is_aggregate=is_aggregate,
                code=code,
            )
        ok = math.isfinite(value)
        message = msg or f"Expected finite number, got {value!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_finite",
            exc_type=ValueError,
            message=message,
            context={"value": value},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_regex(
        self,
        value: Any,
        pattern: str,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a string fully matches a regex pattern.

        Usage:

        ```python
        mo_validation_kit.ensure_regex(
            value: Any,
            pattern: str,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `pattern` (`str`): Regex pattern for full-string matching.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_regex(email, r"^[^@]+@[^@]+\\.[^@]+$", is_exception=True)
        ```
        """
        if not isinstance(value, str):
            return self._record_or_raise(
                ok=False,
                fn="ensure_regex",
                exc_type=TypeError,
                message=msg or f"Expected str to match, got {type(value).__name__}",
                context={"value": value, "pattern": pattern},
                is_exception=is_exception,
                is_aggregate=is_aggregate,
                code=code,
            )
        ok = bool(re.fullmatch(pattern, value))
        message = msg or f"String {value!r} does not match {pattern!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_regex",
            exc_type=ValueError,
            message=message,
            context={"value": value, "pattern": pattern},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_regex(
        self,
        value: Any,
        pattern: str,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a string does not fully match a regex pattern.

        Usage:

        ```python
        mo_validation_kit.ensure_not_regex(
            value: Any,
            pattern: str,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `value` (`Any`): Value being validated.
        - `pattern` (`str`): Regex pattern for full-string matching.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_regex(username, r"^admin$", is_exception=True)
        ```
        """
        if not isinstance(value, str):
            return self._record_or_raise(
                ok=False,
                fn="ensure_not_regex",
                exc_type=TypeError,
                message=msg or f"Expected str to test, got {type(value).__name__}",
                context={"value": value, "pattern": pattern},
                is_exception=is_exception,
                is_aggregate=is_aggregate,
                code=code,
            )
        ok = not bool(re.fullmatch(pattern, value))
        message = msg or f"String {value!r} matches {pattern!r}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_regex",
            exc_type=ValueError,
            message=message,
            context={"value": value, "pattern": pattern},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_path(
        self,
        path: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a filesystem path exists.

        Usage:

        ```python
        mo_validation_kit.ensure_path(
            path: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `path` (`Any`): Filesystem path to validate.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_path("/tmp/report.csv", is_exception=True)
        ```
        """
        p = Path(path)
        ok = p.exists()
        message = msg or f"Path exists (but should not): {p}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_path",
            exc_type=FileNotFoundError,
            message=message,
            context={"path": str(p)},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    def ensure_not_path(
        self,
        path: Any,
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
    ):
        """
        Validate that a filesystem path does not exist.

        Usage:

        ```python
        mo_validation_kit.ensure_not_path(
            path: Any,
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
        )
        ```

        Parameters:

        - `path` (`Any`): Filesystem path to validate.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure_not_path("/tmp/lockfile", is_exception=True)
        ```
        """
        p = Path(path)
        ok = not p.exists()
        message = msg or f"Path does not exist: {p}"
        return self._record_or_raise(
            ok=ok,
            fn="ensure_not_path",
            exc_type=FileExistsError,
            message=message,
            context={"path": str(p)},
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 6. Custom

    def ensure(
        self,
        check: Union[bool, Callable[[], bool]],
        *,
        msg: Optional[str] = None,
        code: str = "VALIDATION_ERR",
        is_exception: bool = False,
        is_aggregate: bool = False,
        exc_type: type[Exception] = ValidationError,
    ):
        """
        Validate a custom boolean or callable check.

        Usage:

        ```python
        mo_validation_kit.ensure(
            check: Union[bool, Callable[[], bool]],
            msg: Optional[str] = None,
            code: str = "VALIDATION_ERR",
            is_exception: bool = False,
            is_aggregate: bool = False,
            exc_type: type[Exception] = ValidationError,
        )
        ```

        Parameters:

        - `check` (`Union[bool, Callable[[], bool]]`): Boolean or callable returning a boolean.
        - `msg` (`Optional[str], default=None`): Custom validation error message.
        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `is_exception` (`bool, default=False`): If `True`, raises the method-specific exception immediately.
        - `is_aggregate` (`bool, default=False`): If `True`, stores validation failure and continues execution.
        - `exc_type` (`type[Exception], default=ValidationError`): Exception class used by `ensure` when `is_exception=True`.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.ensure(lambda: total_qty > 0, msg="total_qty must be > 0", is_exception=True)
        ```
        """
        try:
            ok = bool(check() if callable(check) else check)
        except Exception as e:
            ok = False
            msg = msg or f"ensure check error: {e}"
        message = msg or "ensure check failed"
        return self._record_or_raise(
            ok=ok,
            fn="ensure",
            exc_type=exc_type,
            message=message,
            is_exception=is_exception,
            is_aggregate=is_aggregate,
            code=code,
        )

    # 7. Wrap up and reset

    def finalize(
        self,
        *,
        code: str = "VALIDATION_ERR",
        message: str = "Aggregated Validation Failed",
        return_mode: Literal["list", "error", "exception"] = "error",
    ):
        """
        Finalize aggregated validation failures.

        Usage:

        ```python
        mo_validation_kit.finalize(
            code: str = "VALIDATION_ERR",
            message: str = 'Aggregated Validation Failed',
            return_mode: Literal['list', 'error', 'exception'] = 'error',
        )
        ```

        Parameters:

        - `code` (`str, default="VALIDATION_ERR"`): Error code to attach on validation failure.
        - `message` (`str, default='Aggregated Validation Failed'`): Top-level message used by `finalize` in `error` mode.
        - `return_mode` (`Literal['list', 'error', 'exception'], default='error'`): Finalize output mode: `list`, `error`, or `exception`.

        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        errors = mo_validation_kit.finalize(return_mode="list")
        ```
        """
        try:
            has_errors = bool(self._errors)

            if not has_errors:
                return {
                    "list": [],
                    "error": None,
                    "exception": None,
                }[return_mode]

            if return_mode == "exception":
                raise ValidationError(
                    "\n".join(
                        f"[{e.type}] [{e.code}] {e.message} {e.traceback}"
                        for e in self._errors
                    )
                )

            error_data = [
                {
                    "type": e.type,
                    "code": e.code,
                    "message": e.message,
                    "context": e.context,
                }
                for e in self._errors
            ]

            if return_mode == "list":
                return error_data

            raise MindoffValidationError(
                message=message,
                code=code,
                data=error_data,
            )
        finally:
            self.reset()

    def reset(self):
        """
        Reset validator aggregate state.

        Usage:

        ```python
        mo_validation_kit.reset(
        )
        ```

        Parameters:
        Possible responses:

        - Returns `True` when validation passes.
        - Returns `None` when `is_aggregate=True` and validation fails (error is buffered).
        - Raises method-specific exception when `is_exception=True` and validation fails.
        - Raises `MindoffValidationError` when validation fails in default mode.

        Example:

        ```python
        mo_validation_kit.reset()
        ```
        """
        self._errors.clear()

    def _record_or_raise(
        self,
        *,
        ok: bool,
        fn: str,
        exc_type: type[BaseException],
        message: str,
        context: Optional[Dict[str, Any]] = None,
        is_exception: bool = False,
        is_aggregate: bool = False,
        code: str = "VALIDATION_ERR",
    ):
        context = context or {}
        if ok:
            return True

        tb_text = mo_helper_kit.get_exact_traceback(skip=3)
        item = _ErrorItem(
            fn=fn,
            type=exc_type.__name__,
            message=message,
            context=context,
            traceback=tb_text,
            code=code,
        )

        if is_aggregate:
            self._errors.append(item)
            return None

        if is_exception:
            exc = exc_type(message)
            setattr(exc, "code", code)
            raise exc

        raise MindoffValidationError(
            code=code,
            message=message,
            data={
                "type": item.type,
                "message": item.message,
                "context": item.context,
            },
        )


# ----------------
# Helper Classes
# ----------------
@dataclass
class _ErrorItem:
    fn: str
    type: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    traceback: str = ""
    code: str = "VALIDATION_ERR"


# ----------------
# Entry Point
# ----------------
mo_validation_kit = MindoffValidator()
