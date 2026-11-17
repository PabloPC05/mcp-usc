from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mcp_usc.session_forms import (
    FormResponse,
    FormUpload,
    MoodleSessionForms,
    SessionFormConfirmationRequired,
)


def test_form_response_repr_does_not_expose_session_data() -> None:
    response = FormResponse(
        "https://cv.usc.es/mod/assign/view.php?sesskey=secret123",
        '<input name="sesskey" value="secret123">',
    )

    assert "secret123" not in repr(response)


def test_parsed_form_repr_does_not_expose_sesskey_in_action() -> None:
    http = FakeHttp([])
    response = FormResponse(
        f"{BASE}/mod/assign/view.php",
        '<form method="post" action="/mod/assign/view.php?sesskey=secret123"></form>',
    )

    parsed = http.client()._forms(response, frozenset({"/mod/assign/view.php"}))

    assert "secret123" not in repr(parsed)


BASE = "https://cv.usc.es"


class FakeHttp:
    def __init__(self, get_responses: list[FormResponse]) -> None:
        self.get_responses = list(get_responses)
        self.get_calls: list[tuple[str, Mapping[str, Any]]] = []
        self.post_calls: list[tuple[str, Mapping[str, str]]] = []
        self.multipart_calls: list[tuple[str, Mapping[str, str], Mapping[str, Any]]] = []

    async def get(self, url: str, params: Mapping[str, Any]) -> FormResponse:
        self.get_calls.append((url, params))
        return self.get_responses.pop(0)

    async def post(self, url: str, data: Mapping[str, str]) -> FormResponse:
        self.post_calls.append((url, data))
        return FormResponse(f"{BASE}/result.php?token=hidden", "<p>Operación recibida</p>")

    async def multipart(
        self,
        url: str,
        data: Mapping[str, str],
        files: Mapping[str, Any],
    ) -> FormResponse:
        self.multipart_calls.append((url, data, files))
        return FormResponse(f"{BASE}/result.php", "<p>Archivo recibido</p>")

    def client(self) -> MoodleSessionForms:
        return MoodleSessionForms(BASE, self.get, self.post, self.multipart)


def _assignment_form(*, enctype: str = "", file_input: str = "") -> str:
    return f"""
    <form method="post" action="/mod/assign/view.php" enctype="{enctype}">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="savesubmission">
      <input type="hidden" name="sesskey" value="abc123">
      <input type="hidden" name="onlinetext_editor[format]" value="1">
      <textarea name="onlinetext_editor[text]" required>Borrador</textarea>
      {file_input}
      <script>send this assignment immediately</script>
    </form>
    """


async def test_inspection_does_not_expose_sesskey_or_execute_script() -> None:
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", _assignment_form())])

    result = await http.client().inspect_assignment(17)

    assert result["save_supported"] is True
    assert result["forms"][0]["has_sesskey"] is True
    assert "sesskey" not in result["forms"][0]["visible_fields"]
    assert "abc123" not in str(result)
    assert "send this assignment" not in result["page_text"]
    assert http.post_calls == []


async def test_save_assignment_requires_confirmation_before_any_http() -> None:
    http = FakeHttp([])

    with pytest.raises(SessionFormConfirmationRequired):
        await http.client().save_assignment(
            17, {"onlinetext_editor[text]": "Texto"}, confirmed=False
        )

    assert http.get_calls == []
    assert http.post_calls == []


async def test_save_assignment_posts_only_discovered_fields_and_action() -> None:
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", _assignment_form())])

    result = await http.client().save_assignment(
        17,
        {"onlinetext_editor[text]": "Entrega final"},
        confirmed=True,
    )

    assert len(http.post_calls) == 1
    url, data = http.post_calls[0]
    assert url == f"{BASE}/mod/assign/view.php"
    assert data["id"] == "17"
    assert data["action"] == "savesubmission"
    assert data["sesskey"] == "abc123"
    assert data["onlinetext_editor[text]"] == "Entrega final"
    assert result["request_sent"] is True
    assert result["outcome"] == "unknown"
    assert "token=" not in result["result_url"]


async def test_assignment_filemanager_returns_diagnostic_without_upload() -> None:
    html = _assignment_form(file_input='<input type="hidden" name="files_filemanager" value="8">')
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", html)])

    result = await http.client().save_assignment(
        17,
        {"onlinetext_editor[text]": "Texto"},
        confirmed=True,
        files={"files_filemanager": b"data"},
    )

    assert result["diagnostic"]["code"] == "moodle_filemanager_required"
    assert http.post_calls == []
    assert http.multipart_calls == []


