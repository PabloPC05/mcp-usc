# Auditoría diferencial de seguridad y robustez — v0.7.0

**Baseline estable:** `31ed4cdf6d71a863480173351fb1dc9806dec7ef` (`v0.6`)

**Objetivo:** working tree no comprometido de `v0.7.0`

**Fecha de corte final:** 2026-08-24 17:21:21 +02:00

**Fingerprint del diff tracked:** `47a3d1e1abe711f6b24db42a6781980ce8f4a623`

**Fingerprint SHA-256 del material completo:** `dc6278a9aefd73a0a91518fd644b686f94802522427ca05238836dadf1774c39`

**Estrategia:** FOCUSED, con análisis profundo de todos los cambios HIGH, revisión inicial completa y reauditoría de las remediaciones en los 27 archivos materiales cambiados/nuevos

**Resultado final:** **APPROVE**
**Riesgo residual observado:** **LOW**

## 1. Resumen ejecutivo

| Severidad | Iniciales | Resueltos | Abiertos |
|---|---:|---:|---:|
| CRITICAL | 0 | 0 | 0 |
| HIGH | 0 | 0 | 0 |
| MEDIUM | 3 | 3 | 0 |
| LOW | 2 | 2 | 0 |

La revisión inicial terminó en `CONDITIONAL` con tres hallazgos MEDIUM y dos LOW. La reauditoría final reprodujo las cinco correcciones, la defensa adicional frente a cualquier `Vary` no representado y los escenarios adversarios pedidos. No quedan hallazgos abiertos ni aparecieron hallazgos nuevos.

La implementación conserva las defensas relevantes: los clientes públicos son GET-only, validan destinos y redirecciones, acotan cuerpos, no importan cookies/credenciales y validan el esquema antes de insertar una respuesta nueva en caché. La caché con vida de proceso contiene solo respuestas GET públicas; los gateways autenticados siguen siendo efímeros. La demo mantiene las credenciales fuera de argv y solo permite como mutaciones externas la creación y eliminación explícitas de un evento personal.

El veredicto `APPROVE` se refiere exactamente al fingerprint final indicado y no convierte los servicios remotos compartidos ni sus esquemas futuros en supuestos estables. La evidencia de remediación y sus líneas se detalla en la sección 12.

### Métricas clave

- Archivos materiales revisados: **27/27 (100%)**, excluido este informe.
- Diff final, incluyendo untracked y excluyendo este informe: **+4631 / -233 líneas**.
- Suite final: **446 passed in 11.55s**.
- Regresiones dirigidas de remediación: **10 passed in 6.57s**.
- Ruff: **PASS**.
- `git diff --check`: **PASS**; solo avisos informativos LF→CRLF de Git para el working copy.
- Regresiones de una corrección de seguridad histórica: **0 encontradas**.
- Hallazgos iniciales cerrados: **5/5**; hallazgos nuevos: **0**.

## 2. Baseline, invariantes y límites de confianza

### Baseline v0.6

El baseline ya separaba dos dominios:

- Moodle autenticado (`cv.usc.es`), con token o sesión local y flujos de confirmación para escrituras;
- fuentes públicas USC, sin cookies ni credenciales, para calendarios, planes, páginas y PDF.

Los clientes estructurados del baseline hacían GET, desactivaban redirects automáticos, revalidaban cada salto, restringían rutas/tipos de recurso y acotaban respuestas. Los planes exigían año consecutivo, JSON Drupal conocido, un único `insert`, un contenedor conocido, códigos exactos y unicidad de código/URL. No existían caché pública, catálogo global ni demo REST.

### Invariantes relevantes de v0.7

1. **Destino:** una fuente pública no puede convertir el cliente en un crawler/SSRF general; cada URL y redirect debe seguir en la allowlist y la clase de ruta esperada.
2. **Método/credenciales:** las herramientas públicas USC solo emiten GET anónimos, sin cookies ni credenciales Moodle.
3. **Esquema antes de caché:** una respuesta nueva solo se guarda después de superar la validación específica del parser.
4. **Completitud honesta:** un código solo puede publicarse como `not_found` si todas las titulaciones seleccionadas fueron cubiertas de forma fiable.
5. **Freshness honesta:** `fresh`, `revalidated` y `degraded` describen estado reutilizable entre invocaciones reales, no solo dentro de un objeto efímero.
6. **Demo aislada:** solo `school.moodledemo.net`; ninguna llamada modifica la allowlist ni alcanza `cv.usc.es`.
7. **Comunicación prohibida:** la auditoría demo nunca envía mensajes, email, chat ni contenido de foro.
8. **Reversibilidad demostrada:** `external_state_remaining=false` solo es correcto tras comprobar que el evento creado ya no existe.

