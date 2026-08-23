# READ SUMMARY: This router exposes project import, browsing, wiki, chat, compare, scoring, deletion, and export endpoints.
# CHANGED: Documented shared retrieval and added HTML audit report export support through /export/pdf.
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.security import require_user
from app.db.schemas import (
    ChatRequest,
    CompareRequest,
    DiscoverModulesRequest,
    EvaluationScoreRequest,
    ProjectCreate,
    RunPurposeRequest,
    VerificationRequest,
    WikiGenerateRequest,
)
from app.services.audit_service import chat, compare_models, delete_wiki_page, export_project, generate_wiki, list_wiki_pages, score_evaluation, verify
from app.services.project_service import (
    ANDROID_CASE_STUDIES,
    create_project,
    create_project_from_zip,
    delete_project,
    discover_security_modules,
    file_content,
    file_tree,
    get_project,
    import_and_index_project,
    index_project,
    list_projects,
    project_status,
)
from app.services.methodology import list_formal_runs, update_run_purpose
from app.services.usage_service import usage_summary

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_user)])


@router.get("/{project_id}/usage")
def project_usage(project_id: str) -> dict:
    return usage_summary(project_id)


@router.get("")
def projects() -> list[dict]:
    return list_projects()


@router.post("")
def new_project(request: ProjectCreate, background_tasks: BackgroundTasks) -> dict:
    try:
        project = create_project(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.source_type in {"github", "android"}:
        if not project.get("repo_url"):
            return project
        background_tasks.add_task(import_and_index_project, project["id"])
    return project


@router.post("/zip")
async def new_zip_project(
    file: UploadFile = File(...),
    name: str = Form(...),
    security_goal: str | None = Form(default=None),
) -> dict:
    return await create_project_from_zip(name, file, security_goal)


@router.get("/android-case-studies")
def android_case_studies() -> list[dict]:
    return ANDROID_CASE_STUDIES


@router.get("/{project_id}")
def project(project_id: str) -> dict:
    result = get_project(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.delete("/{project_id}")
def remove_project(project_id: str) -> dict:
    result = delete_project(project_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.get("/{project_id}/status")
def status(project_id: str) -> dict:
    return project_status(project_id)


@router.post("/{project_id}/rebuild-index")
def rebuild_index(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    background_tasks.add_task(index_project, project_id)
    return {"status": "queued", "message": "Project index rebuild started."}


@router.get("/{project_id}/files/tree")
def tree(project_id: str) -> list[dict]:
    return file_tree(project_id)


@router.get("/{project_id}/files/content")
def content(project_id: str, path: str) -> dict:
    try:
        return file_content(project_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc


@router.post("/{project_id}/discover-security-modules")
def discover(project_id: str, request: DiscoverModulesRequest) -> list[dict]:
    return discover_security_modules(project_id, request.security_goal)


@router.post("/{project_id}/wiki/generate")
async def wiki_generate(project_id: str, request: WikiGenerateRequest) -> dict:
    return await generate_wiki(project_id, request)


@router.get("/{project_id}/wiki")
def wiki_pages(project_id: str) -> list[dict]:
    return list_wiki_pages(project_id)



@router.delete("/{project_id}/wiki/{wiki_page_id}")
def wiki_delete(project_id: str, wiki_page_id: str) -> dict:
    result = delete_wiki_page(project_id, wiki_page_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("message") or "Wiki page not found")
    return result
@router.post("/{project_id}/chat")
async def chat_endpoint(project_id: str, request: ChatRequest) -> dict:
    # ASK PATH TRACE: calls audit_service.chat, which calls retrieve_evidence_package(project_id, request.question, 8, db_conn) once with no filters and uses returned wiki_chunks.
    return await chat(project_id, request)


@router.post("/{project_id}/compare-models")
async def compare(project_id: str, request: CompareRequest) -> dict:
    # COMPARE PATH TRACE: calls audit_service.compare_models, which calls retrieve_evidence_package(project_id, request.question, 8, db_conn) once with no filters and shares that package across providers.
    return await compare_models(project_id, request)


@router.post("/{project_id}/verify")
def verification(project_id: str, request: VerificationRequest) -> dict:
    return verify(request)


@router.patch("/{project_id}/evaluations/{evaluation_id}")
def evaluation_score(project_id: str, evaluation_id: str, request: EvaluationScoreRequest) -> dict:
    return score_evaluation(evaluation_id, request)


@router.get("/{project_id}/formal-runs")
def formal_runs(project_id: str) -> list[dict]:
    return list_formal_runs(project_id)


@router.patch("/{project_id}/runs/{run_id}/purpose")
def run_purpose(project_id: str, run_id: str, request: RunPurposeRequest) -> dict:
    try:
        return update_run_purpose(project_id, run_id, request.run_purpose, request.question_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/export")
def export_query(project_id: str, format: str = "markdown", auditor_name: str = Depends(require_user)) -> Response:
    if format not in {"markdown", "json", "csv"}:
        raise HTTPException(status_code=400, detail="Export format must be markdown, json, or csv")
    try:
        media_type, filename, content = export_project(project_id, "md" if format == "markdown" else format, auditor_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{project_id}/export/{export_format}")
def export(project_id: str, export_format: str, auditor_name: str = Depends(require_user)) -> Response:
    if export_format not in {"markdown", "json", "csv", "pdf"}:
        raise HTTPException(status_code=400, detail="Export format must be markdown, json, csv, or pdf")
    try:
        media_type, filename, content = export_project(project_id, "md" if export_format == "markdown" else export_format, auditor_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
