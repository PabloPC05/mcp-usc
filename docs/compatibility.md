# Compatibilidad

Este documento distingue compatibilidad comprobada, contrato estudiado y disponibilidad efectiva.
Una función documentada puede no estar habilitada por la instalación o los permisos de una cuenta.

## Runtime y sistemas operativos

| Elemento | Contrato v0.10 | Verificación continua |
| --- | --- | --- |
| Python | 3.11 o posterior | 3.11 y 3.13 |
| Linux | Compatible | `ubuntu-latest` |
| Windows | Compatible | `windows-latest` |
| macOS | Compatible | `macos-latest` |
| SDK MCP | `mcp>=1.29,<2` | Versión fijada por `uv.lock` |
| Transporte MCP | STDIO | Sesión cliente/servidor real en pytest |

La matriz CI ejecuta las seis combinaciones de sistema y Python. El build se produce una vez en
Linux; el workflow de release instala después el wheel en un entorno limpio.

## Capacidades del host MCP

| Capacidad | Necesaria | Degradación |
| --- | --- | --- |
| Tools | Sí para operar | Sin herramientas no se puede consultar Moodle. |
| Tool annotations/approval | Muy recomendada | El token interno sigue protegiendo escrituras, pero falta la capa de aprobación del host. |
| Resources | Opcional | La misma información está en README y `describe_mcp_usc`. |
| Prompts | Opcional | Se pueden formular las peticiones equivalentes en lenguaje natural. |

Los recursos y prompts siguen el contrato del SDK 1.29. La migración al SDK 2 se evaluará de forma
separada porque es un cambio mayor de la dependencia y no debe mezclarse con el contrato académico.

## Moodle estudiado

El inventario se contrastó con Moodle 4.5, 5.0, 5.1 y 5.2. Eso no certifica cualquier instalación: los
plugins, el servicio REST, el marcado AJAX y los permisos cambian por sitio.

| Área | Token REST legítimo | MoodleSession mediante HTTP |
| --- | --- | --- |
| Cursos, Timeline y calendario | Funciones del servicio | AJAX *same-origin* reconocido |
| Mensajes y conversaciones | REST | AJAX puro cuando está disponible |
| Recursos | REST + `/pluginfile.php` | Metadatos AJAX; descarga solo con `/pluginfile.php` directo |
| Tareas | REST y upload multipart | Listado AJAX; formularios confirmados reconocidos |
| Cuestionarios | REST | Lista básica AJAX; formularios confirmados para acciones |
| Foros | REST | Lista básica AJAX; discusiones fallan cerrado sin función segura |
| Eventos y Choice contextuales | REST anunciado | Falla cerrado sin AJAX seguro |

`list_student_capabilities(available_only=true)` descubre lo anunciado por un token. En modo sesión
no existe siempre un catálogo fiable: cada llamada valida su propia disponibilidad y falla cerrado.

## Fuentes públicas USC

- Catálogo y planes actuales: consulta HTTPS anónima con validación estricta.
- Horarios lectivos: páginas Drupal públicas por titulación, centro, curso, semestre y semana.
- Calendarios estructurados: ETSE y Facultade de Matemáticas para los crosswalks declarados.
- Otros centros: búsqueda genérica solo en URLs USC configuradas y sus enlaces inmediatos.

La localización global de una materia no implica que exista un calendario de exámenes estructurado
compatible para su centro. Los horarios de los centros pertenecientes al mismo plan se pueden
agregar, pero cada sesión conserva su procedencia; los planes homónimos permanecen separados. Una
ausencia de datos se devuelve explícitamente. Toda fecha conserva fuente y curso académico; los
conflictos no se silencian.

## Qué significa “compatible”

- **Comprobado en CI:** el paquete instala, el servidor inicia y el contrato local pasa en el runner.
- **Estudiado:** el código/contrato oficial de esa versión Moodle fue analizado y tiene fixtures.
- **Disponible:** la cuenta y el sitio concretos anuncian o permiten la función en ese momento.

Solo la última condición garantiza una operación real. `mcp-usc doctor` comprueba preparación local;
`mcp-usc status` valida por HTTP la credencial; ninguno eleva los permisos efectivos.
