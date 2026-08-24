# Changelog

Los cambios relevantes de `mcp-usc` se documentan aquí. El proyecto sigue
[Versionado Semántico](https://semver.org/lang/es/) durante la fase beta: una versión menor puede
ampliar el contrato y cualquier cambio incompatible debe explicarse expresamente.

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
