# Revisión de seguridad: sesión web HTTP de Moodle

Fecha: 2026-08-24
Alcance: cambios no committeados de adquisición/importación de `MoodleSession`, transporte HTTP
de sesión, formularios de tareas/cuestionarios, uploads, confirmaciones y esquemas MCP.
Limitación: revisión estática y pruebas con fakes/respx; no se realizaron peticiones autenticadas
ni mutaciones contra el Campus Virtual USC.

## Dictamen

Aprobado para publicar desde el punto de vista de seguridad revisado. No quedan hallazgos críticos,
altos ni medios abiertos en el código. La matriz de compatibilidad de `README.md` se actualizó para
describir los formularios HTTP confirmados y el flujo non-JS de borrador habilitados por este cambio.

## Superficie revisada

- Importación manual mediante prompt oculto, validación contra `/user/preferences.php`, rotación y
  almacenamiento en keyring (`src/mcp_usc/session_auth.py:115`).
- Eliminación exclusivamente local de la credencial, sin logout remoto
  (`src/mcp_usc/session_auth.py:196`).
- Origen HTTPS, allowlists de paths canónicos, redirects, cookie rotation, sesskey y transporte de
  formularios (`src/mcp_usc/campus.py:144`, `src/mcp_usc/campus.py:721`,
  `src/mcp_usc/campus.py:910`, `src/mcp_usc/campus.py:950`).
- Multipart en memoria, límites y nombres/MIME (`src/mcp_usc/campus.py:1039`), además del flujo
  Moodle non-JS descubierto desde formularios frescos (`src/mcp_usc/session_forms.py:576`).
- Preview/confirmación ligados a identidad y parámetros, lectura stateful separada y resultado
  `unknown/do_not_retry` para transportes ambiguos (`src/mcp_usc/service.py:379`,
  `src/mcp_usc/service.py:1419`).
- Anotación MCP no-readonly/no-destructive para inspección stateful
  (`src/mcp_usc/server.py:53`, `src/mcp_usc/server.py:530`).

## Hallazgos corregidos durante la revisión

1. **Alto — traversal de allowlist de paths.** Los prefijos aceptaban `../` y variantes codificadas
   que `httpx` o el servidor podían normalizar fuera de `/pluginfile.php` o del formulario. Se
   sustituyeron por paths exactos/canónicos y se añadieron regresiones sin red.
2. **Alto — segunda mutación mediante redirect.** Un POST podía seguir un `Location` a un GET con
   `sesskey`/acción mutante. Los POST y GET stateful ya no siguen redirects ni se reintentan.
3. **Alto — resultado ambiguo reintentable.** Timeout o HTTP 5xx después de una mutación podían
   parecer un fallo limpio. Ahora se devuelve `outcome=unknown`, `do_not_retry=true`; los borradores
   parciales también se señalan explícitamente.
4. **Alto — GET ocultamente mutantes bajo herramientas READ_ONLY/preview.** Las previews de tareas
   ya no abren `mod/assign/view.php`; la inspección usa un par preview/confirmación y anotación
   stateful. El listado usa `core_courseformat_get_state`, cuya implementación oficial Moodle 4.5
   exporta estado sin disparar eventos.
5. **Medio — exposición de cookie/sesskey mediante excepciones.** Los límites `httpx` eliminan la
   causa que retenía URL, body o cabecera Cookie. Los dataclasses con acciones/HTML/bytes sensibles
   también ocultan esos campos en `repr`.
6. **Medio — TOCTOU de uploads.** La ejecución vuelve a inspeccionar el archivo, comprueba el hash
   aprobado, captura exactamente esos bytes en memoria y reutiliza esa captura para multipart.
7. **Medio — ciclo de cookie.** Redirecciones de login/logout se clasifican antes de guardar un
   `Set-Cookie`; la cookie rotada se valida y un fallo local de keyring no convierte una mutación ya
   respondida en un error reintentable.

## Pruebas y cobertura

- `uv run pytest -q`: **330 passed**.
- `uv run ruff check .`: **OK**.
- `git diff --check`: **OK**.
- Regresiones específicas cubren traversal literal/codificado, cero seguimiento de redirects tras
  POST, redirect de autenticación sin persistir `deleted`, timeout/500 ambiguos, ausencia de causas
  con secretos, confirmación stateful y cambio de archivo tras preview.

## Blast radius e historial

La sesión HTTP se incorporó originalmente en `6a4ddbb`; los formularios y entregas en `83a3bbf`, y
el hardening previo en `cf86988`. El cambio actual afecta todos los consumidores del gateway de
sesión: estado, AJAX, pluginfile, tareas, cuestionarios y uploads. REST conserva su selección
preferente cuando hay token. La CLI añade importación/eliminación local, pero el transporte MCP
STDIO no recibe ni devuelve la cookie.

## Riesgo residual documentado

La matriz de compatibilidad y el apartado de archivos de `README.md` explican: CMID en modo sesión,
GET stateful confirmado, operación de borrador non-JS potencialmente parcial y la prohibición de
reintento automático. Sigue siendo necesaria una prueba manual local para confirmar qué variantes
HTML y repositorios non-JS expone la instalación concreta de la USC.

## Metodología y confianza

Se aplicó revisión diferencial orientada a seguridad, trazado manual de llamadas y estados,
comparación con el historial git, pruebas focales y suite completa. Se consultó el código oficial
Moodle 4.5 de `core_courseformat_get_state`; no se hizo fingerprint autenticado de la instancia USC.
Confianza alta en los invariantes locales y de transporte; confianza media en compatibilidad exacta
del HTML/plugin non-JS de la instalación USC hasta probarlo manualmente con una cuenta de alumno.
