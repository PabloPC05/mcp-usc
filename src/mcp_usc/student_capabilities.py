from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .security import html_to_text

AccessKind = Literal["read", "action"]

MAX_ARGUMENT_NODES = 1_000
MAX_ARGUMENT_DEPTH = 8
MAX_ARGUMENT_STRING = 100_000
MAX_ARGUMENT_BYTES = 1_000_000
MAX_RESULT_BYTES = 2_000_000

_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "chatsid",
        "client_secret",
        "clientsecret",
        "cookie",
        "checksum",
        "guestpassword",
        "moodlesession",
        "enrolmentkey",
        "moodlewsrestformat",
        "password",
        "secret",
        "sesskey",
        "token",
        "wsfunction",
        "wstoken",
    }
)
_SENSITIVE_OUTPUT_QUERY = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "checksum",
        "client_secret",
        "enrolmentkey",
        "key",
        "password",
        "sesskey",
        "signature",
        "token",
        "wstoken",
    }
)
_SENSITIVE_OUTPUT_FIELDS = frozenset(
    {
        "instructorcustomparameters",
        "resourcekey",
        "servicesalt",
    }
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_QUERY_SECRET = re.compile(
    r"(?i)\b(access_token|api_?key|auth|checksum|client_secret|enrolmentkey|key|password|"
    r"sesskey|signature|token|wstoken)="
    r"([^&\s]+)"
)


@dataclass(frozen=True, slots=True)
class StudentCapability:
    function: str
    category: str
    description: str
    access: AccessKind
    consequence: str = ""
    destructive: bool = False


def _read(category: str, description: str, *functions: str) -> list[StudentCapability]:
    return [StudentCapability(name, category, description, "read") for name in functions]


def _action(
    category: str,
    description: str,
    consequence: str,
    *functions: str,
    destructive: bool = False,
) -> list[StudentCapability]:
    return [
        StudentCapability(name, category, description, "action", consequence, destructive)
        for name in functions
    ]


_CAPABILITIES = [
    *_read(
        "account",
        "Perfil, preferencias y datos propios visibles.",
        "core_user_get_users_by_field",
        "core_user_get_course_user_profiles",
        "core_user_get_user_preferences",
        "core_user_get_private_files_info",
        "core_ai_get_policy_status",
        "core_badges_get_badge",
        "core_badges_get_user_badges",
        "core_blog_get_access_information",
        "core_blog_get_entries",
    ),
    *_read(
        "courses",
        "Cursos, secciones, participantes, grupos, bloques y actividad reciente.",
        "core_course_get_categories",
        "core_course_get_contents",
        "core_course_get_course_module",
        "core_course_get_course_module_by_instance",
        "core_course_get_courses_by_field",
        "core_course_search_courses",
        "core_course_get_user_navigation_options",
        "core_course_get_user_administration_options",
        "core_course_check_updates",
        "core_course_get_updates_since",
        "core_course_get_enrolled_courses_by_timeline_classification",
        "core_course_get_enrolled_courses_with_action_events_by_timeline_classification",
        "core_course_get_recent_courses",
        "core_course_get_enrolled_users_by_cmid",
        "core_course_get_course_content_items",
        "core_courseformat_get_overview_information",
        "core_enrol_get_course_enrolment_methods",
        "core_enrol_get_enrolled_users",
        "core_enrol_get_users_courses",
        "enrol_self_get_instance_info",
        "core_group_get_activity_allowed_groups",
        "core_group_get_activity_groupmode",
        "core_group_get_course_user_groups",
        "core_block_get_course_blocks",
        "core_block_get_dashboard_blocks",
        "block_recentlyaccesseditems_get_recent_items",
        "block_starredcourses_get_starred_courses",
    ),
    *_read(
        "grades_and_completion",
        "Calificaciones, feedback y progreso del alumno.",
        "gradereport_overview_get_course_grades",
        "gradereport_user_get_grades_table",
        "gradereport_user_get_grade_items",
        "gradereport_user_get_access_information",
        "core_completion_get_activities_completion_status",
        "core_completion_get_course_completion_status",
    ),
    *_read(
        "calendar",
        "Vistas, eventos, permisos y tipos del calendario.",
        "core_calendar_get_calendar_monthly_view",
        "core_calendar_get_calendar_day_view",
        "core_calendar_get_calendar_upcoming_view",
        "core_calendar_get_calendar_events",
        "core_calendar_get_action_events_by_timesort",
        "core_calendar_get_action_events_by_course",
        "core_calendar_get_action_events_by_courses",
        "core_calendar_get_calendar_event_by_id",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "core_calendar_get_timestamps",
    ),
    *_read(
        "messages_and_notifications",
        "Conversaciones, contactos, preferencias y notificaciones sin marcarlas como leídas.",
        "core_message_get_contact_requests",
        "core_message_get_received_contact_requests_count",
        "core_message_get_blocked_users",
        "core_message_get_user_contacts",
        "core_message_get_conversation",
        "core_message_get_conversation_between_users",
        "core_message_get_messages",
        "core_message_get_unread_conversation_counts",
        "core_message_get_conversation_members",
        "core_message_get_member_info",
        "core_message_get_unread_conversations_count",
        "core_message_get_user_notification_preferences",
        "core_message_get_user_message_preferences",
        "core_message_get_unread_notification_count",
        "core_message_get_unsent_message",
        "message_popup_get_popup_notifications",
        "message_popup_get_unread_popup_notification_count",
    ),
    *_read(
        "search_and_files",
        "Búsqueda global, etiquetas, valoraciones, comentarios y archivos visibles.",
        "core_files_get_files",
        "core_search_get_relevant_users",
        "core_search_get_results",
        "core_search_get_search_areas_list",
        "core_search_get_top_results",
        "core_tag_get_tagindex",
        "core_tag_get_tags",
        "core_tag_get_tagindex_per_area",
        "core_tag_get_tag_areas",
        "core_tag_get_tag_collections",
        "core_tag_get_tag_cloud",
        "core_comment_get_comments",
        "core_rating_get_item_ratings",
        "core_xapi_get_state",
        "core_xapi_get_states",
    ),
    *_read(
        "competencies",
        "Planes y competencias visibles para el alumno.",
        "core_competency_read_competency",
        "core_competency_list_course_module_competencies",
        "core_competency_list_course_competencies",
        "core_competency_read_plan",
        "core_competency_list_user_plans",
        "core_competency_list_plan_competencies",
        "core_competency_read_user_evidence",
        "tool_lp_data_for_course_competencies_page",
        "tool_lp_data_for_plans_page",
        "tool_lp_data_for_plan_page",
        "tool_lp_data_for_user_evidence_list_page",
        "tool_lp_data_for_user_evidence_page",
        "tool_lp_data_for_user_competency_summary",
        "tool_lp_data_for_user_competency_summary_in_plan",
        "tool_lp_data_for_user_competency_summary_in_course",
        "report_competency_data_for_report",
        "tool_policy_get_policy_version",
        "tool_policy_get_user_acceptances",
        "tool_dataprivacy_get_access_information",
        "tool_dataprivacy_get_data_requests",
    ),
    *_read(
        "activities",
        "Datos de actividades estándar visibles para el alumno.",
        "mod_book_get_books_by_courses",
        "mod_bigbluebuttonbn_can_join",
        "mod_bigbluebuttonbn_get_recordings",
        "mod_bigbluebuttonbn_get_bigbluebuttonbns_by_courses",
        "mod_chat_get_chats_by_courses",
        "mod_chat_get_sessions",
        "mod_chat_get_session_messages",
        "mod_choice_get_choice_results",
        "mod_choice_get_choice_options",
        "mod_choice_get_choices_by_courses",
        "mod_data_get_databases_by_courses",
        "mod_data_get_data_access_information",
        "mod_data_get_entries",
        "mod_data_get_entry",
        "mod_data_get_fields",
        "mod_data_search_entries",
        "mod_feedback_get_feedbacks_by_courses",
        "mod_feedback_get_feedback_access_information",
        "mod_feedback_get_current_completed_tmp",
        "mod_feedback_get_items",
        "mod_feedback_get_page_items",
        "mod_feedback_get_unfinished_responses",
        "mod_feedback_get_finished_responses",
        "mod_feedback_get_last_completed",
        "mod_folder_get_folders_by_courses",
        "mod_forum_get_forum_access_information",
        "mod_forum_can_add_discussion",
        "mod_glossary_get_glossaries_by_courses",
        "mod_glossary_get_entries_by_letter",
        "mod_glossary_get_entries_by_date",
        "mod_glossary_get_categories",
        "mod_glossary_get_entries_by_category",
        "mod_glossary_get_authors",
        "mod_glossary_get_entries_by_author",
        "mod_glossary_get_entries_by_author_id",
        "mod_glossary_get_entries_by_search",
        "mod_glossary_get_entries_by_term",
        "mod_glossary_get_entry_by_id",
        "mod_h5pactivity_get_h5pactivity_access_information",
        "mod_h5pactivity_get_attempts",
        "mod_h5pactivity_get_results",
        "mod_h5pactivity_get_h5pactivities_by_courses",
        "mod_imscp_get_imscps_by_courses",
        "mod_label_get_labels_by_courses",
        "mod_lesson_get_lessons_by_courses",
        "mod_lesson_get_lesson_access_information",
        "mod_lesson_get_questions_attempts",
        "mod_lesson_get_user_grade",
        "mod_lesson_get_user_attempt_grade",
        "mod_lesson_get_content_pages_viewed",
        "mod_lesson_get_user_timers",
        "mod_lesson_get_pages",
        "mod_lesson_get_pages_possible_jumps",
        "mod_lesson_get_lesson",
        "mod_lti_get_ltis_by_courses",
        "mod_page_get_pages_by_courses",
        "mod_quiz_get_attempt_review",
        "mod_quiz_get_user_best_grade",
        "mod_quiz_get_combined_review_options",
        "mod_quiz_get_quiz_access_information",
        "mod_quiz_get_quiz_feedback_for_grade",
        "mod_quiz_get_user_quiz_attempts",
        "mod_resource_get_resources_by_courses",
        "mod_scorm_get_scorm_attempt_count",
        "mod_scorm_get_scorm_scoes",
        "mod_scorm_get_scorm_user_data",
        "mod_scorm_get_scorm_sco_tracks",
        "mod_scorm_get_scorms_by_courses",
        "mod_scorm_get_scorm_access_information",
        "mod_survey_get_surveys_by_courses",
        "mod_survey_get_questions",
        "mod_url_get_urls_by_courses",
        "mod_wiki_get_wikis_by_courses",
        "mod_wiki_get_subwikis",
        "mod_wiki_get_subwiki_pages",
        "mod_wiki_get_subwiki_files",
        "mod_wiki_get_page_contents",
        "mod_workshop_get_workshops_by_courses",
        "mod_workshop_get_workshop_access_information",
        "mod_workshop_get_user_plan",
        "mod_workshop_get_submissions",
        "mod_workshop_get_submission",
        "mod_workshop_get_submission_assessments",
        "mod_workshop_get_assessment",
        "mod_workshop_get_assessment_form_definition",
        "mod_workshop_get_reviewer_assessments",
        "mod_workshop_get_grades",
    ),
    *_action(
        "account",
        "Cambiar preferencias o entradas del blog propias.",
        "Modifica datos o preferencias de la cuenta.",
        "core_user_update_user_preferences",
        "core_blog_add_entry",
        "core_blog_update_entry",
    ),
    *_action(
        "account",
        "Eliminar una entrada propia del blog.",
        "Elimina contenido del blog y puede ser irreversible.",
        "core_blog_delete_entry",
        destructive=True,
    ),
    *_action(
        "courses",
        "Marcar o desmarcar cursos y actividades como favoritos.",
        "Cambia la organización personal del panel.",
        "core_course_set_favourite_courses",
    ),
    *_action(
        "courses",
        "Autoinscribirse en un curso abierto.",
        "Crea una matrícula para la cuenta autenticada; las claves secretas no se admiten por MCP.",
        "enrol_self_enrol_user",
    ),
    *_action(
        "courses",
        "Cancelar una matrícula propia cuando Moodle lo permita.",
        "Puede retirar el acceso al curso y a su contenido.",
        "core_enrol_unenrol_user_enrolment",
        destructive=True,
    ),
    *_action(
        "grades_and_completion",
        "Actualizar finalización manual o completar el curso por autoevaluación.",
        "Cambia el progreso registrado y puede activar reglas de finalización.",
        "core_completion_update_activity_completion_status_manually",
        "core_completion_mark_course_self_completed",
    ),
    *_action(
        "calendar",
        "Crear o mover eventos propios del calendario.",
        "Modifica el calendario personal o eventos que los permisos permitan editar.",
        "core_calendar_create_calendar_events",
        "core_calendar_update_event_start_day",
        "core_calendar_submit_create_update_form",
    ),
    *_action(
        "calendar",
        "Eliminar eventos o suscripciones del calendario.",
        "Elimina información del calendario y puede ser irreversible.",
        "core_calendar_delete_calendar_events",
        "core_calendar_delete_subscription",
        destructive=True,
    ),
    *_action(
        "messages_and_notifications",
        "Cambiar lectura, favoritos, silencio, contactos o bloqueos.",
        "Cambia el estado de mensajería y notificaciones de la cuenta.",
        "core_message_mute_conversations",
        "core_message_unmute_conversations",
        "core_message_block_user",
        "core_message_unblock_user",
        "core_message_create_contact_request",
        "core_message_confirm_contact_request",
        "core_message_decline_contact_request",
        "core_message_mark_all_notifications_as_read",
        "core_message_mark_all_conversation_messages_as_read",
        "core_message_mark_message_read",
        "core_message_mark_notification_read",
        "core_message_set_favourite_conversations",
        "core_message_unset_favourite_conversations",
        "core_message_set_unsent_message",
    ),
    *_action(
        "messages_and_notifications",
        "Eliminar contactos, conversaciones o mensajes propios.",
        "Oculta o elimina datos de mensajería y puede ser irreversible.",
        "core_message_delete_contacts",
        "core_message_delete_conversations_by_id",
        "core_message_delete_message",
        destructive=True,
    ),
    *_action(
        "forums",
        "Crear o editar discusiones y respuestas de foro.",
        "Publica contenido visible para otras personas del curso.",
        "mod_forum_add_discussion",
        "mod_forum_add_discussion_post",
        "mod_forum_update_discussion_post",
    ),
    *_action(
        "forums",
        "Cambiar suscripción o favorito de una discusión.",
        "Cambia preferencias del foro y puede generar o detener notificaciones.",
        "mod_forum_set_subscription_state",
        "mod_forum_toggle_favourite_state",
        "mod_forum_set_forum_subscription",
        "mod_forum_set_forum_tracking",
        "mod_forum_mark_posts_read",
    ),
    *_action(
        "forums",
        "Eliminar una publicación propia del foro.",
        "Elimina contenido publicado y puede afectar respuestas asociadas.",
        "mod_forum_delete_post",
        destructive=True,
    ),
    *_action(
        "activities",
        "Responder o retirar una elección.",
        "Cambia la respuesta registrada en una actividad Choice.",
        "mod_choice_submit_choice_response",
        "mod_choice_delete_choice_responses",
    ),
    *_action(
        "activities",
        "Crear o editar una entrada propia en base de datos, glosario o wiki.",
        "Publica o modifica contenido visible según la configuración de la actividad.",
        "mod_data_add_entry",
        "mod_data_update_entry",
        "mod_glossary_add_entry",
        "mod_glossary_update_entry",
        "mod_wiki_new_page",
        "mod_wiki_edit_page",
    ),
    *_action(
        "activities",
        "Eliminar una entrada propia de base de datos o glosario.",
        "Elimina contenido y puede ser irreversible.",
        "mod_data_delete_entry",
        "mod_glossary_delete_entry",
        destructive=True,
    ),
    *_action(
        "activities",
        "Iniciar, avanzar o enviar respuestas de feedback, lección o encuesta.",
        "Registra respuestas, progreso o una entrega evaluable.",
        "mod_feedback_launch_feedback",
        "mod_feedback_process_page",
        "mod_lesson_launch_attempt",
        "mod_lesson_process_page",
        "mod_lesson_finish_attempt",
        "mod_survey_submit_answers",
    ),
    *_action(
        "activities",
        "Participar en chat, SCORM, H5P o herramientas xAPI.",
        "Registra mensajes, lanzamiento, progreso o resultados de actividad.",
        "mod_chat_login_user",
        "mod_chat_send_chat_message",
        "mod_scorm_launch_sco",
        "mod_scorm_insert_scorm_tracks",
        "core_xapi_statement_post",
        "core_xapi_post_state",
        "core_xapi_delete_state",
        "core_xapi_delete_states",
    ),
    *_action(
        "activities",
        "Crear, editar o evaluar una entrega propia de taller.",
        "Modifica una entrega o evaluación del flujo Workshop.",
        "mod_workshop_add_submission",
        "mod_workshop_update_submission",
        "mod_workshop_update_assessment",
    ),
    *_action(
        "activities",
        "Eliminar una entrega propia de taller.",
        "Elimina una entrega Workshop y puede ser irreversible.",
        "mod_workshop_delete_submission",
        destructive=True,
    ),
    *_action(
        "activity_tracking",
        "Registrar que el alumno ha abierto una actividad o informe.",
        "Crea trazas de visualización y puede cambiar la finalización automática.",
        "core_blog_view_entries",
        "core_course_view_course",
        "core_courseformat_log_view_overview_information",
        "core_course_view_module_instance_list",
        "core_search_view_results",
        "core_user_view_user_list",
        "core_user_view_user_profile",
        "gradereport_overview_view_grade_report",
        "gradereport_user_view_grade_report",
        "mod_assign_view_assign",
        "mod_book_view_book",
        "mod_bigbluebuttonbn_view_bigbluebuttonbn",
        "mod_chat_view_chat",
        "mod_chat_view_sessions",
        "mod_choice_view_choice",
        "mod_data_view_database",
        "mod_feedback_view_feedback",
        "mod_folder_view_folder",
        "mod_forum_view_forum",
        "mod_forum_view_forum_discussion",
        "mod_glossary_view_glossary",
        "mod_glossary_view_entry",
        "mod_h5pactivity_view_h5pactivity",
        "mod_h5pactivity_log_report_viewed",
        "mod_imscp_view_imscp",
        "mod_lesson_view_lesson",
        "mod_lti_view_lti",
        "mod_page_view_page",
        "mod_quiz_view_quiz",
        "mod_resource_view_resource",
        "mod_scorm_view_scorm",
        "mod_survey_view_survey",
        "mod_url_view_url",
        "mod_wiki_view_wiki",
        "mod_wiki_view_page",
        "mod_workshop_view_workshop",
        "mod_workshop_view_submission",
    ),
    *_action(
        "social",
        "Añadir comentarios o valoraciones.",
        "Publica un comentario o cambia una valoración visible según el contexto.",
        "core_comment_add_comments",
        "core_rating_add_rating",
    ),
    *_action(
        "social",
        "Eliminar comentarios propios.",
        "Elimina contenido y puede ser irreversible.",
        "core_comment_delete_comments",
        destructive=True,
    ),
    *_action(
        "attempts",
        "Marcar o desmarcar una pregunta dentro de un intento.",
        "Cambia la bandera guardada para una pregunta del intento.",
        "core_question_update_flag",
    ),
]

CAPABILITIES: dict[str, StudentCapability] = {item.function: item for item in _CAPABILITIES}
if len(CAPABILITIES) != len(_CAPABILITIES):  # pragma: no cover - import-time invariant
    raise RuntimeError("Hay funciones Moodle duplicadas en el catálogo estudiantil")

GENERIC_ACTIONS = frozenset(
    {
        "core_user_update_user_preferences",
        "core_course_set_favourite_courses",
        "core_message_mute_conversations",
        "core_message_unmute_conversations",
        "core_message_mark_all_notifications_as_read",
        "core_message_mark_all_conversation_messages_as_read",
        "core_message_mark_message_read",
        "core_message_mark_notification_read",
        "core_message_set_favourite_conversations",
        "core_message_unset_favourite_conversations",
        "core_message_set_unsent_message",
        "core_question_update_flag",
    }
)


def get_capability(function: str, access: AccessKind) -> StudentCapability:
    if not isinstance(function, str) or not function:
        raise ValueError("function es obligatorio")
    capability = CAPABILITIES.get(function)
    if capability is None or capability.access != access:
        kind = "lectura" if access == "read" else "acción"
        raise ValueError(f"{function!r} no está incluida en la lista blanca de {kind} estudiantil")
    return capability


def capability_catalog(
    *,
    category: str | None = None,
    access: AccessKind | None = None,
    available_functions: set[str] | None = None,
) -> list[dict[str, Any]]:
    if access not in {None, "read", "action"}:
        raise ValueError("access debe ser read o action")
    categories = {item.category for item in CAPABILITIES.values()}
    if category is not None and category not in categories:
        raise ValueError("category no pertenece al catálogo estudiantil")
    result: list[dict[str, Any]] = []
    for item in sorted(CAPABILITIES.values(), key=lambda value: (value.category, value.function)):
        if category is not None and item.category != category:
            continue
        if access is not None and item.access != access:
            continue
        available = None if available_functions is None else item.function in available_functions
        result.append(
            {
                "function": item.function,
                "category": item.category,
                "access": item.access,
                "description": item.description,
                "consequence": item.consequence or None,
                "destructive": item.destructive,
                "generic_execution_supported": item.function in GENERIC_ACTIONS,
                "available_for_configured_token": available,
            }
        )
    return result


def validate_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        raise ValueError("arguments debe ser un objeto JSON")
    nodes = 0

    def visit(value: Any, depth: int, key: str | None = None) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_ARGUMENT_NODES:
            raise ValueError("arguments contiene demasiados elementos")
        if depth > MAX_ARGUMENT_DEPTH:
            raise ValueError("arguments supera la profundidad permitida")
        if key is not None:
            normal_key = key.casefold().replace("-", "_")
            if normal_key in _SECRET_KEYS | _SENSITIVE_OUTPUT_FIELDS or normal_key.endswith(
                ("password", "secret", "sesskey", "signature", "token")
            ):
                raise ValueError(f"El parámetro sensible {key!r} no se admite")
        if value is None or isinstance(value, bool | int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("arguments no admite NaN ni valores infinitos")
            return value
        if isinstance(value, str):
            if len(value) > MAX_ARGUMENT_STRING:
                raise ValueError("Un texto de arguments supera el límite permitido")
            return value
        if isinstance(value, Mapping):
            clean: dict[str, Any] = {}
            for child_key, child in value.items():
                if not isinstance(child_key, str) or not child_key or len(child_key) > 100:
                    raise ValueError("Las claves de arguments deben ser textos breves")
                clean[child_key] = visit(child, depth + 1, child_key)
            return clean
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            return [visit(child, depth + 1) for child in value]
        raise ValueError("arguments solo admite valores JSON")

    result = visit(arguments, 0)
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError("arguments supera el límite total de 1 MB")
    return result


def bind_account(
    arguments: dict[str, Any], user_id: int, *, function: str | None = None
) -> dict[str, Any]:
    """Reject explicit attempts to act as another Moodle user."""

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, child_key)
            return
        if isinstance(value, list):
            for child in value:
                visit(child, key)
            return
        normal_key = (key or "").casefold().replace("_", "")
        if normal_key in {"userid", "useridto", "useridfrom"} and value not in {0, user_id}:
            raise ValueError("La operación genérica solo puede usar la identidad autenticada")

    visit(arguments)
    if function == "core_user_get_users_by_field" and arguments != {
        "field": "id",
        "values": [str(user_id)],
    }:
        raise ValueError("El perfil genérico está limitado a la cuenta autenticada")
    return arguments


def sanitise_result(payload: Any) -> Any:
    nodes = 0
    approximate_bytes = 0

    def visit(value: Any, depth: int = 0) -> Any:
        nonlocal approximate_bytes, nodes
        nodes += 1
        if nodes > 20_000 or depth > 20:
            raise ValueError("Moodle devolvió una respuesta demasiado compleja")
        if isinstance(value, str):
            clean = _redact_output_text(html_to_text(value, limit=MAX_ARGUMENT_STRING))
            approximate_bytes += len(clean.encode("utf-8"))
            if approximate_bytes > MAX_RESULT_BYTES:
                raise ValueError("Moodle devolvió más de 2 MB; reduce filtros o paginación")
            return clean
        if isinstance(value, Mapping):
            clean_mapping: dict[str, Any] = {}
            for key, child in value.items():
                clean_key = str(key)[:100]
                approximate_bytes += len(clean_key.encode("utf-8"))
                if approximate_bytes > MAX_RESULT_BYTES:
                    raise ValueError("Moodle devolvió más de 2 MB; reduce filtros o paginación")
                normal_key = clean_key.casefold().replace("-", "_")
                if normal_key in _SECRET_KEYS | _SENSITIVE_OUTPUT_FIELDS or normal_key.endswith(
                    ("password", "secret", "sesskey", "signature", "token")
                ):
                    clean_mapping[clean_key] = "[REDACTED]"
                else:
                    clean_mapping[clean_key] = visit(child, depth + 1)
            return clean_mapping
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return [visit(child, depth + 1) for child in value]
        if value is None or isinstance(value, bool | int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Moodle devolvió un número no finito")
            return value
        return html_to_text(str(value), limit=10_000)

    result = visit(payload)
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError("Moodle devolvió más de 2 MB; reduce filtros o paginación")
    return result


def _redact_output_text(value: str) -> str:
    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        parsed = urlparse(raw)
        if not parsed.query:
            return raw
        query = [
            (key, "[REDACTED]" if key.casefold() in _SENSITIVE_OUTPUT_QUERY else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunparse(parsed._replace(query=urlencode(query), fragment=""))

    redacted = _URL.sub(redact_url, value)
    return _QUERY_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
