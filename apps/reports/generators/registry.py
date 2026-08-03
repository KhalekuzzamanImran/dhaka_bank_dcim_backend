from __future__ import annotations

from typing import Type

from .base import BaseReportGenerator

_GENERATORS: dict[str, type[BaseReportGenerator]] = {}


def register_generator(generator_key: str, generator_class: type[BaseReportGenerator] | None = None):
    normalized_key = str(generator_key or "").strip().lower()
    if not normalized_key:
        raise ValueError("Generator key is required.")
    if generator_class is None:
        def _decorator(cls: type[BaseReportGenerator]):
            register_generator(normalized_key, cls)
            return cls

        return _decorator

    existing = _GENERATORS.get(normalized_key)
    if existing is not None and existing is not generator_class:
        raise ValueError(f"Generator key '{normalized_key}' is already registered by {existing.__name__}.")
    _GENERATORS[normalized_key] = generator_class
    return generator_class


def get_generator_class(generator_key: str) -> type[BaseReportGenerator]:
    normalized_key = str(generator_key or "").strip().lower()
    try:
        return _GENERATORS[normalized_key]
    except KeyError as exc:  # pragma: no cover - defensive
        raise LookupError(f"Unknown generator key: {generator_key}") from exc


def get_registered_generators() -> dict[str, type[BaseReportGenerator]]:
    return dict(_GENERATORS)


def get_supported_formats(generator_key: str) -> tuple[str, ...]:
    generator_class = get_generator_class(generator_key)
    return tuple(str(value).strip().upper() for value in getattr(generator_class, "supported_formats", ()) if str(value).strip())
