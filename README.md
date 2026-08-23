# mcp-usc

Servidor MCP local y HTTP-first para el Campus Virtual Moodle de la Universidade de Santiago de
Compostela. Permite consultar cursos, calendario, mensajes, foros, materiales, tareas y
cuestionarios, además de buscar fechas de examen en páginas y PDF oficiales de la USC.

La versión 0.3.0 amplía la cobertura del alumno a 301 capacidades Moodle estudiadas: 192 lecturas
permitidas y 109 acciones identificadas. Solo doce cambios privados de alcance inequívoco se
pueden ejecutar por la interfaz genérica; publicaciones, actividades evaluables, entregas,
cuestionarios y eliminaciones usan herramientas contextuales. Toda operación con efecto exige
previsualización, token de un solo uso y aprobación del cliente MCP.

## Principios de diseño

- El servidor MCP usa STDIO; «HTTP-first» describe la conexión entre este proceso y Moodle/USC.
- Las consultas y escrituras normales no automatizan un navegador.
- Se prefiere la API REST oficial de Moodle cuando hay un token legítimo.
- Con una cookie `MoodleSession`, las lecturas usan AJAX *same-origin* y descargas directas
  `/pluginfile.php`. Los formularios HTML se reservan a operaciones de cuestionario ya confirmadas.
- Playwright solo abre un navegador visible para completar Microsoft Entra/MFA y obtener la cookie
  inicial. Se cierra al terminar el login.
- Todo texto remoto —nombres, mensajes, preguntas, avisos y documentos— se marca como contenido no
  confiable y nunca se interpreta como instrucciones.
- El conector actúa únicamente con los permisos de la cuenta autenticada: no eleva privilegios ni
  suplanta a profesorado o administración.
- Debe configurarse con una cuenta de alumno y un token de mínimo privilegio. Las APIs compartidas
  de Moodle siempre respetan los permisos efectivos y una cuenta con roles adicionales podría ver
  más datos que un alumno normal.

No consulta correo ni Teams. Un mensaje interno de Moodle puede generar notificaciones externas
según la configuración del destinatario; la vista previa lo advierte antes del envío.

## Requisitos

