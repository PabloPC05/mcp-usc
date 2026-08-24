# Superficie MCP

La versión 0.9 utiliza los tres bloques estándar del protocolo sin ampliar los permisos de la
cuenta: herramientas, recursos y prompts. El transporte del servidor continúa siendo STDIO local.

## Resumen

| Bloque | Cantidad | Control principal | Contacto con el Campus |
| --- | ---: | --- | --- |
| Herramientas | 84 | El modelo solicita la llamada; el host aplica aprobación | Según la herramienta |
| Recursos | 4 | El host o la persona los incorpora como contexto | Nunca; son estáticos y locales |
| Prompts | 4 | La persona los selecciona explícitamente | No por sí mismos; guían herramientas |

Que un host no muestre recursos o prompts no impide usar las 84 herramientas. Estas capacidades son
opcionales en las interfaces cliente y no sustituyen las confirmaciones de escritura.

## Recursos

| URI | MIME | Contenido |
| --- | --- | --- |
| `usc://about` | `application/json` | Propósito, límites, inventario y enlaces del proyecto. |
| `usc://safety` | `text/markdown` | Contrato invariante de secretos, contenido remoto y confirmaciones. |
| `usc://compatibility` | `application/json` | Python, sistemas, SDK, Moodle y transportes estudiados. |
| `usc://workflows` | `application/json` | Catálogo de prompts, intención y presencia de efectos. |

Los cuatro se construyen desde constantes y funciones puras. Leerlos no carga `Settings`, no abre
keyring, no crea `UscService` y no realiza DNS o HTTP.

## Prompts

### `daily_briefing`

Argumentos opcionales: `days` (1–90, predeterminado 7) e `include_archived`.

Guía un resumen de cursos, pendientes, eventos, avisos y notificaciones. Solo permite lecturas puras
y prohíbe previews, inspecciones stateful y escrituras.

### `exam_planning`

Argumento obligatorio: `academic_year` con formato consecutivo `YYYY/YYYY`.

Cruza cursos Moodle, códigos exactos y fuentes oficiales. Obliga a conservar `source_url`, separar
evaluación continua de examen oficial y mostrar conflictos sin resolverlos por similitud.

### `assignment_review`

Argumentos opcionales: `course_query` y `days` (1–90, predeterminado 60).

Combina Timeline y tareas sin abrir una página potencialmente stateful. Si el modo sesión necesita
`inspect_submission_status`, se detiene y explica el preview/confirmación requerido.

### `prepare_assignment_submission`

Argumentos: `assignment_id` obligatorio e `intended_change` opcional.

Resuelve el estado y selecciona un único `preview_*`. La plantilla ordena detenerse después de la
previsualización: invocar el prompt no ejecuta una modificación ni equivale a confirmar. La acción
solo puede ocurrir en un turno posterior con aprobación humana nueva y token coincidente.

## Manifiesto local

```powershell
uv run mcp-usc manifest
uv run mcp-usc manifest --compact
```

La salida JSON incluye:

- versión de esquema, paquete y transporte;
- definiciones completas de herramientas con JSON Schema y anotaciones;
- descriptores de recursos y argumentos de prompts;
- recuentos y `contract_sha256` calculado sobre JSON canónico;
- `network_contacted=false` y `secrets_exposed=false`.

No incluye el contenido de recursos, credenciales, configuración privada o datos del Campus. El hash
permite comparar dos instalaciones o adjuntar evidencia sanitizada a una incidencia.

## Descubrimiento por protocolo

Un cliente puede usar `tools/list`, `resources/list`, `resources/read`, `prompts/list` y
`prompts/get`. La suite inicia el servidor por STDIO y verifica esas cinco operaciones, incluido el
renderizado parametrizado. La referencia humana de las herramientas permanece en [tools.md](tools.md).
