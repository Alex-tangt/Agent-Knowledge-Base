from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse
from ragcore.models.schemas import ChatRequest
from ragcore.config.config import UPLOAD_DIR
from ragcore.utils.logger import logger
from ragcore.utils.model_status import STATUS
import os
import re
import uuid
import locale
from typing import Optional

router = APIRouter()

DEFAULT_VIEW_NAME = "documents"

_chat_service = None
_rag_service = None
_document_service = None


def _resolve_view_name(view_name: Optional[str] = None, kb_name: Optional[str] = None) -> str:
    """#37 命名迁移：canonical 参数 `view_name`；`kb_name` 为兼容别名（已废弃）。

    两个都给时以 `view_name` 为准；都不给时用默认视图。
    """
    return view_name or kb_name or DEFAULT_VIEW_NAME


def _get_chat_service():
    global _chat_service
    if _chat_service is None:
        from ragcore.services.chat_service import ChatService
        _chat_service = ChatService()
    return _chat_service


def _get_rag_service():
    global _rag_service
    if _rag_service is None:
        from ragcore.services.rag_service import RAGService
        _rag_service = RAGService()
    return _rag_service


def _get_document_service():
    global _document_service
    if _document_service is None:
        from ragcore.services.document_service import DocumentService
        _document_service = DocumentService()
    return _document_service


def _recover_filename(name):
    try:
        raw = name.encode("latin-1")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", locale.getpreferredencoding(False) or "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return name


def _safe_filename(name):
    if not name:
        return None
    name = _recover_filename(name)
    name = os.path.basename(name)
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or None


os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    try:
        logger.info(f"Received chat stream request, view={request.view_name}")

        async def generate():
            messages = [msg.model_dump() for msg in request.messages]
            if request.use_rag:
                rag_service = _get_rag_service()
                # 内部接缝 (rag_service) 仍用 kb_name 参数名，待其独立迁移。
                async for chunk in rag_service.chat_stream(
                    messages, use_rag=True,
                    kb_name=request.view_name, session_id=request.session_id
                ):
                    yield chunk
            else:
                chat_service = _get_chat_service()
                async for chunk in chat_service.chat_stream(messages):
                    yield chunk

        return StreamingResponse(generate(), media_type="application/jsonl")
    except Exception as e:
        logger.error(f"Error in chat stream endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), view_name: Optional[str] = None,
                          kb_name: Optional[str] = None):
    view = _resolve_view_name(view_name, kb_name)
    try:
        logger.info(f"Received document upload: {file.filename} to view={view}")

        safe_name = _safe_filename(file.filename) or f"upload_{uuid.uuid4().hex}.md"
        file_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        document_service = _get_document_service()
        split_docs = document_service.process_document(file_path, view_name=view)
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(view)
        doc_ids = vs.add_documents(split_docs)

        os.remove(file_path)

        return {
            "filename": file.filename,
            "view_name": view,
            "chunks": len(split_docs),
            "doc_ids": doc_ids
        }
    except Exception as e:
        logger.error(f"Error in document upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/count")
async def get_document_count(view_name: Optional[str] = None, kb_name: Optional[str] = None):
    view = _resolve_view_name(view_name, kb_name)
    try:
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(view)
        count = vs.get_document_count()
        return {"count": count, "view_name": view}
    except Exception as e:
        logger.error(f"Error getting document count: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/documents/clear")
async def clear_documents(view_name: Optional[str] = None, kb_name: Optional[str] = None):
    view = _resolve_view_name(view_name, kb_name)
    try:
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(view)
        vs.clear_all_documents()
        return {"status": "success", "view_name": view}
    except Exception as e:
        logger.error(f"Error clearing documents: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/health")
async def health_check():
    return {"status": "healthy"}


@router.get("/status")
async def model_status():
    return STATUS


async def list_views():
    from ragcore.services.view_registry import view_registry
    return view_registry.list()


async def create_view(name: str, label: str, description: str = "",
                      split_strategy: str = "default", retrieval_strategy: str = "default"):
    from ragcore.services.view_registry import view_registry
    try:
        view = view_registry.create(
            name, label, description,
            split_strategy=split_strategy,
            retrieval_strategy=retrieval_strategy,
        )
        rag_service = _get_rag_service()
        rag_service.get_vector_store(name)
        return view
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def delete_view(name: str):
    from ragcore.services.view_registry import view_registry
    try:
        view_registry.delete(name)
        return {"status": "deleted", "view_name": name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# canonical：/view/*；/kb/* 为 #37 兼容别名（隐藏于 schema，标记 deprecated）。
router.add_api_route("/view/list", list_views, methods=["GET"])
router.add_api_route("/kb/list", list_views, methods=["GET"], include_in_schema=False, deprecated=True)

router.add_api_route("/view/create", create_view, methods=["POST"])
router.add_api_route("/kb/create", create_view, methods=["POST"], include_in_schema=False, deprecated=True)

router.add_api_route("/view/{name}", delete_view, methods=["DELETE"])
router.add_api_route("/kb/{name}", delete_view, methods=["DELETE"], include_in_schema=False, deprecated=True)
