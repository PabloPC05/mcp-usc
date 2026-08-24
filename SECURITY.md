# Política de seguridad

`mcp-usc` maneja una credencial de sesión o un token Moodle y puede ejecutar acciones académicas. Un
fallo de seguridad puede afectar datos personales, entregas o comunicaciones; trátalo como software
sensible y ejecútalo solo en equipos de confianza.

## Versiones con soporte

| Versión | Soporte de seguridad |
| --- | --- |
| 0.9.x | Sí |
| 0.8.x | Correcciones críticas durante la transición a 0.9 |
| 0.7.x y anteriores | Actualiza antes de reportar salvo que el problema también afecte a 0.9.x |

Mientras el proyecto sea beta, las correcciones se publicarán en la última serie menor.

## Reportar una vulnerabilidad

Usa **Security > Report a vulnerability** en el
[repositorio de GitHub](https://github.com/PabloPC05/mcp-usc/security) para abrir un informe privado.
No abras una incidencia pública con un exploit, una credencial o datos del Campus. Si el formulario
privado no estuviera disponible, abre una incidencia pública mínima que solo solicite un canal
privado, sin detalles técnicos sensibles.

Incluye, de forma anonimizada:

- versión/commit y sistema operativo;
- componente y precondiciones;
- impacto y pasos mínimos para reproducir con datos sintéticos;
- si existe, un parche o mitigación sugerida.

Nunca adjuntes `MoodleSession`, tokens, `sesskey`, contraseñas, URLs firmadas, nombres, mensajes,
entregas, calificaciones ni capturas con información personal. Recibirás acuse en el propio informe
privado; los tiempos de corrección dependen de impacto y disponibilidad del mantenedor.

## Modelo de credenciales

- Un token REST se lee desde `USC_MOODLE_TOKEN` o un archivo local indicado por
  `USC_MOODLE_TOKEN_FILE`; no debe residir en `.env` versionado.
- `MoodleSession` se guarda con `keyring` bajo el nombre `moodle-session`. En Windows se utiliza
  Credential Manager cuando está disponible.
- El `sesskey` AJAX se obtiene en memoria, no se persiste ni se devuelve.
- `mcp-usc doctor` solo informa de presencia/estado local. `mcp-usc status` sí contacta con Moodle
  mediante una lectura para validar la credencial.
- `forget-session` elimina la copia local sin enviar un logout ni alterar la sesión remota.

Una cookie vigente equivale a una credencial. Si crees que se ha expuesto, cierra las sesiones desde
los canales oficiales de la cuenta USC y genera/importa una nueva; no la pegues en GitHub.

## Garantías y límites

- Las escrituras requieren preview, token en memoria de un solo uso y aprobación del host.
- Un timeout de escritura produce resultado ambiguo y no se reintenta automáticamente.
- El conector no eleva privilegios: Moodle decide qué puede hacer la cuenta.
- Solo se permiten fuentes públicas HTTPS bajo dominios USC, con límites de redirección y tamaño.
- Las subidas se restringen a una carpeta local allowlist y muestran hash/tamaño en el preview.
- El contenido remoto se etiqueta conceptualmente como no confiable y no autoriza acciones.
- No se accede a correo, Teams, contraseñas, aceptación de políticas ni funciones administrativas.

Consulta [Arquitectura](docs/architecture.md) y la
[revisión HTTP/sesión](docs/security-http-session-review-2026-08-24.md) para el análisis técnico.

## Operación segura recomendada

- usa una cuenta de alumno y un token de mínimo privilegio;
- conserva la aprobación `writes` del cliente MCP;
- revisa siempre destinatario, texto, actividad, intento y archivos del preview;
- mantén `USC_UPLOAD_ROOT` pequeño y fuera de carpetas sincronizadas públicamente;
- actualiza dependencias desde fuentes verificadas y ejecuta la última versión soportada.
