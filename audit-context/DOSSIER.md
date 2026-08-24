# Audit context dossier — diff v0.11.0

## Alcance y método

Context-building del estado de trabajo frente a `HEAD`, centrado en `campus.py`,
`session_auth.py`, `academic_profile.py`, `activity_actions.py`, `service.py`,
`confirmations.py`, `security.py` y los entrypoints MCP de `server.py`. No se
formulan veredictos, severidades ni cambios propuestos. Las líneas citadas son
las del árbol de trabajo actual.

## Mapa del sistema

`server.py` registra herramientas MCP y delega en un `UscService`; `UscService`
selecciona un gateway según `Settings.moodle_token`
(`service.py:L368-L385`, `campus.py:L1696-L1701`). El gateway REST invoca
funciones Moodle; el gateway de sesión usa una cookie `MoodleSession`, obtiene
un contexto de sesión (sesskey, usuario y sitio) y ejecuta AJAX/HTML dentro de
rutas permitidas (`campus.py:L729-L929`, `L985-L1158`).

El perfil académico local solo selecciona fuentes públicas de horarios y no
contiene credenciales (`academic_profile.py:L1-L5`); `Settings.from_env` lo
carga desde archivo/entorno (`settings.py:L75-L91`). Las acciones de
finalización tienen un flujo preview/contexto/confirmación/mutación, y las
confirmaciones se ligan al usuario autenticado (`service.py:L278-L336`,
`activity_actions.py:L143-L288`).

## Límites de confianza y entradas

| Entrada | Límite | Datos que persisten | Salida externa |
|---|---|---|---|
| Herramienta MCP | cliente MCP → wrappers de `server.py` | tokens en memoria y cache pública | Moodle REST/AJAX/HTML o USC pública |
| `USC_*`/perfil JSON | proceso local/usuario operador | `Settings` y `AcademicProfile` inmutables | solo URLs públicas para horarios |
| cookie de sesión | entrada secreta local → `import_session_cookie` | OS keyring | HTTP autenticado al Campus |
| contenido Moodle/HTML | servidor remoto no confiable | contexto/cache; se etiqueta `content_is_untrusted` | respuestas normalizadas al cliente |
| token de confirmación | cliente MCP → `ActionConfirmationStore` | mapa en memoria, TTL 300 s | habilita una mutación concreta |

Validación de destinos públicos y del Campus se concentra en
`security.validate_usc_url` (`security.py:L21-L36`); la validación de la ruta
de titulación es más específica en `academic_profile._degree_url`
(`academic_profile.py:L35-L58`). No hay red ni escrituras externas realizadas
durante este análisis.

## Invariantes transversales

1. Los identificadores de cuenta usados en lecturas/acciones se obtienen del
   gateway (`service.py:L198-L206`) o de `core_webservice_get_site_info`
   (`activity_actions.py:L91-L95`), no de una identidad elegida para la
   mutación.
2. El gateway de sesión no abre páginas de actividad para operaciones que
   pudieran registrar vistas; `invoke_course_module` declara esa capacidad no
   disponible (`campus.py:L924-L929`) y los listados de estado usan
   `core_courseformat_get_state` (`session_course_state.py:L109-L230`).
3. La mutación contextual solo se emite tras resolver curso/módulo/estado y
   consumir un token ligado a la petición y al usuario (`service.py:L586-L664`).
4. El contenido procedente de Moodle/USC se transporta como no confiable en
   las capas nuevas (`activity_actions.py:L171-L186`,
   `class_timetables.py:L967-L988`, `session_course_state.py:L213-L214`).
5. El token de confirmación es de un solo uso, con digest canónico y TTL
   monotónico (`confirmations.py:L38-L71`).
6. La sesión importada no devuelve cookie ni sesskey; solo identidad pública y
   confirmación de almacenamiento (`session_auth.py:L64-L78`, `L209-L219`).

## Cobertura de funciones

Analizadas en archivos individuales bajo `audit-context/functions/`:

- validación, carga y resolución de `AcademicProfile`.
- importación/olvido de sesión y diagnóstico de autenticación.
- comprobaciones de respuesta, contexto AJAX, descargas y fábrica/login del
  gateway HTTP.
- acciones de finalización de actividad/curso y sus helpers de parsing.
- confirmaciones y validación/redacción de URLs/HTML.
- helpers de `UscService`, acciones contextuales, horarios y fallbacks de
  sesión.
- wrappers MCP nuevos de finalización y horarios, además de `_service`.

Funciones existentes no modificadas en el diff se trataron como callers o
callees cuando eran necesarias para continuidad. Los parsers detallados de
`class_timetables.py` y los adaptadores completos de `session_course_state.py`
quedan referenciados por sus contratos de llamada; no se amplió el alcance a
un inventario función-a-función de esos módulos.

## Estado persistente y concurrencia

- `ActionConfirmationStore` y `_RESOURCE_REFERENCES` son mapas globales en
  memoria (`confirmations.py:L29-L36`, `service.py:L140-L165`); no hay persistencia
  entre procesos visible en estas capas.
- `CredentialStore` es el almacenamiento duradero de la cookie importada
  (`session_auth.py:L202-L208`, `campus.py:L767-L777`, `L1770-L1775`).
- `AcademicProfile` se vuelve a cargar al construir `Settings`; el perfil no
  se modifica durante una consulta (`settings.py:L75-L91`,
  `service.py:L1236-L1269`).
- No se observa coordinación explícita entre dos instancias de proceso que
  usen el mismo keyring, ni transacción conjunta entre preview y mutación.
  La garantía operacional que sí aparece es el consumo inmediato del token en
  memoria antes del invoke mutante (`confirmations.py:L55-L67`).

## Supuestos no verificados / preguntas abiertas

- La semántica exacta de permisos y efectos laterales de cada función Moodle
  depende del servidor configurado; el código solo puede consultar capacidad o
  interpretar payloads (`campus.py:L1160-L1172`, `activity_actions.py:L163-L186`).
- `CredentialStore` y el backend de keyring son componentes externos a este
  alcance; no se encontró en estos archivos su implementación de protección y
  disponibilidad.
- La coincidencia entre los selectores HTML de Moodle y la identidad/sesskey
  extraídas se establece por parseo; no hay otra fuente local equivalente
  (`session_auth.py:L102-L128`, `campus.py:L848-L876`).
- El catálogo/índices públicos pueden cambiar de esquema; el cliente de
  horarios representa fallos parciales como `issues` y marca `complete`
  (`class_timetables.py:L903-L988`).
- No se encontró un mecanismo de aislamiento por sesión MCP para los mapas
  globales de confirmaciones y referencias de recursos; su ámbito observado es
  el proceso.

## Archivos de detalle

Ver `audit-context/functions/*.md`. Cada documento registra propósito,
entradas y confianza, efectos, bloques relevantes, dependencias, callers y
preguntas abiertas con líneas.
