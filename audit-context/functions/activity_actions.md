## Acciones contextuales en `src/mcp_usc/activity_actions.py` (L22-L288)

**Purpose:** Resolver identidad, módulo, curso y estado antes de una mutación de finalización de alcance propio.

**Inputs & Assumptions:**
- `invoke` es un callable asíncrono que llega al gateway y devuelve payloads Moodle (`L12-L15`).
- IDs de entrada son enteros positivos y `completed` es bool (`L22-L25`, `L143-L151`, `L189-L200`).
- Los payloads remotos pueden contener warnings y texto no confiable; se recortan y etiquetan (`L43-L61`).

**Outputs & Effects:**
- `_identity` obtiene `userid`/nombre desde `core_webservice_get_site_info` (`L91-L95`).
- `_module_context` obtiene el módulo, exige que CMID y curso coincidan y produce contexto (`L98-L125`).
- `preview_update_activity_completion_status_manually` consulta capacidad manual y estado propio; devuelve `allowed` y datos para confirmar (`L143-L186`).
- `update_activity_completion_status_manually` emite exactamente la función Moodle de mutación y normaliza `status/warnings` (`L189-L209`).
- `preview_mark_course_self_completed` consulta estado de curso/criterio tipo 1; `mark_course_self_completed` emite la mutación Moodle (`L226-L288`).

**Block-by-Block:**

- `L128-L140`: status debe ser lista y exactamente un item debe corresponder al CMID; la unicidad es condición para continuar.
- `L152-L167`: la preview obtiene identidad, contexto del módulo y estado de actividades con `userid` derivado de Moodle.
- `L163-L186`: `current_state` se convierte si está presente y warnings se agregan al contexto.
- `L233-L280`: la preview de curso requiere envelope `completionstatus`, lista de criterios y máximo un tipo 1; distingue ausencia y ya completado.
- `L200-L209` y `L283-L288`: la mutación solo pasa IDs específicos a Moodle; `_mutation_result` distingue `acknowledged`, `rejected` y warnings.

**Cross-Function Dependencies:**
- Callers principales: `UscService.preview_*`, `UscService.*` y wrappers `server.py:L274-L304`.
- Gateway: REST o sesión vía `gateway.invoke`; `_require_functions` se ejecuta en el service antes de previews (`service.py:L590-L596`, `L634-L639`).
- Shared state: ninguno dentro del módulo; confirmaciones viven en `ActionConfirmationStore` vía service.

**Open Questions:**
- No se encontró en este módulo una comprobación adicional del permiso Moodle fuera de los payloads de módulo/completion; la autoridad final es la respuesta de Moodle.
