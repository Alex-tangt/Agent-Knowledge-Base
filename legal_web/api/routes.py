from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse
from models.schemas import ChatRequest
from config.config import UPLOAD_DIR
from utils.logger import logger
from utils.model_status import STATUS
import os
import re
import uuid
import locale

router = APIRouter()

_chat_service = None
_rag_service = None
_document_service = None


def _get_chat_service():
    global _chat_service
    if _chat_service is None:
        from services.chat_service import ChatService
        _chat_service = ChatService()
    return _chat_service


def _get_rag_service():
    global _rag_service
    if _rag_service is None:
        from services.rag_service import RAGService
        _rag_service = RAGService()
    return _rag_service


def _get_document_service():
    global _document_service
    if _document_service is None:
        from services.document_service import DocumentService
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
        logger.info(f"Received chat stream request, kb={request.kb_name}")

        async def generate():
            messages = [msg.model_dump() for msg in request.messages]
            if request.use_rag:
                rag_service = _get_rag_service()
                async for chunk in rag_service.chat_stream(
                    messages, use_rag=True,
                    kb_name=request.kb_name, session_id=request.session_id
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
async def upload_document(file: UploadFile = File(...), kb_name: str = "documents"):
    try:
        logger.info(f"Received document upload: {file.filename} to kb={kb_name}")

        safe_name = _safe_filename(file.filename) or f"upload_{uuid.uuid4().hex}.md"
        file_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        document_service = _get_document_service()
        split_docs = document_service.process_document(file_path, kb_name=kb_name)
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(kb_name)
        doc_ids = vs.add_documents(split_docs)

        os.remove(file_path)

        return {
            "filename": file.filename,
            "kb_name": kb_name,
            "chunks": len(split_docs),
            "doc_ids": doc_ids
        }
    except Exception as e:
        logger.error(f"Error in document upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/count")
async def get_document_count(kb_name: str = "documents"):
    try:
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(kb_name)
        count = vs.get_document_count()
        return {"count": count, "kb_name": kb_name}
    except Exception as e:
        logger.error(f"Error getting document count: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/documents/clear")
async def clear_documents(kb_name: str = "documents"):
    try:
        rag_service = _get_rag_service()
        vs = rag_service.get_vector_store(kb_name)
        vs.clear_all_documents()
        return {"status": "success", "kb_name": kb_name}
    except Exception as e:
        logger.error(f"Error clearing documents: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/health")
async def health_check():
    return {"status": "healthy"}


@router.get("/status")
async def model_status():
    return STATUS


@router.get("/kb/list")
async def list_kb():
    from services.kb_registry import kb_registry
    return kb_registry.list()


@router.post("/kb/create")
async def create_kb(name: str, label: str, description: str = "",
                    split_strategy: str = "default", retrieval_strategy: str = "default"):
    from services.kb_registry import kb_registry
    try:
        kb = kb_registry.create(
            name, label, description,
            split_strategy=split_strategy,
            retrieval_strategy=retrieval_strategy,
        )
        rag_service = _get_rag_service()
        rag_service.get_vector_store(name)
        return kb
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/kb/{name}")
async def delete_kb(name: str):
    from services.kb_registry import kb_registry
    try:
        kb_registry.delete(name)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
