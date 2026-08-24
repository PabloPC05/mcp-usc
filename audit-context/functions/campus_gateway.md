## `AuthenticationRequired`, respuesta HTTP y gateway de sesión en `src/mcp_usc/campus.py`

**Purpose:** Seleccionar transporte REST/sesión y mantener el contrato HTTP de autenticación, contexto, AJAX y descarga.

**Inputs & Assumptions:**
- `AuthenticationRequired` recibe mensajes internos y publica `code/action` sin secretos (`L62-L83`). El mapeo especial depende de prefijos/texto (`L74-L77`).
- La cookie procede de `CredentialStore` o cache de proceso y pasa regex local (`L739-L748`).
- URLs son derivadas de `base_url` y se vuelven a validar como Campus (`L750-L754`, `L985-L1010`).

**Outputs & Effects:**
- `_ensure_authenticated_response` distingue auth/login HTML, redirects inesperados y HTTP >=400 (`L795-L835`).
- `_session_context` hace una lectura de preferencias, extrae sesskey/usuario/nombres y cachea el contexto por instancia (`L848-L876`).
- `_ajax` envía un lote de un método al endpoint `lib/ajax/service.php`, limita respuesta y traduce envelope/error (`L878-L919`).
- `fetch_file` sigue hasta seis redirects solo si continúan en `/pluginfile.php`, limita bytes y devuelve contenido/media/final URL (`L931-L983`).
- `require_functions` rechaza conjuntos de funciones conocidos para sesión HTML/AJAX (`L1160-L1172`).

**Block-by-Block:**

- `L767-L777`: rota cookie en memoria y best-effort keyring; el contexto de proceso sigue operativo aunque falle ese guardado.
- `L779-L793`: reconoce 401/403 y redirects de login/logout/Entra; otros redirects llegan a protocolo inesperado (`L832-L835`).
- `L808-L831`: inspecciona HTML <=5 MiB y solo considera login un formulario/patrones explícitos; el body puede ser el body ya limitado de streaming (`L811-L825`).
- `L878-L919`: depende del sesskey cacheado, de la cookie actual y del esquema de envelope JSON de Moodle.
- `L946-L974`: el flujo de archivo valida cada redirect antes de continuar y repite la comprobación auth después de leer bytes.

**Cross-Function Dependencies:**
- Callers: `UscService._campus`, `call_student_read`, acciones contextuales, `session_course_state` y clientes de formularios.
- Callees: `CredentialStore`, `httpx`, BeautifulSoup, `_read_limited_response`, `_moodle_error` y `validate_usc_url`.
- Shared state: `_context` y `_cookie_value` por instancia; keyring compartido por proceso/usuario.

**Open Questions:**
- La semántica exacta de cada función AJAX y de la rotación de cookie la determina el Campus; no hay servidor Moodle local en el alcance.
- No se encontró invalidación explícita de `_context` al detectar una respuesta de auth posterior; el ciclo de vida de la instancia del gateway queda a cargo de `UscService` (`service.py:L383-L385`).
