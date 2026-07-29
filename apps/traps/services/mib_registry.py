from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.traps.models import MIBDefinitionStatus, SNMPMIBDefinition

logger = logging.getLogger(__name__)

MIB_REGISTRY_CACHE_KEY = "snmp-mib-registry-cache-version"

BUILTIN_OIDS = {
    "iso": "1",
    "org": "1.3",
    "dod": "1.3.6",
    "internet": "1.3.6.1",
    "directory": "1.3.6.1.1",
    "mgmt": "1.3.6.1.2",
    "mib-2": "1.3.6.1.2.1",
    "transmission": "1.3.6.1.2.1.10",
    "experimental": "1.3.6.1.3",
    "private": "1.3.6.1.4",
    "enterprises": "1.3.6.1.4.1",
    "snmpTraps": "1.3.6.1.6.3.1.1.5",
}

_CACHE_MISS = {"__missing__": True}

_MODULE_RE = re.compile(r"^\s*(?P<module>[A-Za-z][A-Za-z0-9-]*)\s+DEFINITIONS\s+::=\s+BEGIN\b", re.MULTILINE)
_OBJECT_ID_RE = re.compile(r"^\s*(?P<symbol>[A-Za-z][A-Za-z0-9-]*)\s+OBJECT\s+IDENTIFIER\s+::=\s+\{\s*(?P<expr>[^}]+)\s*\}\s*$")
_TRAP_START_RE = re.compile(r"^\s*(?P<symbol>[A-Za-z][A-Za-z0-9-]*)\s+TRAP-TYPE\b")
_NOTIFICATION_START_RE = re.compile(r"^\s*(?P<symbol>[A-Za-z][A-Za-z0-9-]*)\s+NOTIFICATION-TYPE\b")
_TYPE_START_RE = re.compile(r"^\s*(?:[A-Za-z][A-Za-z0-9-]*)\s+(?:TRAP-TYPE|NOTIFICATION-TYPE)\b")
_STATUS_RE = re.compile(r"\bSTATUS\s+(?P<status>[A-Za-z-]+)\b")
_ENTERPRISE_RE = re.compile(r"\bENTERPRISE\s+(?P<enterprise>[A-Za-z][A-Za-z0-9-]*)\b")
_OBJECTS_RE = re.compile(r"\b(?:OBJECTS|VARIABLES)\s+\{\s*(?P<objects>[^}]*)\s*\}", re.IGNORECASE)
_SPECIFIC_RE = re.compile(r"^\s*::=\s*(?P<number>-?\d+)\s*$", re.MULTILINE)
_NOTIFICATION_OID_RE = re.compile(r"::=\s*\{\s*(?P<expr>[^}]+)\s*\}\s*$")


@dataclass(frozen=True)
class MIBTrapDefinition:
    oid: str
    module: str
    symbol: str
    description: str | None = None
    status: str | None = None
    objects: tuple[str, ...] = ()
    source_file: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class MIBImportSummary:
    files_processed: int = 0
    definitions_imported: int = 0
    definitions_updated: int = 0
    definitions_deactivated: int = 0
    files_skipped: int = 0
    files_failed: int = 0


def normalize_oid(value) -> str:
    if value is None:
        raise ValueError("OID value cannot be empty.")
    text = str(value).strip().lstrip(".")
    if not text:
        raise ValueError("OID value cannot be empty.")
    return text


def is_mib_fallback_enabled() -> bool:
    return bool(getattr(settings, "SNMP_MIB_FALLBACK_ENABLED", True))


def get_mib_registry_cache_ttl_seconds() -> int:
    return int(getattr(settings, "SNMP_MIB_CACHE_TTL_SECONDS", 3600))


def get_mib_max_file_size_bytes() -> int:
    return int(getattr(settings, "SNMP_MIB_MAX_IMPORT_FILE_SIZE_BYTES", 5 * 1024 * 1024))


def get_mib_max_description_length() -> int:
    return int(getattr(settings, "SNMP_MIB_MAX_DESCRIPTION_LENGTH", 8000))


def _strip_control_characters(value: str) -> str:
    return "".join(ch for ch in value if ch >= " " or ch in "\n\r\t")