### Mapa de llamadas de alto riesgo

```text
MCP public tool
  -> server._service()                         (crea servicio/gateway por llamada)
  -> server._process_public_cache              (solo caché GET pública de proceso)
  -> UscService._public_http_cache             (inyecta esa caché explícitamente)
  -> degree_catalog / official_exams
  -> UscStudyPlanClient / UscExamCalendarClient
  -> SafePublicHttpFetcher.get
  -> GET público USC -> parser -> commit de caché

CLI demo
  -> DemoRestClient.acquire                    (POST token, host exacto)
  -> run_demo_audit
  -> reads REST allowlisted
  -> create user event -> delete returned IDs  (solo con opt-in)
```

El contenido HTML/JSON y los metadatos HTTP son no confiables. El catálogo y las páginas de titulación determinan qué rutas GET se consultan después, pero las rutas se validan de nuevo. En la demo, las respuestas REST también son no confiables y no deben usarse como prueba de un efecto externo sin una lectura posterior.

## 3. Qué cambió en el corte inicial

No hay commits entre baseline y `HEAD`: `HEAD` sigue siendo `31ed4cdf`. v0.7 existe íntegramente como working tree modificado/no rastreado. El baseline fue creado el 2026-08-24 15:27:24 +02:00 con el mensaje `feat: discover USC exam subjects dynamically`.

| Archivo | + | - | Riesgo del cambio | Blast radius |
|---|---:|---:|---|---|
| `.env.example` | 7 | 0 | LOW | Configuración |
| `README.md` | 56 | 13 | LOW | Usuarios |
| `pyproject.toml` | 1 | 1 | LOW | Paquete/versionado |
| `src/mcp_usc/__init__.py` | 1 | 1 | LOW | Paquete |
| `src/mcp_usc/exam_calendar.py` | 155 | 94 | HIGH: HTTP/parser/caché | 3 herramientas oficiales |
| `src/mcp_usc/official_exams.py` | 14 | 3 | MEDIUM: orquestación pública | 3 herramientas oficiales |
| `src/mcp_usc/public_web.py` | 1 | 1 | LOW: User-Agent | Búsqueda genérica |
| `src/mcp_usc/server.py` | 24 | 0 | MEDIUM: API MCP nueva | 5 rutas públicas |
| `src/mcp_usc/service.py` | 50 | 0 | HIGH: estado de caché/servicio | 5 rutas públicas |
| `src/mcp_usc/settings.py` | 32 | 0 | MEDIUM: límites configurables | Toda instancia de servicio |
| `src/mcp_usc/study_plans.py` | 225 | 82 | HIGH: HTTP/parser/validación | 4 rutas públicas |
| `tests/test_mcp_stdio.py` | 2 | 0 | LOW | Registro de tools |
| `tests/test_official_exams.py` | 9 | 1 | LOW | Pruebas |
| `tests/test_settings.py` | 35 | 0 | LOW | Pruebas |
| `tests/test_study_plans.py` | 73 | 1 | LOW | Pruebas |
| `uv.lock` | 1 | 1 | LOW | Lock/versionado |
| `scripts/moodle_demo_audit.py` | 6 | 0 | MEDIUM: entrypoint REST | CLI demo |
| `src/mcp_usc/degree_catalog.py` | 649 | 0 | HIGH: HTTP/parser/fan-out | 2 tools nuevas |
| `src/mcp_usc/demo_audit.py` | 1052 | 0 | HIGH: credenciales/REST/write | 1 CLI opt-in |
| `src/mcp_usc/public_http_cache.py` | 554 | 0 | HIGH: estado HTTP compartido | 3 clientes/5 tools |
| `tests/test_degree_catalog.py` | 491 | 0 | LOW | 21 casos recogidos |
| `tests/test_demo_audit.py` | 258 | 0 | LOW | 10 casos recogidos |
| `tests/test_public_http_cache.py` | 348 | 0 | LOW | 12 casos recogidos |

Los nuevos archivos tenían estos hashes en el corte auditado:

| Archivo | Git blob hash |
|---|---|
| `scripts/moodle_demo_audit.py` | `41e5df3932437ee14c91a5649c2f04d5c4328d6d` |
| `src/mcp_usc/degree_catalog.py` | `2d01e5c1423f3d421b14f64459796b53ea347f2d` |
| `src/mcp_usc/demo_audit.py` | `e147a67dd05b03d8ee9d15e0fac8f4d5955b63e1` |
| `src/mcp_usc/public_http_cache.py` | `e88321ae9d7389c7a761810f4afe861f2ceb5d04` |
| `tests/test_degree_catalog.py` | `b6cbcb1126e3d1344c1274a30abc01d046905398` |
| `tests/test_demo_audit.py` | `eb6773054e34f25c1c9096f774db0d4f9ba84c8e` |
| `tests/test_public_http_cache.py` | `9f21542eb858d7e4093bf7e93e5082e550572119` |

