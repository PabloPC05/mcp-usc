## `AcademicProfile` y cargadores en `src/mcp_usc/academic_profile.py` (L31-L318)

**Purpose:** Representar una selección académica local para consultar horarios públicos; no guarda credenciales ni escribe en USC (`L1-L5`).

**Inputs & Assumptions:**
- URL, curso, plan, grupos, año, semestre y fecha llegan de variables `USC_*` o JSON local (`L217-L316`): semi-trusted, bajo control del proceso local.
- `_degree_url` confía en `validate_usc_url` para host/esquema y añade una gramática de path USC sin query/fragmento (`L35-L58`).
- `normalise_academic_year` es un callee externo al archivo; su aceptación exacta se toma como contrato (`L152-L159`, `L184-L189`).

**Outputs & Effects:**
- `__post_init__` normaliza y valida en una dataclass congelada (`L141-L168`).
- `resolve` elige override explícito, perfil y defaults derivados de la fecha local (`L170-L200`).
- `public_dict` expone solo selección y marca `read_only` (`L202-L214`).
- `load_academic_profile` lee como máximo 64 KiB, acepta aliases de archivo/entorno y devuelve `None` si no hay valores (`L217-L318`). Efecto externo: lectura local únicamente.

**Block-by-Block:**

- `L35-L58`: valida host/path oficial de titulación, URL HTTPS y caracteres/rutas codificadas. Establece `degree_url` apta para el cliente público.
- `L61-L100`: valida enteros, grupos y cardinalidades. Supone que conversiones `int` son la representación de configuración deseada (`L61-L70`).
- `L102-L125`: valida fecha y deriva año/semestre según `date.today()` (`L102-L125`). La fecha del sistema es entrada implícita.
- `L141-L168`: normaliza todos los campos al construir el perfil; cualquier excepción de tipo queda en `AcademicProfileError` solo en `load_academic_profile` (`L315-L318`).
- `L170-L200`: overrides de llamada no mutan el perfil; la fecha `today` permite pruebas y default real si falta.
- `L217-L233`: `Path.expanduser`, `stat`, lectura UTF-8 y `json.loads`; presupone que el path local apunta al archivo elegido por el operador.
- `L244-L318`: combina archivo y entorno, rechaza claves desconocidas y exige `degree_url`/`course_number`; entorno tiene precedencia por asignación (`L293-L296`).

**Cross-Function Dependencies:**
- Callees: `validate_usc_url` (`security.py:L21-L36`), `normalise_academic_year` y `Path`/JSON estándar.
- Callers: `Settings.from_env` (`settings.py:L75-L91`), `UscService.get_my_class_timetable` (`service.py:L1236-L1258`).
- Shared state: ninguno; el objeto es inmutable, aunque la configuración se recarga al crear `Settings`.

**Open Questions:**
- No se encontró validación adicional que confirme que el `program_id` o grupos seleccionados existen en el índice público; esa comprobación queda al cliente de horarios (`L147-L150`, `service.py:L1247-L1258`).
