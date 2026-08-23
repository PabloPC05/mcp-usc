# mcp-usc

Servidor MCP local y **HTTP-first** para consultar el Campus Virtual (Moodle) de la Universidade de
Santiago de Compostela y buscar fechas de exámenes en páginas/PDF oficiales. Las consultas son de
solo lectura; la única operación de escritura prevista es mensajería Moodle protegida por
previsualización y confirmación explícita.

El proyecto está pensado para responder preguntas como:

- «¿Qué trabajos o cuestionarios tengo pendientes?»
- «¿Qué avisos nuevos han publicado los profesores?»
- «¿Qué eventos tengo en las próximas semanas?»
- «¿Cuándo es el examen de una materia y cuál es la fuente oficial?»

## Estado

MVP en desarrollo. Ya incluye:

- conexión MCP por STDIO;
- consultas al Campus exclusivamente por HTTP, usando la API REST de Moodle o sus endpoints AJAX
  autenticados con la cookie `MoodleSession`;
- login interactivo opcional con Playwright únicamente para iniciar la sesión Microsoft Entra/MFA
  y guardar la cookie en el almacén de credenciales del sistema;
- modo preferente mediante un token legítimo de Moodle Web Services;
- cursos, Timeline de pendientes, detalle de eventos y foros de avisos;
- lectura limitada de HTML/PDF oficiales bajo dominios USC, con URL y página como evidencia;
- validación de destinos, límites de descarga, redirecciones controladas y limpieza de HTML.

No entrega trabajos, no inicia cuestionarios, no cambia eventos y no modifica matrículas. Tampoco
consulta correo ni accede a Teams. La mensajería de Moodle se limita a una herramienta protegida:
genera una previsualización por defecto y solo envía tras una segunda llamada autorizada.

## Requisitos

