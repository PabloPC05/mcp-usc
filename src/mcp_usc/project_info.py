from __future__ import annotations

from . import __version__

PROJECT_URL = "https://github.com/PabloPC05/mcp-usc"

TOOL_INVENTORY = {
    "total": 84,
    "read_only": 46,
    "previews": 19,
    "effects": 18,
    "stateful_reads": 1,
}

RESOURCE_URIS = {
    "about": "usc://about",
    "safety": "usc://safety",
    "compatibility": "usc://compatibility",
    "workflows": "usc://workflows",
}

PROMPT_NAMES = (
    "daily_briefing",
    "exam_planning",
    "assignment_review",
    "prepare_assignment_submission",
)

CAPABILITY_TOOL_GROUPS = {
    "server_and_catalog": (
        "describe_mcp_usc",
        "auth_status",
        "list_student_capabilities",
        "call_student_read",
        "preview_student_action",
        "execute_student_action",
    ),
    "profile_and_progress": (
        "get_my_profile",
        "get_my_preferences",
        "list_course_participants",
        "list_my_groups",
        "get_my_grades",
        "get_my_completion",
        "list_notifications",
        "list_my_badges",
        "get_private_files_info",
    ),
    "courses_and_calendar": (
        "list_courses",
        "list_pending_work",
        "list_upcoming_events",
        "get_work_item",
        "list_announcements",
        "list_calendar_events",
        "preview_create_personal_calendar_event",
        "create_personal_calendar_event",
        "preview_delete_personal_calendar_event",
        "delete_personal_calendar_event",
    ),
    "collaboration": (
        "list_conversations",
        "list_messages",
        "list_conversation_messages",
        "search_message_contacts",
        "preview_message",
        "send_message",
        "list_forums",
        "list_forum_discussions",
        "list_discussion_posts",
        "preview_inspect_discussion_posts",
        "inspect_discussion_posts",
        "preview_create_forum_discussion",
        "create_forum_discussion",
        "preview_reply_forum_post",
        "reply_forum_post",
        "preview_submit_choice_response",
        "submit_choice_response",
        "preview_cancel_choice_response",
        "cancel_choice_response",
    ),
    "materials_and_exams": (
        "list_course_contents",
        "list_course_resources",
        "read_course_resource",
        "list_exam_sources",
        "search_exam_dates",
        "list_usc_degrees",
        "locate_usc_subject_codes",
        "list_official_exam_degrees",
        "list_official_exam_subjects",
        "get_official_exam_dates",
        "get_my_official_exam_schedule",
    ),
    "assignments": (
        "list_assignments",
        "get_submission_status",
        "preview_inspect_submission_status",
        "inspect_submission_status",
        "check_submission_reopen",
        "preview_save_online_submission",
        "save_online_submission",
        "preview_replace_submission_files",
        "replace_submission_files",
        "preview_delete_submission_files",
        "delete_submission_files",
        "preview_submit_assignment",
        "submit_assignment",
        "preview_remove_submission",
        "remove_submission",
    ),
    "quizzes": (
        "list_quizzes",
        "list_quiz_attempts",
        "get_quiz_attempt_page",
        "get_quiz_attempt_summary",
        "get_quiz_attempt_review",
        "get_quiz_best_grade",
        "preview_inspect_quiz_attempt",
        "inspect_quiz_attempt",
        "preview_start_quiz",
        "start_quiz",
        "preview_save_quiz_answers",
        "save_quiz_answers",
        "preview_finish_quiz",
        "finish_quiz",
    ),
}


def project_overview() -> dict[str, object]:
    """Return a stable, network-free description for MCP clients and people."""

    return {
        "name": "mcp-usc",
        "version": __version__,
        "status": "community_preview",
        "purpose": (
            "Conectar asistentes MCP con el Campus Virtual Moodle y las fuentes "
            "académicas públicas de la Universidade de Santiago de Compostela."
        ),
        "audience": "Alumnado de la USC que ejecuta el servidor localmente con su propia cuenta.",
        "example_requests": [
            "¿Qué trabajos tengo pendientes esta semana?",
            "Resume los avisos recientes de mis asignaturas.",
            "¿Cuándo son mis exámenes oficiales de este curso?",
            "Enséñame el estado de una entrega sin modificarla.",
        ],
        "capability_groups": [
            "cursos, agenda, avisos y progreso",
            "mensajes, conversaciones, foros y actividades Choice",
            "materiales y recursos descargables",
            "tareas, borradores, archivos y entregas",
            "cuestionarios e intentos propios",
            "grados, planes de estudio y exámenes oficiales USC",
        ],
        "tool_groups": {name: list(tools) for name, tools in CAPABILITY_TOOL_GROUPS.items()},
        "tool_inventory": dict(TOOL_INVENTORY),
        "mcp_surface": {
            "tools": TOOL_INVENTORY["total"],
            "resources": dict(RESOURCE_URIS),
            "prompts": list(PROMPT_NAMES),
        },
        "transport": {
            "mcp": "STDIO local",
            "campus": "HTTP-first: REST oficial o MoodleSession/AJAX same-origin",
            "browser": "Opcional y solo para el bootstrap interactivo de Microsoft/MFA",
        },
        "safety": {
            "read_first": True,
            "writes_require_preview": True,
            "writes_require_one_use_confirmation": True,
            "host_approval_recommended": "writes",
            "remote_content_is_untrusted": True,
            "credentials_are_never_returned": True,
        },
        "boundaries": [
            "No consulta correo, Microsoft Teams ni sistemas ajenos al Campus/USC.",
            "No eleva privilegios y solo puede hacer lo permitido a la cuenta autenticada.",
            "La disponibilidad real depende de la configuración y versión de Moodle.",
            "No acepta políticas ni consentimientos legales en nombre del usuario.",
            "Es un proyecto comunitario independiente, no un servicio oficial de la USC.",
        ],
        "documentation": {
            "repository": PROJECT_URL,
            "getting_started": f"{PROJECT_URL}/blob/main/docs/getting-started.md",
            "tools": f"{PROJECT_URL}/blob/main/docs/tools.md",
            "architecture": f"{PROJECT_URL}/blob/main/docs/architecture.md",
            "mcp_surface": f"{PROJECT_URL}/blob/main/docs/mcp-surface.md",
            "compatibility": f"{PROJECT_URL}/blob/main/docs/compatibility.md",
            "security": f"{PROJECT_URL}/blob/main/SECURITY.md",
        },
        "network_contacted": False,
    }
