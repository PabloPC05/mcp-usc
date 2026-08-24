# Estudio de capacidades de una cuenta de estudiante

## Resumen ejecutivo

Este estudio delimita qué puede hacer razonablemente `mcp-usc` con una cuenta de estudiante en
Moodle 4.5–5.2. El objetivo no es exponer toda la administración de Moodle, sino cubrir la vida
académica propia con mínimo privilegio: consultar cursos, agenda, materiales, comunicaciones,
calificaciones y progreso; y realizar únicamente cambios que el estudiante podría hacer en su
propia cuenta y contexto.

La versión 0.2 ya resolvía el núcleo operativo mediante 42 herramientas MCP. La versión 0.8 expone
84 herramientas (46 lecturas puras, 19 previsualizaciones, 18 operaciones con efecto y una
inspección potencialmente *stateful* confirmada), añade una descripción autoverificable del
proyecto, descubrimiento público de titulaciones y materias, lecturas de alto nivel y un catálogo de
301 funciones externas candidatas: 192 lecturas y 109 acciones. Ese catálogo es una lista blanca de
estudio, no una promesa de disponibilidad ni una autorización para ejecutar todas sus acciones. La
[referencia pública](tools.md) enumera el contrato completo.

La regla central es:

- toda lectura genérica debe estar expresamente incluida en la lista blanca, quedar ligada a la
  identidad autenticada y devolver contenido remoto saneado y marcado como no confiable;
- solo un subconjunto pequeño de acciones privadas, reversibles y de alcance inequívoco puede usar
  la pareja genérica `preview_student_action` / `execute_student_action`;
- publicar ante terceros, responder actividades evaluables, iniciar o finalizar intentos y borrar
  datos requiere una herramienta específica con previsualización contextual;
- las operaciones de docente, administración, cumplimiento legal o servicios de terceros quedan
  fuera del alcance.

## Alcance técnico y temporal