def _sanitize_description(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _strip_control_characters(value).strip()
    if not cleaned:
        return None
    max_length = get_mib_max_description_length()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def _normalize_status(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"CURRENT", "DEPRECATED", "OBSOLETE"}:
        return normalized
    return MIBDefinitionStatus.UNKNOWN


def _clean_text(text: str) -> str:
    return re.sub(r"--.*$", "", text, flags=re.MULTILINE)


def _load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"{path} is not a regular file.")
    size = path.stat().st_size
    if size > get_mib_max_file_size_bytes():
        raise ValueError(f"{path.name} exceeds the maximum supported MIB file size.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _resolve_oid_expression(expr: str, resolved: dict[str, str]) -> str:
    tokens = [token for token in re.split(r"\s+", expr.strip()) if token]
    if not tokens:
        raise ValueError("OID expression cannot be empty.")

    parent_token, *suffix_tokens = tokens
    if re.fullmatch(r"\d+(?:\.\d+)*", parent_token):
        oid = normalize_oid(parent_token)
    else:
        oid = resolved.get(parent_token) or BUILTIN_OIDS.get(parent_token)
        if not oid:
            raise KeyError(parent_token)

    for token in suffix_tokens:
        if re.fullmatch(r"\d+(?:\.\d+)*", token):
            oid = f"{oid}.{token}"
            continue
        nested = resolved.get(token) or BUILTIN_OIDS.get(token)
        if not nested:
            raise KeyError(token)
        oid = nested if nested.startswith(f"{oid}.") else f"{oid}.{nested.split('.')[-1]}"
    return normalize_oid(oid)


def _extract_module_name(text: str, fallback: str) -> str:
    match = _MODULE_RE.search(text)
    if match:
        return match.group("module")
    return fallback


def _extract_status(block: str) -> str:
    match = _STATUS_RE.search(block)
    return _normalize_status(match.group("status") if match else None)


def _extract_objects(block: str) -> tuple[str, ...]:
    match = _OBJECTS_RE.search(block)
    if not match:
        return ()
    raw = match.group("objects")
    objects: list[str] = []
    for token in re.split(r"[\s,]+", raw.strip()):
        token = token.strip()
        if token:
            objects.append(token)
    return tuple(objects)


def _extract_description(block: str) -> str | None:
    marker = "DESCRIPTION"
    index = block.find(marker)
    if index == -1:
        return None
    tail = block[index + len(marker) :]
    end_candidates = [pos for pos in (tail.find("::="), tail.find("--#")) if pos != -1]
    segment = tail[: min(end_candidates)] if end_candidates else tail
    quoted = re.search(r'"(?P<desc>.*?)"', segment, re.S)
    if quoted:
        return _sanitize_description(quoted.group("desc"))
    return _sanitize_description(segment)


def _parse_trap_block(block: str, *, module_name: str, source_file: str, content_hash: str, resolved_oids: dict[str, str]) -> MIBTrapDefinition | None:
    start = _TRAP_START_RE.match(block.splitlines()[0] if block else "")
    if not start:
        return None
    symbol = start.group("symbol")
    enterprise_match = _ENTERPRISE_RE.search(block)
    specific_match = _SPECIFIC_RE.search(block)
    if not enterprise_match or not specific_match:
        logger.warning("Skipping trap definition with incomplete OID data module=%s symbol=%s source=%s", module_name, symbol, source_file)
        return None

    enterprise_symbol = enterprise_match.group("enterprise")
    enterprise_oid = resolved_oids.get(enterprise_symbol) or BUILTIN_OIDS.get(enterprise_symbol)
    if not enterprise_oid:
        logger.warning("Skipping trap definition because enterprise could not be resolved module=%s symbol=%s enterprise=%s source=%s", module_name, symbol, enterprise_symbol, source_file)
        return None

    specific = int(specific_match.group("number"))
    if specific < 0:
        logger.warning("Skipping trap definition because specific trap was negative module=%s symbol=%s source=%s", module_name, symbol, source_file)
        return None

    description = _extract_description(block)
    objects = _extract_objects(block)
    status = _extract_status(block)
    return MIBTrapDefinition(
        oid=f"{normalize_oid(enterprise_oid)}.0.{specific}",
        module=module_name,
        symbol=symbol,
        description=description,
        status=status,
        objects=objects,
        source_file=source_file,
        content_hash=content_hash,
    )


def _parse_notification_block(block: str, *, module_name: str, source_file: str, content_hash: str, resolved_oids: dict[str, str]) -> MIBTrapDefinition | None:
    start = _NOTIFICATION_START_RE.match(block.splitlines()[0] if block else "")
    if not start:
        return None
    symbol = start.group("symbol")
    oid_match = _NOTIFICATION_OID_RE.search(block)
    if not oid_match:
        logger.warning("Skipping notification definition without canonical OID module=%s symbol=%s source=%s", module_name, symbol, source_file)
        return None
    try:
        canonical_oid = _resolve_oid_expression(oid_match.group("expr"), resolved_oids)
    except Exception:
        logger.warning("Skipping notification definition with unresolved OID module=%s symbol=%s source=%s", module_name, symbol, source_file, exc_info=True)
        return None

    return MIBTrapDefinition(
        oid=canonical_oid,
        module=module_name,
        symbol=symbol,
        description=_extract_description(block),
        status=_extract_status(block),
        objects=_extract_objects(block),
        source_file=source_file,
        content_hash=content_hash,
    )


def _collect_definition_blocks(lines: Sequence[str]) -> list[str]:
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if not _TYPE_START_RE.match(lines[index]):
            index += 1
            continue

        start = index
        index += 1
        while index < len(lines) and not _TYPE_START_RE.match(lines[index]):
            index += 1
        blocks.append("\n".join(lines[start:index]))
    return blocks


def parse_mib_file(path: Path) -> tuple[str, list[MIBTrapDefinition], str]:
    """Parse a trusted local MIB file into canonical trap definitions."""

    raw_text = _load_text(path)
    cleaned_text = _clean_text(raw_text)
    module_name = _extract_module_name(cleaned_text, path.stem)
    content_hash = _file_hash(raw_text)

    resolved_oids: dict[str, str] = dict(BUILTIN_OIDS)
    pending_assignments: dict[str, str] = {}

    for line in cleaned_text.splitlines():
        match = _OBJECT_ID_RE.match(line)
        if match:
            pending_assignments[match.group("symbol")] = match.group("expr")

    while pending_assignments:
        progress = False
        for symbol, expr in list(pending_assignments.items()):
            try:
                resolved_oids[symbol] = _resolve_oid_expression(expr, resolved_oids)
            except Exception:
                continue
            del pending_assignments[symbol]
            progress = True
        if not progress:
            break

    definitions: list[MIBTrapDefinition] = []
    for block in _collect_definition_blocks(cleaned_text.splitlines()):
        if _TRAP_START_RE.match(block.splitlines()[0]):
            definition = _parse_trap_block(
                block,
                module_name=module_name,
                source_file=str(path),
                content_hash=content_hash,
                resolved_oids=resolved_oids,
            )
        else:
            definition = _parse_notification_block(
                block,
                module_name=module_name,
                source_file=str(path),
                content_hash=content_hash,
                resolved_oids=resolved_oids,
            )
        if definition:
            definitions.append(definition)
    return module_name, definitions, content_hash


def _is_trusted_path(path: Path, trusted_roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    for root in trusted_roots:
        try:
            resolved_root = root.resolve()
        except Exception:
            continue
        if resolved == resolved_root or resolved.is_relative_to(resolved_root):
            return True
    return False


def get_configured_mib_directories() -> list[Path]:
    raw = getattr(settings, "SNMP_MIB_DIRECTORIES", [])
    directories: list[Path] = []
    for item in raw:
        if not item:
            continue
        directories.append(Path(item).expanduser())
    return directories


def invalidate_mib_registry_cache() -> str:
    version = timezone.now().strftime("%Y%m%d%H%M%S%f")
    cache.set(MIB_REGISTRY_CACHE_KEY, version, None)
    return version


def get_mib_registry_cache_version() -> str:
    version = cache.get(MIB_REGISTRY_CACHE_KEY)
    if version:
        return str(version)
    return invalidate_mib_registry_cache()


def _definition_to_payload(definition: SNMPMIBDefinition) -> dict:
    return {
        "oid": definition.oid,
        "module": definition.module_name,
        "symbol": definition.symbol,
        "description": definition.description,
        "status": definition.status,
        "objects": tuple(definition.object_names or ()),
        "source_file": definition.source_file,
        "content_hash": definition.content_hash,
    }


def _payload_to_definition(payload: dict) -> MIBTrapDefinition:
    return MIBTrapDefinition(
        oid=payload["oid"],
        module=payload["module"],
        symbol=payload["symbol"],
        description=payload.get("description"),
        status=payload.get("status"),
        objects=tuple(payload.get("objects") or ()),
        source_file=payload.get("source_file"),
        content_hash=payload.get("content_hash"),
    )


class MIBRegistry:
    """Cached lookup for imported MIB trap definitions."""

    def resolve(self, oid: str) -> MIBTrapDefinition | None:
        if not is_mib_fallback_enabled():
            return None

        normalized_oid = normalize_oid(oid)
        cache_key = ":".join(
            [
                "snmp-mib-registry",
                get_mib_registry_cache_version(),
                normalized_oid,
            ]
        )
        cached = cache.get(cache_key)
        if cached == _CACHE_MISS:
            return None
        if isinstance(cached, dict):
            return _payload_to_definition(cached)

        try:
            definitions = list(
                SNMPMIBDefinition.objects.filter(oid=normalized_oid, is_active=True).order_by("-is_active", "module_name", "symbol", "created_at")
            )
        except Exception:
            logger.exception("Failed to resolve MIB definition oid=%s", normalized_oid)
            return None

        if not definitions:
            cache.set(cache_key, _CACHE_MISS, get_mib_registry_cache_ttl_seconds())
            return None

        if len(definitions) > 1:
            logger.warning(
                "Multiple active MIB definitions found for oid=%s choosing deterministic first definition count=%s",
                normalized_oid,
                len(definitions),
            )

        payload = _definition_to_payload(definitions[0])
        cache.set(cache_key, payload, get_mib_registry_cache_ttl_seconds())
        return _payload_to_definition(payload)


def iter_trusted_mib_files(paths: Iterable[str | Path] | None = None) -> list[Path]:
    trusted_roots = get_configured_mib_directories()
    requested = [Path(item).expanduser() for item in (paths or trusted_roots)]
    files: list[Path] = []
    for requested_path in requested:
        if not _is_trusted_path(requested_path, trusted_roots):
            raise ValueError(f"MIB path is outside the configured trusted directories: {requested_path}")
        if requested_path.is_dir():
            for candidate in sorted(requested_path.iterdir()):
                if candidate.is_file():
                    files.append(candidate)
        elif requested_path.is_file():
            files.append(requested_path)
    return files


def import_snmp_mib_file(path: str | Path) -> dict[str, int | str]:
    resolved_path = Path(path).expanduser().resolve()
    module_name, definitions, content_hash = parse_mib_file(resolved_path)

    created = 0
    updated = 0
    seen_ids: list[int] = []

    with transaction.atomic():
        for definition in definitions:
            obj, was_created = SNMPMIBDefinition.objects.update_or_create(
                module_name=definition.module,
                symbol=definition.symbol,
                oid=definition.oid,
                defaults={
                    "description": definition.description,
                    "status": definition.status or MIBDefinitionStatus.UNKNOWN,
                    "object_names": list(definition.objects),
                    "source_file": definition.source_file,
                    "content_hash": definition.content_hash,
                    "imported_at": timezone.now(),
                    "is_active": True,
                },
            )
            seen_ids.append(obj.pk)
            if was_created:
                created += 1
            else:
                updated += 1

        deactivated = (
            SNMPMIBDefinition.objects.filter(module_name=module_name, source_file=str(resolved_path), is_active=True)
            .exclude(pk__in=seen_ids)
            .update(is_active=False, imported_at=timezone.now())
        )

    invalidate_mib_registry_cache()
    return {
        "module_name": module_name,
        "source_file": str(resolved_path),
        "content_hash": content_hash,
        "created": created,
        "updated": updated,
        "deactivated": deactivated,
        "total": len(definitions),
    }


def import_snmp_mib_files(paths: Iterable[str | Path] | None = None) -> MIBImportSummary:
    summary = MIBImportSummary()
    files = iter_trusted_mib_files(paths)
    files_processed = 0
    created = 0
    updated = 0
    deactivated = 0
    skipped = 0
    failed = 0

    for file_path in files:
        try:
            result = import_snmp_mib_file(file_path)
        except Exception:
            logger.exception("Failed to import MIB file path=%s", file_path)
            failed += 1
            continue
        files_processed += 1
        created += int(result["created"])
        updated += int(result["updated"])
        deactivated += int(result["deactivated"])
        if int(result["total"]) == 0:
            skipped += 1

    return MIBImportSummary(
        files_processed=files_processed,
        definitions_imported=created,
        definitions_updated=updated,
        definitions_deactivated=deactivated,
        files_skipped=skipped,
        files_failed=failed,
    )


def get_mib_registry() -> MIBRegistry:
    return MIBRegistry()
