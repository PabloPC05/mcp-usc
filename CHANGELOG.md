# Changelog

Los cambios relevantes de `mcp-usc` se documentan aquí. El proyecto sigue
[Versionado Semántico](https://semver.org/lang/es/) durante la fase beta: una versión menor puede
ampliar el contrato y cualquier cambio incompatible debe explicarse expresamente.

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

[0.9.0]: https://github.com/PabloPC05/mcp-usc/releases/tag/v0.9.0
[0.8.0]: https://github.com/PabloPC05/mcp-usc/releases/tag/v0.8.0
[0.7.0]: https://github.com/PabloPC05/mcp-usc/commit/d9320ad875a058e5ff30e76514ed37f6667cc241
[0.6.0]: https://github.com/PabloPC05/mcp-usc/commit/31ed4cd
