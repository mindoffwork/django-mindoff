from typing import Any, Dict, List, Union, get_args, get_origin, Literal
from ..validation_kit import mo_validation_kit

# ----------------
# Constants
# ----------------
MAX_LIST_VARIANTS = 1


# ----------------
# Functions
# ----------------
def validate_schema(
    data: Any,
    schema: Any,
    *,
    max_nesting_depth: int | None = None,
    validation_mode: str = "strict",
) -> None:
    """
    Validate payload data against a nested schema definition used by Mindoff kits.

    The validator supports core Python/typing schema forms (types, `Union`,
    `Literal`, list/dict shorthand, and typed `List`/`Dict`) and performs iterative
    depth-aware traversal to accumulate validation errors through `mo_validation_kit`.

    Args:
        data: Incoming payload value to validate.
        schema: Expected schema definition for the payload.
        max_nesting_depth: Optional maximum allowed nesting depth.
        validation_mode: Validation mode (`"strict"` requires all schema keys).
    """
    depth = 0
    stack = [(data, schema, depth, "root")]

    while stack:
        value, sch, current_depth, path = stack.pop()
        if max_nesting_depth is not None:
            mo_validation_kit.ensure_lesser_equal(
                current_depth,
                max_nesting_depth,
                msg=f"Payload nesting too deep at {path}",
                is_aggregate=True,
            )

        origin = get_origin(sch)
        args = get_args(sch)

        # ------------------------
        # Dispatch table for schema handling
        # ------------------------
        handler = _get_handler(sch, origin)
        handler(value, sch, origin, args, stack, current_depth, path, validation_mode)

    mo_validation_kit.finalize(code="INVALID_PAYLOAD")


# ------------------------
# Helper Functions
# ------------------------
def _get_handler(sch, origin):
    if isinstance(sch, type):
        return _handle_type
    if origin is Union:
        return _handle_union
    if origin is Literal:
        return _handle_literal
    if isinstance(sch, list):
        return _handle_list_shorthand
    if origin in (list, List):
        return _handle_list_typehint
    if origin in (dict, Dict):
        return _handle_dict_typehint
    if isinstance(sch, dict):
        return _handle_dict_shorthand
    raise TypeError(f"Unsupported payload schema: {sch}")


def _handle_type(value, sch, origin, args, stack, depth, path, mode):
    mo_validation_kit.ensure_type(
        value,
        sch,
        msg=f"{path} must be {sch.__name__}, got {type(value).__name__}",
        is_aggregate=True,
    )


def _handle_union(value, sch, origin, args, stack, depth, path, mode):
    if type(None) in args and value is None:
        return
    non_none_args = [a for a in args if a is not type(None)]
    __success = False
    for t in non_none_args:
        stack.append((value, t, depth, path))
        __success = True
        break
    mo_validation_kit.ensure_truthy(
        __success,
        msg=f"{path} must match one of {args}, got {type(value).__name__}",
        is_aggregate=True,
    )


def _handle_literal(value, sch, origin, args, stack, depth, path, mode):
    mo_validation_kit.ensure_in(
        value,
        args,
        msg=f"{path} must be one of {args}, got {value}",
        is_aggregate=True,
    )


def _handle_list_shorthand(value, sch, origin, args, stack, depth, path, mode):
    mo_validation_kit.ensure_type(
        value, list, msg=f"{path} must be a list", is_aggregate=True
    )
    if len(sch) == 0:
        return
    mo_validation_kit.ensure_equal(
        len(sch),
        MAX_LIST_VARIANTS,
        msg="List schema must have exactly one type",
        is_aggregate=True,
    )
    for i, item in enumerate(value):
        stack.append((item, sch[0], depth + 1, f"{path}[{i}]"))


def _handle_list_typehint(value, sch, origin, args, stack, depth, path, mode):
    item_type = args[0] if args else Any
    mo_validation_kit.ensure_type(
        value, list, msg=f"{path} must be a list", is_aggregate=True
    )
    for i, item in enumerate(value):
        stack.append((item, item_type, depth + 1, f"{path}[{i}]"))


def _handle_dict_typehint(value, sch, origin, args, stack, depth, path, mode):
    key_type, val_type = args if args else (str, Any)
    mo_validation_kit.ensure_type(value, dict, is_aggregate=True)
    for k, v in value.items():
        mo_validation_kit.ensure_type(
            k,
            key_type,
            msg=f"{path} key must be {key_type.__name__}",
            is_aggregate=True,
        )
        stack.append((v, val_type, depth + 1, f"{path}.{k}"))


def _handle_dict_shorthand(value, sch, origin, args, stack, depth, path, mode):
    mo_validation_kit.ensure_type(value, dict, is_aggregate=True)

    for k, subschema in sch.items():
        if k not in value:
            if mode == "strict":
                mo_validation_kit.ensure_truthy(
                    False,
                    msg=f"Missing key '{k}' in {path}",
                    is_aggregate=True,
                )
            continue
        stack.append((value[k], subschema, depth + 1, f"{path}.{k}"))
