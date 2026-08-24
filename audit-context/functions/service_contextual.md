## Helpers y acciones contextuales de `src/mcp_usc/service.py` (L191-L365, L368-L664)

**Purpose:** Orquestar gateway, identidad, confirmación y operaciones Moodle; transformar errores de mutaciones cuyo resultado puede ser desconocido.

**Inputs & Assumptions:**
- `_gateway_user_id` toma `gateway.status()` como fuente de identidad (`L198-L206`).
- `_require_functions` delega capacidad al gateway si existe (`L209-L213`); un gateway sin checker no establece capacidad por sí mismo.
- Preview/contexto se guarda en el digest junto con la identidad (`L278-L336`).

**Outputs & Effects:**
- `UscService.__init__` carga settings y crea cache pública (`L368-L381`); `_campus` construye gateway nuevo (`L383-L385`).
- `preview_update_activity_completion_status_manually` y `preview_mark_course_self_completed` comprueban funciones, invocan preview contextual y emiten token solo si `allowed` (`L586-L606`, `L632-L644`).
- Sus métodos de mutación reevalúan contexto, consumen token y emiten una única operación; errores Campus/acción se convierten en outcome desconocido (`L608-L630`, `L646-L664`).
- `_unknown_contextual_result` y `_session_form_mutation` comunican `request_may_have_been_sent` y `do_not_retry` (`L339-L365`).

**Block-by-Block:**

- `L305-L319`: contextos denegados no crean confirmación; los permitidos incorporan token y usuario.
- `L322-L336`: la mutación debe presentar contexto que siga `allowed=True` y el digest coincide exactamente con preview.
- `L586-L606`: preview de actividad permite funciones concretas y pasa request `{course_id, cmid, completed}`.
- `L608-L630`: la segunda lectura puede cambiar contexto; solo después de consumir token se llama al mutador con `{cmid, completed}`.
- `L632-L664`: mismo patrón para curso, con excepción separada de capability/auth y resultado desconocido para errores restantes.

**Cross-Function Dependencies:**
- Callees: `activity_actions`, `ActionConfirmationStore`, `CampusGateway`, `Settings` y `create_campus_gateway`.
- Callers: wrappers MCP de `server.py:L274-L304`; el resto del service comparte helpers de identidad/confirmación.
- Shared state: cache pública y confirmaciones globales; gateway es una instancia por llamada a `_campus`.

**Open Questions:**
- No se encontró una garantía de que preview y mutación usen la misma instancia de gateway; ambos reobtienen gateway y vuelven a consultar identidad/contexto (`L589-L600`, `L649-L652`).