- Windows, Linux o macOS;
- Python 3.11 o posterior;
- [`uv`](https://docs.astral.sh/uv/) recomendado;
- una cuenta USC activa para los datos privados;
- opcionalmente, un token de Moodle Web Services que exponga las funciones necesarias.

## Instalación

```powershell
git clone https://github.com/PabloPC05/mcp-usc.git
cd mcp-usc
uv sync --extra dev
```

Esto basta para ejecutar el servidor con un token REST o con una sesión ya almacenada. Instala
Playwright únicamente si necesitas crear o renovar la sesión mediante el asistente de login:

```powershell
uv sync --extra dev --extra browser-auth
uv run playwright install chromium
```

El asistente puede usar Chromium o un Chrome/Edge instalado:

```powershell
$env:USC_BROWSER_CHANNEL = "chrome" # también "msedge" o "chromium"
```

## Autenticación y transportes HTTP

El conector selecciona automáticamente el transporte privado en este orden:

1. REST oficial si `USC_MOODLE_TOKEN` o `USC_MOODLE_TOKEN_FILE` proporciona un token.
2. HTTP con la cookie `MoodleSession` guardada por `keyring`.

### Token REST

Usa únicamente un token legítimo emitido por Moodle para tu cuenta y servicio:

```powershell
$env:USC_MOODLE_TOKEN = "..."
uv run mcp-usc status
```

También puede leerse desde un archivo local protegido:

```powershell
$env:USC_MOODLE_TOKEN_FILE = "C:\ruta\privada\moodle-token.txt"
```

No uses tu contraseña USC con `login/token.php` ni la guardes en `.env`. Que una función exista en
Moodle no implica que esté habilitada en el servicio asociado al token.

### Sesión por cookie

```powershell
uv run mcp-usc login
uv run mcp-usc status
```

Completa personalmente Microsoft Entra y MFA en la ventana visible. El programa extrae solo
`MoodleSession`, comprueba la sesión mediante HTTP y guarda la cookie con la clave
`moodle-session` en el almacén seguro del sistema —Credential Manager en Windows—. La contraseña
no pasa por el MCP.

Después del login, todas las operaciones usan `httpx`:

- `/user/preferences.php` aporta la identidad y el `sesskey` efímero sin abrir el dashboard;
- `/lib/ajax/service.php` ejecuta funciones marcadas como AJAX;
- las lecturas fallan de forma cerrada si Moodle no las publica por AJAX;
- las descargas autenticadas conservan la cookie, aceptan solo `/pluginfile.php` directo y aplican
  límites locales;
- únicamente ciertas operaciones de cuestionario, después de confirmación explícita, pueden usar
  formularios HTML.

El `sesskey` no se persiste ni se devuelve. Por exigencia del protocolo AJAX puede aparecer en la
URL que ve la infraestructura de Moodle. La cookie equivale a una credencial mientras esté vigente:
no la copies, registres, publiques ni sincronices. Cuando caduque, repite `mcp-usc login`.

### Matriz de compatibilidad

| Capacidad | Token REST | Sesión HTTP |
| --- | --- | --- |
| Cursos, Timeline y calendario | API REST | AJAX; sin fallback a páginas que registren vistas |
| Conversaciones y mensajes | REST | AJAX |
| Foros y discusiones | REST | AJAX cuando existe; sin fallback HTML |
| Posts de una discusión | REST con confirmación | AJAX con confirmación, si la función existe |
| Publicar discusión/respuesta de foro | REST | No disponible de forma segura por AJAX |
| Crear/borrar eventos personales | REST | No disponible de forma segura por AJAX |
| Enviar/retirar respuesta Choice | REST | No disponible de forma segura por AJAX |
| Materiales y recursos | REST | AJAX y descarga `/pluginfile.php` directa; nunca `view.php` |
| Lectura y modificación de tareas | REST | No disponible de forma segura |
| Archivos de entregas | REST + `/webservice/upload.php` multipart | No se manipula el `filemanager` JavaScript |
| Cuestionarios | REST | AJAX para lecturas puras; formulario solo tras confirmar acciones |

El gestor `filemanager` de Moodle crea borradores mediante JavaScript y no equivale a un campo
multipart estándar. Si una entrega solo ofrece ese gestor, reemplazar o borrar sus archivos requiere
un token REST autorizado; las herramientas públicas de archivos en modo sesión se detienen sin
modificar nada. No se usa Playwright para emular el gestor de archivos.

## Archivos locales autorizados

Las herramientas de subida están desactivadas hasta configurar una carpeta allowlist:

```powershell
$env:USC_UPLOAD_ROOT = "C:\Users\TU_USUARIO\Documents\mcp-usc-uploads"
$env:USC_MAX_UPLOAD_BYTES = "52428800"
```

`USC_UPLOAD_ROOT` debe existir. Solo se aceptan archivos regulares resueltos dentro de esa carpeta;
no se siguen rutas que escapen de ella y no se admite el mismo archivo dos veces. La vista previa
muestra ruta relativa, nombre, tamaño y SHA-256 antes de emitir un token.

Límites locales de subida:

- máximo 20 archivos por operación;
- `USC_MAX_UPLOAD_BYTES` se aplica tanto a cada archivo como al total;
- valor predeterminado: 50 MiB (`52428800` bytes);
- rango configurable: de 1 byte a 100 MiB;
- el texto online tiene un límite adicional de 1 MiB.

`replace_submission_files` reemplaza el conjunto completo de archivos de la entrega; no añade uno
silenciosamente a los existentes. Antes de emitir la confirmación comprueba que el servicio permite
subidas y que la entrega solo tiene activo el complemento `file`. De igual modo, el guardado de
texto REST solo se habilita cuando `onlinetext` es el único complemento activo. Moodle procesa todos
los complementos en `mod_assign_save_submission`, por lo que una combinación desconocida se rechaza
antes de crear un borrador o modificar la entrega.

## Fuentes públicas de exámenes

Cada centro USC publica sus propios calendarios. Configura páginas o PDF canónicos separados por
punto y coma:

```powershell
$env:USC_EXAM_SOURCES = "https://www.usc.gal/gl/centro/MI_CENTRO/horarios/cursos;https://assets.usc.gal/ruta/calendario.pdf"
```

La búsqueda usa HTTP directo, acepta únicamente HTTPS bajo `usc.gal`/`usc.es`, sigue como máximo
cinco redirecciones y descarga como máximo 15 MB por documento. No hace crawling masivo: consulta
las fuentes indicadas y sus enlaces inmediatos de examen/PDF. Cada evidencia conserva URL, página
PDF cuando procede y hora de consulta; las fuentes discrepantes se muestran como conflicto.

## Conectar con Codex

Desde PowerShell en este equipo:

```powershell
codex mcp add usc-campus -- uv --directory C:\Users\pablo\mcp-usc run mcp-usc serve
codex mcp list
```

Para incluir fuentes públicas desde la configuración MCP:

```powershell
codex mcp remove usc-campus
codex mcp add usc-campus --env USC_EXAM_SOURCES="https://www.usc.gal/gl/centro/MI_CENTRO/horarios/cursos" -- uv --directory C:\Users\pablo\mcp-usc run mcp-usc serve
```

Reinicia el cliente o abre una sesión nueva para cargar el servidor. Según la
[documentación oficial de OpenAI](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), la
configuración MCP se comparte entre la app de ChatGPT, Codex CLI y la extensión IDE del mismo host.

Activa además la aprobación del host para toda escritura en `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.usc-campus]
command = "uv"
args = ["--directory", 'C:\Users\pablo\mcp-usc', "run", "mcp-usc", "serve"]
default_tools_approval_mode = "writes"
```

Las anotaciones MCP, la previsualización, el token y la aprobación del host son capas
complementarias; ninguna sustituye una decisión humana sobre los parámetros exactos.

## Herramientas MCP

La versión 0.3.0 expone 75 herramientas: 39 lecturas, 18 previsualizaciones y 18 operaciones con
efecto. El [estudio completo de capacidades](docs/student-capability-study.md) explica el inventario,
las fronteras de seguridad y las diferencias entre Moodle 4.5 y 5.2.

| Grupo | Lectura | Previsualización | Escritura |
| --- | --- | --- | --- |
| Catálogo del alumno | `list_student_capabilities`, `call_student_read`, perfil, preferencias, participantes, grupos, notas, progreso, notificaciones, insignias y archivos privados | `preview_student_action` | `execute_student_action` |
| Campus y agenda | `auth_status`, `list_courses`, `list_pending_work`, `list_upcoming_events`, `get_work_item`, `list_announcements`, `list_calendar_events` | crear o borrar un evento personal | crear o borrar un evento personal |
| Mensajes y foros | `list_messages`, `list_conversation_messages`, `list_forums`, `list_forum_discussions`, `search_message_contacts`; `list_discussion_posts` se conserva pero falla cerrado | mensaje, inspección de posts, nueva discusión o respuesta | enviar mensaje, inspeccionar posts, crear discusión o responder |
| Choice | funciones de lectura del catálogo | enviar o retirar respuesta | enviar o retirar respuesta propia |
| Materiales y exámenes | `list_course_contents`, `list_course_resources`, `read_course_resource`, `list_exam_sources`, `search_exam_dates` | — | — |
| Tareas | `list_assignments`, `get_submission_status`, `check_submission_reopen` | `preview_save_online_submission`, `preview_replace_submission_files`, `preview_delete_submission_files`, `preview_submit_assignment`, `preview_remove_submission` | `save_online_submission`, `replace_submission_files`, `delete_submission_files`, `submit_assignment`, `remove_submission` |
| Cuestionarios | `list_quizzes`, `list_quiz_attempts`, revisión final y mejor nota | inspeccionar intento activo, iniciar, guardar o finalizar | inspeccionar intento activo, iniciar, guardar o finalizar |

`call_student_read` solo acepta las 192 funciones incluidas expresamente en la lista blanca; no es
un proxy Moodle arbitrario. Con token REST, `list_student_capabilities(available_only=true)` permite
ver cuáles anuncia el servicio configurado. Con sesión AJAX la disponibilidad completa no siempre
es descubrible y cada llamada falla cerrada si Moodle no expone la función.

Las doce acciones genéricas se limitan a preferencias propias, favoritos privados, silenciar o
marcar conversaciones/notificaciones, conservar un borrador no enviado y marcar una pregunta. Las
acciones contextuales nuevas resuelven por HTTP propietario, curso, foro, grupo, audiencia, fase y
opciones antes de emitir confirmación:

- crear o borrar eventos personales del calendario;
- iniciar una discusión o responder públicamente en un foro, sin adjuntos ni respuesta privada;
- enviar o retirar las respuestas propias de una actividad Choice.

Estas seis acciones contextuales requieren que un token REST legítimo las anuncie. Moodle 4.5–5.2
no marca normalmente sus funciones como AJAX; el modo cookie se detiene antes de previsualizar y no
intenta emularlas con navegador.

El catálogo también identifica acciones estudiantiles que todavía no tienen ejecutor seguro. Se
publican como `generic_execution_supported=false`: aparecer en el inventario no permite
ejecutarlas ni implica que la USC tenga activo el módulo o plugin correspondiente.

### Mensajes, foros y materiales

- `list_messages` lee mensajes recibidos o enviados sin marcarlos. `list_conversations` se conserva
  solo para compatibilidad y falla de forma cerrada: ciertas versiones de Moodle pueden crear y
  marcar como favorita una conversación consigo mismo al ejecutar esa supuesta lectura.
- Los foros incluyen todos los visibles, no solo novedades. Moodle puede marcar posts como leídos
  al ejecutar `mod_forum_get_discussion_posts`; por eso `list_discussion_posts` falla cerrado y el
  par `preview_inspect_discussion_posts` / `inspect_discussion_posts` exige confirmación antes de
  recorrer posts y metadatos de adjuntos.
- `search_message_contacts` crea una referencia temporal al destinatario. `preview_message` exige
  una búsqueda reciente, muestra nombre, ID y texto, y nunca envía.
- `list_course_contents` lista secciones, actividades, páginas, enlaces y archivos.
- `list_course_resources` devuelve referencias opacas de diez minutos. Solo una referencia reciente
  puede usarse con `read_course_resource`.
- `read_course_resource` admite PDF, texto/HTML y OOXML (`.docx`, `.pptx`, `.xlsx`). De forma
  predeterminada limita la descarga a 25 MiB, el texto a 100 000 caracteres y los PDF a 100 páginas;
  los máximos aceptados por llamada son 50 MiB, 500 000 caracteres y 300 páginas.
- En modo sesión, contenidos y anuncios exigen una función AJAX pura y los recursos deben apuntar
  directamente a `/pluginfile.php`; abrir `course/view.php`, `mod/*/view.php` o páginas de foro se
  rechaza porque puede registrar visitas, marcar lecturas o cambiar la finalización.

### Tareas y entregas

- Con un token REST que anuncie las funciones necesarias se pueden listar tareas y consultar
  borrador, archivos, texto online, feedback y permisos.
- Las páginas HTML de tareas registran vistas y pueden cambiar la finalización; por ello todas las
  lecturas, previsualizaciones y escrituras de tareas fallan antes de abrirlas en modo sesión.
- Guardar texto, reemplazar/borrar archivos, enviar para calificación o eliminar la entrega completa
  son escrituras distintas, cada una con su propia vista previa.
- `submit_assignment` puede cerrar la edición del borrador y debe respetar la declaración de entrega
  que muestre Moodle.
- `remove_submission` usa `mod_assign_remove_submission`, disponible en Moodle 4.5 o posterior. Es
  destructivo y no equivale a «reabrir».
- `check_submission_reopen` nunca cambia el estado. Si la entrega ya es editable lo informa; si está
  cerrada, la API estándar reserva la reapertura al profesorado. El conector no intenta eludir esa
  restricción: hay que solicitar la reapertura al docente por los canales normales.

### Cuestionarios

- Se pueden listar cuestionarios e intentos propios y leer la revisión permitida de un intento ya
  finalizado.
- Abrir los datos o el resumen de un intento activo puede hacer que Moodle procese un vencimiento y
  cambie su estado. Por ello `get_quiz_attempt_page` y `get_quiz_attempt_summary` fallan cerrados;
  `preview_inspect_quiz_attempt` muestra el riesgo y `inspect_quiz_attempt` exige confirmación.
- En modo sesión, las listas puras requieren AJAX. Los formularios solo se abren en la segunda
  llamada confirmada para inspeccionar un intento potencialmente stateful, iniciarlo, guardar o
  finalizar; la previsualización no abre `mod/quiz/view.php`.
- `start_quiz` puede activar inmediatamente un temporizador.
- `save_quiz_answers` modifica un intento abierto pero no lo finaliza.
- `finish_quiz` normalmente es irreversible.
- Las preguntas y nombres de campos proceden de Moodle, se tratan como datos no confiables y el
  conector nunca infiere si una respuesta es correcta.
- Cada operación de escritura exige una vista previa independiente; una aprobación anterior no
  autoriza el siguiente paso del intento.

## Confirmaciones y escrituras

Toda escritura sigue dos llamadas:

1. `preview_*` valida el estado y devuelve los parámetros visibles más un `confirmation_token`.
2. La herramienta de escritura consume ese token únicamente si acción y parámetros coinciden
   exactamente.

Los tokens viven solo en memoria, caducan a los cinco minutos y son de un solo uso. Cambiar texto,
destinatario, archivos, respuestas, intento o cualquier otra entrada invalida la confirmación. La
aprobación `writes` del host debe seguir activa para que la segunda llamada requiera intervención
humana.

Cada referencia de contacto y token de confirmación también queda ligado al `user_id` Moodle que lo
creó. Si cambia la cuenta o sesión entre la vista previa y la escritura, la operación se rechaza.
Una respuesta válida a un formulario HTML solo confirma que la petición se envió: se devuelve
`outcome="unknown"` cuando Moodle no ofrece una postcondición inequívoca, y nunca se reintenta por un
segundo transporte ante una respuesta ambigua.

Un timeout o corte de conexión durante una escritura es ambiguo: Moodle puede haber aplicado la
operación aunque el cliente no recibiese la respuesta. No repitas automáticamente un mensaje,
entrega, guardado o finalización. Vuelve a leer la conversación, el estado de entrega o el intento y
decide con esa evidencia; en un cuestionario temporizado comprueba también el reloj directamente en
Moodle.

## Pruebas

```powershell
uv run pytest
uv run ruff check .
```

La suite sustituye HTTP, keyring, formularios, subidas y descargas por dobles de prueba. No contiene
tokens, cookies ni datos reales y no ejecuta ninguna escritura contra la USC. El acceso real se
valida solo de forma manual y local.

## Fuentes oficiales

El contrato se contrastó con documentación y código oficial:

- [External Services de Moodle](https://moodledev.io/docs/4.5/apis/subsystems/external) y sus
  [recomendaciones de seguridad](https://moodledev.io/docs/4.5/apis/subsystems/external/security).
- Definiciones Moodle 4.5 de
  [mensajería](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/message/externallib.php),
  [foros](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/forum/externallib.php),
  [contenidos](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/course/externallib.php),
  [tareas](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/assign/externallib.php) y
  [cuestionarios](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/quiz/classes/external.php)
  del repositorio oficial GPL-3.0.
- [`moodlehq/moodleapp`](https://github.com/moodlehq/moodleapp) (Apache-2.0), referencia oficial de
  uso de servicios, contenidos y recursos desde un cliente.

## Trabajo previo revisado

Se estudiaron proyectos con licencia para evitar repetir patrones ya resueltos. Se reutilizaron
ideas de arquitectura y contratos públicos, no credenciales ni código incompatible:

- [`haolamnm/moodle-mcp-srv`](https://github.com/haolamnm/moodle-mcp-srv) (Apache-2.0): arquitectura,
  diagnóstico y cliente REST.
- [`Snaw80/moodle-mcp`](https://github.com/Snaw80/moodle-mcp) (MIT): login SSO y flujo móvil. El
  endpoint móvil público de la USC devuelve 404, por lo que se usa una cookie obtenida localmente.
- [`GhaithAlHallak8/moodler-mcp`](https://github.com/GhaithAlHallak8/moodler-mcp) (MIT): sesión Moodle
  y AJAX *same-origin*.
- [`1alexandrer/moodle-mcp`](https://github.com/1alexandrer/moodle-mcp) (MIT): herramientas orientadas
  al alumnado y eventos accionables.
- [`mrcinv/moodle_api.py`](https://github.com/mrcinv/moodle_api.py) (MIT): cliente genérico y
  `core_course_get_contents`.
- [`lmscloud-io/moodle-mcp-server`](https://github.com/lmscloud-io/moodle-mcp-server) (GPL-3.0):
  exposición MCP de funciones Moodle con mínimo privilegio.

`loyaniu/moodle-mcp` se usó solo para comparar alcance porque el repositorio no declara licencia; no
se copió código.

## Límites conocidos

- La disponibilidad de cada Web Service depende de la versión, configuración y permisos que la USC
  asigne al token o sesión.
- La sesión OIDC y `MoodleSession` caducan; hay que ejecutar de nuevo `mcp-usc login`.
- AJAX y los formularios de cuestionario pueden cambiar entre versiones. El conector falla de forma
  cerrada si no reconoce con seguridad una operación.
- Las tareas exigen REST: sus páginas registran vistas y el `filemanager` JavaScript no equivale a
  un campo multipart nativo.
- Eliminar una entrega completa exige Moodle 4.5+ y permisos vigentes. Reabrir una entrega cerrada
  corresponde al profesorado.
- No todo el profesorado usa el Campus Virtual; correo o Teams pueden contener información que este
  servidor no consulta.
- Una fecha de Moodle puede ser evaluación continua y una fecha pública, examen oficial. Se
  conservan como fuentes distintas.