## 4. Hallazgos

Esta sección conserva el razonamiento y las reproducciones del corte inicial. Los cinco hallazgos quedaron **RESUELTOS** en el fingerprint final; las rutas y pruebas de cierre están en la sección 12.

### M-01 — La caché se destruye en cada invocación MCP — RESUELTO

**Severidad:** MEDIUM

**Archivos:** `src/mcp_usc/server.py:63-64`, `src/mcp_usc/service.py:407-415`

**Blast radius:** 3 adaptadores HTTP y 5 entrypoints MCP (`list_usc_degrees`, `locate_usc_subject_codes`, listado/consulta/horario oficial)
**Cobertura:** PARTIAL

`UscService.__init__` crea la caché configurada como estado de instancia. Sin embargo, `_service()` devuelve un `UscService()` nuevo en cada llamada de herramienta. El servidor pasa expresamente esa caché nueva a catálogo, planes y calendarios, por lo que el singleton alternativo `DEFAULT_PUBLIC_HTTP_CACHE` tampoco se utiliza en los flujos MCP.

Consecuencias observables:

- una segunda llamada no puede ser un fresh hit ni hacer revalidación condicional de la primera;
- `stale-if-error` no puede degradar desde una respuesta de una invocación anterior;
- se repiten GET y parsing, aumentando latencia y probabilidad de rate-limit;
- las variables `USC_PUBLIC_CACHE_*` prometen una vida útil que el servidor real no ofrece.

**PoC local, sin red:**

```text
{'same_service': False, 'same_public_cache': False, 'a_entries': 0, 'b_entries': 0}
```

**Escenario adversario/operacional:**

1. un usuario llama dos veces a `locate_usc_subject_codes` o a una herramienta oficial;
2. la primera llamada descarga y valida las fuentes;
3. la segunda crea otra caché vacía y vuelve a consultar USC;
4. un 429/503 en la segunda llamada no puede usar el dato validado de la primera y la herramienta falla/informa incompletitud.

**Historia:** la factory por llamada procede de `6a4ddbb` (`feat: implement HTTP-first USC campus MCP`). Era compatible con el baseline sin caché. v0.7 añadió estado por instancia sin adaptar ese ciclo de vida; no es la reversión de un fix de seguridad.

**Gap de prueba:** `tests/test_degree_catalog.py:394-406` reutiliza manualmente una misma instancia para dos operaciones. No hay prueba que invoque dos veces el entrypoint real de `server.py` y observe un hit/revalidación/degradación entre llamadas.

**Recomendación:** alojar la caché en un estado de proceso deliberado y acotado, o reutilizar una instancia de servicio segura, y probar dos invocaciones MCP separadas. No compartir nunca con clientes autenticados; esta caché debe seguir siendo exclusivamente de GET públicos anónimos.

### M-02 — Una deriva de etiqueta se convierte en falso `not_found` — RESUELTO

**Severidad:** MEDIUM

**Archivos:** `src/mcp_usc/study_plans.py:179-219,536-568`, `src/mcp_usc/degree_catalog.py:181-205,452-476`

**Blast radius:** el parser alimenta 4 rutas públicas; el falso `complete/not_found` afecta directamente al locator global
**Cobertura:** NO para deriva de markup

`discover_study_plan_endpoint` convierte cero coincidencias de la regex de etiqueta en `StudyPlanAcademicYearUnavailable`. Para decidir que esto es una ausencia legítima, `validate_page` solo comprueba que `parse_study_plan_page_html` vea algún endpoint AJAX. Ese parser no relaciona endpoints con etiquetas/años.

Por tanto, si Drupal conserva el endpoint del curso pero cambia la etiqueta —por ejemplo, añade `:`— se acepta la página, se clasifica el grado como `academic_year_unavailable` con `affects_completeness=False`, y `DegreeSubjectSearch.complete` puede devolver `true`. El código solicitado termina como `not_found`, precisamente el falso negativo que el diseño declara evitar.

**PoC ejecutado:** página con un endpoint válido y etiqueta `Curso académico: 2026/2027`.

```json
{
  "affects_completeness": false,
  "classified_as": "academic_year_unavailable",
  "complete": true,
  "endpoint_count_seen_by_fallback_parser": 1,
  "subject_status": "not_found"
}
```

**Escenario adversario:**

