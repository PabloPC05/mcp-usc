# Despliegue privado en Vercel

El repositorio incluye una función ASGI específica para Vercel en `api/index.py`. El servidor local
sigue usando `stdio`; el despliegue remoto publica MCP mediante **Streamable HTTP** en `/mcp`.

## Seguridad y alcance

El endpoint MCP exige siempre:

```http
Authorization: Bearer <MCP_AUTH_TOKEN>
```

La aplicación admite dos verificadores:

- `MCP_AUTH_TOKEN`: secreto en texto claro, de al menos 32 caracteres, guardado como variable
  cifrada de Vercel;
- `MCP_AUTH_TOKEN_SHA256`: hash SHA-256 hexadecimal del secreto. El hash puede estar en el código
  porque no permite recuperar un token aleatorio suficientemente largo; el token original solo se
  conserva en el cliente.

Si no hay un verificador válido, `/mcp` responde con `503` y no expone ninguna herramienta. Para
rotar un despliegue basado en hash, genera un token aleatorio nuevo, sustituye únicamente su hash
en `api/index.py` y actualiza el secreto del cliente.

El modo Vercel es deliberadamente **solo lectura**. Solo quedan habilitadas herramientas con
`readOnlyHint=true`; cualquier herramienta no marcada explícitamente como lectura falla cerrada.
Las confirmaciones de escritura del servidor local son de un solo uso y viven en memoria del
proceso. Una función serverless podría ejecutar la vista previa y la confirmación en instancias
distintas, por lo que no se relaja esa garantía para hacer funcionar escrituras remotas.

## Credenciales de Moodle sin persistencia

Las credenciales privadas de la USC pueden configurarse como variables del proyecto o enviarse en
cada petición MCP mediante cabeceras:

```http
X-USC-Moodle-Token: <token oficial de Moodle Web Services>
X-USC-Moodle-Session: <valor de la cookie MoodleSession>
```

El token REST tiene prioridad. Las cabeceras solo viven en el contexto de esa petición, se eliminan
antes de entregar la petición a FastMCP y no se escriben en disco, keyring ni variables de Vercel.
Usa una sola cabecera cuando sea posible. `MoodleSession` es menos estable: Moodle puede caducarla o
rotarla y, cuando ocurra, el cliente debe enviar el nuevo valor.

## Variables del proyecto

Configura en Vercel, para Production y Preview cuando corresponda:

| Variable | Obligatoria | Uso |
|---|---:|---|
| `MCP_AUTH_TOKEN` | Alternativa | Secreto privado que protege `/mcp`; mínimo 32 caracteres. |
| `MCP_AUTH_TOKEN_SHA256` | Alternativa | Hash SHA-256 del secreto del cliente. |
| `USC_MOODLE_TOKEN` | No | Token oficial de Moodle Web Services persistido en Vercel. |
| `USC_MOODLE_SESSION` | No | Cookie de sesión persistida cuando no existe token REST. |
| `USC_MOODLE_URL` | No | Por defecto, `https://cv.usc.es`. |
| `USC_EXAM_SOURCES` | No | Fuentes oficiales adicionales separadas por `;`. |

Debe existir exactamente uno de los dos verificadores MCP. `MCP_AUTH_TOKEN` tiene prioridad si ambos
están configurados. Nunca guardes los secretos originales en Git, `.env.example` ni logs.

## Rutas

- `/mcp`: endpoint MCP autenticado.
- `/healthz`: comprobación pública mínima; indica si el verificador MCP está listo, sin revelarlo.
- `/`: metadatos mínimos del servicio.

## Despliegues desde GitHub

Importa `PabloPC05/mcp-usc` como proyecto de Vercel y deja `main` como **Production Branch**. Con la
integración Git activa:

- cada push o merge a `main` crea un despliegue de producción;
- las demás ramas y pull requests crean previews;
- el runtime Python detecta `api/index.py` e instala las dependencias declaradas por el proyecto.

La URL que debe recibir el cliente MCP es:

```text
https://<dominio-del-proyecto>/mcp
```

El cliente debe enviar el secreto mediante `Authorization`. Añade también una de las cabeceras USC
si necesita datos privados del Campus. Mantén todos esos valores en el almacén de secretos del
cliente, no dentro de una configuración sincronizada o pública.
