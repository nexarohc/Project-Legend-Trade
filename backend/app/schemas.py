from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[str] = []
    permission_needed: str | None = None


class PermissionUpdate(BaseModel):
    scope: str


class PermissionStatus(BaseModel):
    statuses: dict[str, bool]
    onboarding_complete: bool


class SpeakRequest(BaseModel):
    text: str