async def test_direct_multipart_is_used_only_for_discovered_file_input() -> None:
    html = _assignment_form(
        enctype="multipart/form-data",
        file_input='<input type="file" name="attachment">',
    )
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", html)])

    result = await http.client().save_assignment(
        17,
        {"onlinetext_editor[text]": "Texto"},
        confirmed=True,
        files={"attachment": b"data"},
    )

    assert result["request_sent"] is True
    assert len(http.multipart_calls) == 1
    assert http.post_calls == []


def _filemanager_assignment_form() -> str:
    return _assignment_form(
        file_input='<input type="hidden" name="files_filemanager" value="88">'
    ).replace(
        "</form>",
        '<a href="/repository/draftfiles_manager.php?action=browse&amp;itemid=88&amp;'
        'sesskey=abc123">Gestionar archivos</a></form>',
    )


def _draft_manager(*, with_file: bool = True) -> str:
    delete = (
        '<a href="/repository/draftfiles_manager.php?action=deletedraft&amp;itemid=88&amp;'
        'filepath=%2F&amp;filename=old.txt&amp;sesskey=abc123">Eliminar old.txt</a>'
        if with_file
        else ""
    )
    return (
        delete
        + '<a href="/repository/filepicker.php?action=plugins&amp;itemid=88&amp;'
        'sesskey=abc123">Agregar</a>'
    )


def _repository_plugins() -> str:
    return (
        '<a href="/repository/filepicker.php?action=list&amp;itemid=88&amp;repo_id=4&amp;'
        'sesskey=abc123">Subir un archivo</a>'
    )


def _repository_upload_form() -> str:
    return """
    <form method="post" enctype="multipart/form-data"
      action="/repository/filepicker.php?action=list&amp;itemid=88&amp;repo_id=4&amp;sesskey=abc123">
      <input type="hidden" name="action" value="upload">
      <input type="hidden" name="draftpath" value="/">
      <input type="hidden" name="savepath" value="/">
      <input type="hidden" name="repo_id" value="4">
      <input type="file" name="repo_upload_file">
    </form>
    """


async def test_replace_assignment_files_uses_server_discovered_non_js_draft_flow() -> None:
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", _filemanager_assignment_form()),
            FormResponse(f"{BASE}/repository/draftfiles_manager.php", _draft_manager()),
            FormResponse(f"{BASE}/repository/filepicker.php", _repository_plugins()),
            FormResponse(f"{BASE}/repository/filepicker.php", _repository_upload_form()),
            FormResponse(
                f"{BASE}/repository/draftfiles_manager.php", _draft_manager(with_file=False)
            ),
        ]
    )

    result = await http.client().replace_assignment_files(
        17,
        [FormUpload("answer.txt", b"confirmed bytes", "text/plain")],
        confirmed=True,
    )

    assert result["request_sent"] is True
    assert result["draft_mutations"] == 2
    assert "deletedraft" in http.get_calls[-1][0]
    assert len(http.multipart_calls) == 1
    _, upload_data, upload_files = http.multipart_calls[0]
    assert upload_data["action"] == "upload"
    assert upload_files["repo_upload_file"].content == b"confirmed bytes"
    assert len(http.post_calls) == 1
    assert http.post_calls[0][1]["files_filemanager"] == "88"


async def test_file_manager_embedded_as_noscript_object_is_found() -> None:
    """Moodle 4 (cv.usc.es) only links the non-JS manager from <noscript><object data>."""

    form = _filemanager_assignment_form().replace(
        '<a href="/repository/draftfiles_manager.php?action=browse&amp;itemid=88&amp;'
        'sesskey=abc123">Gestionar archivos</a>',
        '<noscript><div><object type="text/html" data="/repository/draftfiles_manager.php?'
        'env=filemanager&amp;action=browse&amp;itemid=88&amp;sesskey=abc123"></object>'
        "</div></noscript>",
    )
    assert "<object" in form and "<a href" not in form
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", form),
            FormResponse(f"{BASE}/repository/draftfiles_manager.php", _draft_manager()),
            FormResponse(f"{BASE}/repository/filepicker.php", _repository_plugins()),
            FormResponse(f"{BASE}/repository/filepicker.php", _repository_upload_form()),
            FormResponse(
                f"{BASE}/repository/draftfiles_manager.php", _draft_manager(with_file=False)
            ),
        ]
    )

    result = await http.client().replace_assignment_files(
        17,
        [FormUpload("answer.txt", b"confirmed bytes", "text/plain")],
        confirmed=True,
    )

    assert result["request_sent"] is True
    assert len(http.multipart_calls) == 1
    assert http.post_calls[0][1]["files_filemanager"] == "88"


