# Revisión diferencial de seguridad — v0.11.0

Fecha: 2026-08-25

Base: `v0.9.0` / `604f293`

Alcance: cambios locales que preparan `v0.11.0`

## Resumen ejecutivo

La revisión cubrió los nuevos horarios públicos, el perfil académico local, los
fallbacks de lectura con `MoodleSession`, el diagnóstico de sesión y las acciones
contextuales de finalización. El cambio tiene riesgo alto por tocar autenticación y
mutaciones, aunque las nuevas consultas de horario son anónimas y de solo lectura.

No quedan hallazgos de seguridad conocidos abiertos. Durante la revisión se
encontraron y corrigieron cuatro defectos antes de publicar:

1. El estado de auto-finalización se interpretaba inicialmente con una forma plana
   que no coincide con el contrato oficial de Moodle. Ahora exige
   `completionstatus.completions` y el criterio propio de tipo `1`.
2. Una mutación que devolviese `status: false` podía parecer satisfactoria. Ahora se
   informa como `rejected`; una forma desconocida falla cerrada.
3. Una página de login servida con HTTP 200 podía escapar a la detección en cuerpos
   leídos en streaming. La comprobación se repite tras la lectura acotada y reconoce
   HTML sin depender de mayúsculas/minúsculas.
4. El logging informativo de `httpx` heredado de FastMCP mostraba la URL AJAX con el
   `sesskey`. Los loggers HTTP quedan deshabilitados al importar y arrancar el servidor.

## Superficie modificada y flujo de datos

- `server.py` expone 91 herramientas y delega en `UscService`.
- `academic_profile.py` acepta configuración local no secreta y solo URLs HTTPS de
  páginas oficiales de titulaciones USC.
- `class_timetables.py` sigue enlaces públicos con rutas tipadas, límites de tamaño,
  validación de redirecciones y selección explícita de plan cuando hay ambigüedad.
- `session_course_state.py` usa `core_courseformat_get_state` y no abre páginas de
  actividades, evitando registrar vistas durante listados.
- `activity_actions.py` obtiene del servidor la identidad, pertenencia y estado antes
  de permitir una acción propia.
- `campus.py` conserva la cookie en el almacén de credenciales y no la incluye en
  diagnósticos ni respuestas MCP.

El dossier detallado de callers, callees, estado y límites de confianza está en
`audit-context/DOSSIER.md`.

## Hallazgos

| Severidad | Estado | Hallazgo |
|---|---|---|
| Media | Corregido | Contrato incorrecto del payload de auto-finalización de Moodle. |
| Media | Corregido | `status: false` no se distinguía de una mutación aceptada. |
| Baja | Corregido | Detección incompleta de login HTTP 200 en respuestas streaming. |
| Alta | Corregido | El logging HTTP informativo exponía `sesskey` en stderr. |
| — | Sin hallazgos abiertos | No se identificaron bypasses restantes en el diff revisado. |

## Invariantes verificadas

- Las mutaciones nuevas afectan a la cuenta autenticada: no aceptan `user_id`.
- Cada ejecución exige preview, identidad y contexto remoto idénticos, token aleatorio
  con TTL de cinco minutos y consumo de un solo uso.
- Un contexto denegado nunca emite token.
- Tras una excepción posterior al envío no se reintenta; se devuelve resultado
  `unknown` con `do_not_retry`.
- Las rutas públicas y autenticadas se validan antes de seguir redirecciones.
- Los contenidos de Moodle/USC se marcan como no confiables y se acotan.
- Los fallbacks de sesión no inventan IDs internos de plugins ni URLs de descarga.

## Pruebas y cobertura

- Suite completa: 549 pruebas superadas.
- Casos contextuales: parámetros alterados, contexto remoto alterado, preview denegada,
  una sola mutación, token no reutilizable y error posterior al envío.
- Fixtures de sesión para contratos comunes de Moodle 4.5, 5.0 y 5.2.
- Detección de login HTTP 200, incluyendo respuestas streaming.
- Parsers de horarios, selección de plan, varios centros, datos parciales y límites.
- `ruff check .`, `compileall`, `git diff --check` y build sdist/wheel correctos.

## Radio de impacto y compatibilidad

La detección de autenticación se comparte entre AJAX y descargas de sesión; un falso
positivo bloquearía una lectura, no ejecutaría una acción. Los nuevos fallbacks solo
se seleccionan para el gateway de sesión; REST conserva sus clientes anteriores. El
perfil es opcional, por lo que instalaciones sin configuración mantienen el flujo
explícito de horarios. Las cuatro herramientas nuevas de finalización amplían la
superficie sin modificar firmas existentes.

## Riesgos residuales y requisitos externos

- Los permisos y plugins disponibles dependen de la configuración real del Campus.
- La USC puede cambiar el HTML o los endpoints públicos; el cliente falla cerrado y
  devuelve incidencias/completitud en vez de mezclar planes por semejanza.
- No se han realizado mutaciones reales: requieren una cuenta de pruebas controlada
  y autorización explícita.
- Aún se necesita validación con varias cuentas/roles y versiones reales para declarar
  estabilidad 1.0; no es un cambio de código que pueda completarse de forma segura en
  esta máquina.

## Método

Se revisó el diff completo desde la base, el historial de los módulos afectados, los
límites de confianza y los call chains MCP → servicio → gateway. Los contratos de las
dos funciones de finalización se contrastaron con el código oficial de Moodle 4.5. La
revisión fue adversarial para identidad, replay, TOCTOU, ambigüedad de plan, SSRF,
redirecciones, respuestas parciales y resultados de mutación inciertos.
