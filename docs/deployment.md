# Servirlo por HTTP

Para uso personal, lo normal es el modo local por STDIO: lo instalas en tu equipo y tu asistente lo
arranca solo (ver el [README](../README.md#instalación-rápida)). El modo HTTP sirve para usar el
mismo servidor desde otros equipos o agentes. Es una única aplicación ASGI que funciona igual en
cuatro sitios:

| Dónde | Cómo se arranca |
|---|---|
| Tu equipo o un servidor propio | `mcp-usc serve --transport http` (uvicorn) |
| Cualquier servidor ASGI | `uvicorn --factory mcp_usc.http_app:create_app` |
| Contenedor (Docker, Podman, PaaS) | `Dockerfile` del repositorio |
| Vercel | `api/index.py`, que adapta la misma app |

Las rutas son `/mcp` (MCP Streamable HTTP, autenticado), y `/healthz` y `/` (estado mínimo y
público, sin secretos).

## Reglas comunes

- **Token obligatorio.** Toda petición a `/mcp` necesita `Authorization: Bearer <token>`. Sin
  verificador configurado, `/mcp` responde `503`. Un token de menos de 32 caracteres impide arrancar.
- **Solo lectura por defecto.** Se publican solo las 50 herramientas de lectura pura; las previews y
  escrituras ni siquiera aparecen en `tools/list`. Con `MCP_USC_ALLOW_WRITES=true` se publican
  todas, siempre con su preview y su token de confirmación de un solo uso. Esto solo se admite en
  un **único proceso persistente**, porque las confirmaciones viven en su memoria: nunca en
  serverless (se niega a arrancar) ni con varias réplicas o workers.
- **Sin estado de sesión HTTP.** Cada petición es independiente, así que reiniciar el servidor no
  rompe a los clientes conectados.
- **Credenciales de Moodle.** En un servidor no suele haber almacén seguro del sistema (`keyring`).
  Usa `USC_MOODLE_TOKEN`, `USC_MOODLE_TOKEN_FILE` o `USC_MOODLE_SESSION` como secretos del servicio,
  o envíalas en cada petición con las cabeceras `X-USC-Moodle-Token` o `X-USC-Moodle-Session`.
  Esas cabeceras solo viven durante su petición, se eliminan antes de llegar a FastMCP y no se
  escriben en disco. El token REST tiene prioridad.
- **Escucha en local y publica detrás de TLS.** Deja el servidor en `127.0.0.1` y pon delante un
  proxy inverso con HTTPS (Caddy, nginx) o una pasarela MCP que añada el token. No lo expongas en
  claro a Internet.

## Variables

| Variable | Uso |
|---|---|
| `MCP_AUTH_TOKEN` | Token del cliente (mínimo 32 caracteres). |
| `MCP_AUTH_TOKEN_FILE` | Archivo con ese token; cómodo con systemd, Docker secrets o Kubernetes. |
| `MCP_AUTH_TOKEN_SHA256` | Solo el SHA-256 en hexadecimal del token, si no quieres guardar el original. |
| `MCP_USC_HOST` / `MCP_USC_PORT` | Interfaz y puerto (por defecto `127.0.0.1:8000`). |
| `MCP_USC_TRANSPORT` | `http` para que `mcp-usc serve` arranque en HTTP sin argumentos. |
| `MCP_USC_ALLOW_WRITES` | `true` para publicar previews y escrituras (solo un proceso persistente). |
| `MCP_USC_STATE_SECRET` | Clave compartida (32+ caracteres) para que varias instancias acepten las referencias de `read_course_resource` de las demás. Necesaria en serverless. |
| `USC_MOODLE_TOKEN`, `USC_MOODLE_TOKEN_FILE`, `USC_MOODLE_SESSION` | Credenciales de Moodle del servidor (opcionales). |

Si hay varios verificadores, gana el primero de la lista: `MCP_AUTH_TOKEN`, `MCP_AUTH_TOKEN_FILE` y
`MCP_AUTH_TOKEN_SHA256`. Para generar un token y su hash:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "<token>"
```

## En tu equipo o en un servidor propio

```sh
uv tool install "mcp-usc @ https://github.com/PabloPC05/mcp-usc/archive/refs/heads/main.zip"
MCP_AUTH_TOKEN_FILE=/ruta/al/token mcp-usc serve --transport http --port 8000
```

Como servicio de systemd, con el token entregado como credencial y sin usuario fijo:

```ini
[Unit]
Description=mcp-usc (MCP por HTTP)
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/ruta/a/mcp-usc serve --transport http --host 127.0.0.1 --port 8000
LoadCredential=token:/etc/mcp-usc/token
Environment=MCP_AUTH_TOKEN_FILE=%d/token
# Opcional: USC_MOODLE_TOKEN_FILE, USC_MOODLE_SESSION, MCP_USC_ALLOW_WRITES...
EnvironmentFile=-/etc/mcp-usc/env
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

En NixOS es lo mismo con `systemd.services.mcp-usc`, y el entorno se puede crear con `uv` como
cualquier otro servicio Python. Si lo pones detrás de una pasarela MCP, dale la URL
`http://127.0.0.1:8000/mcp` y el mismo token como cabecera `Authorization: Bearer`.

## En un contenedor

```sh
docker build -t mcp-usc .
docker run -d --name mcp-usc -p 127.0.0.1:8000:8000 \
  -e MCP_AUTH_TOKEN_FILE=/run/secrets/mcp-token \
  -v /ruta/segura/token:/run/secrets/mcp-token:ro \
  mcp-usc
```

La imagen corre sin privilegios, escucha en `0.0.0.0:8000` dentro del contenedor e incluye un
`HEALTHCHECK` sobre `/healthz`. Usa una sola réplica si activas `MCP_USC_ALLOW_WRITES`. El CI
construye la imagen y la prueba en cada cambio.

## En Vercel

1. Importa el repositorio como proyecto de Vercel. `vercel.json` ya enruta `/`, `/healthz` y `/mcp`
   hacia `api/index.py`.
2. Define como variables cifradas `MCP_AUTH_TOKEN` (o `MCP_AUTH_TOKEN_SHA256`) y
   `MCP_USC_STATE_SECRET`. Sin esta última, `read_course_resource` solo acepta referencias emitidas
   por la misma instancia.
3. Añade, si quieres, credenciales de Moodle (`USC_MOODLE_TOKEN` o `USC_MOODLE_SESSION`), o envíalas
   por cabecera desde el cliente.

Vercel define `VERCEL=1`, así que el despliegue es siempre de solo lectura. Las ramas y pull
requests generan despliegues de prueba, y `main` el de producción.

> [!IMPORTANT]
> Hasta la versión 0.11.0, `api/index.py` traía un hash de token fijo en el código. Ya no existe:
> antes de desplegar esta versión, define `MCP_AUTH_TOKEN_SHA256` (o `MCP_AUTH_TOKEN`) en las
> variables del proyecto o `/mcp` responderá `503`.

## Conectar un cliente remoto

Claude Code:

```sh
claude mcp add --transport http usc-campus https://tu-dominio/mcp \
  --header "Authorization: Bearer <token>"
```

Otros clientes con configuración JSON y transporte HTTP:

```json
{
  "mcpServers": {
    "usc-campus": {
      "type": "http",
      "url": "https://tu-dominio/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Guarda el token en el almacén de secretos del cliente, no en una configuración sincronizada o
pública.