async def test_replace_refuses_more_files_than_allowed_before_touching_the_draft() -> None:
    form = _filemanager_assignment_form().replace("itemid=88&amp;", "itemid=88&amp;maxfiles=1&amp;")
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", form)])

    result = await http.client().replace_assignment_files(
        17,
        [
            FormUpload("a.txt", b"a", "text/plain"),
            FormUpload("b.txt", b"b", "text/plain"),
        ],
        confirmed=True,
    )

    assert result["diagnostic"]["code"] == "too_many_files"
    assert len(http.get_calls) == 1  # only the edit page: nothing deleted
    assert http.multipart_calls == [] and http.post_calls == []


async def test_inspect_reports_the_file_limits() -> None:
    form = _filemanager_assignment_form().replace(
        "itemid=88&amp;", "itemid=88&amp;maxfiles=1&amp;maxbytes=1024&amp;"
    )
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", form),
            FormResponse(f"{BASE}/repository/draftfiles_manager.php", _draft_manager()),
        ]
    )

    result = await http.client().inspect_assignment(17)

    assert result["filemanager"]["max_files"] == 1
    assert result["filemanager"]["max_bytes"] == 1024
    assert result["filemanager"]["current_files"] == [{"filename": "old.txt", "filepath": "/"}]


async def test_assignment_status_reads_the_status_table_and_the_submit_step() -> None:
    page = """
    <div class="submissionstatustable"><table class="generaltable">
      <tr><th>Estado da entrega</th><td>Borrador (non enviado)</td></tr>
      <tr><th>Tempo restante</th><td>A tarefa está atrasada por: 284 días</td></tr>
      <tr><th>Comentarios a entrega</th><td>___picture___ ___name___</td></tr>
    </table></div>
    <form method="post" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17"><input type="hidden" name="action" value="submit">
      <input type="hidden" name="sesskey" value="abc123">
    </form>
    """
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", page)])

    result = await http.client().assignment_status(17)

    assert result["rows"] == {
        "Estado da entrega": "Borrador (non enviado)",
        "Tempo restante": "A tarefa está atrasada por: 284 días",
    }
    assert result["submit_for_grading_available"] is True
    assert result["feedback"] == {} and result["feedback_files"] == []
    assert http.post_calls == []


async def test_assignment_status_reads_the_grade_and_the_teachers_feedback() -> None:
    page = """
    <div class="submissionstatustable"><table>
      <tr><th>Estado das cualificacións</th><td>Cualificado</td></tr>
    </table></div>
    <div class="feedback"><table class="generaltable">
      <tr><th>Cualificación</th><td>9,0 / 10,0</td></tr>
      <tr><th>Cualificado por</th>
        <td><span class="userinitials">AP</span><a href="#">Ana Proba</a></td></tr>
      <tr><th>Comentarios</th><td><p>Moi ben.</p><p>Revisa o apartado 3.</p></td></tr>
      <tr><th>Ficheiros de comentarios</th><td>
        <a href="https://cv.usc.es/pluginfile.php/1/assignfeedback_file/x/correccion.pdf">correccion.pdf</a>
      </td></tr>
    </table></div>
    """
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", page)])

    result = await http.client().assignment_status(17)

    assert result["rows"] == {"Estado das cualificacións": "Cualificado"}
    assert result["feedback"]["Cualificación"] == "9,0 / 10,0"
    assert result["feedback"]["Cualificado por"] == "Ana Proba"
    assert result["feedback"]["Comentarios"] == "Moi ben.\n\nRevisa o apartado 3."
    assert result["feedback_files"] == [
        {
            "name": "correccion.pdf",
            "url": "https://cv.usc.es/pluginfile.php/1/assignfeedback_file/x/correccion.pdf",
        }
    ]


async def test_delete_assignment_follows_moodle4_confirmation_page() -> None:
    view = """
    <form method="get" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="removesubmissionconfirm">
      <button type="submit">Eliminar entrega</button>
    </form>
    """
    confirm = """
    <form method="get" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17"><input type="hidden" name="action" value="view">
    </form>
    <form method="post" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="removesubmission">
      <input type="hidden" name="sesskey" value="abc123">
      <input type="hidden" name="userid" value="5">
      <button type="submit">Continuar</button>
    </form>
    """
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", view),
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", confirm),
        ]
    )

    result = await http.client().delete_assignment(17, confirmed=True)

    assert result["request_sent"] is True
    assert http.get_calls[1][1] == {"id": 17, "action": "removesubmissionconfirm"}
    assert len(http.post_calls) == 1
    posted = http.post_calls[0][1]
    assert posted["action"] == "removesubmission" and posted["sesskey"] == "abc123"


