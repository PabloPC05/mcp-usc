# Primeros pasos

Esta guía lleva una instalación nueva desde el clon hasta la primera consulta. `mcp-usc` se ejecuta
en tu equipo y se comunica con el Campus por HTTP; el cliente MCP se comunica con él por STDIO.

## 1. Instalación rápida

Solo necesitas [`uv`](https://docs.astral.sh/uv/getting-started/installation/); él se encarga de
Python.

```sh
uv tool install "mcp-usc[browser-auth] @ https://github.com/PabloPC05/mcp-usc/archive/refs/heads/main.zip"
mcp-usc setup
```

El asistente `setup` hace en orden lo que el resto de esta guía explica paso a paso:

1. inicia sesión en el Campus con tu Chrome o Edge (o importa la cookie `MoodleSession`);
2. guarda tu grado y tu curso en un perfil local, para `get_my_class_timetable` y
   `get_my_official_exam_schedule`;
3. añade el servidor a Claude Desktop, Claude Code o Codex, preguntando antes de tocar cada uno.

Las búsquedas públicas de grados, planes, horarios y exámenes funcionan sin cuenta.

## 2. Instalación desde el código

Para desarrollar, o si prefieres trabajar desde un clon:

```sh
git clone https://github.com/PabloPC05/mcp-usc.git
cd mcp-usc
uv sync --extra dev --extra browser-auth
uv run mcp-usc doctor
uv run mcp-usc manifest --compact
```

`doctor` es completamente local. Informa de si hay un token o una sesión almacenados, pero nunca
muestra su valor y no contacta con el Campus. Un estado `public_only` es válido: significa que las
herramientas públicas están listas y falta configurar acceso privado.

`manifest` también es local y exporta el contrato exacto que verá un cliente: 92 herramientas,
cuatro recursos, cuatro prompts y un SHA-256 determinista. Sirve para diagnóstico y revisión sin
arrancar una conexión con Moodle.

Para ejecutar la suite completa:

```sh
uv run ruff check .
uv run pytest
```

Desde un clon, antepón `uv run` a los comandos `mcp-usc` de esta guía.

## 3. Autenticarse

Elige una de estas rutas. El servidor prefiere el token REST si ambas están configuradas.

### A. Reutilizar una sesión del navegador

Es la ruta habitual cuando la USC no ofrece al alumno un token de Web Services.

1. Inicia sesión personalmente en `https://cv.usc.es`.
2. Abre las herramientas de desarrollo del navegador.
3. Ve a **Aplicación/Almacenamiento > Cookies > https://cv.usc.es**.
4. Copia solo el valor de `MoodleSession`.
5. Ejecuta el prompt oculto y pega el valor:

```sh
mcp-usc import-session
mcp-usc status
```

La cookie se valida mediante HTTP antes de guardarse en el almacén seguro del sistema. No la pongas
en el comando, en `.env`, en una captura, en una incidencia ni en un archivo del repositorio.

Para borrar solo la copia local, sin cerrar ni modificar la sesión remota:

```sh
mcp-usc forget-session
```

### B. Login asistido con Microsoft/MFA

Esta opción abre temporalmente un navegador visible. Después del login, todas las consultas vuelven
a usar HTTP y el navegador se cierra.

```sh
mcp-usc login
mcp-usc status
```

Usa el Chrome o Edge instalado. Si no tienes ninguno (solo Safari o Firefox), te ofrece descargar
una vez un navegador solo para el login (WebKit en macOS, Chromium en el resto); sin preguntar,
`mcp-usc login --install-browser`.

Completa personalmente Microsoft Entra y MFA. El programa no recibe ni guarda tu contraseña.

### C. Token REST legítimo

Úsalo solo si Moodle/USC ha emitido un token para tu cuenta y servicio. La existencia de una función
en Moodle no garantiza que ese servicio la habilite.

```powershell
$env:USC_MOODLE_TOKEN = "..."
mcp-usc status
```

También puedes apuntar a un archivo local protegido con `USC_MOODLE_TOKEN_FILE`. No intentes obtener
un token enviando tu contraseña a `login/token.php` y nunca confirmes un token en Git.

## 4. Conectar un cliente MCP

`mcp-usc setup` lo hace por ti. A mano, `mcp-usc client-config` imprime el comando exacto para
Claude Code y Codex, y el bloque JSON para Claude Desktop y otros clientes, con la ruta absoluta de
tu instalación (las apps de escritorio no siempre ven tu `PATH`).

El perfil académico que guarda `setup` vive en la carpeta de configuración del usuario
(`perfil.json`). También puedes definirlo con variables, que tienen prioridad:
`USC_ACADEMIC_PROFILE_FILE`, o bien `USC_ACADEMIC_DEGREE_URL`, `USC_ACADEMIC_COURSE_NUMBER`,
`USC_ACADEMIC_PROGRAM_ID`, `USC_ACADEMIC_GROUP_CODES`, `USC_ACADEMIC_YEAR` y
`USC_ACADEMIC_SEMESTER`. Estos valores no son credenciales ni modifican la matrícula.
`USC_ACADEMIC_PROGRAM_ID` solo hace falta si la USC publica varios planes con el mismo nombre.

Reinicia el cliente después de cambiar su configuración. Si admite aprobación por tipo de
herramienta, configura las escrituras para que siempre pidan permiso (`writes`).

Para usar el servidor desde otro equipo o agente, sírvelo por HTTP: ver
[Servirlo por HTTP](deployment.md).

## 5. Primera comprobación

Empieza con peticiones que no escriben:

- «Explica qué puede hacer `mcp-usc` y qué no.»
- «Comprueba el estado de autenticación sin mostrar secretos.»
- «Lista mis asignaturas, incluidas las archivadas.»
- «Dime qué trabajos tengo pendientes en los próximos 14 días.»
- «Busca la página de mi titulación y consulta el horario oficial de segundo curso esta semana.»
- «Busca las fechas oficiales de mis exámenes para 2026/2027 y cita las fuentes.»

Si el cliente muestra prompts MCP, también puedes elegir **Resumen académico**, **Planificar
exámenes**, **Revisar tareas** o **Preparar una entrega**. El último flujo se detiene obligatoriamente
después del preview; seleccionarlo no confirma ni ejecuta una entrega.

Una operación con efecto debe aparecer siempre en dos pasos: primero una herramienta `preview_*` y,
tras una confirmación nueva sobre los parámetros exactos, la herramienta final. No aceptes una
escritura inesperada.

### Diagnóstico y renovación segura de MoodleSession

`mcp-usc status` no imprime cookies ni `sesskey`. Si Moodle devuelve una redirección de login,
un error 401/403 o incluso la pantalla de login con HTTP 200, el resultado de error incluye un
diagnóstico estable (`session_expired`/`renew_session`). Ejecuta `mcp-usc import-session` con la
entrada oculta de una cookie recién copiada, o `mcp-usc login` para completar el acceso visible y
MFA. Si el diagnóstico es `session_missing`, importa o inicia sesión; si es
`session_invalid_local`, ejecuta antes `mcp-usc forget-session`. El borrado solo elimina la
credencial local y no cierra la sesión remota.

La compatibilidad entre Moodle 4.5, 5.0 y 5.2 se prueba únicamente para el contrato común de la
página de preferencias (identidad y `sesskey`). La disponibilidad de funciones AJAX sigue siendo
dependiente del servidor: cuando una función no se anuncia, el transporte falla cerrado y no
intenta navegar ni emularla.

## 6. Subir archivos a una entrega

Las subidas están desactivadas hasta definir una carpeta permitida existente:

```powershell
$env:USC_UPLOAD_ROOT = "C:\Users\TU_USUARIO\Documents\mcp-usc-uploads"
$env:USC_MAX_UPLOAD_BYTES = "52428800"
mcp-usc doctor
```

Solo se aceptan archivos regulares dentro de esa carpeta. La previsualización presenta nombres,
tamaños y SHA-256 antes de solicitar confirmación.

## Problemas frecuentes

| Síntoma | Qué comprobar |
| --- | --- |
| `public_only` en `doctor` | Importa una sesión o configura un token si necesitas datos privados. |
| `status` indica sesión caducada | Repite `import-session` o `login`; no publiques la cookie. |
| Una función REST no está disponible | Revisa `list_student_capabilities(available_only=true)`; el servicio del token puede no exponerla. |
| Una función AJAX falla cerrada | La instalación Moodle no la declara segura para AJAX; el MCP no intenta simularla. |
| No se puede subir un archivo | Confirma que `USC_UPLOAD_ROOT` existe y que el archivo está dentro. |
| Una fecha oficial es ambigua | Conserva el curso académico, plan, centro y `source_url`; no elijas por similitud de nombre. |

Consulta el [inventario de herramientas](tools.md), la [arquitectura](architecture.md) y la
[política de seguridad](../SECURITY.md) para profundizar.
