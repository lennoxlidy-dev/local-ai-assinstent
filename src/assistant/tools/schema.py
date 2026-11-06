"""A tiny JSON-Schema builder + validator for tool parameters.

The same objects produce (a) the strict JSON schema sent to the LLM
(``additionalProperties: false``, explicit ``required``) and (b) the
validation that runs before any tool executes - never trust model output.

Validation is strict about types and ranges but forgiving in two safe ways
that small models commonly need: numeric/boolean strings are coerced
(``"40"`` -> 40, ``"true"`` -> True) and unknown keys are dropped (reported
in ``dropped``) instead of failing the whole call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class SchemaError(ValueError):
    pass


_MISSING = object()


@dataclass
class Schema:
    description: str = ""
    default: Any = _MISSING

    def to_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def validate(self, value: Any, path: str) -> Any:
        raise NotImplementedError

    def _base(self, typ: str) -> dict[str, Any]:
        out: dict[str, Any] = {"type": typ}
        if self.description:
            out["description"] = self.description
        return out

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING


@dataclass
class Str(Schema):
    enum: tuple[str, ...] | None = None
    min_len: int = 0
    max_len: int | None = None
    pattern: str | None = None

    def to_json(self) -> dict[str, Any]:
        out = self._base("string")
        if self.enum:
            out["enum"] = list(self.enum)
        if self.min_len:
            out["minLength"] = self.min_len
        if self.max_len is not None:
            out["maxLength"] = self.max_len
        return out

    def validate(self, value: Any, path: str) -> Any:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not self.enum:
            value = str(value)
        if not isinstance(value, str):
            raise SchemaError(f"{path}: expected a string")
        if self.enum:
            match = next((e for e in self.enum if e.lower() == value.strip().lower()), None)
            if match is None:
                raise SchemaError(f"{path}: must be one of {', '.join(self.enum)}")
            value = match
        if len(value) < self.min_len:
            raise SchemaError(f"{path}: must not be empty" if self.min_len == 1 else f"{path}: too short")
        if self.max_len is not None and len(value) > self.max_len:
            raise SchemaError(f"{path}: longer than {self.max_len} characters")
        if self.pattern and not re.fullmatch(self.pattern, value):
            raise SchemaError(f"{path}: invalid format")
        return value


@dataclass
class Int(Schema):
    min: int | None = None
    max: int | None = None

    def to_json(self) -> dict[str, Any]:
        out = self._base("integer")
        if self.min is not None:
            out["minimum"] = self.min
        if self.max is not None:
            out["maximum"] = self.max
        return out

    def validate(self, value: Any, path: str) -> Any:
        if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
            value = int(value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{path}: expected an integer")
        if self.min is not None and value < self.min:
            raise SchemaError(f"{path}: must be >= {self.min}")
        if self.max is not None and value > self.max:
            raise SchemaError(f"{path}: must be <= {self.max}")
        return value


@dataclass
class Num(Schema):
    min: float | None = None
    max: float | None = None

    def to_json(self) -> dict[str, Any]:
        out = self._base("number")
        if self.min is not None:
            out["minimum"] = self.min
        if self.max is not None:
            out["maximum"] = self.max
        return out

    def validate(self, value: Any, path: str) -> Any:
        if isinstance(value, str):
            try:
                value = float(value.strip().replace(",", "."))
            except ValueError:
                raise SchemaError(f"{path}: expected a number") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{path}: expected a number")
        value = float(value)
        if value != value:  # NaN
            raise SchemaError(f"{path}: expected a number")
        if self.min is not None and value < self.min:
            raise SchemaError(f"{path}: must be >= {self.min:g}")
        if self.max is not None and value > self.max:
            raise SchemaError(f"{path}: must be <= {self.max:g}")
        return value


@dataclass
class Bool(Schema):
    def to_json(self) -> dict[str, Any]:
        return self._base("boolean")

    def validate(self, value: Any, path: str) -> Any:
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        if not isinstance(value, bool):
            raise SchemaError(f"{path}: expected true or false")
        return value


@dataclass
class Arr(Schema):
    items: Schema = field(default_factory=Str)
    min_items: int = 0
    max_items: int | None = None

    def to_json(self) -> dict[str, Any]:
        out = self._base("array")
        out["items"] = self.items.to_json()
        if self.min_items:
            out["minItems"] = self.min_items
        if self.max_items is not None:
            out["maxItems"] = self.max_items
        return out

    def validate(self, value: Any, path: str) -> Any:
        if not isinstance(value, list):
            raise SchemaError(f"{path}: expected a list")
        if len(value) < self.min_items:
            raise SchemaError(f"{path}: needs at least {self.min_items} item(s)")
        if self.max_items is not None and len(value) > self.max_items:
            raise SchemaError(f"{path}: at most {self.max_items} items")
        return [self.items.validate(v, f"{path}[{i}]") for i, v in enumerate(value)]


@dataclass
class Obj(Schema):
    props: dict[str, Schema] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: free-form object (e.g. tool args inside a routine step): no property checks
    open: bool = False

    def to_json(self) -> dict[str, Any]:
        out = self._base("object")
        if self.open:
            return out
        out["properties"] = {k: v.to_json() for k, v in self.props.items()}
        out["required"] = list(self.required)
        out["additionalProperties"] = False
        return out

    def validate(self, value: Any, path: str = "args", dropped: list[str] | None = None) -> Any:
        if value is None and not self.required:
            value = {}
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: expected an object")
        if self.open:
            return dict(value)
        out: dict[str, Any] = {}
        for key, raw in value.items():
            sch = self.props.get(key)
            if sch is None:
                if dropped is not None:
                    dropped.append(f"{path}.{key}")
                continue
            if raw is None and key not in self.required:
                continue  # models often send null for "not set"
            if isinstance(sch, Obj):
                out[key] = sch.validate(raw, f"{path}.{key}", dropped)
            else:
                out[key] = sch.validate(raw, f"{path}.{key}")
        for key in self.required:
            if key not in out:
                raise SchemaError(f"{path}.{key}: required")
        for key, sch in self.props.items():
            if key not in out and sch.has_default:
                out[key] = sch.default
        return out


def obj(required: tuple[str, ...] | list[str] = (), **props: Schema) -> Obj:
    """``obj(("path",), path=Str("File path"), overwrite=Bool(default=False))``"""
    return Obj(props=props, required=tuple(required))