1. una modificación accidental o maliciosa del HTML cambia la forma textual de las etiquetas sin retirar el endpoint;
2. la regex exacta de `study_plans.py:39,199-201` deja de asociar el año;
3. `study_plans.py:550-558` interpreta la existencia de cualquier endpoint como página válida;
4. `degree_catalog.py:460-463` marca la incidencia como no relevante para completitud;
5. `degree_catalog.py:198-205` publica un falso `not_found` con `complete=true`.

**Baseline/historia:** en v0.6, cero candidatos era un `StudyPlanParseError`; la nueva excepción que no afecta completitud es una relajación v0.7. Todo el parser del plan y su rechazo fail-closed nació en `31ed4cd`; no existe una explicación histórica que justifique tratar markup no reconocido como ausencia demostrada.

**Gap de prueba:** `tests/test_degree_catalog.py:255-285` inyecta directamente `StudyPlanAcademicYearUnavailable`, por lo que no demuestra que el HTML permita distinguir ausencia real de cambio de esquema.

**Recomendación:** solo marcar el año como no ofertado cuando la página ofrezca evidencia estructural positiva y exhaustiva de los años disponibles. En caso de etiqueta no reconocida con endpoints presentes, fallar como `schema_changed`/incompleto.

### M-03 — La demo afirma ausencia de estado sin read-back — RESUELTO

**Severidad:** MEDIUM

**Archivo:** `src/mcp_usc/demo_audit.py:557-652`

**Blast radius:** 1 CLI opt-in contra una demo pública con reset horario
**Cobertura:** PARTIAL; las pruebas codifican la inferencia incorrecta

Tras una respuesta de borrado sin excepción, el script devuelve `external_state_remaining: False` sin consultar el calendario. Además, `_positive_ids(..., maximum=5)` limita la limpieza a cinco IDs y el código intenta borrar los IDs extraídos incluso cuando el conjunto de propietarios no coincide con el usuario esperado. Una respuesta anómala con más de cinco eventos puede dejar residuos mientras el informe declara lo contrario para los cinco considerados.

**Escenario adversario/de fallo:**

1. la demo devuelve una respuesta de creación anómala, duplicada o mal ligada;
2. el script toma como máximo cinco IDs (`demo_audit.py:593-603`);
3. el endpoint de borrado acepta la petición, devuelve un resultado inesperado o solo aplica parte del efecto;
4. no se ejecuta `core_calendar_get_calendar_events`/lectura por ID;
5. las líneas 636-652 publican `external_state_remaining=false` sin evidencia del estado final.

**Ejecución real del comando solicitado:**

```text
python scripts/moodle_demo_audit.py --confirm-demo --allow-reversible-write
exit code: 0
started_at: 2026-08-24T14:45:21.051641+00:00
finished_at: 2026-08-24T14:45:43.618820+00:00
summary: overall=pass, pass=67, fail=0, skip=21
write.personal_calendar_round_trip: created=1, deleted=1,
  external_state_remaining=false
policy.messages_email_chat_forum_posts_sent=false
```

Para separar la afirmación del script de la evidencia, se hizo después una única lectura segura del calendario del mismo usuario/intervalo. No se repitió la escritura:

```json
{
  "external_state_remaining": false,
  "matching_audit_events": 0,
  "readback_function": "core_calendar_get_calendar_events"
}
```

El estado real de esta ejecución quedó limpio, pero fue la lectura adicional —no el JSON original— la que lo demostró.

**Comunicación externa:** el JSON marcó como `skip` las funciones de mensaje/chat/foro. `DemoRestClient.call` las rechaza en `demo_audit.py:331-337` antes de `_post_json`; no se enviaron mensajes, email, chat ni posts de foro.

**Gaps de prueba:** `tests/test_demo_audit.py:167-209` simula un DELETE exitoso y exige directamente `external_state_remaining=False`; `:212-249` prueba dos IDs anómalos, pero no read-back, más de cinco IDs, propietario distinto ni borrado parcial.

**Recomendación:** conservar el UUID/ID esperado, limpiar solo eventos demostrablemente creados por esta operación y leer el estado final. Si la lectura no prueba ausencia, informar `unknown`, nunca `false`. Tratar más de un evento/propietario inesperado como incidente de protocolo sin borrar IDs ajenos a ciegas.

### L-01 — `--token` y `--password` exponen secretos en argv — RESUELTO

**Severidad:** LOW

**Archivo:** `src/mcp_usc/demo_audit.py:990-1029`

**Blast radius:** 1 CLI demo
**Cobertura:** NO

