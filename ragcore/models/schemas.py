from pydantic import AliasChoices, BaseModel, Field
from typing import List

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    use_rag: bool = True
    # #37 命名迁移：canonical = view_name（视图 = 具名读谓词）。旧字段 kb_name 作为
    # 校验别名保留，未升级的客户端仍可提交。
    view_name: str = Field("documents", validation_alias=AliasChoices("view_name", "kb_name"))
    session_id: str = ""
