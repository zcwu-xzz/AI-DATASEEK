from types import SimpleNamespace

import pytest

from app.application.services.jupyter_service import JupyterService
from app.interfaces.api.session_routes import open_jupyter_notebook
from app.interfaces.schemas.session import OpenJupyterRequest


@pytest.mark.asyncio
async def test_jupyter_rejects_non_python_before_allocating_resources():
    service = JupyterService()
    with pytest.raises(ValueError, match="Only Python"):
        await service.open_notebook(
            session_id="session-a",
            user_id="user-a",
            code="console.log('x')",
            language="javascript",
            sandbox_id=None,
        )


def test_jupyter_names_are_isolated_by_session():
    assert JupyterService._name("session-a") != JupyterService._name("session-b")
    assert JupyterService._volume("session-a") != JupyterService._volume("session-b")


@pytest.mark.asyncio
async def test_jupyter_route_keeps_internal_token_out_of_response():
    class Sandbox:
        async def open_browser_url(self, url):
            assert "private-token" in url

    class AgentService:
        async def ensure_interactive_sandbox(self, session_id, user_id):
            return SimpleNamespace(id=session_id, sandbox_id="sandbox-a"), Sandbox()

    class NotebookService:
        async def open_notebook(self, **kwargs):
            assert kwargs["session_id"] == "session-a"
            assert kwargs["user_id"] == "user-a"
            return {
                "notebook_path": "DataSeek.ipynb",
                "browser_url": "http://private:8888/lab?token=private-token",
            }

    response = await open_jupyter_notebook(
        session_id="session-a",
        request=OpenJupyterRequest(code="print(1)", language="python"),
        current_user=SimpleNamespace(id="user-a"),
        agent_service=AgentService(),
        jupyter_service=NotebookService(),
    )
    assert response.data.notebook_path == "DataSeek.ipynb"
    assert "token" not in response.data.model_dump()
