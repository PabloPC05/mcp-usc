from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .settings import Settings


def inspect_upload_files(settings: Settings, paths: list[str] | None) -> list[dict[str, Any]]:
    requested = paths or []
    if len(requested) > 20:
        raise ValueError("No se pueden adjuntar más de 20 archivos en una operación")
    if not requested:
        return []
    if settings.upload_root is None:
        raise ValueError(
            "La subida local está desactivada. Configura USC_UPLOAD_ROOT con la carpeta que "
            "contiene los archivos autorizados."
        )
    try:
        root = settings.upload_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("USC_UPLOAD_ROOT no existe o no es accesible") from exc
    if not root.is_dir():
        raise ValueError("USC_UPLOAD_ROOT debe ser una carpeta")

    inspected: list[dict[str, Any]] = []
    total_size = 0
    seen: set[Path] = set()
    for raw_path in requested:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Cada ruta de archivo debe ser una cadena no vacía")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError("Uno de los archivos autorizados no existe o no es accesible") from exc
        if not resolved.is_relative_to(root):
            raise ValueError("Todos los archivos deben estar dentro de USC_UPLOAD_ROOT")
        if not resolved.is_file():
            raise ValueError("Solo se pueden subir archivos regulares")
        if resolved in seen:
            raise ValueError("No se puede adjuntar el mismo archivo dos veces")
        seen.add(resolved)
        size = resolved.stat().st_size
        total_size += size
        if size > settings.max_upload_bytes or total_size > settings.max_upload_bytes:
            raise ValueError("Los archivos superan el límite de subida configurado")
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        inspected.append(
            {
                "path": str(resolved),
                "relative_path": resolved.relative_to(root).as_posix(),
                "filename": resolved.name,
                "size": size,
                "sha256": digest.hexdigest(),
            }
        )
    return inspected
