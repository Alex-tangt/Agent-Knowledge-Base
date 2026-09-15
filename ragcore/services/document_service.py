from langchain_core.documents import Document
from ragcore.config.config import ARTICLE_MAX_CHARS
from ragcore.services.langsmith_service import langsmith_service
from ragcore.utils.logger import logger
from ragcore.strategies import get_split_strategy
import os


class DocumentService:
    def __init__(self):
        try:
            logger.info("DocumentService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize DocumentService: {e}")
            raise

    @langsmith_service.trace(name="document_load", metadata={"service": "DocumentService"})
    def load_document(self, file_path):
        try:
            from langchain_community.document_loaders import PyPDFLoader, TextLoader

            file_extension = os.path.splitext(file_path)[1].lower()

            if file_extension == '.pdf':
                loader = PyPDFLoader(file_path)
            elif file_extension in ('.txt', '.md'):
                loader = TextLoader(file_path, encoding='utf-8')
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")

            documents = loader.load()
            logger.info(f"Loaded document: {file_path}, {len(documents)} pages")
            return documents
        except Exception as e:
            logger.error(f"Error loading document: {e}")
            raise

    @langsmith_service.trace(name="document_split", metadata={"service": "DocumentService"})
    def split_document(self, documents, kb_name=None):
        try:
            strategy = get_split_strategy(kb_name)
            split_docs = strategy.split(documents)
            logger.info(f"Split documents into {len(split_docs)} chunks (strategy={type(strategy).__name__})")
            return split_docs
        except Exception as e:
            logger.error(f"Error splitting document: {e}")
            raise

    @langsmith_service.trace(name="document_process", metadata={"service": "DocumentService"})
    def process_document(self, file_path, kb_name=None):
        try:
            documents = self.load_document(file_path)
            split_docs = self.split_document(documents, kb_name=kb_name)
            logger.info(f"Processed document: {file_path}")
            return split_docs
        except Exception as e:
            logger.error(f"Error processing document: {e}")
            raise
