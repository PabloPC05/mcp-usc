# Soporte

## Preguntas y problemas de uso

Busca primero en [Primeros pasos](docs/getting-started.md),
[Herramientas](docs/tools.md) y las [incidencias existentes](https://github.com/PabloPC05/mcp-usc/issues).
Si sigue fallando, abre un bug con la plantilla del repositorio.

Puedes adjuntar la salida de `uv run mcp-usc doctor --compact` tras revisarla. El comando no imprime
valores de credenciales ni contacta con el Campus. `uv run mcp-usc manifest --compact` permite
comparar el contrato y su SHA-256 sin incluir configuración privada. No adjuntes la salida de
herramientas que contenga nombres, cursos, mensajes, calificaciones o entregas reales.

## Qué información ayuda

- versión de `mcp-usc`, Python y sistema operativo;
- modo de autenticación (`token` o `MoodleSession`), sin el valor;
- herramienta y parámetros anonimizados;
- error completo tras retirar rutas, IDs y datos personales;
- resultado de tests con fixtures sintéticos, si puedes reproducirlo.

## Qué no puede resolver este proyecto

El repositorio no gestiona cuentas USC, matrículas, permisos, tokens institucionales, MFA, reapertura
de entregas ni fechas que un centro no haya publicado. Para esos asuntos usa los canales oficiales
de la universidad; no publiques aquí información privada ni datos de terceros.

Las vulnerabilidades no son solicitudes de soporte: repórtalas de forma privada siguiendo
[SECURITY.md](SECURITY.md).