El CLI acepta bearer token y contraseña mediante argumentos. Aunque el programa no los imprime ni persiste, argv puede quedar visible para otros procesos/usuarios locales y en historial/logs del shell. La contraseña pública de la demo tiene poco valor, pero el token efímero permite actuar como la cuenta hasta caducar/resetearse.

El README recomienda correctamente variables de entorno (`README.md:409-420`), pero el help de `--token` dice “no se guarda ni se imprime” sin advertir la exposición del propio argumento.

**Escenario:** un usuario ejecuta `--token <bearer>`; otro proceso local inspecciona la línea de comandos y reutiliza el token para las funciones que el servicio móvil anuncia.

**Recomendación:** eliminar argumentos de secretos o exigir una fuente que no aparezca en argv (entorno, stdin/terminal sin eco o archivo protegido); como mínimo, advertir y no presentarlos como opción normal.

### L-02 — Contrato inconsistente para códigos con sufijo — RESUELTO

**Severidad:** LOW

**Archivos:** `src/mcp_usc/study_plans.py:34`, `src/mcp_usc/exam_catalog.py:15,66-83`, `src/mcp_usc/official_exams.py:50-59`

**Blast radius:** listado/consulta/horario oficial; sin incidencia en los dos grados curados actuales
**Cobertura:** PARTIAL

v0.7 amplía el parser compartido a `G\d{7}[A-Z]?`, por lo que `discover_official_exam_subjects` puede publicar un código con sufijo. Sin embargo, `normalise_subject_code` aún exige siete cifras y la consulta oficial lo rechaza. `extract_subject_code` es peor para el horario Moodle: en `G1012106A` extrae silenciosamente `G1012106`.

**PoC:**

```json
{
  "study_plan_parser_output": "G1012106A",
  "official_exam_normaliser_output": null,
  "official_exam_normaliser_error": "formato G seguido de 7 cifras",
  "moodle_course_extractor_output": "G1012106"
}
```

La comprobación GET real de los dos grados curados para 2026/2027 obtuvo 87 materias por grado y cero códigos con sufijo, por lo que hoy no rompe esas tres herramientas. El nuevo locator global sí acepta variantes. Es una incompatibilidad latente, no un fallo observado en los perfiles oficiales actuales.

**Historia:** el rechazo `G\d{7}` del baseline y la unicidad estricta nacieron en `31ed4cd`. v0.7 relaja parser/duplicados para itinerarios; `git blame` no muestra un fix de seguridad anterior, pero faltó propagar el nuevo contrato a todos los consumidores.

**Recomendación:** definir un único normalizador de código y usarlo en parser, locator, consulta oficial y extracción desde Moodle; añadir round-trip de variantes entre listado y consulta.

## 5. Análisis de caché y HTTP

### Propiedades verificadas

- `SafePublicHttpFetcher` solo emite GET y desactiva redirects automáticos (`public_http_cache.py:403-412`).
- Cada redirect debe conservar origen exacto y superar el validador de ruta del caller (`:420-437`).
- Los cuerpos se acotan por `Content-Length` y por streaming (`:449-462`).
- ETag/Last-Modified pasan por un filtro de longitud/caracteres de control (`:94-99`).
- `no-store` y `Vary: *|cookie|authorization` impiden almacenar; `no-cache/max-age=0` fuerza revalidación y `must-revalidate` impide stale (`:102-129`).
- Una respuesta candidata se valida antes de `commit` (`:463-471`). Una respuesta con schema inválido no sustituye la última entrada válida.
- La LRU está acotada por entradas y bytes (`:305-333`) y colapsa concurrencia por URL (`:335-352`).
- El resumen MCP no expone ETag/Last-Modified y acota el detalle visible (`:480-540`).

### Supuestos y limitaciones

- La clave es solo la URL (`public_http_cache.py:382`), no los headers nombrados por un `Vary` no sensible. Los callers actuales usan headers estables por clase de URL, pero la abstracción es un sharp edge para futuros consumidores.
- El cache acepta `Vary` distintos de `*`, `cookie` y `authorization` sin construir variantes.
- `Age`/`Expires` no intervienen; el TTL empieza al recibir la respuesta.
- El beneficio real en MCP queda anulado por M-01 hasta corregir el ciclo de vida.
- La caché debe permanecer separada de cualquier GET autenticado. En el código revisado solo recibe fuentes públicas anónimas.

## 6. Historial y validaciones removidas

Se ejecutaron `git log -S/-G`, `git blame` al baseline y búsqueda de mensajes `security|fix|CVE|vulnerab|validation|SSRF|cache|schema|redirect`.