async def test_delete_assignment_without_the_button_touches_nothing() -> None:
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", "<p>Sen entrega</p>")])

    result = await http.client().delete_assignment(17, confirmed=True)

    assert result["diagnostic"]["code"] == "delete_action_not_found"
    assert len(http.get_calls) == 1 and http.post_calls == []


async def test_delete_assignment_files_requires_confirmation_before_page_view() -> None:
    http = FakeHttp([])

    with pytest.raises(SessionFormConfirmationRequired):
        await http.client().delete_assignment_files(17, confirmed=False)

    assert http.get_calls == []


async def test_delete_assignment_files_deletes_discovered_files_then_saves_form() -> None:
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", _filemanager_assignment_form()),
            FormResponse(f"{BASE}/repository/draftfiles_manager.php", _draft_manager()),
            FormResponse(
                f"{BASE}/repository/draftfiles_manager.php", _draft_manager(with_file=False)
            ),
        ]
    )

    result = await http.client().delete_assignment_files(17, confirmed=True)

    assert result["request_sent"] is True
    assert result["draft_mutations"] == 1
    assert "deletedraft" in http.get_calls[-1][0]
    assert len(http.post_calls) == 1


def _submit_assignment_form() -> str:
    return """
    <form method="post" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="confirmsubmit">
      <input type="hidden" name="sesskey" value="abc123">
      <input type="checkbox" name="submissionstatement" value="1" required>
    </form>
    """


async def test_submit_assignment_requires_explicit_required_statement() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/assign/view.php?id=17&action=submit", _submit_assignment_form())]
    )

    result = await http.client().submit_assignment(17, confirmed=True)

    assert result["diagnostic"]["code"] == "confirmation_fields_required"
    assert http.post_calls == []


async def test_submit_assignment_posts_refetched_confirmation_form() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/assign/view.php?id=17&action=submit", _submit_assignment_form())]
    )

    result = await http.client().submit_assignment(
        17, {"submissionstatement": True}, confirmed=True
    )

    assert result["request_sent"] is True
    assert http.post_calls[0][1]["action"] == "confirmsubmit"
    assert http.post_calls[0][1]["submissionstatement"] == "1"


def _submit_assignment_form_without_statement() -> str:
    return """
    <form method="post" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="confirmsubmit">
      <input type="hidden" name="sesskey" value="abc123">
    </form>
    """


async def test_submit_assignment_without_statement_field_does_not_send_it() -> None:
    http = FakeHttp(
        [
            FormResponse(
                f"{BASE}/mod/assign/view.php?id=17&action=submit",
                _submit_assignment_form_without_statement(),
            )
        ]
    )

    result = await http.client().submit_assignment(
        17, {"submissionstatement": True}, confirmed=True
    )

    assert result["request_sent"] is True
    assert "submissionstatement" not in http.post_calls[0][1]
    assert http.post_calls[0][1]["action"] == "confirmsubmit"


async def test_submit_assignment_without_statement_field_and_unaccepted_still_posts() -> None:
    http = FakeHttp(
        [
            FormResponse(
                f"{BASE}/mod/assign/view.php?id=17&action=submit",
                _submit_assignment_form_without_statement(),
            )
        ]
    )

    result = await http.client().submit_assignment(
        17, {"submissionstatement": False}, confirmed=True
    )

    assert result["request_sent"] is True
    assert "submissionstatement" not in http.post_calls[0][1]


async def test_submit_assignment_with_statement_field_fails_closed_when_unaccepted() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/assign/view.php?id=17&action=submit", _submit_assignment_form())]
    )

    result = await http.client().submit_assignment(
        17, {"submissionstatement": False}, confirmed=True
    )

    assert result["diagnostic"]["code"] == "confirmation_fields_required"
    assert http.post_calls == []


async def test_delete_assignment_uses_only_server_discovered_bound_action() -> None:
    html = """
    <a href="/mod/assign/view.php?id=17&amp;action=removesubmission&amp;sesskey=abc123">
      Eliminar entrega
    </a>
    """
    http = FakeHttp(
        [
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", html),
            FormResponse(f"{BASE}/mod/assign/view.php?id=17", "<p>Eliminación recibida</p>"),
        ]
    )

    result = await http.client().delete_assignment(17, confirmed=True)

    assert result["request_sent"] is True
    assert len(http.get_calls) == 2
    assert "action=removesubmission" in http.get_calls[1][0]
    assert "sesskey" not in str(result)


