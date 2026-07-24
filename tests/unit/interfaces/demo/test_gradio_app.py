"""Optional Gradio component smoke tests without starting a server."""

from agent_factory.container import build_container
from agent_factory.interfaces.demo.gradio_app import create_demo_app
from agent_factory.interfaces.demo.workflow import DemoWorkflow
from agent_factory.sdk import AgentFactoryClient
from agent_factory.settings import Settings


def test_gradio_app_builds_fixed_private_workflow() -> None:
    container = build_container(Settings.model_validate({"auth_token": "x" * 32}))
    workflow = DemoWorkflow(
        client_factory=lambda: AgentFactoryClient(
            base_url="http://127.0.0.1:8000",
            token="x" * 32,
        ),
        runtime=container.demo_runtime,
    )

    app = create_demo_app(workflow)
    config = app.get_config_file()
    buttons = [
        component for component in config["components"] if component["type"] == "button"
    ]

    assert [button["props"]["value"] for button in buttons] == [
        "1 初始化工厂",
        "2 运行并评估",
        "3 批准并晋升",
    ]
    assert [button["props"]["interactive"] for button in buttons] == [
        True,
        False,
        False,
    ]
    assert all(
        dependency["api_visibility"] == "private"
        for dependency in config["dependencies"]
    )
