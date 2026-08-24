# Hoja de ruta

## Estado actual: 0.8.x

La serie 0.8 es una beta comunitaria funcional. Tiene un contrato MCP estable dentro de la serie,
documentación para usuarios y contribuidores, diagnóstico local, CI y un modelo de confirmación
para cualquier operación con efecto. No se denomina 1.0 porque todavía falta demostrar
compatibilidad sostenida con más configuraciones reales de Moodle sin depender de una sola cuenta.

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

## Prioridades candidatas para 0.9

- matriz automatizada de Python y sistemas operativos en CI;
- exportación de diagnósticos sanitizados para incidencias;
- fixtures adicionales de Moodle 4.5, 5.0 y 5.2;
- mayor cobertura de calendarios oficiales de otros centros USC cuando exista un vínculo verificable
  entre plan, código de materia y fuente de examen;
- revisión de accesibilidad y claridad de mensajes de error;
- documentación de integración verificada para más clientes MCP.

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