- Windows, Linux o macOS;
- Python 3.11 o posterior;
- [`uv`](https://docs.astral.sh/uv/) (recomendado);
- una cuenta USC activa para los datos privados.

## Instalación

```powershell
git clone https://github.com/PabloPC05/mcp-usc.git
cd mcp-usc
uv sync --extra dev
```

Esto basta si vas a usar un token REST. Playwright no participa en las consultas. Instálalo solo si
necesitas el asistente de login para crear o renovar una sesión HTTP después de completar Microsoft
Entra/MFA:

```powershell
uv sync --extra dev --extra browser-auth
uv run playwright install chromium
```

El asistente puede usar Chromium administrado por Playwright o un Google Chrome/Microsoft Edge ya
instalado:

```powershell
$env:USC_BROWSER_CHANNEL = "chrome" # o "msedge"
```

## Acceso seguro al Campus

El conector elige automáticamente uno de estos transportes, por este orden:

1. API REST oficial si existe un `USC_MOODLE_TOKEN` legítimo.
2. HTTP autenticado con `MoodleSession` y llamadas AJAX *same-origin* si se ha iniciado sesión.

En ambos casos, `list_courses`, `list_pending_work`, `list_upcoming_events`, `get_work_item` y
`list_announcements` hacen peticiones HTTP directas. No mantienen ni automatizan un navegador.

### Opción preferente: token REST emitido por Moodle

Solo si Moodle te muestra un token legítimo de solo lectura (por ejemplo, en Preferencias →
Seguridad → Claves de seguridad), puedes establecerlo temporalmente en el entorno:

```powershell
$env:USC_MOODLE_TOKEN = "..."
uv run mcp-usc status
```

También puede leerse desde un archivo local mediante `USC_MOODLE_TOKEN_FILE`. Protege ese archivo y
no lo guardes dentro del repositorio. No uses la contraseña de la USC en `login/token.php` ni la
guardes en `.env`: que exista el endpoint REST no garantiza que la USC permita tokens al alumnado.

### Alternativa: bootstrap de una sesión HTTP

```powershell
uv run mcp-usc login
```

El comando abre temporalmente un navegador. Completa personalmente el acceso de Microsoft y el MFA;
el programa no recibe ni guarda tu contraseña. Cuando Moodle termina el acceso, extrae únicamente
la cookie `MoodleSession`, la guarda mediante `keyring` con la clave `moodle-session` en el almacén
seguro del sistema —Credential Manager en Windows— y cierra el navegador. Comprueba después:

```powershell
uv run mcp-usc status
```

La cookie `MoodleSession` equivale a una credencial mientras siga vigente. No la copies, imprimas,
publiques ni sincronices. No se escribe en `.env`, en logs ni en la configuración MCP. Cuando caduque,
vuelve a ejecutar `mcp-usc login`. El navegador no es necesario entre renovaciones. El `sesskey`
necesario para AJAX no se persiste: se obtiene mediante HTTP desde `/my/` para la sesión vigente.

## Fuentes públicas de exámenes

La USC no publica todas las fechas en una única página: cada centro fija su calendario. Configura
una o varias páginas canónicas de tu centro/titulación, separadas por punto y coma:

```powershell
$env:USC_EXAM_SOURCES = "https://www.usc.gal/gl/centro/MI_CENTRO/horarios/cursos;https://assets.usc.gal/ruta/calendario.pdf"
```

Estas búsquedas también usan HTTP directo. El conector solo acepta HTTPS bajo `usc.gal`/`usc.es`,
sigue como máximo cinco redirecciones y descarga como máximo 15 MB por documento. No realiza
crawling masivo: consulta las fuentes que configura o proporciona el usuario y sus enlaces de
examen/PDF inmediatos.

## Conectar con Codex

Desde PowerShell, en este equipo:

```powershell
codex mcp add usc-campus -- uv --directory C:\Users\pablo\mcp-usc run mcp-usc serve
codex mcp list
```

Para fijar las fuentes públicas en la configuración del servidor:

```powershell
codex mcp remove usc-campus
codex mcp add usc-campus --env USC_EXAM_SOURCES="https://www.usc.gal/gl/centro/MI_CENTRO/horarios/cursos" -- uv --directory C:\Users\pablo\mcp-usc run mcp-usc serve
```

Después hay que reiniciar el cliente local o abrir una sesión nueva para que cargue el servidor. La
configuración MCP es compartida por la app de ChatGPT, Codex CLI y la extensión IDE en el mismo
host, según la [documentación oficial de OpenAI](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

Para que toda escritura requiera aprobación del host, añade esta opción al bloque que acaba de crear
Codex en `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.usc-campus]
command = "uv"
args = ["--directory", 'C:\Users\pablo\mcp-usc', "run", "mcp-usc", "serve"]
default_tools_approval_mode = "writes"
```

La previsualización, el token de un solo uso y esta política son capas complementarias. Mantén la
aprobación de escrituras activada: las instrucciones y anotaciones MCP no sustituyen por sí solas
una confirmación humana del cliente.

## Herramientas MCP

- `auth_status`
- `list_courses`
- `list_pending_work`
- `list_upcoming_events`
- `get_work_item`
- `list_announcements`
- `search_message_contacts`
- `preview_message`
- `send_message`
- `list_exam_sources`
- `search_exam_dates`

Las respuestas normalizan horas a `Europe/Madrid`. Las fechas públicas incluyen siempre
`source_url`, página del PDF cuando procede y hora de consulta. Si existen fuentes discrepantes, el
modelo debe mostrar el conflicto y no elegir silenciosamente una fecha.

`preview_message` nunca envía nada: exige un destinatario devuelto por una búsqueda reciente y
muestra su nombre, ID y texto. Devuelve un `confirmation_token` aleatorio, válido durante cinco
minutos y ligado al destinatario y texto exactos. Solo `send_message`, marcado como escritura, puede
usar ese token una vez después de que el usuario confirme la vista previa. El conector no llama a
ningún endpoint de correo, aunque Moodle podría generar notificaciones externas según la
configuración del destinatario. Las pruebas usan HTTP simulado y nunca mandan mensajes a la USC.
Si una llamada de envío termina por timeout, el resultado puede ser desconocido; comprueba la
conversación antes de volver a enviarlo para no crear un duplicado.

En el modo de sesión, Moodle exige incluir su `sesskey` efímero en la URL de las llamadas AJAX. El
conector no lo registra ni lo devuelve, pero podría aparecer en los registros de acceso gestionados
por la propia plataforma. Usa un token REST si necesitas evitar esta limitación del protocolo AJAX.

## Pruebas

```powershell
uv run pytest
uv run ruff check .
```

Las pruebas no contienen sesiones ni datos reales. El acceso autenticado se valida únicamente de
forma manual y local. Los contratos HTTP se prueban con respuestas simuladas, sin contactar con la
USC, y el almacén de credenciales se sustituye por dobles de prueba.

## Trabajo previo reutilizado

Antes de implementar se revisaron proyectos existentes. Se reutilizaron sus patrones públicos y
compatibles —Moodle Timeline como fuente primaria, cliente REST, login SSO local, AJAX same-origin,
errores sin secretos y herramientas de mínimo privilegio—, sin incorporar proyectos sin licencia
ni herramientas administrativas:

- [`haolamnm/moodle-mcp-srv`](https://github.com/haolamnm/moodle-mcp-srv) (Apache-2.0): arquitectura,
  diagnóstico y cliente REST.
- [`Snaw80/moodle-mcp`](https://github.com/Snaw80/moodle-mcp) (MIT): login SSO y validación del flujo
  móvil. En la USC el endpoint móvil público devuelve 404, por lo que el MVP obtiene localmente una
  cookie de sesión y después usa HTTP directo.
- [`GhaithAlHallak8/moodler-mcp`](https://github.com/GhaithAlHallak8/moodler-mcp) (MIT): sesión Moodle
  y llamadas AJAX same-origin como fallback.
- [`1alexandrer/moodle-mcp`](https://github.com/1alexandrer/moodle-mcp) (MIT): herramientas orientadas
  al alumno y uso de eventos accionables.
- [`moodlehq/moodleapp`](https://github.com/moodlehq/moodleapp) (Apache-2.0): referencia oficial de
  compatibilidad con servicios Moodle.

`loyaniu/moodle-mcp` sirvió para comparar el alcance funcional, pero no se copió código porque el
repositorio no declara licencia.

## Límites conocidos

- La cookie de sesión obtenida después del acceso OIDC caduca y habrá que ejecutar de nuevo
  `mcp-usc login`; las consultas intermedias siguen siendo HTTP y no usan Playwright.
- Los endpoints AJAX y el HTML de Moodle/Drupal pueden cambiar; los adaptadores están aislados para
  poder actualizarlos.
- La USC advierte de que no todo el profesorado utiliza el Campus Virtual; correo y Teams pueden
  contener información adicional.
- Una fecha de Moodle puede ser una prueba de evaluación continua y una fecha pública puede ser un
  examen oficial. Se muestran como fuentes distintas.