- No aparecieron commits con mensajes de fix/CVE/seguridad en el historial disponible.
- `validate_usc_url` y el cliente público genérico proceden de `6a4ddbb`.
- El calendario estructurado y sus checks de redirect/cookies/límites proceden de `347b38c`.
- El plan estructurado, incluida unicidad de código/URL, procede de `31ed4cd`.
- La factory `server._service()` también procede de `6a4ddbb`; M-01 es una incompatibilidad nueva entre ese diseño y el estado v0.7.
- El rechazo baseline de códigos/URLs duplicados (`study_plans.py` baseline 294-301) se reemplaza por fusión condicionada: mismo código exige mismo título y una ficha no puede pertenecer a códigos distintos. La relajación es deliberada para itinerarios; no se encontró un fix de seguridad histórico revertido.
- El transporte HTTP eliminado de `study_plans.py`/`exam_calendar.py` fue sustituido por el fetcher común que conserva checks de método, redirect, tamaño, content-type AJAX y cookies. No se observó una validación de seguridad eliminada sin reemplazo en ese traslado.

## 7. Cobertura y verificación local del corte inicial

### Comandos

```text
.\.venv\Scripts\python.exe -m pytest
432 passed in 12.55s

.\.venv\Scripts\ruff.exe check .
All checks passed!

git diff --check 31ed4cdf6d71a863480173351fb1dc9806dec7ef
PASS
```

Pruebas recogidas en los módulos directamente afectados:

| Módulo | Casos recogidos |
|---|---:|
| `test_degree_catalog.py` | 21 |
| `test_demo_audit.py` | 10 |
| `test_mcp_stdio.py` | 1 |
| `test_official_exams.py` | 11 |
| `test_public_http_cache.py` | 12 |
| `test_settings.py` | 12 |
| `test_study_plans.py` | 18 |
| **Total** | **83** |

No se ejecutó instrumentación de line/branch coverage; por tanto no se afirma un porcentaje de cobertura de líneas.

### Gaps concretos

Los gaps del corte inicial enumerados a continuación quedaron cubiertos en la reauditoría final; se conservan para trazabilidad.

| Gap | Riesgo |
|---|---|
| Dos invocaciones MCP reales deben compartir caché y permitir stale/revalidación | MEDIUM, M-01 |
| Cambio de etiqueta con endpoint presente debe fallar incompleto, no `not_found` | MEDIUM, M-02 |
| DELETE demo exitoso pero read-back aún presente/parcial/desconocido | MEDIUM, M-03 |
| Respuesta create con >5 IDs o propietario distinto | MEDIUM, M-03 |
| Código `GdddddddA/B` listado debe poder consultarse/extrarse sin truncar | LOW, L-02 |

## 8. Verificación externa acotada del corte inicial

### Demo oficial Moodle

- Destino exacto: `https://school.moodledemo.net`.
- Credenciales públicas actuales usadas mediante variables de entorno: `student` / contraseña pública indicada para la auditoría; no se imprimió el token.
- Ejecución única con escritura: exit 0, 67 pass, 0 fail, 21 skip.
- Política JSON: no mensajes/email/chat/foro; la inspección del código y probes confirma que esas funciones quedaron en `skip` y son rechazadas antes de HTTP.
- Evento: JSON informó 1 creado y 1 borrado.
- Read-back posterior: 0 eventos con prefijo `mcp-usc demo audit` en el intervalo esperado; estado externo observado limpio.
- No se repitió la escritura.

### Herramientas públicas USC reales

Se usó un único grado filtrado y `concurrency=1`, evitando el barrido global:

```json
{
  "catalog_count": 65,
  "filtered_degree_url": "https://www.usc.gal/gl/estudos/graos/ciencias/dobre-grao-matematicas-fisica-1",
  "selected_degree_count": 1,
  "scanned_degree_count": 1,
  "subject_code": "G1012106",
  "subject_status": "matched",
  "location_count": 1,
  "degree_issue_count": 0,
  "complete": true,
  "catalog_status": "fresh",
  "cache_status": "fresh"
}
```

El flujo pasó por `UscDegreeCatalogClient` y `UscStudyPlanClient`, cuyos únicos accesos de red son GET. No se invocó ningún gateway Moodle ni hubo petición o mutación contra `cv.usc.es`.

Adicionalmente se consultaron por GET los dos planes curados oficiales de 2026/2027 para acotar L-02: 87 materias en cada uno, cero códigos con sufijo.

## 9. Recomendaciones priorizadas

### Bloqueantes para aprobar v0.7

- [x] M-01: dar vida de proceso a la caché pública y probar dos invocaciones MCP separadas.
- [x] M-02: distinguir ausencia de año mediante evidencia positiva; cualquier markup no reconocido debe afectar completitud.
- [x] M-03: verificar por lectura la ausencia del evento y no declarar `false` ante resultado no demostrado.

