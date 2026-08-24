# Revisión de release v0.9.0

Fecha: 2026-08-24

## Alcance

La revisión cubre el delta desde `8e3d741` (v0.8.0): recursos y prompts MCP, manifiesto local,
diagnóstico compacto, matriz CI multiplataforma, automatización de release y documentación de
compatibilidad. No se modifican los clientes REST/AJAX, parsers de formularios, confirmaciones ni
operaciones remotas de v0.8.

## Contrato MCP revisado

- Las 84 herramientas mantienen nombres, parámetros y anotaciones.
- `resources/list` anuncia cuatro URI `usc://`; leerlas ejecuta funciones locales puras.
- `prompts/list` anuncia cuatro plantillas y `prompts/get` valida/castea sus argumentos.
- El test STDIO recorre `tools/list`, `resources/list`, `resources/read`, `prompts/list` y
  `prompts/get` mediante una sesión cliente/servidor real.
- El manifiesto enumera 84 herramientas, cuatro recursos y cuatro prompts, sin contenido privado, y
  genera el digest de contrato
  `bef6534587387fe3321908c560b8025c97c1833cdb895bac9442f8b1cbb5927a`.

## Seguridad

### Recursos y prompts

- Ningún endpoint nuevo invoca `_service`, `Settings`, keyring, DNS o HTTP; un test sustituye
  `_service` por un fallo y renderiza los ocho endpoints correctamente.
- Los rangos de días, curso académico, IDs y longitudes de texto se validan antes de renderizar.
- Las cadenas proporcionadas por el usuario se citan como datos literales dentro del prompt.
- Los tres prompts de consulta prohíben escrituras; el prompt de entrega se detiene expresamente
  después de un único preview y exige confirmación humana en un turno posterior.
- Seleccionar un prompt no ejecuta una herramienta ni concede permisos adicionales.

### Manifiesto y diagnóstico

- `manifest` serializa descriptores y JSON Schema, no lee valores de configuración o recursos.
- `doctor --compact` conserva los mismos booleanos sanitizados de v0.8.
- Ambos declaran `network_contacted=false`; no se observaron valores secretos en las pruebas.

### Automatización

- CI usa dependencias congeladas y acciones fijadas a commits.
- Checkout no persiste credenciales en CI ni release.
- El workflow de release tiene únicamente `contents: write`, no recibe secretos Moodle y solo se
  activa para tags SemVer.
- Antes de publicar verifica tag/versión/notas, lint, tests, build e instalación limpia del wheel;
  genera `SHA256SUMS` en el runner.

## Evidencia

| Comprobación | Resultado |
| --- | --- |
| `pytest` local | 463 passed |
| `ruff check .` | correcto |
| `uv lock --check` | correcto, 59 paquetes resueltos |
| `uv pip check` | 57 paquetes compatibles |
| YAML de GitHub | parseado correctamente |
| `uv build` | wheel y sdist 0.9.0 generados |
| Instalación aislada del wheel | versión de paquete e import 0.9.0 correctos |
| Enlaces Markdown relativos | todos resuelven |
| CI multiplataforma | [seis jobs correctos](https://github.com/PabloPC05/mcp-usc/actions/runs/32768327814) |
| Release automática | [workflow completo correcto](https://github.com/PabloPC05/mcp-usc/actions/runs/32768411924) |
| Demo Moodle | no repetida; v0.9 no modifica rutas remotas y no había credenciales públicas configuradas |

No se ejecutaron mensajes, foros, entregas, cuestionarios ni otras operaciones sobre el Campus.

## Riesgos residuales

- Un host puede no ofrecer UI para recursos o prompts; las herramientas y solicitudes naturales
  siguen siendo la degradación compatible.
- El proyecto permanece en SDK MCP 1.29.x con límite `<2`; la migración a 2.x requiere una revisión
  separada por sus cambios incompatibles.
- La disponibilidad Moodle real continúa dependiendo de versión, plugins, servicio y permisos.
- La primera publicación automática se completó correctamente; futuros cambios del proveedor de
  Actions o GitHub CLI siguen siendo una dependencia operativa externa.

## Conclusión

No se identificaron bloqueos. La superficie nueva es aditiva, local y no amplía autoridad. El tag
`v0.9.0` publicó wheel, sdist y `SHA256SUMS`; los hashes del archivo coinciden con los digests que
GitHub muestra para ambos artefactos.
