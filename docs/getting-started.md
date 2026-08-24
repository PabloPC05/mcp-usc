# Primeros pasos

Esta guía lleva una instalación nueva desde el clon hasta la primera consulta. `mcp-usc` se ejecuta
en tu equipo y se comunica con el Campus por HTTP; el cliente MCP se comunica con él por STDIO.

## 1. Requisitos

- Windows, Linux o macOS;
- Python 3.11 o posterior;
- [`uv`](https://docs.astral.sh/uv/) recomendado;
- una cuenta USC activa para consultar datos privados.

Las búsquedas públicas de grados, planes de estudio y exámenes oficiales no necesitan una cuenta.

## 2. Instalar

```powershell
git clone https://github.com/PabloPC05/mcp-usc.git
cd mcp-usc
uv sync
uv run mcp-usc doctor
```

`doctor` es completamente local. Informa de si hay un token o una sesión almacenados, pero nunca
muestra su valor y no contacta con el Campus. Un estado `public_only` es válido: significa que las
herramientas públicas están listas y falta configurar acceso privado.

Para desarrollar o ejecutar la suite completa:

```powershell
uv sync --extra dev
uv run ruff check .
uv run pytest
```

## 3. Autenticarse

Elige una de estas rutas. El servidor prefiere el token REST si ambas están configuradas.

### A. Reutilizar una sesión del navegador

Es la ruta habitual cuando la USC no ofrece al alumno un token de Web Services.

1. Inicia sesión personalmente en `https://cv.usc.es`.
2. Abre las herramientas de desarrollo del navegador.
3. Ve a **Aplicación/Almacenamiento > Cookies > https://cv.usc.es**.
4. Copia solo el valor de `MoodleSession`.
5. Ejecuta el prompt oculto y pega el valor:

```powershell
uv run mcp-usc import-session
uv run mcp-usc status
```

La cookie se valida mediante HTTP antes de guardarse en el almacén seguro del sistema. No la pongas
en el comando, en `.env`, en una captura, en una incidencia ni en un archivo del repositorio.

Para borrar solo la copia local, sin cerrar ni modificar la sesión remota:

```powershell
uv run mcp-usc forget-session
```

### B. Login asistido con Microsoft/MFA

Esta opción abre temporalmente un navegador visible. Después del login, todas las consultas vuelven
a usar HTTP y el navegador se cierra.

```powershell
uv sync --extra browser-auth
uv run playwright install chromium
uv run mcp-usc login
uv run mcp-usc status
```

Completa personalmente Microsoft Entra y MFA. El programa no recibe ni guarda tu contraseña.

### C. Token REST legítimo

Úsalo solo si Moodle/USC ha emitido un token para tu cuenta y servicio. La existencia de una función
en Moodle no garantiza que ese servicio la habilite.

```powershell
$env:USC_MOODLE_TOKEN = "..."
uv run mcp-usc status
```

También puedes apuntar a un archivo local protegido con `USC_MOODLE_TOKEN_FILE`. No intentes obtener
un token enviando tu contraseña a `login/token.php` y nunca confirmes un token en Git.

## 4. Conectar un cliente MCP

Con Codex CLI en Windows, sustituye la ruta por la del clon:

```powershell
codex mcp add usc-campus -- uv --directory C:\ruta\absoluta\mcp-usc run mcp-usc serve
codex mcp list
```

La configuración STDIO equivalente para otros clientes compatibles es:

```json
{
  "mcpServers": {
    "usc-campus": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\ruta\\absoluta\\mcp-usc",
        "run",
        "mcp-usc",
        "serve"
      ]
    }
  }
}
```

Reinicia el cliente después de cambiar su configuración. Si admite aprobación por tipo de
herramienta, configura las escrituras para que siempre pidan permiso (`writes`).

## 5. Primera comprobación

Empieza con peticiones que no escriben:

- «Explica qué puede hacer `mcp-usc` y qué no.»
- «Comprueba el estado de autenticación sin mostrar secretos.»
- «Lista mis asignaturas, incluidas las archivadas.»
- «Dime qué trabajos tengo pendientes en los próximos 14 días.»
- «Busca las fechas oficiales de mis exámenes para 2026/2027 y cita las fuentes.»

Una operación con efecto debe aparecer siempre en dos pasos: primero una herramienta `preview_*` y,
tras una confirmación nueva sobre los parámetros exactos, la herramienta final. No aceptes una
escritura inesperada.

## 6. Subir archivos a una entrega

Las subidas están desactivadas hasta definir una carpeta permitida existente:

```powershell
$env:USC_UPLOAD_ROOT = "C:\Users\TU_USUARIO\Documents\mcp-usc-uploads"
$env:USC_MAX_UPLOAD_BYTES = "52428800"
uv run mcp-usc doctor
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
