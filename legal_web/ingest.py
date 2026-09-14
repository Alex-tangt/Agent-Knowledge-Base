"""将 legal_web/data/raw 下的领域文档批量灌入向量库，构建可复现的知识库。

用法（仓库根）：
    venv\\Scripts\\python.exe legal_web/ingest.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ragcore"))
sys.path.insert(0, _HERE)

from services.document_service import DocumentService
from services.vector_store_service import VectorStoreService
from utils.logger import logger

RAW_DIR = os.path.join(_HERE, "data", "raw")


def list_raw_files(raw_dir: str):
    supported = (".pdf", ".txt", ".md")
    files = []
    for name in sorted(os.listdir(raw_dir)):
        if name.lower().endswith(supported):
            files.append(os.path.join(raw_dir, name))
    return files


def main():
    if not os.path.isdir(RAW_DIR):
        logger.error(f"未找到数据目录: {RAW_DIR}")
        sys.exit(1)

    files = list_raw_files(RAW_DIR)
    if not files:
        logger.error("data/raw 下没有可加载的文档（支持 pdf/txt/md）")
        sys.exit(1)

    document_service = DocumentService()
    vector_store = VectorStoreService()

    try:
        vector_store.clear_all_documents()
        logger.info("已清空旧知识库，准备全量重建")
    except Exception as e:
        logger.warning(f"清空旧知识库失败（可忽略首次构建）：{e}")

    total_chunks = 0
    for file_path in files:
        logger.info(f"处理文档: {file_path}")
        try:
            docs = document_service.load_document(file_path)
            split_docs = document_service.split_document(docs)
            vector_store.add_documents(split_docs)
            total_chunks += len(split_docs)
            logger.info(f"  已加入 {len(split_docs)} 个片段")
        except Exception as e:
            logger.error(f"  处理失败: {e}")

    logger.info(f"知识库构建完成，共 {total_chunks} 个片段，来自 {len(files)} 个文档。")


if __name__ == "__main__":
    main()