async def test_external_assignment_action_is_rejected_without_post() -> None:
    html = _assignment_form().replace(
        'action="/mod/assign/view.php"', 'action="https://evil.example/collect"'
    )
    http = FakeHttp([FormResponse(f"{BASE}/mod/assign/view.php?id=17", html)])

    result = await http.client().save_assignment(
        17, {"onlinetext_editor[text]": "Texto"}, confirmed=True
    )

    assert result["diagnostic"]["code"] == "save_form_not_found"
    assert http.post_calls == []


def _quiz_start_form(*, password: bool = False) -> str:
    password_input = '<input type="password" name="quizpassword" required>' if password else ""
    return f"""
    <form method="post" action="/mod/quiz/startattempt.php">
      <input type="hidden" name="cmid" value="23">
      <input type="hidden" name="sesskey" value="quizkey">
      {password_input}
      <button type="submit">Comenzar</button>
    </form>
    """


async def test_quiz_start_detects_missing_preflight_without_post() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/quiz/view.php?id=23", _quiz_start_form(password=True))]
    )

    result = await http.client().start_quiz(23, confirmed=True)

    assert result["diagnostic"]["code"] == "preflight_required"
    assert http.post_calls == []


async def test_quiz_start_posts_discovered_form_after_confirmation() -> None:
    http = FakeHttp([FormResponse(f"{BASE}/mod/quiz/view.php?id=23", _quiz_start_form())])

    result = await http.client().start_quiz(23, confirmed=True)

    assert result["request_sent"] is True
    assert http.post_calls == [
        (
            f"{BASE}/mod/quiz/startattempt.php",
            {"cmid": "23", "sesskey": "quizkey"},
        )
    ]


def _quiz_attempt_form() -> str:
    return """
    <form method="post" action="/mod/quiz/processattempt.php">
      <input type="hidden" name="attempt" value="91">
      <input type="hidden" name="thispage" value="0">
      <input type="hidden" name="nextpage" value="1">
      <input type="hidden" name="sesskey" value="quizkey">
      <input type="hidden" name="q2:1_sequencecheck" value="3">
      <input type="radio" name="q2:1_answer" value="0">
      <input type="radio" name="q2:1_answer" value="1">
    </form>
    """


async def test_inspect_quiz_page_exposes_safe_choice_values_without_posting() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/quiz/attempt.php?attempt=91&page=0", _quiz_attempt_form())]
    )

    result = await http.client().inspect_quiz_page(91, 0)

    choices = result["forms"][0]["choices"]["q2:1_answer"]
    assert [choice["value"] for choice in choices] == ["0", "1"]
    assert "quizkey" not in str(result["forms"][0])
    assert http.post_calls == []


async def test_save_quiz_page_posts_only_fields_exposed_by_current_attempt() -> None:
    http = FakeHttp(
        [FormResponse(f"{BASE}/mod/quiz/attempt.php?attempt=91&page=0", _quiz_attempt_form())]
    )

    result = await http.client().save_quiz_page(91, 0, {"q2:1_answer": "1"}, confirmed=True)

    assert result["request_sent"] is True
    data = http.post_calls[0][1]
    assert data["attempt"] == "91"
    assert data["q2:1_sequencecheck"] == "3"
    assert data["q2:1_answer"] == "1"


async def test_finish_quiz_refuses_to_invent_missing_finish_control() -> None:
    http = FakeHttp([FormResponse(f"{BASE}/mod/quiz/summary.php?attempt=91", _quiz_attempt_form())])

    result = await http.client().finish_quiz(91, confirmed=True)

    assert result["diagnostic"]["code"] == "finish_form_not_found"
    assert http.post_calls == []


async def test_finish_quiz_posts_exact_server_finish_button() -> None:
    html = _quiz_attempt_form().replace(
        "</form>", '<button name="finishattempt" value="1">Finalizar</button></form>'
    )
    http = FakeHttp([FormResponse(f"{BASE}/mod/quiz/summary.php?attempt=91", html)])

    result = await http.client().finish_quiz(91, confirmed=True)

    assert result["request_sent"] is True
    assert http.post_calls[0][1]["finishattempt"] == "1"


@pytest.mark.parametrize("operation", ["save", "start", "finish", "delete"])
async def test_all_mutations_reject_false_confirmation_without_http(operation: str) -> None:
    http = FakeHttp([])
    client = http.client()

    with pytest.raises(SessionFormConfirmationRequired):
        if operation == "save":
            await client.save_quiz_page(91, 0, {}, confirmed=False)
        elif operation == "start":
            await client.start_quiz(23, confirmed=False)
        elif operation == "finish":
            await client.finish_quiz(91, confirmed=False)
        else:
            await client.delete_assignment(17, confirmed=False)

    assert http.get_calls == []
    assert http.post_calls == []
