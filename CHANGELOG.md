# Changelog

Los cambios relevantes de `mcp-usc` se documentan aquí. El proyecto sigue
[Versionado Semántico](https://semver.org/lang/es/) durante la fase beta: una versión menor puede
ampliar el contrato y cualquier cambio incompatible debe explicarse expresamente.

## [Sin publicar]

### Horarios y exámenes más rápidos entre procesos

- dónde vive cada código G en los planes de estudio (7 días) y el descubrimiento de horarios
  (12 h) se guardan en disco entre procesos, en ficheros privados;
- las variantes de plan homónimas se resuelven en paralelo y no se vuelve a parsear un plan ya
  validado;
- en procesos nuevos, `horario` baja de ~60 s a ~6 s y `examenes` de ~30 s a ~2 s;
- `USC_CACHE_DIR` cambia la carpeta de estas cachés, y `USC_LOCATION_CACHE=0` y
  `USC_TIMETABLE_DISCOVERY_CACHE=0` las desactivan.

### El Campus completo, sin cortes

- el Timeline se pide por páginas (Moodle da 50 eventos por llamada): `list_pending_work` y
  `list_upcoming_events` aceptan hasta 200, y con un filtro de cursos ya no se pierden eventos;
- las etiquetas de un curso traen su texto («Profesorado: …») en vez del nombre recortado que
  genera Moodle: se piden todas en una sola llamada AJAX con `core_course_get_module`, que no
  abre la página del curso ni registra visitas;
- la sesión HTTP puede agrupar varias llamadas AJAX de solo lectura en una petición
  (`invoke_many`), porque Moodle atiende una petición por sesión cada vez.

### Entrar sin volver a teclear la contraseña

- la ventana de `mcp-usc login` recuerda tu cuenta de Microsoft entre logins, como cualquier
  navegador (perfil propio en `USC_BROWSER_PROFILE`, carpeta 0700); la cookie del Campus sigue
  yendo solo al llavero del sistema;
- va directa a «Acceder con Microsoft» (`/auth/oidc/`), sin pasar por la página de acceso;
- nueva `renew_session_silently`: renueva la sesión sin ventana cuando Microsoft aún te recuerda,
  y se rinde en segundos (sin guardar nada) si te pide algo, para que lo resuelva la ventana.

### Entregas en el Campus de la USC (Moodle 4)

- el gestor de archivos sin JavaScript se encuentra también cuando Moodle lo enlaza desde
  `<noscript><object data>`, que es como lo hace cv.usc.es; antes `replace_submission_files` no
  podía subir nada allí;
- antes de borrar los archivos del borrador se comprueban `maxfiles` y `maxbytes` de la tarea, así
  que una subida rechazada ya no deja la entrega vacía; `inspect_submission_status` informa de
  esos límites;
- la inspección confirmada de una entrega devuelve también el «Estado da entrega» de la página de
  la tarea (enviada o borrador, tiempo restante) y si falta «Enviar para cualificar»;
- `remove_submission` sigue la página de confirmación de Moodle 4 («Confirma que quere retirar a
  súa entrega?»), que antes no encontraba;
- probado de verdad en una tarea del curso 2025/2026: entregar un archivo, comprobarlo y retirarlo.

### Para cualquier grado

- `get_official_exam_dates` y `get_my_official_exam_schedule` funcionan para cualquier grado de la
  USC: localizan cada código G en los planes oficiales (primero en los grados del perfil, luego en
  todo el catálogo) y leen las fechas de la ficha de la materia. El cruce curado del doble grado
  queda como opción con `degree_keys`; `degree_urls` acota la búsqueda;
- nueva `get_my_subjects_timetable`: horario semanal solo de las asignaturas del alumno, aunque sean
  de varios cursos o grados (SICUE, dobles titulaciones, optativas), desde Moodle, el perfil o
  `subject_codes`, filtrado por identificador exacto de materia;
- los planes en extinción que el catálogo ya no anuncia (p. ej. `grao-medicina` junto a
  `grao-medicina-2a-edicion`) se descubren junto a su grado y solo si publican plan para el curso;
- el plan de estudios incluye curso y semestre de cada materia (65 de 65 grados verificados);
- el perfil solo exige el grado: el curso es opcional y admite `extra_degree_urls` y
  `subject_codes`; `mcp-usc setup` ya no obliga a elegir curso;
- los horarios aceptan tutorías, tipos lectivos nuevos o sin indicar, filtros de TFG y prácticas
  y semestres sin semanas publicadas (de 15 a 39 de 39 consultas correctas en una muestra de 20
  grados);
- planes y catálogo se guardan seis horas en caché y la ubicación de cada código también, así que
  las consultas repetidas bajan a segundos.

### Añadido

- comandos de terminal `pendientes`, `horario`, `examenes`, `cursos`, `materiales` y `descargar`,
  con `--json`; las asignaturas se eligen por número, código, nombre o siglas (`ia`, `soi`);
- descarga de los archivos y carpetas de una asignatura (`mcp-usc descargar` y, con vista previa y
  confirmación, `preview_download_course_files`/`download_course_files`), incremental y por
  secciones. Con la sesión de alumno abre cada material como el navegador, lo que Moodle registra;
  con token usa la URL directa;
- instalación en un paso con `uv tool install` y asistente `mcp-usc setup`: login con el Chrome o
  Edge instalado, perfil académico guardado en la carpeta de configuración del usuario y registro
  en Claude Desktop, Claude Code o Codex con confirmación;
- `mcp-usc client-config`, que muestra la configuración exacta para cada cliente;
- modo HTTP independiente del proveedor: `mcp-usc serve --transport http`, factoría ASGI
  `mcp_usc.http_app:create_app`, `Dockerfile` probado en CI y adaptador de Vercel;
- `MCP_AUTH_TOKEN_FILE`, y escrituras por HTTP opcionales con `MCP_USC_ALLOW_WRITES` en un único
  proceso persistente;
- referencias de recursos firmadas (`MCP_USC_STATE_SECRET`) que no dependen de la memoria del
  proceso;
- fechas de examen desde la ficha oficial de cada materia cuando el calendario del centro no la
  incluye (`evidence.source = "subject_sheet"`);
- login sin Chrome ni Edge: prueba los motores de Playwright ya descargados y ofrece descargar uno
  solo para el login (WebKit en macOS, Chromium en el resto), o `mcp-usc login --install-browser`.

### Cambiado

- el modo HTTP de solo lectura publica únicamente las 49 lecturas puras en vez de anunciar
  herramientas que fallaban al llamarlas;
- conexiones HTTP reutilizadas entre llamadas e identidad de Moodle cacheada cinco minutos por
  credencial; `auth_status` sigue comprobándola siempre;
- «mi horario» muestra por defecto la semana actual (hora de Madrid) en lugar de la primera del
  semestre;
- el curso académico y el semestre se infieren con la fecha de Madrid, no la del servidor;
- User-Agent único con la versión real del paquete;
- la caché pública ya funciona contra usc.gal: su `no-cache` por defecto de Drupal no anula la TTL
  local (solo `no-store` impide guardar) y `Vary` solo bloquea cabeceras que dependen del llamante;
  las consultas repetidas bajan de 0,3-20 s a 25-500 ms sin peticiones;
- respuestas más pequeñas: el resumen de caché solo detalla recursos degradados y las fechas de
  examen ya no se repiten en `candidates` y `evidence` (−53 % en una consulta de 7 materias).
- `list_pending_work` y `list_upcoming_events` solo traen atrasadas del curso académico actual,
  aplican el filtro de curso antes del límite y recortan el enunciado a 400 caracteres (el
  completo sigue en `get_work_item`): en una cuenta real, de ~17.000 a ~7.600 tokens;
- `auth_status` comprueba siempre la sesión, pero la identidad interna usa la caché;
- el login asistido no consulta el Campus mientras la ventana está en Microsoft.

### Corregido

- con `uv tool install` (sin lockfile) llegaba soupsieve 4, que no busca dentro de
  `<template>`: fallaban todos los horarios, planes y calendarios. Se fija `soupsieve<3` y el CI
  prueba también sin lockfile;
- `list_notifications` fallaba siempre con `invalidparameter`: Moodle no admite `status`, ahora se
  filtra en local;
- horarios de la ETSE y de cualquier curso con materias anuales: el filtro «Anual» hacía fallar
  todo el horario como «semestres ambiguos»;
- la tabla de exámenes de las fichas de materia no se leía porque está dentro de un `<template>`;
- fechas de clases erróneas cuando la semana publicada no empieza en lunes;
- entrega en modo sesión imposible cuando la tarea no exige declaración de autoría;
- archivos de entrega por REST releídos tras la confirmación: ahora se suben exactamente los bytes
  confirmados;
- nombres de archivo duplicados o no válidos detectados en la vista previa y no tras consumir el
  token;
- `force_new` y los parámetros no admitidos de `finish_quiz` en modo sesión se rechazan antes de
  emitir el token;
- respuestas a debates «para todos los participantes» en foros con grupos cuando Moodle las
  permite;
- `extract_academic_year` ignoraba un curso válido tras una fecha anterior;
- la caché pública se desactivaba con cualquier cabecera `Vary`;
- el rastreador de fuentes de exámenes podía seguir enlaces y redirecciones a otros hosts USC.

### Seguridad

- `api/index.py` ya no lleva un hash de token fijo: el verificador se configura por entorno y un
  token corto o inválido impide arrancar;
- el cliente HTTP compartido nunca guarda cookies de respuesta, así que una credencial no puede
  filtrarse a la petición de otra.

## [0.11.0] - 2026-08-25

### Añadido

- descubrimiento de páginas oficiales de horario desde una titulación y sus centros;
- consulta semanal de clases por curso académico, semestre, fecha, grupos y materia;
- resolución por titulación seleccionada y agregación automática de sus centros;
- selección explícita de `program_id` cuando la USC conserva planes homónimos;
- sesiones estructuradas con fecha, horas, tipo lectivo, grupo, aula, materia y fuentes USC;
- conservación de franjas que la fuente marca con varios tipos lectivos;
- representación explícita `no_data` para centros que no publican horario en el curso solicitado.
- listados básicos de cuestionarios, contenidos, recursos y foros desde el estado AJAX puro de los
  cursos cuando la sesión no dispone de sus funciones REST;
- perfil académico local validado y `get_my_class_timetable` para consultar horario sin repetir
  titulación, curso, plan y grupos;
- previews y acciones contextuales para finalización manual de actividades y auto-finalización de
  cursos cuando Moodle publica el criterio propio correspondiente;
- diagnósticos seguros y accionables para sesiones ausentes, inválidas o caducadas;
- fixtures del contrato común de sesión para Moodle 4.5, 5.0 y 5.2.

### Seguridad

- todas las consultas de horarios son GET públicos anónimos, sin cookies ni Playwright;
- rutas de titulación, centro, horario, AJAX y fichas quedan en allowlists separadas;
- los enlaces encadenados deben conservar plan, curso, controlador, semestre y centro;
- planes homónimos y titulaciones multicentro permanecen separados y nunca se fusionan por nombre.
- las nuevas acciones de finalización exigen estado propio, contexto exacto, preview, confirmación
  de un solo uso y una única mutación sin reintentos.
- el proceso MCP silencia el logging informativo de `httpx`/`httpcore` para impedir que valores
  `sesskey` de AJAX o cabeceras autenticadas aparezcan en stderr.

### Corregido

- el Timeline respeta el máximo real de 50 elementos de Moodle;
- la fecha formateada de un evento ya no se confunde con el entero `timeusermidnight`.
- una página de login devuelta con HTTP 200 se reconoce también después de lecturas AJAX acotadas.

## [0.9.0] - 2026-08-24

### Añadido

- cuatro recursos MCP locales para descripción, seguridad, compatibilidad y workflows;
- cuatro prompts controlados por el usuario para resumen, exámenes, revisión de tareas y preparación
  segura de entregas;
- comando `mcp-usc manifest` con JSON Schema, anotaciones y digest SHA-256 determinista;
- salida compacta para `mcp-usc doctor`;
- CI en Linux, Windows y macOS para Python 3.11 y 3.13;
- release automática con validación de versión/tag, instalación limpia del wheel y `SHA256SUMS`;
- documentación de superficie MCP, compatibilidad y proceso de publicación.

### Cambiado

- versión mínima del SDK fijada en `mcp>=1.29,<2`;
- descripción autoverificable ampliada con recursos y prompts;
- roadmap actualizado con los requisitos restantes para 1.0.

### Seguridad

- recursos, prompts y manifiesto son locales y no instancian clientes HTTP;
- el prompt de preparación de entregas se detiene obligatoriamente tras el preview;
- el workflow de release no recibe secretos del Campus y limita permisos a contenidos del repo.

## [0.8.0] - 2026-08-24

### Añadido

- herramienta de lectura local `describe_mcp_usc`, con propósito, límites, transportes, seguridad e
  inventario completo agrupado;
- comando `mcp-usc doctor`, que diagnostica la configuración local sin contactar con el Campus ni
  mostrar secretos;
- guía de primeros pasos, referencia de herramientas, arquitectura, roadmap y notas de release;
- archivos comunitarios para contribución, seguridad, conducta, soporte, incidencias y pull requests;
- CI reproducible con dependencias bloqueadas, lint, tests y build;
- metadatos del paquete y del repositorio orientados a descubrimiento comunitario.

### Cambiado

- README reorganizado alrededor de los casos de uso, la audiencia y los límites del proyecto;
- inventario MCP ampliado de 83 a 84 herramientas (46 lecturas puras).

### Seguridad

- el diagnóstico solo devuelve presencia/estado de credenciales y declara explícitamente que no ha
  realizado contacto de red;
- el inventario autodescriptivo se contrasta en tests con las herramientas STDIO reales para evitar
  documentación desactualizada.

## [0.7.0] - 2026-08-24

- catálogo público completo de grados y planes USC por HTTP;
- localización segura de códigos de materia y caché pública con revalidación;
- revisión de seguridad diferencial y auditoría opt-in sobre la demo oficial de Moodle.

## [0.6.0] - 2026-08-24

- ampliación del catálogo de capacidades de alumno;
- acciones contextuales para calendario, foros y Choice con confirmación;
- cobertura de tareas, cuestionarios y transportes REST/sesión.

[0.11.0]: https://github.com/PabloPC05/mcp-usc/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/PabloPC05/mcp-usc/releases/tag/v0.9.0
[0.8.0]: https://github.com/PabloPC05/mcp-usc/releases/tag/v0.8.0
[0.7.0]: https://github.com/PabloPC05/mcp-usc/commit/d9320ad875a058e5ff30e76514ed37f6667cc241
[0.6.0]: https://github.com/PabloPC05/mcp-usc/commit/31ed4cd
