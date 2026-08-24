# Hoja de ruta

## Estado actual: 0.9.x

La serie 0.9 es una beta candidata a estabilización. Conserva el contrato de 84 herramientas y
añade recursos, prompts, manifiesto determinista, CI multiplataforma y releases reproducibles. No se
denomina 1.0 porque todavía falta demostrar compatibilidad sostenida con más configuraciones reales
de Moodle y completar una auditoría final sobre el contrato que vaya a declararse estable.

## Criterios para 1.0.0

La versión 1.0 se publicará cuando se cumplan todos estos criterios:

- pruebas de compatibilidad documentadas para los modos REST y MoodleSession en versiones Moodle
  soportadas;
- validación repetible con cuentas de alumno de mínimo privilegio, sin datos personales en fixtures;
- cobertura end-to-end de las operaciones críticas de tareas y cuestionarios, incluidos timeouts y
  resultados ambiguos;
- documentación de instalación verificada en Windows, Linux y macOS;
- política clara de compatibilidad, deprecaciones y soporte de versiones;
- auditoría de seguridad final de autenticación, SSRF, rutas locales, contenido no confiable y
  confirmaciones;
- proceso reproducible de release con artefactos, checksums y notas de migración;
- al menos un ciclo de feedback comunitario sobre nombres, respuestas y ergonomía de herramientas.

## Entregado en 0.9

- matriz CI de Python 3.11/3.13 en Linux, Windows y macOS;
- diagnóstico compacto y manifiesto sanitizado con digest del contrato;
- recursos MCP pasivos y prompts guiados con límites explícitos;
- documentación de compatibilidad y degradación para hosts MCP;
- release automática con verificación de wheel y checksums SHA-256.

## Prioridades antes de 1.0

- fixtures versionados adicionales para respuestas diferenciales de Moodle 4.5, 5.0 y 5.2;
- validación de mínimo privilegio con más de una instalación/cuenta, sin persistir datos reales;
- mayor cobertura de calendarios oficiales de otros centros USC cuando exista un vínculo verificable
  entre plan, código de materia y fuente de examen;
- revisión de accesibilidad y claridad de errores con feedback comunitario;
- decisión y prueba de migración al SDK MCP 2 o política explícita de permanencia temporal en 1.x;
- auditoría final del contrato estable y política formal de deprecaciones.

## Fuera de alcance

- obtener o almacenar contraseñas USC;
- evitar Microsoft MFA o elevar permisos;
- aceptar consentimientos o políticas legales;
- consultar correo, Teams o sistemas no declarados;
- suplantar a profesorado/administración;
- scraping con navegador para operaciones normales;
- ejecutar cambios sin preview, confirmación de un solo uso y aprobación del host.

Las propuestas deben abrirse como una
[solicitud de funcionalidad](https://github.com/PabloPC05/mcp-usc/issues/new/choose) y explicar el
caso de uso estudiantil, la API HTTP disponible y los efectos observables.