El intervalo Moodle 4.5–5.2 comprende la versión 4.5 LTS, publicada el 7 de octubre de 2024, y
Moodle 5.2, publicado el 20 de abril de 2026. La investigación compara la documentación versionada,
las declaraciones `db/services.php` y las implementaciones externas de las ramas oficiales
`MOODLE_405_STABLE` y `MOODLE_502_STABLE`. Moodle documenta que sus servicios externos sustentan
tanto clientes Web Service como interacciones AJAX y la aplicación móvil oficial
([External Services](https://moodledev.io/docs/5.2/apis/subsystems/external),
[Moodle 4.5](https://moodledev.io/docs/4.5) y
[Moodle 5.2](https://moodledev.io/general/releases/5.2)).

El catálogo es deliberadamente una unión conservadora de funciones relevantes encontradas en ese
intervalo. No significa que cada nombre exista en las cuatro versiones 4.5, 5.0, 5.1 y 5.2. Una
función solo es utilizable cuando coinciden estas condiciones:

1. existe en la versión instalada;
2. el componente o plugin está instalado y habilitado;
3. la función pertenece al servicio asociado al token REST o está marcada para AJAX en sesión;
4. la cuenta conserva acceso al contexto y las capacidades requeridas;
5. los parámetros corresponden a la identidad y a recursos visibles de esa cuenta.

Moodle exige que cada función declare y valide parámetros y retornos, compruebe el contexto y las
capacidades del usuario. La documentación de la instancia es, por tanto, la autoridad final, no el
catálogo estático del cliente
([definición de funciones](https://moodledev.io/docs/5.2/apis/subsystems/external/functions),
[seguridad](https://moodledev.io/docs/5.2/apis/subsystems/external/security) y
[configuración de servicios](https://moodledev.io/docs/5.2/apis/subsystems/external/advanced/custom-services)).

## Clasificación usada

La tabla de este documento usa tres marcas:

| Marca | Significado | Tratamiento MCP |
| --- | --- | --- |
| `R` | Lectura sin cambio funcional intencionado. | Puede usar lectura específica o `call_student_read` si está en la lista blanca. |
| `R!` | Operación de apariencia lectora que registra una visita, marca elementos como leídos o altera seguimiento/progreso. | Se trata como escritura: preview, confirmación exacta y aprobación del host. |
| `W` | Mutación explícita de preferencias, contenido, respuesta, intento, matrícula o datos. | Genérica solo si es privada y de alcance inequívoco; en los demás casos necesita herramienta contextual específica. |

`R!` nunca se anuncia como `readOnly`. Moodle puede crear eventos de visualización, cambiar la
finalización automática, reducir contadores de no leídos o disparar otras reglas. Esta distinción
evita que una exploración aparentemente inocua modifique el estado académico.

## Punto de partida: versión 0.2

La versión 0.2 cubría cinco recorridos completos, además de autenticación y consulta de agenda:

| Área v0.2 | Lecturas | Escrituras con preview específico |
| --- | --- | --- |
| Campus y agenda | Cursos, Timeline, pendientes, eventos, detalle de evento y anuncios. | Ninguna. |
| Mensajería y foros | Conversaciones, mensajes, contactos, foros y discusiones. La auditoría v0.3 reclasifica la lectura de posts como `R!`. | Envío de mensaje interno a un contacto resuelto recientemente. |
| Materiales y exámenes | Secciones, actividades, archivos, extracción local de documentos y búsqueda en fuentes públicas oficiales. | Ninguna. |
| Tareas | Tareas, estado propio, texto online, archivos, feedback y posibilidad de reapertura. | Guardar texto, reemplazar o borrar archivos, enviar para calificación y retirar la entrega cuando Moodle lo permita. |
| Cuestionarios | Cuestionarios, intentos, páginas y resumen. | Iniciar intento, guardar respuestas y finalizarlo. |

Estas herramientas siguen siendo preferibles al genérico cuando conocen la semántica de la
actividad. Por ejemplo, la vista previa de una entrega puede comprobar plugins activos, estado de
borrador, declaración de autoría y archivos exactos; la de un cuestionario puede comprobar intento,
página y temporizador; y la de mensajería resuelve un destinatario reciente antes de enviar.

## Nuevas lecturas y descubrimiento

La ampliación incorpora dos capas complementarias.

### Herramientas de lectura de alto nivel

- `get_my_profile`: perfil de la cuenta autenticada, sin aceptar búsquedas arbitrarias de usuarios.
- `get_my_preferences`: preferencias propias, con filtro opcional por nombre.
- `list_course_participants`: participantes que la cuenta ya tiene permiso para ver, con
  paginación limitada.
- `list_my_groups`: grupos propios dentro de un curso.
- `get_my_grades`: resumen global o calificaciones propias de un curso.
- `get_my_completion`: finalización del curso y de sus actividades sin marcar nada como completado.
- `list_notifications`: notificaciones leídas, no leídas o todas, sin cambiar su estado.
- `list_calendar_events`: eventos visibles de un intervalo, más amplio que el Timeline accionable.
- `list_my_badges`: insignias visibles de la cuenta.
- `get_private_files_info`: cuota y metadatos del área privada, sin crear borradores ni descargar.
- `get_quiz_attempt_review` y `get_quiz_best_grade`: revisión permitida de intentos terminados y
  mejor nota propia, respetando las opciones de revisión del cuestionario.

### Descubrimiento y llamada genérica

- `list_student_capabilities` filtra por categoría y tipo de acceso. Con token REST puede contrastar
  la lista con las funciones que anuncia `core_webservice_get_site_info`; con sesión, la
  disponibilidad puede permanecer desconocida hasta intentar una función AJAX permitida.
- `call_student_read` ejecuta exclusivamente funciones `R` incluidas en la lista blanca.
- `preview_student_action` no invoca la función mutadora: valida función, argumentos, identidad y
  disponibilidad, y emite un nonce temporal ligado a todo ello.
- `execute_student_action` consume una sola vez ese nonce para los mismos parámetros y la misma
  cuenta. Un cambio de función, argumentos o `user_id` lo invalida.

Los argumentos genéricos son JSON limitado: no se admiten secretos, números no finitos, más de ocho
niveles, más de mil nodos, cadenas mayores de 100 000 caracteres ni más de 1 MB total. Los resultados
se convierten a texto seguro, redactan secretos y parámetros sensibles de URL, y se limitan a 2 MB.
Esto reduce exposición accidental, pero no convierte el contenido de Moodle en instrucciones
confiables.

## Categorías del catálogo

| Categoría | Qué descubre | Frontera principal |
| --- | --- | --- |
| Cuenta | Perfil propio, preferencias, blog, archivos privados, estado de políticas e insignias. | No editar perfil institucional, asumir otra identidad ni aceptar políticas. |
| Cursos | Cursos, secciones, módulos, participantes, grupos, bloques, favoritos y métodos de matrícula. | No crear cursos, gestionar matrículas ajenas, roles, grupos ni contenido oculto. |
| Calificaciones y finalización | Notas propias, feedback y progreso. | No calificar, reabrir entregas ni completar actividades ajenas. |
| Calendario | Vistas, eventos, tipos permitidos y eventos personales. | Crear, editar o borrar exige resolver propietario, tipo y contexto. |
| Mensajes y notificaciones | Conversaciones, miembros, contactos, contadores y preferencias. | Publicar, bloquear, borrar o marcar como leído cambia estado y nunca es una lectura. |
| Búsqueda y archivos | Búsqueda global, etiquetas, comentarios, ratings, archivos y estado xAPI. | No recorrer archivos fuera del contexto visible ni aceptar URL arbitrarias. |
| Competencias, políticas y privacidad | Planes, evidencias, competencias, versiones de políticas, aceptaciones y solicitudes propias. | Solo consulta; no aceptar políticas, resolver solicitudes ni evaluar competencias. |
| Actividades | Book, BBB, Chat, Choice, Database, Feedback, Folder, Glossary, H5P, IMSCP, Lesson, LTI, Page, Quiz, Resource, SCORM, Survey, URL, Wiki y Workshop. | La presencia del módulo no garantiza plugin, permiso, fase abierta ni API utilizable. |
| Foros | Acceso, discusiones, posts, suscripción, seguimiento y favoritos. | Publicar, editar, marcar o borrar requiere conocer foro, discusión, autoría y audiencia. |
| Seguimiento de actividad | Funciones `*_view_*` y registros equivalentes. | Son `R!`: generan trazas y pueden cambiar finalización automática. |
| Social | Comentarios y valoraciones. | Publicación visible; borrar puede ser irreversible. |
| Intentos | Banderas de preguntas y datos propios de intentos. | Cualquier respuesta evaluable conserva el flujo específico de cuestionario. |

## Funciones representativas R/W/R!

La siguiente matriz no reproduce las más de 300 entradas; selecciona las funciones con mayor valor
para una cuenta de estudiante y las que mejor ilustran los límites.

| Área | Tipo | Funciones Moodle relevantes | Uso previsto y condición |
| --- | --- | --- | --- |
| Perfil | `R` | `core_user_get_users_by_field`, `core_user_get_user_preferences` | Solo la identidad autenticada; perfil arbitrario rechazado. |
| Archivos privados | `R` | `core_user_get_private_files_info`, `core_files_get_files` | Metadatos o rutas visibles; descarga mediante URL previamente validada. |
| Insignias y blog | `R` | `core_badges_get_user_badges`, `core_blog_get_entries` | Solo contenido que Moodle permita ver. |
| Políticas y privacidad | `R` | `core_ai_get_policy_status`, `tool_policy_get_policy_version`, `tool_policy_get_user_acceptances`, `tool_dataprivacy_get_data_requests` | Estado informativo; no aceptar términos ni crear/cancelar solicitudes. |
| Cursos | `R` | `core_enrol_get_users_courses`, `core_course_get_contents`, `core_course_get_course_module`, `core_courseformat_get_overview_information` | Cursos y módulos visibles. |
| Participantes y grupos | `R` | `core_enrol_get_enrolled_users`, `core_group_get_course_user_groups`, `core_group_get_activity_allowed_groups` | Según permisos del rol estudiante; grupos propios por defecto. |
| Agenda | `R` | `core_calendar_get_calendar_events`, `core_calendar_get_action_events_by_timesort`, `core_calendar_get_calendar_event_by_id` | No confundir evento de evaluación continua con examen oficial publicado. |
| Calificaciones | `R` | `gradereport_overview_get_course_grades`, `gradereport_user_get_grade_items` | Siempre para el usuario autenticado. |
| Finalización | `R` | `core_completion_get_activities_completion_status`, `core_completion_get_course_completion_status` | Consulta sin marcar manualmente ni generar visita. |
| Mensajes | `R` | `core_message_get_messages`, `core_message_get_conversation_members` | Se excluye `core_message_get_conversations`: puede crear y marcar como favorita una conversación propia. |
| Notificaciones | `R` | `message_popup_get_popup_notifications`, `core_message_get_unread_notification_count` | Paginada y sin vaciar contadores. |
| Foros | `R`/`R!` | `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions`; `mod_forum_get_discussion_posts` es `R!` | Foros y discusiones son lectura; los posts requieren el par contextual confirmado porque Moodle puede marcarlos como leídos. |
| Búsqueda | `R` | `core_search_get_results`, `core_search_get_top_results`, `core_tag_get_tags` | Resultados remotos no confiables y paginados. |
| Lección y feedback | `R` | `mod_lesson_get_pages`, `mod_feedback_get_items` | Se excluye `mod_lesson_get_page_data`: puede mutar el estado de revisión de la sesión. |
| H5P y SCORM | `R` | `mod_h5pactivity_get_h5pactivities_by_courses`, `mod_h5pactivity_get_attempts`, `mod_scorm_get_scorm_scoes` | Disponibilidad dependiente del módulo, versión y opciones de revisión. |
| Taller | `R` | `mod_workshop_get_workshops_by_courses`, `mod_workshop_get_user_plan`, `mod_workshop_get_submission_assessments` | Respeta fase, asignación de revisores y visibilidad. |
| Cuestionarios | `R` | `mod_quiz_get_user_quiz_attempts`, `mod_quiz_get_attempt_review`, `mod_quiz_get_user_best_grade` | Solo revisión permitida; no elude cierre ni opciones de feedback. |
| Preferencias privadas | `W` | `core_user_update_user_preferences`, `core_course_set_favourite_courses` | Admitidas por genérico con preview y nonce exacto. |
| Organización de mensajes | `W` | `core_message_mute_conversations`, `core_message_unmute_conversations`, `core_message_set_favourite_conversations`, `core_message_unset_favourite_conversations`, `core_message_set_unsent_message` | Admitidas por genérico: estado privado y alcance explícito. |
| Marcar como leído | `R!` | `core_message_mark_message_read`, `core_message_mark_notification_read`, `core_message_mark_all_notifications_as_read`, `core_message_mark_all_conversation_messages_as_read` | Admitidas por genérico, pero tratadas como escritura. |
| Bandera de pregunta | `W` | `core_question_update_flag` | Admitida por genérico para un intento propio; no cambia la respuesta. |
| Registro de vistas | `R!` | `core_course_view_course`, `mod_assign_view_assign`, `mod_forum_view_forum_discussion`, `mod_h5pactivity_view_h5pactivity`, `mod_quiz_view_quiz`, `mod_workshop_view_workshop` | Catalogadas para descubrir semántica, pero no se ejecutan automáticamente al leer. |
| Finalización manual | `W` | `core_completion_update_activity_completion_status_manually`, `core_completion_mark_course_self_completed` | Requiere herramienta contextual: puede activar reglas académicas. |
| Publicaciones | `W` | `mod_forum_add_discussion`, `mod_forum_add_discussion_post`, `core_comment_add_comments`, `mod_wiki_edit_page` | Requiere preview específico con curso, audiencia, contenido y autoría. |
| Actividades evaluables | `W` | `mod_choice_submit_choice_response`, `mod_feedback_process_page`, `mod_lesson_process_page`, `mod_survey_submit_answers`, `mod_workshop_update_assessment` | Requiere flujo específico que compruebe fase, intento y consecuencias. |
| Tareas y cuestionarios | `W` | `mod_assign_save_submission`, `mod_assign_submit_for_grading`, `mod_quiz_start_attempt`, `mod_quiz_process_attempt` | Ya cubiertas mediante herramientas específicas de v0.2, nunca por el genérico. |
| Eliminaciones | `W` | `mod_forum_delete_post`, `mod_data_delete_entry`, `mod_glossary_delete_entry`, `mod_workshop_delete_submission`, `core_enrol_unenrol_user_enrolment` | Destructivas: prohibidas en el genérico y solo planteables con resolución contextual fuerte. |

Las declaraciones oficiales pueden contrastarse en el
[código de Moodle 4.5](https://github.com/moodle/moodle/tree/MOODLE_405_STABLE) y
[Moodle 5.2](https://github.com/moodle/moodle/tree/MOODLE_502_STABLE), en particular en los
`db/services.php` de `lib`, `message`, `course` y cada `mod_*`. Los archivos `externallib.php` de
[mensajería](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/message/externallib.php),
[foros](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/forum/externallib.php),
[tareas](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/mod/assign/externallib.php) y las
clases externas de
[cuestionarios](https://github.com/moodle/moodle/tree/MOODLE_405_STABLE/mod/quiz/classes/external)
muestran además los parámetros y comprobaciones de contexto de la base 4.5.

## Política de ejecución genérica

El catálogo diferencia `access=read|action`, `destructive` y
`generic_execution_supported`. Que una acción aparezca en el catálogo sirve para explicar que
Moodle la ofrece en alguna configuración, no para hacerla invocable.

El genérico se limita actualmente a preferencias propias, favoritos privados de cursos y
conversaciones, silencio de conversaciones, borradores no enviados, marcas de leído y banderas de
preguntas. Incluso estas acciones siguen este protocolo:

1. verificar identidad y disponibilidad de la función;
2. validar y sanear argumentos, bloqueando secretos y referencias a otro usuario;
3. devolver una previsualización sin llamar a la mutación;
4. requerir una confirmación humana nueva;
5. consumir un nonce de cinco minutos, de un solo uso y ligado a función, argumentos y cuenta;
6. ejecutar una sola vez, sin cambiar de transporte ni reintentar un timeout ambiguo;
7. volver a leer el estado antes de decidir si hace falta otra acción.

Las publicaciones, evaluaciones y operaciones destructivas necesitan información que un mapa JSON
genérico no puede demostrar de forma suficiente: propietario, curso, grupo, destinatarios,
visibilidad, fase, intento, archivos, plugins, declaración de autoría y reversibilidad. Por eso se
rechazan antes de emitir el nonce. En v0.3 ya tienen herramienta contextual los eventos personales
del calendario, las discusiones y respuestas públicas de foro sin adjuntos, y las respuestas propias
de Choice. Estas seis operaciones requieren un token REST que anuncie sus funciones porque Moodle
4.5–5.2 no las expone normalmente por AJAX. Las restantes necesitan el mismo nivel de resolución
antes de poder ejecutarse.

## Particularidades y evidencia pública de la USC

La página institucional de la USC describe el Campus Virtual como una plataforma basada en Moodle,
destinada a materiales, actividades, tareas, comunicación, seguimiento y evaluación continuada.
También enlaza una guía de introducción, un protocolo de uso y condiciones para los materiales
([Campus Virtual USC](https://assets.usc.gal/gl/campusvirtual)). Esto confirma la familia tecnológica
y el marco de uso, pero no publica la versión exacta de producción, el servicio REST asignado a cada
token ni los plugins activos en cada aula.

Hay evidencia pública adicional de funcionalidades posibles:

- el plan de formación de personal de la USC incluye creación de contenidos interactivos H5P en el
  Campus Virtual y formación sobre Turnitin
  ([Plan de Formación 2024](https://assets.usc.gal/sites/default/files/paragraphs/moreinfo/2024-04/Presentacio%CC%81n_Plan_Formacio%CC%81n_2024.pdf));
- el repositorio audiovisual institucional contiene formación específica sobre la actividad Taller
  de Moodle y enlaza contenidos sobre H5P
  ([La actividad de Taller en Moodle](https://xoc.usc.gal/video/6847f44dea030fe6320711b2));
- el programa público de formación docente describe tareas, calificaciones y formación en Turnitin
  ([Programa de Formación e Innovación Docente](https://www.usc.gal/en/institutional/government/area/managementstaff/training/pfid/schedule));
- el consorcio universitario gallego comunicó la contratación de Turnitin para las universidades de
  A Coruña, Santiago de Compostela y Vigo
  ([CIXUG, contratación de software antiplagio](https://www.cixug.gal/noticias/2020-contratacion-de-software-antiplagio/?lang=es)).

Estas fuentes son evidencia de uso, soporte o formación institucional, no una garantía técnica. H5P
puede estar deshabilitado en un curso; Taller puede no estar en la fase adecuada; Turnitin puede
integrarse como plugin de plagio, LTI o flujo externo y no exponer ninguna función estándar
`mod_*`. `mcp-usc` no debe inventar una API de Turnitin ni enviar un trabajo directamente a ese
tercero. Solo puede reflejar los metadatos que Moodle entregue legítimamente y advertir de que una
entrega podría activar tratamiento o notificaciones externas.

El protocolo y las condiciones de uso enlazados por la USC también son una frontera funcional: el
conector puede leer el estado de políticas que Moodle exponga (`tool_policy_*`), pero no debe aceptar
términos, consentir tratamientos ni tomar decisiones de integridad académica en nombre del alumno.

## Límites por transporte, versión, plugins y permisos

- **REST con token.** Es el transporte más completo, pero el administrador decide qué funciones
  pertenecen al servicio. La lista anunciada por el token puede filtrar el catálogo antes de una
  llamada. La guía oficial confirma que los servicios pueden restringir usuarios, capacidades,
  subida y descarga de archivos
  ([Using web services](https://docs.moodle.org/501/en/Using_web_services)).
- **AJAX con `MoodleSession`.** Solo admite funciones declaradas con `ajax => true`; requiere
  `sesskey` efímero de la página y mismo origen. Moodle documenta que únicamente esas funciones están
  disponibles en `core/ajax`
  ([AJAX](https://moodledev.io/docs/5.2/guides/javascript/ajax)). El contexto se obtiene de
  `user/preferences.php`, no del dashboard `/my/`, porque este último registra el evento
  `dashboard_viewed` incluso en GET.
- **HTML autenticado.** Las páginas `course/view.php`, `mod/*/view.php` y las de foro no se usan como
  lecturas puras: pueden registrar visitas, marcar elementos como leídos o cambiar la finalización.
  Solo se abren formularios de tareas o cuestionarios después de una confirmación contextual.
- **Archivos.** REST usa los endpoints oficiales de subida. La sesión usa únicamente el flujo
  non-JS de borrador y selector de repositorio que Moodle exponga mediante formularios frescos; si
  no reconoce ese contrato, falla cerrado
  ([file handling](https://moodledev.io/docs/5.2/apis/subsystems/external/files)).
- **Cambios de versión.** Una función puede aparecer, cambiar de firma, quedar obsoleta o migrar de
  `externallib.php` a una clase externa. La comprobación de disponibilidad y las pruebas de contrato
  por versión son obligatorias.
- **Plugins.** BBB, H5P, LTI, Turnitin y extensiones locales pueden faltar o tener APIs distintas.
  La ausencia de una función no justifica raspar un endpoint privado ni simular el plugin con
  Playwright.
- **Permisos y contexto.** Ver un nombre de función no concede acceso. Moodle debe validar curso,
  módulo, grupo, propietario y capacidades en cada llamada.
- **Resultados y contenido.** HTML, nombres, mensajes, preguntas, URLs y ficheros son datos remotos
  no confiables. Se sanean, limitan y nunca se interpretan como instrucciones para el agente.
- **Escrituras ambiguas.** Ante timeout o respuesta no concluyente, el resultado es desconocido y no
  se reintenta. Primero se consulta el estado actual.

## Exclusiones deliberadas

### Funciones de docente o administrador

Quedan fuera crear/editar cursos y actividades, publicar anuncios como docente, acceder a contenido
oculto, administrar participantes, roles o grupos, matricular a terceros, ampliar plazos, reabrir
entregas, calificar, moderar, asignar revisores, ver respuestas ajenas, acceder a bancos de preguntas
o alterar la configuración de calificaciones. El catálogo omite las funciones puramente docentes,
pero Moodle sigue aplicando los permisos efectivos de la cuenta a las lecturas compartidas. Debe
usarse una cuenta de alumno y un servicio/token de mínimo privilegio; una cuenta con rol adicional
podría recibir campos o registros que un alumno normal no vería.

### Decisiones legales, institucionales o académicas

El MCP no acepta políticas o consentimientos, no presenta ni resuelve solicitudes de privacidad, no
tramita matrículas oficiales, renuncias o convocatorias, no interpreta un informe de similitud como
plagio y no decide autoría, fraude, nota o sanción. Las fechas de examen de Moodle y las publicadas
por centros se conservan como fuentes distintas, con su URL y curso académico.

Desde la versión 0.6, los códigos, títulos y fichas de examen se descubren por HTTP desde los planes
oficiales de las dos ediciones del doble grado. El único crosswalk curado relaciona cada edición con
su clase institucional de calendario, ya que la web pública no expone una clave externa directa.
Los códigos presentes en varias ediciones solo se unifican cuando sus convocatorias completas son
idénticas; cualquier divergencia se devuelve como ambigua con todas sus evidencias.

### Servicios de terceros

No se conecta directamente a Turnitin, Microsoft Teams, BigBlueButton, proveedores LTI, correo ni
repositorios externos usando credenciales extraídas de Moodle. Un enlace visible puede devolverse
como metadato seguro, pero seguirlo, aceptar condiciones o transmitir contenido requiere un
conector y una autorización independientes.

### Automatización invasiva

Playwright se reserva al bootstrap visible del inicio de sesión. Las consultas y acciones usan HTTP
REST, AJAX o formularios explícitos; no se automatiza la interfaz para sortear MFA, permisos,
plugins, fases, límites o confirmaciones.

## Consecuencia para el desarrollo

La prioridad razonable es ampliar primero lecturas `R` portables y paginadas; después, crear
herramientas específicas para mutaciones privadas con un estado previo verificable. Las funciones
`R!`, publicaciones, respuestas evaluables y borrados no deben ascender al genérico por comodidad.

Cada incorporación debe tener pruebas con dobles de transporte que demuestren: lista blanca exacta,
identidad ligada, validación de límites y secretos, saneamiento, ausencia de invocación durante el
preview, token exacto y de un solo uso, rechazo tras cambio de cuenta, y cero red o escrituras reales
en la suite. Esta estrategia permite aprovechar el amplio ecosistema HTTP de Moodle sin confundir
descubrimiento con permiso ni cobertura potencial con garantía operativa en la USC.
