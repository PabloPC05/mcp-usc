## `SessionImportError`, `import_session_cookie` y `forget_session_cookie` en `src/mcp_usc/session_auth.py` (L34-L236)

**Purpose:** Validar una cookie Moodle contra una página del Campus, guardar la cookie en `CredentialStore` y ofrecer eliminación local.

**Inputs & Assumptions:**
- `raw_cookie` es secreto local semi-trusted; `_validated_cookie` exige caracteres y 16–512 bytes lógicos (`L81-L90`).
- `Settings.moodle_url` se considera configuración local y se vuelve a validar como destino Campus (`L149-L153`).
- HTML, headers, redirect y JSON embebido son contenido remoto no confiable; los extractores aceptan solo patrones acotados (`L93-L128`).

**Outputs & Effects:**
- `import_session_cookie` hace un GET sin seguir redirects, valida estado/login/sesskey/usuario y escribe el valor (posiblemente rotado) en OS keyring (`L155-L208`). Devuelve `ImportedSession.as_dict` sin cookie/sesskey (`L209-L219`).
- `forget_session_cookie` borra solo `moodle-session`; no llama logout remoto (`L222-L236`).
- `SessionImportError.as_dict` expone código/acción/mensaje (`L37-L61`); la clasificación se deriva del texto del mensaje.

**Block-by-Block:**

- `L149-L165`: valida URL de preferencias y crea cliente HTTP con cookie, timeout y `follow_redirects=False`; errores HTTP se convierten en diagnóstico local.
- `L167-L186`: redirects a login/Microsoft, 401/403, HTTP >=400 y body >5 MiB detienen almacenamiento.
- `L188-L195`: parsea HTML, rechaza login, exige sesskey e identidad positiva.
- `L197-L219`: acepta una cookie rotada solo si pasa el mismo formato; `CredentialStore.set` es una dependencia externa y cualquier excepción se traduce a `SessionImportError`.
- `L225-L236`: eliminación local y estado explícito `remote_session_unchanged=True`.

**Cross-Function Dependencies:**
- Callees: `validate_usc_url`, `html_to_text`, `CredentialStore`, `httpx`, BeautifulSoup (`L17-L19`).
- Callers: CLI/flujo de login y el gateway HTTP consumen la misma entrada `SESSION_CREDENTIAL_NAME` (`campus.py:L739-L748`, `L1696-L1701`).
- Shared state: keyring del sistema; no hay cookie en el retorno.

**Open Questions:**
- No se encontró en estos archivos una verificación criptográfica independiente de que el `sesskey` y `userid` extraídos pertenezcan al mismo usuario más allá de la respuesta HTML recibida (`L188-L195`).
