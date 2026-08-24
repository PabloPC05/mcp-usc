## Confirmaciones y utilidades de seguridad en `src/mcp_usc/confirmations.py` y `security.py`

**Purpose:** Ligar una acción a parámetros exactos y limitar destinos/representación de contenido.

**Inputs & Assumptions:**
- `payload` debe ser JSON serializable para el digest canónico (`confirmations.py:L12-L19`).
- Tokens son cadenas entregadas por el cliente MCP; el almacén es memoria de proceso (`L29-L36`).
- URLs pueden venir de configuración o HTML remoto; `validate_usc_url` solo permite HTTPS, host permitido, puerto 443/none y no claves sensibles (`security.py:L21-L36`).

**Outputs & Effects:**
- `issue` purga expirados, genera token aleatorio y guarda acción/digest/expiry (`confirmations.py:L38-L53`).
- `consume` purga, elimina el token antes de comparar y rechaza inexistente, expirado, acción o digest distinto (`L55-L71`).
- `html_to_text` elimina tags de contenido activo y limita longitud (`security.py:L39-L48`).
- `redact_secret` reemplaza strings indicados en mensajes (`security.py:L51-L56`).

**Cross-Function Dependencies:**
- `UscService._issue_action_confirmation` y `_consume_action_confirmation` añaden `authenticated_user_id` antes del digest (`service.py:L278-L302`).
- `AcademicProfile`, `session_auth` y clientes públicos llaman a `validate_usc_url`.

**Open Questions:**
- No se encontró persistencia compartida ni namespace por cliente en `ACTION_CONFIRMATIONS`; el alcance observado es la instancia global del proceso (`confirmations.py:L82`).
- `redact_secret` depende de que el caller le suministre todos los secretos relevantes; no mantiene un inventario propio (`security.py:L51-L56`).
