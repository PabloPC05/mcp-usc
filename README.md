# mcp-usc

[![CI](https://github.com/PabloPC05/mcp-usc/actions/workflows/ci.yml/badge.svg)](https://github.com/PabloPC05/mcp-usc/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Tu Campus Virtual de la USC, accesible desde un asistente compatible con MCP.

`mcp-usc` es un servidor local y HTTP-first para alumnado de la Universidade de Santiago de
Compostela. Permite que un asistente consulte el Campus Virtual Moodle y fuentes académicas
públicas de la USC, siempre con los permisos de tu propia cuenta.

> [!IMPORTANT]
> Este es un proyecto comunitario independiente. No está desarrollado, respaldado ni operado por
> la USC. Tus credenciales se guardan localmente y nunca deben publicarse en GitHub.

## Qué puedes pedirle

| Necesidad | Ejemplo de petición |
| --- | --- |
| Organizar el trabajo | «¿Qué tareas tengo pendientes esta semana?» |
| Seguir una asignatura | «Resume los avisos recientes de AED y enséñame sus materiales.» |
| Revisar entregas | «¿Qué archivos entregué y qué feedback recibí?» |
| Preparar exámenes | «¿Cuándo son mis exámenes oficiales del curso 2026/2027?» |
| Consultar horarios | «¿Qué clases tiene segundo del doble grado esta semana y en qué aulas?» |
| Consultar actividad | «Enséñame mis notas, calendario, mensajes y progreso.» |
| Actuar con aprobación | «Prepara esta entrega, pero no la envíes hasta que la confirme.» |

También cubre foros, actividades Choice, cuestionarios, borradores y descargas de recursos. No
consulta correo ni Teams, no eleva privilegios y no actúa como profesorado o administración.

## Estado del proyecto

La versión **0.11.0** es una beta comunitaria: expone **91 herramientas MCP**, cuatro recursos
pasivos y cuatro prompts guiados. Las herramientas se dividen en 49 lecturas puras, 21
previsualizaciones, 20 operaciones con efecto y una inspección potencialmente stateful. El catálogo
interno estudiado cubre 301 capacidades de alumno de Moodle; aparecer en el catálogo no significa
que la USC o el token de cada persona habiliten todas ellas.

Tres puntos de entrada ayudan a entender y comprobar la instalación sin contactar con el Campus:

```powershell
uv run mcp-usc doctor
uv run mcp-usc manifest --compact
```

- `describe_mcp_usc`: explica al cliente MCP el propósito, alcance, límites y modelo de seguridad.
- `mcp-usc doctor`: revisa Python, autenticación configurada, keyring y carpeta de subidas; solo
  muestra presencia/estado, nunca valores secretos.
- `mcp-usc manifest`: exporta esquemas de herramientas, recursos y prompts con un SHA-256
  determinista; tampoco usa la red.

Para validar de verdad un token o una sesión mediante una lectura HTTP usa después
`uv run mcp-usc status`.

## Documentación

- [Primeros pasos](docs/getting-started.md): instalación, autenticación y conexión con un cliente.
- [Inventario de herramientas](docs/tools.md): las 91 herramientas, tipos y requisitos.
- [Superficie MCP](docs/mcp-surface.md): recursos, prompts y manifiesto local.
- [Compatibilidad](docs/compatibility.md): sistemas, Python, hosts y transportes Moodle.
- [Arquitectura](docs/architecture.md): transportes, componentes y fronteras de confianza.
- [Modelo de seguridad](SECURITY.md): credenciales, confirmaciones y reporte responsable.
- [Estudio de capacidades](docs/student-capability-study.md): análisis detallado de Moodle.
- [Hoja de ruta](docs/roadmap.md): alcance de la beta y criterios para llegar a 1.0.
- [Cómo contribuir](CONTRIBUTING.md) y [cambios por versión](CHANGELOG.md).

## Principios de diseño

- El servidor MCP usa STDIO; «HTTP-first» describe la conexión entre este proceso y Moodle/USC.
- Las consultas y escrituras normales no automatizan un navegador.
- Se prefiere la API REST oficial de Moodle cuando hay un token legítimo.
- Con una cookie `MoodleSession`, las lecturas usan AJAX *same-origin* y descargas directas
  `/pluginfile.php`. Los formularios HTML de tareas y cuestionarios solo se abren después de una
  confirmación explícita.
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

Ruta sin Playwright, reutilizando una sesión que ya hayas abierto personalmente en el navegador:

```powershell
uv run mcp-usc import-session
uv run mcp-usc status
```

En las herramientas de desarrollo del navegador, abre **Aplicación/Almacenamiento > Cookies >
https://cv.usc.es**, copia solo el valor de `MoodleSession` y pégalo en el prompt oculto. No lo
incluyas en un comando, captura, `.env` o archivo. El programa lo valida mediante una lectura HTTP de
Preferencias y solo entonces lo guarda en Credential Manager.

La alternativa asistida abre una ventana visible una sola vez y requiere el extra `browser-auth`:

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
- ciertas operaciones de tareas y cuestionarios, después de confirmación explícita, usan
  formularios HTML oficiales descubiertos en una respuesta fresca del servidor.

El `sesskey` no se persiste ni se devuelve. Por exigencia del protocolo AJAX puede aparecer en la
URL que ve la infraestructura de Moodle. La cookie equivale a una credencial mientras esté vigente:
si utilizas la importación manual, cópiala únicamente al prompt oculto; nunca la registres, publiques
ni sincronices. Cuando caduque, repite `mcp-usc import-session` o `mcp-usc login`.
Para borrar únicamente la copia local, sin enviar un cierre de sesión al Campus, ejecuta
`uv run mcp-usc forget-session`.

### Matriz de compatibilidad

| Capacidad | Token REST | Sesión HTTP |
| --- | --- | --- |
| Cursos, Timeline y calendario | API REST | AJAX; incluye cursos ocultos del tablero al solicitar archivados |
| Conversaciones y mensajes | REST | AJAX |
| Foros y discusiones | REST | Lista básica por estado AJAX; discusiones solo con función segura |
| Posts de una discusión | REST con confirmación | AJAX con confirmación, si la función existe |
| Publicar discusión/respuesta de foro | REST | No disponible de forma segura por AJAX |
| Crear/borrar eventos personales | REST | No disponible de forma segura por AJAX |
| Enviar/retirar respuesta Choice | REST | No disponible de forma segura por AJAX |
| Materiales y recursos | REST | Metadatos por estado AJAX; descarga solo con `/pluginfile.php` directo |
| Lectura y modificación de tareas | REST | Listado AJAX puro; estado y cambios mediante formularios confirmados |
| Archivos de entregas | REST + `/webservice/upload.php` multipart | Formularios non-JS de borrador y selector de archivos, tras confirmar |
| Cuestionarios | REST | Lista básica por estado AJAX; formulario solo tras confirmar acciones |

En modo sesión no se ejecuta JavaScript ni se usa Playwright. Para los archivos se sigue el flujo
non-JS que el propio Moodle publica: gestor de borradores, selector de repositorio y formulario
multipart descubiertos desde HTML fresco. Si la instalación o un complemento no expone una variante
reconocida, la operación se detiene sin inventar campos. Un fallo después de modificar el borrador
puede dejarlo parcialmente cambiado; se devuelve `outcome="unknown"` y `do_not_retry=true` para que
no se repita automáticamente.

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

## Fuentes públicas de grados, horarios y exámenes

El MCP integra por HTTP el catálogo completo de grados y los horarios lectivos que sus centros
enlazan, además de los calendarios dinámicos de exámenes compatibles de la ETSE y la Facultade de
Matemáticas. No necesita Playwright, cookies ni `USC_EXAM_SOURCES` para estas ocho herramientas:

- `list_usc_degrees`: lista las titulaciones enlazadas por el catálogo oficial actual;
- `locate_usc_subject_codes`: localiza códigos exactos en los planes oficiales de un curso;
- `list_degree_timetables`: parte de la página oficial de una titulación y encuentra sus páginas de
  horario por centro, plan y curso;
- `get_degree_class_timetable`: recibe la titulación seleccionada, el curso y el año académico;
  resuelve y agrega sus centros y devuelve fechas, horas, materia, tipo de clase, grupo, aula y
  URLs de procedencia;
- `list_official_exam_degrees`: ediciones y crosswalks institucionales admitidos;
- `list_official_exam_subjects`: descubre códigos, nombres y fichas para un curso académico;
- `get_official_exam_dates`: convocatorias estructuradas para códigos y curso académico explícitos;
- `get_my_official_exam_schedule`: cruza esos calendarios con los códigos de los cursos Moodle,
  incluidos los ocultos del tablero.

La versión 0.10 conserva la resolución específica del doble grado y la búsqueda independiente
en todos los grados actuales. En la comprobación real de 2026/2027 se procesaron las 65 entradas del
catálogo, incluidas materias repetidas por itinerarios y códigos oficiales con sufijo como
`G3131324B`. Las repeticiones solo se fusionan si código y título coinciden exactamente, y se
conservan todas sus fichas. Un título contradictorio, una respuesta parcial o un cambio de HTML se
devuelve como incidencia; nunca como un falso `not_found`.

El barrido global puede superar un minuto porque consulta todos los planes y la USC exige
revalidación. Para una consulta rápida, `locate_usc_subject_codes` acepta `area_slugs` o
`degree_urls`; ambos filtros se verifican contra el catálogo recién obtenido. Localizar una materia
en cualquier grado no implica que exista todavía un calendario estructurado compatible para su
centro. Solo permanece curado el crosswalk institucional entre las dos ediciones del doble grado y
sus identificadores de calendario, porque la USC no publica una clave externa directa entre ambos
sistemas.

El código se obtiene de la lista oficial y queda ligado a su título y ficha adyacentes. Después se
busca ese título únicamente dentro del plan de calendario correspondiente. Si un código aparece en
ambas ediciones, se unifica solo cuando todas las convocatorias coinciden; si difieren, el estado es
`ambiguous`. Por ello `G1012106` se resuelve al plan actual `19955` y nunca toma las fechas del plan
antiguo homónimo. El curso académico es obligatorio con formato `2025/2026`. Cada resultado conserva
URL, endpoint, convocatoria, oportunidad, fecha, hora, aulas, grupos y evidencia por plan y centro.

Los horarios lectivos se consultan por `degree_url`, `course_number`, curso académico, semestre y una
fecha contenida en la semana. `get_degree_class_timetable` descubre y consulta automáticamente todos
los centros de la titulación, pero conserva la procedencia de cada sesión. Si la USC publica varios
planes homónimos que no se pueden distinguir desde la titulación, devuelve
`status="program_selection_required"`; al repetir la consulta con uno de sus `program_id` no mezcla
planes. También acepta `group_codes` y `subject_query`. Un centro que no publique datos queda como
fuente `no_data`, sin sustituirlo por otra carrera parecida.

Las respuestas públicas incluyen un resumen `cache` con frescura, aciertos y degradación. La caché
local es LRU, acotada, se comparte durante la vida del proceso MCP únicamente entre GET públicos
anónimos, valida el esquema antes de guardar y admite ETag/Last-Modified. Nunca se comparte con el
Campus autenticado. Puede
configurarse con `USC_PUBLIC_CACHE_TTL_SECONDS`,
`USC_PUBLIC_CACHE_STALE_IF_ERROR_SECONDS`, `USC_PUBLIC_CACHE_MAX_ENTRIES` y
`USC_PUBLIC_CACHE_MAX_BYTES`. Actualmente las páginas dinámicas de la USC responden
`no-cache, must-revalidate` y no publican validadores; el conector respeta esa política, muestra TTL
cero y vuelve a consultar la red en lugar de servir datos potencialmente obsoletos.

Además, cada centro USC puede publicar otras páginas o PDF. Para el buscador genérico
`search_exam_dates`, configura fuentes adicionales separadas por punto y coma:

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

La versión 0.11.0 expone 91 herramientas: 49 lecturas puras, 21 previsualizaciones, 20 operaciones con
efecto y una inspección potencialmente *stateful* que también exige confirmación. El
[inventario completo](docs/tools.md) y el
[estudio de capacidades](docs/student-capability-study.md) explican las fronteras de seguridad y
las diferencias entre Moodle 4.5 y 5.2.

| Grupo | Lectura pura | Confirmación previa | Operación confirmada |
| --- | --- | --- | --- |
| Catálogo del alumno | `list_student_capabilities`, `call_student_read`, perfil, preferencias, participantes, grupos, notas, progreso, notificaciones, insignias y archivos privados | `preview_student_action` | `execute_student_action` |
| Campus y agenda | `auth_status`, `list_courses`, `list_pending_work`, `list_upcoming_events`, `get_work_item`, `list_announcements`, `list_calendar_events` | crear o borrar un evento personal | crear o borrar un evento personal |
| Mensajes y foros | `list_messages`, `list_conversation_messages`, `list_forums`, `list_forum_discussions`, `search_message_contacts`; `list_discussion_posts` se conserva pero falla cerrado | mensaje, inspección de posts, nueva discusión o respuesta | enviar mensaje, inspeccionar posts, crear discusión o responder |
| Choice | funciones de lectura del catálogo | enviar o retirar respuesta | enviar o retirar respuesta propia |
| Materiales, horarios y exámenes | `list_course_contents`, `list_course_resources`, `read_course_resource`, `list_exam_sources`, `search_exam_dates`, `list_usc_degrees`, `list_degree_timetables`, `get_degree_class_timetable`, `locate_usc_subject_codes`, `list_official_exam_degrees`, `list_official_exam_subjects`, `get_official_exam_dates`, `get_my_official_exam_schedule` | — | — |
| Tareas | `list_assignments`, `get_submission_status`, `check_submission_reopen` | `preview_inspect_submission_status`, `preview_save_online_submission`, `preview_replace_submission_files`, `preview_delete_submission_files`, `preview_submit_assignment`, `preview_remove_submission` | `inspect_submission_status`, `save_online_submission`, `replace_submission_files`, `delete_submission_files`, `submit_assignment`, `remove_submission` |
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
- enviar o retirar las respuestas propias de una actividad Choice;
- marcar o desmarcar la finalización manual de una actividad propia;
- marcar el criterio de auto-finalización de un curso cuando Moodle lo publica explícitamente.

Estas ocho acciones contextuales requieren que un token REST legítimo las anuncie. Moodle 4.5–5.2
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
- `list_course_contents` lista secciones, actividades, páginas, enlaces y archivos. Con sesión usa
  el estado AJAX puro y marca `metadata_only=true`: no abre las páginas de los módulos.
- `list_course_resources` devuelve referencias opacas de diez minutos cuando Moodle expone una URL
  `/pluginfile.php`. Si la sesión solo publica módulos, los lista como metadatos no descargables,
  con `resource_token=null`.
- `read_course_resource` admite PDF, texto/HTML y OOXML (`.docx`, `.pptx`, `.xlsx`). De forma
  predeterminada limita la descarga a 25 MiB, el texto a 100 000 caracteres y los PDF a 100 páginas;
  los máximos aceptados por llamada son 50 MiB, 500 000 caracteres y 300 páginas.
- En modo sesión, contenidos, cuestionarios y foros se pueden descubrir mediante
  `core_courseformat_get_state`. Solo se devuelve el CMID y metadatos limitados. Los recursos deben
  apuntar directamente a `/pluginfile.php` para poder descargarse; abrir `course/view.php`,
  `mod/*/view.php` o páginas de foro se rechaza porque puede registrar visitas, marcar lecturas o
  cambiar la finalización.

### Tareas y entregas

- Con un token REST que anuncie las funciones necesarias se pueden listar tareas y consultar
  borrador, archivos, texto online, feedback y permisos.
- En modo sesión, `list_assignments` usa el estado AJAX puro del curso y devuelve
  `course_module_id` (CMID); no inventa un `assignment_id`, que queda como `null`.
- Abrir la página HTML de una tarea puede registrar una vista o cambiar la finalización. Por eso
  `preview_inspect_submission_status` no accede al Campus y `inspect_submission_status` solo abre la
  página después de consumir su confirmación. `get_submission_status` conserva la lectura REST.
- Las previsualizaciones de cambios tampoco abren la tarea. Tras confirmar, la operación obtiene un
  formulario oficial fresco y comprueba acción, `sesskey`, usuario y campos antes de enviarlo.
- Guardar texto, reemplazar/borrar archivos, enviar para calificación o eliminar la entrega completa
  son escrituras distintas, cada una con su propia vista previa.
- Los archivos de sesión usan los formularios non-JS de borrador y repositorio que exponga Moodle.
  Un formulario desconocido falla cerrado; un resultado de red ambiguo nunca se reintenta.
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

También hay una auditoría opt-in separada para la demo oficial de Moodle, que se reinicia cada hora.
El host está fijado a `school.moodledemo.net`; el script no modifica la allowlist USC, no guarda ni
imprime el token y bloquea mensajes, chats y publicaciones de foro. Token y contraseña se aceptan
solo mediante variables de entorno, nunca como argumentos visibles del proceso:

```powershell
$env:MOODLE_DEMO_USERNAME = "usuario público actual de la demo"
$env:MOODLE_DEMO_PASSWORD = "contraseña pública actual de la demo"
uv run python scripts/moodle_demo_audit.py --confirm-demo
```

Por defecto solo ejecuta lecturas y marca las escrituras como `skip`. La opción adicional
`--allow-reversible-write` crea un único evento personal desechable, comprueba por ID, nombre y
propietario que es exactamente el recién creado, lo elimina y vuelve a leerlo para demostrar que no
queda estado externo; no habilita ninguna comunicación. En la validación de v0.7 sobre Moodle 5.2,
la pasada de solo lectura produjo 66 comprobaciones correctas y cero fallos; las omisiones
justificadas pueden variar porque otras personas usan la demo simultáneamente y el sitio se
reinicia cada hora.

## Fuentes oficiales

El contrato se contrastó con documentación y código oficial:

- [Conceptos de servidores MCP](https://modelcontextprotocol.io/docs/learn/server-concepts), incluidos
  tools, resources y prompts, y
  [SDK oficial de Python 1.x](https://py.sdk.modelcontextprotocol.io/v1/).
- [External Services de Moodle](https://moodledev.io/docs/4.5/apis/subsystems/external) y sus
  [recomendaciones de seguridad](https://moodledev.io/docs/4.5/apis/subsystems/external/security).
- Definiciones Moodle 4.5 de
  [mensajería](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/message/externallib.php),
  [foros](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/forum/externallib.php),
  [contenidos](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/course/externallib.php),
  [tareas](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/assign/externallib.php) y
  [cuestionarios](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/quiz/classes/external.php)
  del repositorio oficial GPL-3.0.
- Flujo web oficial de tareas en
  [`mod/assign/view.php`](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/assign/view.php)
  y [`mod/assign/locallib.php`](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/assign/locallib.php),
  listado puro [`core_courseformat_get_state`](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/course/format/classes/external/get_state.php)
  y formularios non-JS de
  [borradores](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/repository/draftfiles_manager.php)
  y [repositorios](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/repository/filepicker.php).
- [`moodlehq/moodleapp`](https://github.com/moodlehq/moodleapp) (Apache-2.0), referencia oficial de
  uso de servicios, contenidos y recursos desde un cliente.
- [Demo oficial de Moodle](https://moodle.org/demo), entorno reseteable usado por la auditoría
  opt-in; otras personas pueden usarlo simultáneamente y sus credenciales públicas cambian.

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
- La sesión OIDC y `MoodleSession` caducan; hay que ejecutar de nuevo `mcp-usc import-session` o
  `mcp-usc login`.
- AJAX y los formularios de tareas o cuestionarios pueden cambiar entre versiones. El conector
  falla de forma cerrada si no reconoce con seguridad una operación.
- Las entregas con complementos personalizados, repositorios no expuestos en modo non-JS o
  formularios distintos de los reconocidos pueden seguir necesitando un token REST autorizado.
- Eliminar una entrega completa exige Moodle 4.5+ y permisos vigentes. Reabrir una entrega cerrada
  corresponde al profesorado.
- No todo el profesorado usa el Campus Virtual; correo o Teams pueden contener información que este
  servidor no consulta.
- Una fecha de Moodle puede ser evaluación continua y una fecha pública, examen oficial. Se
  conservan como fuentes distintas.
- El localizador global conoce titulaciones y materias, pero las fechas oficiales estructuradas
  siguen limitadas a los centros y crosswalks declarados. Los demás calendarios se consultan con
  `search_exam_dates` y fuentes configuradas, sin atribuir fechas por similitud de nombre.
