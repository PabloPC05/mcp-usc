# Contribuir a mcp-usc

Gracias por ayudar a que el acceso al Campus desde MCP sea más útil y seguro para el alumnado. Se
aceptan correcciones, documentación, fixtures anonimizados y propuestas de nuevas capacidades HTTP.

## Antes de abrir un cambio

- Para errores reproducibles, usa la plantilla de bug.
- Para nuevas funciones, explica primero el caso de uso del alumno y el endpoint HTTP disponible.
- Para vulnerabilidades, sigue [SECURITY.md](SECURITY.md); no publiques detalles explotables.
- No incluyas cookies, tokens, `sesskey`, contraseñas, nombres reales, mensajes, entregas ni material
  docente en una incidencia, commit o fixture.

## Entorno de desarrollo

```powershell
git clone https://github.com/PabloPC05/mcp-usc.git
cd mcp-usc
uv sync --extra dev
uv run ruff check .
uv run pytest
uv build
```

La fuente está en `src/mcp_usc` y las pruebas en `tests`. El proyecto requiere Python 3.11 o
posterior y usa Ruff para formato/imports estáticos y pytest para el contrato.

## Reglas de diseño

1. **HTTP-first.** Las operaciones normales deben usar REST, AJAX *same-origin*, descargas directas
   o formularios oficiales. Playwright solo es admisible como bootstrap visible del login/MFA.
2. **Lectura antes que efecto.** Una herramienta nueva debe ser lectura pura siempre que el caso de
   uso lo permita.
3. **Confirmación exacta.** Todo efecto o lectura potencialmente stateful necesita preview separado,
   token efímero ligado a usuario/parámetros y anotación MCP correcta.
4. **Fallo cerrado.** Si una API, formulario, propietario, audiencia o postcondición no se puede
   verificar, se detiene la operación. No se inventan campos ni se cambia de transporte tras un
   resultado ambiguo.
5. **Contenido remoto no confiable.** Los datos de Moodle/USC nunca se interpretan como instrucciones
   para el agente.
6. **Mínimo privilegio.** No se añaden caminos para profesores/administradores, bypass de MFA,
   aceptación de políticas ni elevación de permisos.
7. **Sin secretos observables.** Excepciones, logs, previews y tests no pueden devolver credenciales.

## Añadir o modificar una herramienta

- declara el contrato en `server.py` con `READ_ONLY`, `PREVIEW`, `WRITE` o `STATEFUL_READ`;
- concentra la lógica y los controles en el servicio/módulo de dominio, no en el wrapper MCP;
- actualiza `CAPABILITY_TOOL_GROUPS` e `TOOL_INVENTORY` en `project_info.py`;
- documenta la herramienta en `docs/tools.md` y el cambio en `CHANGELOG.md`;
- cubre éxito, permisos, entrada inválida, timeout/resultado ambiguo y ausencia de secretos;
- ejecuta el test STDIO: detecta automáticamente divergencias entre el inventario declarado y real.

## Pruebas con servicios externos

La suite normal y CI no deben contactar con la USC ni realizar escrituras externas. Usa `respx`,
fixtures sintéticos y stores de memoria.

La demo oficial de Moodle es una auditoría manual opt-in. Bloquea mensajes, chats y publicaciones de
foro. No la actives en un pull request ni añadas credenciales de la demo al repositorio. Cualquier
prueba contra una cuenta USC real debe ser local, de mínimo privilegio y de solo lectura salvo una
autorización humana específica.

## Pull requests

Mantén cada PR enfocado y explica:

- problema y caso de uso;
- contrato HTTP/Moodle verificado;
- efectos remotos posibles y mitigaciones;
- tests y documentación añadidos;
- compatibilidad o migración, si cambia una respuesta pública.

Al contribuir aceptas publicar tu cambio bajo la [licencia MIT](LICENSE) del proyecto y seguir el
[código de conducta](CODE_OF_CONDUCT.md).