### Antes de publicar

- [x] L-01: retirar secretos de argv o documentar/bloquear claramente esa vía.
- [x] L-02: unificar el contrato de códigos con sufijo en todos los consumidores.
- [x] Añadir los cinco escenarios de cobertura de la sección 7.

### Deuda técnica

- [x] Rechazar todo `Vary` no representado en la clave de caché.
- [ ] Documentar explícitamente el ciclo de vida, aislamiento y modelo de concurrencia de la caché.
- [ ] Añadir cobertura de líneas/ramas para parsers y cleanup demo sin sustituir los tests adversarios por un porcentaje.

## 10. Limitaciones y confianza

- La revisión cubrió todos los archivos del diff y dependencias de primer salto; no fue una auditoría de todas las 66 unidades del repositorio.
- No se hizo packet capture. El carácter GET-only USC se verificó en código, pruebas con transport mock y rutas ejecutadas; la demo REST de Moodle usa POST por diseño.
- No se hizo un barrido global de 65 grados: la prueba real fue deliberadamente filtrada a uno, conforme al alcance solicitado.
- La demo es compartida y se resetea cada hora; el read-back demuestra el estado observado inmediatamente después, no una garantía permanente.
- El JSON de demo no expone el ID/nombre completo del evento. El read-back buscó el prefijo único de auditoría dentro del intervalo de creación.
- No se auditó la implementación de Moodle/Drupal/httpx/BeautifulSoup ni la cadena de suministro.
- El árbol estaba no comprometido y puede cambiar después del fingerprint indicado. Los siete archivos nuevos estaban untracked.
- No se ejecutó ninguna mutación ni prueba de envío en `cv.usc.es`.

**Confianza final:** HIGH en el cierre de los hallazgos y en la evidencia local; MEDIUM en comportamiento externo futuro por depender de servicios compartidos y esquemas remotos.

## 11. Veredicto

**APPROVE.** Para el fingerprint final auditado, M-01, M-02, M-03, L-01 y L-02 están resueltos, la caché rechaza representaciones `Vary` que su clave no modela y no se observaron hallazgos nuevos. La suite, Ruff, `git diff --check`, el GET USC filtrado y el único round-trip externo autorizado terminaron satisfactoriamente.

## 12. Reauditoría final: remediación, fingerprint y evidencia

### Estado de las remediaciones

| ID | Evidencia de implementación | Prueba/reproducción final | Estado |
|---|---|---|---|
| M-01 | `server.py:66-91` conserva con `lru_cache` únicamente un `PublicHttpCache` parametrizado e inyecta un servicio nuevo; `service.py:407-423` mantiene la creación del gateway autenticado fuera de ese estado compartido. | `test_degree_catalog.py:495-540`: dos entrypoints MCP separados producen miss y hit sobre la misma caché. Inspección de dos `_service()` confirmó servicios/settings distintos y solo `_public_http_cache` idéntica. | RESUELTO |
| M-02 | `study_plans.py:217-250` exige que todo endpoint `study-plan-by-course` tenga una etiqueta reconocida; solo la evidencia positiva de otros años produce `StudyPlanAcademicYearUnavailable`. `study_plans.py:592-611` convierte markup no reconocido en `StudyPlanSchemaChangedError`; `degree_catalog.py:181-204,445-469` lo hace incompleto y evita `not_found`. | PoC `Curso académico: 2026/2027` con `:` en `test_degree_catalog.py:322-353`: `complete=false`, estado `source_changed_or_unavailable`, issue `schema_changed`. La página sin endpoints también falla como schema change (`test_study_plans.py:248-259`). | RESUELTO |
| M-03 | `demo_audit.py:517-784` exige exactamente un evento, hace read-back por ID, verifica propietario/marcador/tipo antes de borrar y hace un segundo read-back después. Solo la ausencia comprobada produce `false`; persistencia produce `true` y respuestas/lecturas ambiguas producen `unknown`. | `test_demo_audit.py:168-229` prueba 1 create/1 delete/2 reads; `232-270`, `274-313` y `316-356` cubren `unknown`, `true` y propietario inesperado sin borrado ciego. La demo real final confirmó `created=1`, `deleted=1`, `read_back_checks=2`, `external_state_remaining=false`. | RESUELTO |
| L-01 | `demo_audit.py:1121-1159` ya no define `--token` ni `--password`; lee `MOODLE_DEMO_TOKEN`/`MOODLE_DEMO_PASSWORD` del entorno. | `test_demo_audit.py:368-372` inspecciona el help. Búsqueda global no encontró opciones de secretos en argv; la ejecución externa usó variables de entorno y no imprimió token ni contraseña. | RESUELTO |
| L-02 | `exam_catalog.py:15-18,69-90` normaliza y extrae `G` + 7 cifras + un sufijo A-Z opcional con límites que impiden truncar códigos más largos; todos los parsers reutilizan ese contrato. | `test_exam_catalog.py:25-41`, `test_study_plans.py:133-150`, `test_exam_calendar.py:271-278` y `test_official_exams.py:110-139` cubren minúsculas, round-trip, ficha y flujo oficial sin truncado. | RESUELTO |
| Vary | `public_http_cache.py:102-114` rechaza toda lista `Vary` no vacía porque la clave actual solo representa la URL; `candidate` no llega a almacenar la respuesta. | `test_public_http_cache.py:271-289` hace dos GET con `Vary: Accept-Language`, obtiene dos misses y cero entradas. La condición es genérica: también rechaza `*` y listas múltiples no vacías. | CERRADO |

