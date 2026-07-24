"""Gradio Blocks view for the fixed M3.6 workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from agent_factory.interfaces.demo.contracts import (
    DemoActionResult,
    DemoPhase,
    DemoSession,
)
from agent_factory.interfaces.demo.workflow import DemoWorkflow

if TYPE_CHECKING:
    import gradio as gr


class _ClickableButton(Protocol):
    """Typed view of Gradio's runtime-attached event registration method."""

    def click(self, **kwargs: Any) -> Any: ...


DEMO_CSS = """
.gradio-container {
  width: min(1180px, 100%) !important;
  max-width: 1180px !important;
  margin: 0 auto !important;
  box-sizing: border-box;
  color: #18211d;
}
#demo-header {
  border-bottom: 1px solid #d9e0dc;
  padding: 12px 2px 16px;
}
#demo-header h1 {
  font-size: 30px;
  line-height: 1.2;
  letter-spacing: 0;
  margin: 0;
}
#demo-header p {
  color: #58645e;
  margin: 6px 0 0;
}
.status-band {
  background: #f4f7f5;
  border-left: 4px solid #247a52;
  border-radius: 4px;
  padding: 10px 14px;
}
.status-band, .status-band strong {
  color: #18211d !important;
}
.status-band code {
  background: #e2eae5 !important;
  color: #163d2b !important;
}
.workflow-actions button {
  border-radius: 6px !important;
  min-height: 44px;
}
.section-heading h2 {
  font-size: 18px;
  letter-spacing: 0;
  margin: 14px 0 4px;
}
.error-band {
  background: #fff6e8;
  border: 1px solid #d8952c;
  border-radius: 4px;
  color: #6a3c00;
  padding: 10px 14px;
}
@media (max-width: 700px) {
  .gradio-container { padding: 0 10px !important; }
  #demo-header h1 { font-size: 24px; }
  .workflow-actions { gap: 8px !important; }
}
"""


def create_demo_app(workflow: DemoWorkflow) -> gr.Blocks:
    """Build the optional UI without importing Gradio from core modules."""

    try:
        import gradio as gradio
    except ModuleNotFoundError as exc:
        if exc.name == "gradio":
            raise RuntimeError(
                "Gradio is not installed; install agent-factory[demo]"
            ) from None
        raise

    with gradio.Blocks(
        title="Agent Factory Demo",
        fill_width=True,
        analytics_enabled=False,
    ) as demo:
        state = gradio.State(value=None)

        gradio.Markdown(
            "# Agent Factory\n固定 Writer 生产、运行、评估与晋升工作台",
            elem_id="demo-header",
        )
        status = gradio.Markdown(
            _status_text(DemoSession()),
            elem_classes="status-band",
        )

        with gradio.Row(elem_classes="workflow-actions"):
            initialize_button = gradio.Button(
                "1 初始化工厂",
                variant="primary",
                interactive=True,
            )
            run_button = gradio.Button(
                "2 运行并评估",
                interactive=False,
            )
            promote_button = gradio.Button(
                "3 批准并晋升",
                interactive=False,
            )

        error = gradio.Markdown(
            "",
            visible=False,
            elem_classes="error-band",
        )

        gradio.Markdown("## 来源与版本", elem_classes="section-heading")
        sources = gradio.Dataframe(
            value=[],
            headers=["类型", "标识", "版本", "Checksum"],
            datatype=["str", "str", "str", "str"],
            type="array",
            interactive=False,
            wrap=True,
            max_height=260,
        )

        gradio.Markdown("## RunResult", elem_classes="section-heading")
        run_result = gradio.JSON(
            value={},
            label="脱敏运行结果",
        )

        gradio.Markdown("## 审计时间线", elem_classes="section-heading")
        audit = gradio.Dataframe(
            value=[],
            headers=[
                "时间",
                "事件",
                "实体类型",
                "实体标识",
                "Revision",
                "Correlation ID",
            ],
            datatype=["str", "str", "str", "str", "number", "str"],
            type="array",
            interactive=False,
            wrap=True,
            max_height=420,
        )

        outputs = [
            state,
            status,
            sources,
            run_result,
            audit,
            error,
            initialize_button,
            run_button,
            promote_button,
        ]

        demo.load(
            fn=DemoSession,
            outputs=state,
            api_visibility="private",
            show_progress="hidden",
        )

        async def initialize(value: object) -> tuple[Any, ...]:
            session = _session(value)
            return render(await workflow.initialize_factory(session))

        async def run(value: object) -> tuple[Any, ...]:
            session = _session(value)
            return render(await workflow.run_and_evaluate(session))

        async def promote(value: object) -> tuple[Any, ...]:
            session = _session(value)
            return render(await workflow.approve_and_promote(session))

        def render(result: DemoActionResult) -> tuple[Any, ...]:
            session = result.session
            error_component = (
                gradio.Markdown(
                    value=_error_text(result),
                    visible=True,
                    elem_classes="error-band",
                )
                if result.error is not None
                else gradio.Markdown(value="", visible=False)
            )
            return (
                session,
                _status_text(session),
                _source_rows(session),
                _run_payload(session),
                _audit_rows(session),
                error_component,
                gradio.Button(
                    "1 初始化工厂",
                    variant=(
                        "primary" if session.phase is DemoPhase.NEW else "secondary"
                    ),
                    interactive=session.phase is DemoPhase.NEW,
                ),
                gradio.Button(
                    "2 运行并评估",
                    variant=(
                        "primary"
                        if session.phase is DemoPhase.READY_TO_RUN
                        else "secondary"
                    ),
                    interactive=session.phase is DemoPhase.READY_TO_RUN,
                ),
                gradio.Button(
                    "3 批准并晋升",
                    variant=(
                        "primary"
                        if session.phase is DemoPhase.AWAITING_REVIEW
                        else "secondary"
                    ),
                    interactive=session.phase is DemoPhase.AWAITING_REVIEW,
                ),
            )

        cast("_ClickableButton", initialize_button).click(
            fn=initialize,
            inputs=state,
            outputs=outputs,
            api_visibility="private",
            show_progress="minimal",
            concurrency_limit=1,
            concurrency_id="m3.6-fixed-demo",
        )
        cast("_ClickableButton", run_button).click(
            fn=run,
            inputs=state,
            outputs=outputs,
            api_visibility="private",
            show_progress="minimal",
            concurrency_limit=1,
            concurrency_id="m3.6-fixed-demo",
        )
        cast("_ClickableButton", promote_button).click(
            fn=promote,
            inputs=state,
            outputs=outputs,
            api_visibility="private",
            show_progress="minimal",
            concurrency_limit=1,
            concurrency_id="m3.6-fixed-demo",
        )

    return cast("gr.Blocks", demo)


