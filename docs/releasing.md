# Proceso de release

Desde v0.9 las releases de GitHub se construyen de forma reproducible desde un tag. No se publica
automáticamente en PyPI.

## Preparación

1. Actualiza la misma versión en `pyproject.toml` y `src/mcp_usc/__init__.py`.
2. Regenera `uv.lock` y añade `docs/releases/vX.Y.Z.md`.
3. Actualiza README, changelog, compatibilidad y cualquier contrato afectado.
4. Ejecuta:

```powershell
uv lock --check
uv run ruff check .
uv run pytest
uv build
```

5. Publica el commit en `main` y espera a que CI termine en las seis combinaciones.

## Publicación automática

Un tag anotado exacto `vX.Y.Z` activa `.github/workflows/release.yml`. El job:

1. instala dependencias desde `uv.lock` con `--frozen`;
2. comprueba que tag, `__version__` y archivo de notas coinciden;
3. ejecuta Ruff y toda la suite;
4. construye wheel y sdist;
5. instala el wheel en un entorno virtual limpio y comprueba su versión;
6. genera `SHA256SUMS` para ambos artefactos;
7. crea la GitHub Release con notas y los tres archivos.

El workflow solo recibe `contents: write`; no tiene credenciales Moodle, variables del Campus o
permisos para otros sistemas.

## Invariantes

- No mover, reemplazar ni reutilizar un tag publicado.
- No adjuntar artefactos construidos desde un árbol sucio o un commit distinto.
- No omitir tests para una release; si falla el workflow, corregir en un commit y publicar una nueva
  versión conforme a SemVer.
- No introducir credenciales de demo o USC en Actions.
- Mantener las acciones de terceros fijadas a un commit revisado.

La revisión específica de cada release debe documentar pruebas, CI, artefactos y cualquier
verificación externa omitida o realizada.
