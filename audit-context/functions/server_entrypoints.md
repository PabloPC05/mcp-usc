## Entrypoints MCP de `src/mcp_usc/server.py` (L84-L1180; cambios L274-L304, L884-L931)

**Purpose:** Registrar herramientas MCP, clasificar sus efectos y delegar sin implementar lógica de dominio en el entrypoint.

**Inputs & Assumptions:**
- Argumentos llegan del cliente MCP y el framework aplica los decoradores `READ_ONLY`, `PREVIEW`, `WRITE` o `STATEFUL_READ` (`server.py:L274-L304`, `L884-L931`).
- `_service()` devuelve la instancia singleton lazy creada en `_process_public_cache`/`UscService` (`server.py:L84-L100`).
- Las instrucciones del servidor describen límites de lectura/escritura y procedencia (`server.py:L46-L57`).

**Outputs & Effects:**
- Wrappers de finalización delegan a `UscService` con anotación preview o write (`L274-L304`).
- Wrappers de horarios delegan discovery, selección explícita o perfil local; todos son `READ_ONLY` (`L884-L931`).
- El resto de tools sigue el mismo patrón wrapper → service; `run()` inicia stdio (`L1179-L1180`).

**Block-by-Block:**

- `L46-L57`: instrucciones son contexto para el modelo/cliente, no una comprobación ejecutable dentro de cada wrapper.
- `L274-L304`: la firma write exige `confirmation_token`; la preview no lo acepta.
- `L884-L914`: el caller puede proporcionar URL, curso, año, grupos y `program_id`; service/client valida y conserva fuentes.
- `L918-L931`: el perfil local se resuelve dentro del service, sin pedir identificadores por la herramienta.

**Cross-Function Dependencies:**
- Callers: cliente MCP/transport de FastMCP.
- Callees: `UscService` y sus gateways; decoradores FastMCP son dependencia externa.
- Shared state: `_SERVICE`/cache pública y confirmaciones en el service.

**Open Questions:**
- No se encontró en el cuerpo de estos wrappers una autenticación independiente del framework MCP; la autenticación Campus ocurre en el gateway invocado por service.
- La anotación de efectos es metadato del tool; la garantía efectiva depende de que service mantenga el flujo correspondiente.
