## Horarios públicos y fallbacks de sesión en `src/mcp_usc/service.py` (L1099-L1123, L1190-L1269, L1470-L1623, L2535-L2540)

**Purpose:** Exponer horarios USC anónimos y adaptar el transporte HTTP de sesión a listados de estado sin abrir actividades.

**Inputs & Assumptions:**
- Entradas de horarios provienen de herramientas MCP; `UscClassTimetableClient` valida curso/año/semestre/grupos/programa (`class_timetables.py:L990-L1015`).
- `get_my_class_timetable` confía en `Settings.academic_profile` ya cargado y validado (`service.py:L1236-L1246`).
- Para fallbacks, `session_forms()` no nulo identifica gateway de sesión (`service.py:L1473-L1476`, `L1582-L1585`).

**Outputs & Effects:**
- `list_degree_timetables` y `get_degree_class_timetable` crean cliente público con timeout/cache y devuelven resultados de descubrimiento/agregación (`L1190-L1225`).
- `get_my_class_timetable` resuelve defaults/overrides, consulta el mismo cliente y añade perfil/resolución pública (`L1227-L1269`).
- `list_forums`, `list_course_contents`, `list_course_resources`, `list_quizzes` eligen `session_course_state` para gateway de sesión; REST conserva clientes Moodle (`L1470-L1482`, `L1575-L1623`, `L2535-L2540`).
- `list_events` limita días/resultado a 50 y delega al gateway (`L1103-L1123`).

**Block-by-Block:**

- Horarios: el cliente descubre centros y programas; si hay varios programas devuelve selección requerida, no selecciona por semejanza (`class_timetables.py:L1016-L1087`).
- Agregación: selecciona `program_id`, consulta centros en paralelo y conserva `issues`/`complete` (`class_timetables.py:L1088-L1129`, `L967-L988`).
- Fallbacks: `fetch_session_course_states` valida que cada curso pertenezca al listado anunciado y limita a 100 cursos (`session_course_state.py:L109-L131`), luego decodifica módulos bounded y etiqueta contenido (`L133-L230`).

**Cross-Function Dependencies:**
- Callees: `UscClassTimetableClient`, `fetch_session_course_states` y transformadores `session_*`.
- Callers: `server.py:L884-L931` para horarios; múltiples wrappers de listados para fallbacks.
- Shared state: cache pública inyectada en `UscService`; snapshots son temporales por llamada.

**Open Questions:**
- La completitud de horarios depende de índices públicos remotos y del parseo de HTML/AJAX; no se encontró una fuente institucional local alternativa.
- Los snapshots de sesión no incluyen instance IDs/metadata interna por contrato (`session_course_state.py:L243-L300`); callers deben conservar esa distinción.
