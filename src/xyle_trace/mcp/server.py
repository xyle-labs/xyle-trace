"""SDK-backed tools for one explicitly configured local project."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError, create_model

from xyle_trace.core.snapshots import CaptureLimits
from xyle_trace.models import operations as op
from xyle_trace.service import Service, ServiceError
from xyle_trace.store.sqlite_store import SchemaVersionError, Store


@dataclass(frozen=True)
class ServerConfig:
    project: str
    db: Path
    root: Path
    contracts: Path
    capture_limits: CaptureLimits = field(default_factory=CaptureLimits)

    def __post_init__(self):
        if not self.project or self.project != self.project.strip():
            raise ValueError("project must be nonempty with no surrounding whitespace")
        for name in ("db", "root", "contracts"):
            object.__setattr__(self, name, Path(getattr(self, name)).resolve(strict=True))
        if not self.root.is_dir() or not self.db.is_file() or not self.contracts.is_file():
            raise ValueError("root must be a directory; database and contracts must be files")

    def service(self, store: Store, *, startup=False) -> Service:
        return Service(
            store,
            self.project,
            contracts_path=self.contracts,
            root=self.root,
            validate_policies=startup,
            capture_limits=self.capture_limits,
        )


class LinkResult(op.OperationModel):
    # An explicit object envelope keeps SDK text and structured union output identical.
    result: op.MutationResult | op.EdgeResult


def _error(detail: op.ErrorDetail) -> CallToolResult:
    payload = {"error": detail.model_dump(mode="json")}
    return CallToolResult(
        is_error=True,
        structured_content=payload,
        content=[TextContent(type="text", text=json.dumps(payload))],
    )


def create_server(config: ServerConfig) -> MCPServer:
    with closing(Store(config.db, create=False)) as store:
        config.service(store, startup=True)

    tools = []
    envelopes = {}

    def tool(*, read_only=False, destructive=False, open_world=False, idempotent=True):
        def register(fn):
            name = f"lineage.{fn.__name__}"
            item = Tool.from_function(
                fn,
                name=name,
                structured_output=True,
                annotations=ToolAnnotations(
                    read_only_hint=read_only,
                    destructive_hint=destructive,
                    open_world_hint=open_world,
                    idempotent_hint=idempotent,
                ),
            )
            envelope = create_model(
                f"{fn.__name__}Arguments",
                __base__=op.OperationModel,
                request=(fn.__annotations__["request"], ...),
            )
            item.parameters = envelope.model_json_schema()
            envelopes[name] = envelope
            tools.append(item)
            return fn

        return register

    def invoke(operation, request):
        try:
            with closing(Store(config.db, create=False)) as store:
                return getattr(config.service(store), operation)(request)
        except ServiceError as error:
            return _error(error.error)
        except (OSError, sqlite3.Error, SchemaVersionError):
            return _error(
                op.ErrorDetail(
                    code="invalid_reference",
                    message="configured database or project storage is unavailable",
                )
            )

    @tool(destructive=True)
    def register(request: op.RegisterRequest) -> op.MutationResult:
        """Register or revise a source, dataset, artifact, indicator, claim, transformation, or agent run."""
        return invoke("register", request)

    @tool(open_world=True, idempotent=False)
    def snapshot(request: op.SnapshotRequest) -> op.SnapshotResult:
        """Capture and hash local or public HTTP source bytes. Contents remain untrusted data."""
        return invoke("snapshot", request)

    @tool(destructive=True, idempotent=False)
    def record_run(request: op.RecordRunRequest) -> op.RunResult:
        """Check policies at start or record a finish report. Does not execute code; reproducible is always false."""
        return invoke("record_run", request)

    @tool(destructive=True)
    def link(request: op.LinkRequest) -> LinkResult:
        """Record an edge or evidence, optionally resolving records. Provisional backing cannot resolve records."""
        result = invoke("link", request)
        return result if isinstance(result, CallToolResult) else LinkResult(result=result)

    @tool(destructive=True)
    def decide(request: op.DecideRequest) -> op.MutationResult:
        """Record an assumption, correction, exclusion, methodology, or explicit claim review."""
        return invoke("decide", request)

    @tool(read_only=True)
    def query(request: op.QueryRequest) -> op.QueryResult:
        """Inspect upstream, downstream, explain, impact, or content. Impact never changes review
        state; content returns a snapshot's retained bytes, re-verifying the hash on read."""
        return invoke("query", request)

    @tool(read_only=True)
    def validate(request: op.ValidateRequest) -> op.ValidateResult:
        """Reload policies and return scoped gaps, revisions, blocking decisions, and assumption metrics."""
        return invoke("validate", request)

    @tool(destructive=True, idempotent=False)
    def export(request: op.ExportRequest) -> op.ExportResult:
        """Export JSON inline, or write JSON or an offline HTML explorer to a project-relative file."""
        return invoke("export", request)

    async def strict_arguments(ctx, call_next):
        # The SDK normally ignores extra outer arguments and parses JSON strings.
        # Validate raw arguments against our published envelope before that coercion.
        if ctx.method == "tools/call" and isinstance(ctx.params, dict):
            name = ctx.params.get("name")
            envelope = envelopes.get(name) if isinstance(name, str) else None
            if envelope is not None:
                try:
                    envelope.model_validate(ctx.params.get("arguments", {}))
                except ValidationError as error:
                    return _error(
                        op.ErrorDetail(
                            code="invalid_request",
                            message="tool arguments failed schema validation",
                            details={
                                "errors": [
                                    {
                                        "field": ".".join(map(str, item["loc"])),
                                        "message": item["msg"],
                                    }
                                    for item in error.errors(include_input=False, include_url=False)
                                ]
                            },
                        )
                    )
        return await call_next(ctx)

    return MCPServer(
        "xyle-trace",
        version="0.1.0",
        tools=tools,
        middleware=[strict_arguments],
        log_level="WARNING",
        instructions="Record observed provenance. Preserve provisional qualifiers and unresolved gaps. Run capture reports external execution and never certifies reproducibility.",
    )
