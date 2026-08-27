# Despliegue privado en Vercel

El repositorio incluye un entrypoint ASGI específico para Vercel. El servidor local sigue usando
`stdio`; el despliegue remoto publica MCP mediante **Streamable HTTP** en `/mcp`.

## Seguridad y alcance

El endpoint MCP exige siempre:

```http
Authorization: Bearer <MCP_AUTH_TOKEN>
```

`MCP_AUTH_TOKEN` debe tener al menos 32 caracteres y configurarse como variable cifrada del
proyecto. Si falta o es demasiado corto, `/mcp` responde con `503` y no expone ninguna herramienta.

El modo Vercel es deliberadamente **solo lectura**. Solo quedan habilitadas herramientas con
`readOnlyHint=true`; cualquier herramienta no marcada explícitamente como lectura falla cerrada.
Las confirmaciones de escritura del servidor local son de un solo uso y viven en memoria del
proceso. Una función serverless podría ejecutar la vista previa y la confirmación en instancias
distintas, por lo que no se relaja esa garantía para hacer funcionar escrituras remotas.

## Variables del proyecto

Configura en Vercel, para Production y Preview cuando corresponda:

| Variable | Obligatoria | Uso |
|---|---:|---|
| `MCP_AUTH_TOKEN` | Sí | Token privado que protege `/mcp`; mínimo 32 caracteres. |
| `USC_MOODLE_TOKEN` | Recomendada | Token oficial de Moodle Web Services. |
| `USC_MOODLE_SESSION` | Alternativa | Valor de `MoodleSession` cuando no existe token REST. |
| `USC_MOODLE_URL` | No | Por defecto, `https://cv.usc.es`. |
| `USC_EXAM_SOURCES` | No | Fuentes oficiales adicionales separadas por `;`. |

No configures simultáneamente una credencial de prueba y una real en distintas ramas sin revisar
qué entornos heredan cada variable. Nunca guardes los valores en Git, `.env.example` ni logs.

`USC_MOODLE_SESSION` permite usar la cookie desde el entorno serverless sin keyring. Es menos
estable que `USC_MOODLE_TOKEN`: Moodle puede caducarla o rotarla y Vercel no puede escribir el nuevo
valor de vuelta a las variables del proyecto. Cuando ocurra, sustituye manualmente la variable.

## Rutas

- `/mcp`: endpoint MCP autenticado.
- `/healthz`: comprobación pública mínima; indica si el token MCP está configurado, sin revelarlo.
- `/`: metadatos mínimos del servicio.

## Despliegues desde GitHub

Importa `PabloPC05/mcp-usc` como proyecto de Vercel y deja `main` como **Production Branch**. Con la
integración Git activa:

- cada push o merge a `main` crea un despliegue de producción;
- las demás ramas y pull requests crean previews;
- Vercel usa `app:app`, declarado en `pyproject.toml`, y el runtime Python instala las dependencias
del proyecto.

La URL que debe recibir el cliente MCP es:

```text
https://<dominio-del-proyecto>/mcp
```

El cliente debe enviar el mismo `MCP_AUTH_TOKEN` mediante el encabezado `Authorization`. Mantén ese
token en el almacén de secretos del cliente, no dentro de una configuración sincronizada o pública.
