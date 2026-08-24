# Inventario de herramientas MCP

La versión 0.9.0 expone 84 herramientas. Este documento enumera el contrato público completo; el
servidor también lo devuelve agrupado mediante `describe_mcp_usc` y un test lo compara con el
inventario STDIO real.

Los cuatro recursos y cuatro prompts añadidos en v0.9 se documentan por separado en
[Superficie MCP](mcp-surface.md); no cambian las herramientas ni sus parámetros.

## Tipos

- **R — lectura pura:** no pretende cambiar estado remoto. Aun así, el contenido devuelto debe
  tratarse como no confiable.
- **P — preview:** valida y explica una posible operación. No ejecuta el efecto y puede emitir un
  token efímero de confirmación.
- **E — efecto confirmado:** modifica o puede modificar estado. Exige un token de un solo uso para
  los mismos parámetros y aprobación del host MCP.
- **S — inspección stateful:** lectura cuya página puede registrar vista/completion o procesar un
  vencimiento. También exige preview y confirmación.

## Servidor y catálogo genérico (6)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `describe_mcp_usc` | R | Describe propósito, alcance, límites, seguridad, transportes e inventario sin usar la red. |
| `auth_status` | R | Valida por HTTP la autenticación configurada sin devolver secretos. |
| `list_student_capabilities` | R | Cataloga funciones de alumno permitidas y disponibilidad anunciada por un token REST. |
| `call_student_read` | R | Ejecuta únicamente una de las lecturas de la allowlist estudiada. |
| `preview_student_action` | P | Previsualiza una acción genérica permitida y sus parámetros exactos. |
| `execute_student_action` | E | Ejecuta la acción genérica aprobada; no es un proxy Moodle arbitrario. |

## Perfil y progreso (9)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `get_my_profile` | R | Perfil de la cuenta autenticada. |
| `get_my_preferences` | R | Preferencias propias, opcionalmente filtradas por nombre. |
| `list_course_participants` | R | Participantes visibles para la cuenta en una materia. |
| `list_my_groups` | R | Grupos propios dentro de una materia. |
| `get_my_grades` | R | Calificaciones propias globales o de una materia. |
| `get_my_completion` | R | Estado de finalización propio. |
| `list_notifications` | R | Notificaciones sin marcarlas como leídas. |
| `list_my_badges` | R | Insignias visibles de la cuenta. |
| `get_private_files_info` | R | Metadatos y cuota de archivos privados, sin descargar ni modificar. |

## Cursos y calendario (10)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `list_courses` | R | Materias actuales y, opcionalmente, archivadas/ocultas del tablero. |
| `list_pending_work` | R | Acciones pendientes del Timeline en un intervalo. |
| `list_upcoming_events` | R | Próximos eventos visibles. |
| `get_work_item` | R | Detalle de un elemento del Timeline. |
| `list_announcements` | R | Avisos de los foros de novedades visibles. |
| `list_calendar_events` | R | Eventos de calendario de un intervalo y cursos seleccionados. |
| `preview_create_personal_calendar_event` | P | Previsualiza un evento personal nuevo. |
| `create_personal_calendar_event` | E | Crea el evento personal exacto confirmado. Requiere REST anunciado. |
| `preview_delete_personal_calendar_event` | P | Comprueba propietario y alcance antes de borrar. |
| `delete_personal_calendar_event` | E | Elimina el evento o serie personal confirmados. Requiere REST anunciado. |

## Mensajes, foros y Choice (19)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `list_conversations` | R | Compatibilidad; falla cerrado en versiones donde la llamada podría crear una conversación. |
| `list_messages` | R | Mensajes enviados o recibidos sin marcarlos. |
| `list_conversation_messages` | R | Historial paginado de una conversación. |
| `search_message_contacts` | R | Busca destinatarios y crea una referencia temporal ligada al usuario. |
| `preview_message` | P | Muestra destinatario y texto exactos; no envía. |
| `send_message` | E | Envía el mensaje interno confirmado; puede activar avisos externos del destinatario. |
| `list_forums` | R | Foros visibles de una materia. |
| `list_forum_discussions` | R | Discusiones visibles de un foro. |
| `list_discussion_posts` | R | Compatibilidad; falla cerrado si Moodle puede marcar posts como leídos. |
| `preview_inspect_discussion_posts` | P | Advierte del posible marcado de lectura antes de inspeccionar posts. |
| `inspect_discussion_posts` | E | Inspecciona posts tras confirmación; puede registrar lectura. |
| `preview_create_forum_discussion` | P | Resuelve curso, foro, grupo y audiencia; no publica. |
| `create_forum_discussion` | E | Publica la discusión confirmada sin adjuntos. Requiere REST anunciado. |
| `preview_reply_forum_post` | P | Resuelve discusión, post padre y audiencia; no responde. |
| `reply_forum_post` | E | Publica la respuesta confirmada. Requiere REST anunciado. |
| `preview_submit_choice_response` | P | Previsualiza las opciones propias de una actividad Choice. |
| `submit_choice_response` | E | Guarda la respuesta Choice confirmada. Requiere REST anunciado. |
| `preview_cancel_choice_response` | P | Comprueba si se puede retirar la respuesta propia. |
| `cancel_choice_response` | E | Retira la respuesta confirmada. Requiere REST anunciado. |