### Verificación local final

```text
.\.venv\Scripts\python.exe -m pytest
446 passed in 11.55s

.\.venv\Scripts\python.exe -m pytest -vv <10 regresiones dirigidas>
10 passed in 6.57s

.\.venv\Scripts\python.exe -m ruff check .
All checks passed!

git diff --check
PASS
```

No se modificó producción ni tests durante esta reauditoría. El único archivo editado por el auditor fue este informe.

### Verificación externa final y blast radius observado

La prueba independiente de demo usó el cliente final con credenciales públicas actuales inyectadas mediante variables de entorno. Para cumplir literalmente la prohibición de probar mensajes/chat/foro, se ejecutó un runner estrecho que abortaba antes de HTTP ante prefijos `core_message`, `mod_chat` o `mod_forum`. La secuencia exacta observada fue:

```json
{
  "called_functions": [
    "core_webservice_get_site_info",
    "core_calendar_get_calendar_access_information",
    "core_calendar_get_allowed_event_types",
    "core_calendar_create_calendar_events",
    "core_calendar_get_calendar_events",
    "core_calendar_delete_calendar_events",
    "core_calendar_get_calendar_events"
  ],
  "collaboration_functions_called": false,
  "counts": {"create": 1, "delete": 1, "read_back": 2},
  "round_trip": {
    "status": "pass",
    "metrics": {
      "created": 1,
      "deleted": 1,
      "external_state_remaining": false,
      "read_back_checks": 2
    }
  }
}
```

No se repitió la mutación. Como corroboración separada, el mantenedor comunicó una ejecución del runner completo final con `67 pass / 0 fail / 21 skip` y dos read-backs; no se trata como sustituto de la ejecución independiente y estrecha anterior.

La consulta real USC volvió a usar `degree_urls` con un único grado y `concurrency=1`. Observó tres recursos GET (catálogo, página del grado y endpoint del plan): 65 titulaciones catalogadas, 1 seleccionada, 1 escaneada, `G1012106` en estado `matched`, cero issues y `complete=true`. No hubo barrido global. No se llamó ni mutó `cv.usc.es`.

### Fingerprint final reproducible

El informe no forma parte del material fingerprintado, para que su propia edición no cambie el objeto auditado.

- Baseline: `31ed4cdf6d71a863480173351fb1dc9806dec7ef`.
- Archivos materiales: 27 (20 tracked modificados y 7 untracked), `+4631/-233` líneas.
- Blob Git del `git diff --binary` tracked: `47a3d1e1abe711f6b24db42a6781980ce8f4a623`.
- SHA-256 del manifiesto completo: `dc6278a9aefd73a0a91518fd644b686f94802522427ca05238836dadf1774c39`.

El manifiesto se construyó con una primera línea `baseline <commit>` y, ordenadas por ruta con comparación ordinal, 27 líneas `<git hash-object del archivo> <ruta>`, unidas con LF y sin LF final. Así incluye los siete archivos materiales aún untracked y permite detectar cualquier cambio posterior de contenido o ruta.

### Limitaciones específicas de la reauditoría

- No se hizo packet capture: los métodos se verificaron por implementación, mocks y la lista de funciones observada.
- La demo compartida puede resetearse o cambiar; los dos read-backs prueban el estado inmediatamente observado, no el comportamiento futuro del servicio.
- La ejecución externa estrecha no recorrió el resto de capacidades de la demo porque el alcance final prohibía llamar o probar mensajes/chat/foro; esas ramas quedaron cubiertas offline por la suite.
- La consulta USC fue deliberadamente filtrada a un grado; no demuestra que los otros 64 grados mantengan hoy el mismo esquema.
- El árbol continúa sin commit; cualquier cambio exige recalcular el fingerprint y revalidar el veredicto.
