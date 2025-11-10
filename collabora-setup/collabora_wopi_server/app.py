from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict
import uuid

app = FastAPI(title="WOPI Server")

# In-memory storage for demo purposes (use database in production)
file_storage: Dict[str, Dict] = {}


class FileInfo(BaseModel):
    BaseFileName: str
    Size: int
    OwnerId: str
    UserId: str
    UserFriendlyName: str
    Version: str


class CheckFileInfo(BaseModel):
    BaseFileName: str
    Size: int
    OwnerId: str
    UserId: str
    UserFriendlyName: str
    Version: str
    SupportsUpdate: bool = True
    SupportsLocks: bool = True
    SupportsGetLock: bool = True
    SupportsDeleteFile: bool = True
    SupportsRename: bool = True
    UserCanWrite: bool = True
    UserCanNotWriteRelative: bool = True
    UserCanRename: bool = True
    UserCanDeleteFile: bool = True


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/wopi/files/{file_id}")
async def check_file_info(file_id: str, request: Request):
    """WOPI CheckFileInfo endpoint."""
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")

    file_data = file_storage[file_id]

    return CheckFileInfo(
        BaseFileName=file_data["name"],
        Size=len(file_data["content"]),
        OwnerId="demo_user",
        UserId="demo_user",
        UserFriendlyName="Demo User",
        Version="1.0",
    )


@app.get("/wopi/files/{file_id}/contents")
async def get_file_contents(file_id: str):
    """WOPI GetFile endpoint."""
    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")

    return JSONResponse(
        content=file_storage[file_id]["content"],
        media_type="application/octet-stream",
    )


@app.post("/wopi/files/{file_id}/contents")
async def put_file_contents(file_id: str, request: Request):
    """WOPI PutFile endpoint."""
    content = await request.body()

    if file_id not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")

    file_storage[file_id]["content"] = content

    return JSONResponse(content={"status": "success"})


@app.post("/wopi/files")
async def create_file(request: Request):
    """Create a new file."""
    body = await request.json()
    file_name = body.get("name", "document.docx")
    file_content = body.get("content", b"")

    file_id = str(uuid.uuid4())
    file_storage[file_id] = {
        "name": file_name,
        "content": file_content,
    }

    return JSONResponse(content={"file_id": file_id})
