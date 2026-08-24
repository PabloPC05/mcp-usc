# Validación real USC de v0.10.0

Fecha: 25 de agosto de 2026. Transporte: `moodle_http_session` sobre HTTPS.

## Límites de la prueba

La validación utilizó una cuenta ordinaria de alumno y una sesión renovada mediante el login visible
con Microsoft/MFA. Después del login, todas las comprobaciones usaron HTTP directo. No se conservaron
credenciales, identificadores, nombres de materias, mensajes, notas ni contenidos en este documento.

No se llamó ninguna herramienta de ejecución: no se enviaron mensajes, no se abrieron tareas o
intentos, no se guardaron respuestas y no se modificó ningún dato remoto.

## Resultados de lectura

| Flujo | Resultado con sesión USC | Observación |
| --- | --- | --- |
| Autenticación, perfil y cursos | Correcto | La sesión y la identidad se validaron sin exponer la cookie. |
| Cursos archivados u ocultos del tablero | Correcto | Se recuperaron mediante el endpoint AJAX de clasificación. |
| Timeline y pendientes | Correcto tras corrección | La USC limita la página a 50 elementos. |
| Tareas | Correcto | El estado AJAX de los cursos devolvió CMID, nombre, curso y visibilidad. |
| Notificaciones | Correcto | La lectura no cambió el estado leído/no leído. |
| Mensajes recibidos y enviados | Correcto | La lectura no creó conversaciones ni marcó mensajes. |
| Anuncios | No disponible de forma pura | Abrir el HTML alternativo puede registrar vistas o lecturas. |
| Cuestionarios | Lista básica correcta | El estado AJAX devuelve CMID, nombre y visibilidad; no configuración ni intentos. |
| Notas y finalización | No disponible por AJAX | Las funciones REST correspondientes no están anunciadas a la sesión. |
| Calendario amplio | No disponible por AJAX | El Timeline accionable sí está disponible. |
| Contenidos | Lista básica correcta | Se validaron 72 módulos como metadatos, sin abrir sus páginas. |
| Recursos | Metadatos correctos | Se validaron 35 módulos; cero tokens porque AJAX no publicó `/pluginfile.php`. |
| Foros | Lista básica correcta | Se validaron dos CMID; no se inventaron identificadores internos de foro. |
| Insignias y archivos privados | No disponible por AJAX | Requieren un servicio REST que anuncie esas funciones. |

La sesión HTTP no ofrece un catálogo de capacidades completo. Una función del inventario solo se
considera disponible cuando una llamada segura real lo confirma. Los rechazos anteriores son
degradaciones cerradas, no autorizan scraping de páginas con efectos laterales.

## Previews verificadas

Se eligió internamente una tarea visible, sin registrar su identidad, y se comprobaron estas cinco
previsualizaciones:

- inspeccionar el estado de la entrega;
- guardar texto en línea;
- eliminar los archivos del borrador;
- enviar la entrega para calificación;
- retirar la entrega propia.

Todas emitieron un token local, efímero y de un solo uso y declararon cero efectos remotos. Ningún
token se entregó a una herramienta de ejecución.

## Incidencias corregidas

- `list_pending_work` y `list_upcoming_events` aceptaban hasta 200 elementos aunque Moodle limita
  `core_calendar_get_action_events_by_timesort` a 50. El contrato y los valores predeterminados se
  redujeron a 50.
- `normalise_event` trataba el entero `timeusermidnight` como HTML. Ahora conserva
  `formattedtime`, que es el campo textual publicado por Moodle.
- `list_quizzes`, `list_course_contents`, `list_course_resources` y `list_forums` dependían de
  funciones REST que la sesión USC no expone. Ahora usan `core_courseformat_get_state` para devolver
  únicamente CMID y metadatos limitados.

## Trabajo posterior

Las lecturas de intentos, notas, finalización, anuncios, discusiones o datos privados deben seguir
requiriendo una función AJAX segura o un token REST legítimo; no se deben emular con navegación. Un
recurso solo puede ascender de metadato a descarga cuando Moodle publique una URL directa
`/pluginfile.php` validada.