def create_demo_theme() -> Any:
    """Return one restrained light palette for either system color scheme."""

    try:
        import gradio as gradio
    except ModuleNotFoundError as exc:
        if exc.name == "gradio":
            raise RuntimeError(
                "Gradio is not installed; install agent-factory[demo]"
            ) from None
        raise
    return gradio.themes.Base(
        primary_hue="green",
        secondary_hue="gray",
        neutral_hue="gray",
        radius_size="sm",
    ).set(
        body_background_fill="#f4f6f5",
        body_background_fill_dark="#f4f6f5",
        body_text_color="#18211d",
        body_text_color_dark="#18211d",
        body_text_color_subdued="#58645e",
        body_text_color_subdued_dark="#58645e",
        background_fill_primary="#ffffff",
        background_fill_primary_dark="#ffffff",
        background_fill_secondary="#f4f7f5",
        background_fill_secondary_dark="#f4f7f5",
        block_background_fill="#ffffff",
        block_background_fill_dark="#ffffff",
        block_border_color="#d9e0dc",
        block_border_color_dark="#d9e0dc",
        panel_background_fill="#ffffff",
        panel_background_fill_dark="#ffffff",
        block_radius="6px",
        code_background_fill="#e8eeea",
        code_background_fill_dark="#e8eeea",
        table_text_color="#18211d",
        table_text_color_dark="#18211d",
        table_border_color="#d9e0dc",
        table_border_color_dark="#d9e0dc",
        table_even_background_fill="#ffffff",
        table_even_background_fill_dark="#ffffff",
        table_odd_background_fill="#f7f9f8",
        table_odd_background_fill_dark="#f7f9f8",
        button_primary_background_fill="#247a52",
        button_primary_background_fill_dark="#247a52",
        button_primary_background_fill_hover="#1c6242",
        button_primary_background_fill_hover_dark="#1c6242",
        button_primary_border_color="#247a52",
        button_primary_border_color_dark="#247a52",
        button_primary_text_color="#ffffff",
        button_primary_text_color_dark="#ffffff",
        button_secondary_background_fill="#ffffff",
        button_secondary_background_fill_dark="#ffffff",
        button_secondary_background_fill_hover="#edf2ef",
        button_secondary_background_fill_hover_dark="#edf2ef",
        button_secondary_border_color="#adb8b2",
        button_secondary_border_color_dark="#adb8b2",
        button_secondary_text_color="#25312b",
        button_secondary_text_color_dark="#25312b",
        input_background_fill="#ffffff",
        input_background_fill_dark="#ffffff",
        input_border_color="#c9d2cd",
        input_border_color_dark="#c9d2cd",
    )


def _session(value: object) -> DemoSession:
    if isinstance(value, DemoSession):
        return value
    return DemoSession.model_validate(value)


def _status_text(session: DemoSession) -> str:
    instance = str(session.instance_id) if session.instance_id is not None else "-"
    revision = str(session.revision) if session.revision is not None else "-"
    active = ", ".join(session.active_nodes) if session.active_nodes else "-"
    return (
        f"**阶段** `{session.phase.value}` &nbsp; "
        f"**Revision** `{revision}` &nbsp; "
        f"**Active nodes** `{active}`  \n"
        f"**Instance** `{instance}`"
    )


def _source_rows(session: DemoSession) -> list[list[str]]:
    return [
        [source.source_type, source.source_id, source.version, source.checksum]
        for source in session.sources
    ]


def _run_payload(session: DemoSession) -> dict[str, object]:
    if session.run_view is None:
        return {}
    return session.run_view.model_dump(mode="json")


def _audit_rows(session: DemoSession) -> list[list[str | int | None]]:
    return [
        [
            row.created_at.isoformat(),
            row.event_type,
            row.entity_type,
            row.entity_id,
            row.entity_revision,
            str(row.correlation_id),
        ]
        for row in session.audit_rows
    ]


def _error_text(result: DemoActionResult) -> str:
    if result.error is None:
        return ""
    correlation = (
        str(result.error.correlation_id)
        if result.error.correlation_id is not None
        else "-"
    )
    return (
        f"**{result.error.code}**  \n"
        f"{result.error.message}  \n"
        f"Correlation ID: `{correlation}`"
    )
