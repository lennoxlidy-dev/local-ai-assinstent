"""Build small QObject view-models for QML: one notifying property per field, ``set(**values)`` to update."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal


def model_class(name: str, fields: dict[str, tuple[Any, Any]], base: Any = QObject) -> type:
    """``fields``: name -> (Qt type, default). Returns a class with read-only notifying properties."""
    ns: dict[str, Any] = {"_FIELDS": fields}
    for fname, (typ, _default) in fields.items():
        sig = Signal()
        ns[f"{fname}Changed"] = sig
        ns[fname] = Property(typ, (lambda n: lambda self: self._values[n])(fname), notify=sig)

    def __init__(self: Any, parent: QObject | None = None) -> None:
        base.__init__(self, parent)
        self._values = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                        for k, (_, v) in fields.items()}

    def get(self: Any, key: str) -> Any:
        return self._values[key]

    def set(self: Any, **values: Any) -> None:
        for k, v in values.items():
            if k not in self._values:
                raise AttributeError(k)
            if self._values[k] != v or isinstance(v, (list, dict)):
                self._values[k] = v
                getattr(self, f"{k}Changed").emit()

    ns.update(__init__=__init__, get=get, set=set)
    return type(name, (base,), ns)


__all__ = ["model_class"]
