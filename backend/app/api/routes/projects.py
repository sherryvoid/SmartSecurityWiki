# READ SUMMARY: This router exposes project import, browsing, wiki, chat, compare, scoring, deletion, and export endpoints.
# CHANGED: Documented that chat and compare now delegate to the same shared retrieval package in audit_service.
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.security import require_user
from app.db.schemas import (
    ChatRequest,
    CompareRequest,
    DiscoverModulesRequest,
    EvaluationScoreRequest,
    ProjectCreate,
    VerificationRequest,
    WikiGenerateRequest,
)
from app.services.audit_service import chat, compare_models, export_project, generate_wiki, list_wiki_pages, score_evaluation, verify
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

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_user)])


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


@router.get("/{project_id}/export")
def export_query(project_id: str, format: str = "markdown") -> Response:
    if format not in {"markdown", "json", "csv"}:
        raise HTTPException(status_code=400, detail="Export format must be markdown, json, or csv")
    try:
        media_type, filename, content = export_project(project_id, "md" if format == "markdown" else format)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{project_id}/export/{export_format}")
def export(project_id: str, export_format: str) -> Response:
    if export_format not in {"markdown", "json", "csv"}:
        raise HTTPException(status_code=400, detail="Export format must be markdown, json, or csv")
    try:
        media_type, filename, content = export_project(project_id, "md" if export_format == "markdown" else export_format)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
