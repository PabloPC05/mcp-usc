# Revisión de release v0.8.0

Fecha: 2026-08-24

## Alcance

La revisión cubre el delta desde `d9320ad` (v0.7.0): nueva herramienta autodescriptiva, diagnóstico
local, metadatos de paquete, documentación comunitaria, plantillas y CI. No cambian los clientes
REST/AJAX, los parsers de formularios, las confirmaciones ni las operaciones remotas existentes.

## Fronteras revisadas

### `describe_mcp_usc`

- Construye una respuesta a partir de constantes locales y `__version__`.
- No instancia `UscService`, `Settings`, cliente HTTP ni keyring.
- Declara `network_contacted=false` y está anotada como lectura pura.
- Un test STDIO compara las 84 herramientas reales con los 84 nombres agrupados; una divergencia
  rompe la suite.

### `mcp-usc doctor`

- Lee configuración local validada, versión de Python y presencia de la credencial
  `moodle-session` en keyring.
- Convierte token y cookie únicamente a booleanos; ninguna respuesta contiene sus valores.
- Comprueba existencia/tipo de `USC_UPLOAD_ROOT` sin listar archivos ni devolver la ruta.
- Detecta la instalación opcional de Playwright sin abrir un navegador.
- No construye `UscService`, no resuelve DNS y no realiza peticiones HTTP.
- Distingue `ready`, `public_only`, `configuration_error` y `unsupported_python`; `ready` solo indica
  que hay una credencial configurada, no que sea válida. La validación online sigue siendo `status`.

### GitHub Actions y supply chain

- El workflow tiene `permissions: contents: read` y no recibe secretos del Campus.
- `actions/checkout` y `astral-sh/setup-uv` están fijados a commits correspondientes a v5 y v10.0.1.
- CI instala desde `uv.lock` con `--frozen` antes de lint y tests.
- La auditoría externa de Moodle no se ejecuta automáticamente ni tiene credenciales en Actions.

## Evidencia

| Comprobación | Resultado |
| --- | --- |
| `pytest` | 450 passed |
| `ruff check .` | correcto |
| `uv lock --check` | lock resuelto y actualizado |
| `uv pip check` | 57 paquetes compatibles |
| `uv build` | wheel y sdist 0.8.0 generados |
| Inspección del wheel | versión, URLs, clasificadores y nuevos módulos correctos |
| Enlaces Markdown relativos | todos resuelven |
| Inventario en `docs/tools.md` | contiene los 84 nombres reales |
| `mcp-usc doctor` local | `ready`, `campus_contacted=false`, `secrets_exposed=false` |
| Auditoría demo v0.8 | no ejecutada: variables públicas de la demo no configuradas |

La auditoría de lectura/escritura reversible de v0.7 sigue siendo la evidencia de los transportes
Moodle, porque v0.8 no modifica esas rutas. No se ejecutaron mensajes, foros, entregas,
cuestionarios ni otras operaciones sobre la USC durante esta release.

## Riesgos residuales

- La validez real de token/cookie solo puede comprobarse con `status`, que sí contacta con Moodle.
- La disponibilidad de herramientas privadas depende de versión, plugins, servicio REST y permisos
  efectivos de la cuenta.
- Los documentos históricos de v0.7 no auditan cambios posteriores; este documento solo cubre el
  delta descrito.
- El primer workflow remoto debe verificarse después del push en GitHub Actions.

## Conclusión

No se identificaron bloqueos para publicar v0.8.0. El cambio funcional es aditivo, local y sin
efectos remotos; las 83 herramientas anteriores mantienen nombre y parámetros.