## Materiales, grados y exámenes (11)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `list_course_contents` | R | Secciones, actividades, páginas, enlaces y archivos de una materia. |
| `list_course_resources` | R | Referencias opacas y temporales a recursos descargables. |
| `read_course_resource` | R | Descarga y extrae texto de PDF, texto/HTML y OOXML con límites. |
| `list_exam_sources` | R | Fuentes públicas configuradas para el buscador genérico. |
| `search_exam_dates` | R | Busca evidencia de fechas en páginas/PDF USC configurados. |
| `list_usc_degrees` | R | Titulaciones enlazadas por el catálogo oficial actual. |
| `locate_usc_subject_codes` | R | Localiza códigos exactos en planes oficiales, con filtros opcionales. |
| `list_official_exam_degrees` | R | Ediciones y crosswalks de calendarios estructurados admitidos. |
| `list_official_exam_subjects` | R | Materias oficiales de un grado y curso académico. |
| `get_official_exam_dates` | R | Convocatorias estructuradas para códigos y curso explícitos. |
| `get_my_official_exam_schedule` | R | Cruza cursos Moodle con códigos y calendarios oficiales admitidos. |

Las fechas conservan `source_url`, curso académico, plan/centro y evidencia. Un conflicto se devuelve
como conflicto: la herramienta no elige una fecha por parecido del nombre.

## Tareas y entregas (15)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `list_assignments` | R | Tareas de las materias indicadas o de todas las materias accesibles. |
| `get_submission_status` | R | Estado, borrador, archivos, texto, feedback y permisos mediante REST. |
| `preview_inspect_submission_status` | P | Advierte antes de abrir una página que puede registrar visita/completion. |
| `inspect_submission_status` | S | Inspección confirmada del estado en modo sesión. |
| `check_submission_reopen` | R | Informa si la entrega ya es editable; nunca la reabre. |
| `preview_save_online_submission` | P | Previsualiza el reemplazo del texto online del borrador. |
| `save_online_submission` | E | Guarda el texto online confirmado sin enviar para calificación. |
| `preview_replace_submission_files` | P | Valida allowlist, tamaños y hashes antes de reemplazar archivos. |
| `replace_submission_files` | E | Reemplaza el conjunto completo de archivos del borrador. |
| `preview_delete_submission_files` | P | Previsualiza el borrado del conjunto de archivos del borrador. |
| `delete_submission_files` | E | Elimina los archivos confirmados del borrador. |
| `preview_submit_assignment` | P | Previsualiza el envío para calificación y la declaración requerida. |
| `submit_assignment` | E | Envía la entrega confirmada para calificación; puede cerrar la edición. |
| `preview_remove_submission` | P | Previsualiza la eliminación completa de la entrega propia. |
| `remove_submission` | E | Elimina la entrega confirmada cuando Moodle y los permisos lo permiten. |

En modo sesión, las operaciones confirmadas descubren formularios oficiales frescos y fallan si la
estructura no es reconocida. Reabrir una entrega cerrada corresponde al profesorado; el MCP no lo
elude.

## Cuestionarios (14)

| Herramienta | Tipo | Propósito |
| --- | --- | --- |
| `list_quizzes` | R | Cuestionarios visibles de las materias. |
| `list_quiz_attempts` | R | Intentos propios y su estado. |
| `get_quiz_attempt_page` | R | Compatibilidad REST; falla cerrado para intentos activos que puedan mutar al abrirse. |
| `get_quiz_attempt_summary` | R | Compatibilidad REST; falla cerrado si Moodle puede procesar vencimientos. |
| `get_quiz_attempt_review` | R | Revisión permitida de un intento ya finalizado. |
| `get_quiz_best_grade` | R | Mejor calificación visible del cuestionario. |
| `preview_inspect_quiz_attempt` | P | Advierte del posible cambio de estado/vencimiento antes de inspeccionar. |
| `inspect_quiz_attempt` | E | Inspección confirmada de un intento activo potencialmente stateful. |
| `preview_start_quiz` | P | Muestra cuestionario, intento y efecto del temporizador; no inicia. |
| `start_quiz` | E | Inicia el intento confirmado y puede arrancar el temporizador. |
| `preview_save_quiz_answers` | P | Previsualiza respuestas/campos exactos; no infiere corrección. |
| `save_quiz_answers` | E | Guarda respuestas en un intento abierto sin finalizarlo. |
| `preview_finish_quiz` | P | Presenta el intento y advierte de la irreversibilidad. |
| `finish_quiz` | E | Finaliza el intento exacto confirmado. |

## Disponibilidad según autenticación

| Capacidad | Token REST | MoodleSession por HTTP |
| --- | --- | --- |
| Cursos, Timeline, mensajes y calendario | REST si el servicio expone la función | AJAX *same-origin* cuando Moodle la declara |
| Recursos | REST + descarga autenticada | AJAX + `/pluginfile.php` directo |
| Tareas | REST y upload multipart | Listado AJAX; formularios oficiales solo tras confirmar |
| Cuestionarios | REST | Lecturas AJAX puras; formularios solo tras confirmar |
| Eventos, foros y Choice contextuales | REST anunciado | Falla cerrado si no existe AJAX seguro |
| Grados y exámenes públicos | No requiere autenticación | No requiere autenticación |

`list_student_capabilities(available_only=true)` muestra las funciones anunciadas por el servicio
REST configurado. En modo sesión, Moodle no siempre publica un catálogo completo de disponibilidad;
cada llamada comprueba su contrato y falla cerrado.

## Catálogo genérico

Además de estas 84 herramientas, `call_student_read` puede acceder a 192 funciones Moodle incluidas
expresamente en una allowlist. `execute_student_action` admite 12 acciones genéricas acotadas. El
[estudio de capacidades](student-capability-study.md) documenta las 301 funciones evaluadas, sus
efectos y las diferencias entre versiones Moodle.
