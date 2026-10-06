from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse

from spy_market_agent.api.schemas import (
    ApiErrorResponse,
    BacktestDetailResponse,
    BacktestRunListResponse,
    DataStatusResponse,
    EquityPageResponse,
    FillPageResponse,
    HealthResponse,
    ModelRunDetailResponse,
    ModelRunListResponse,
    OrderPageResponse,
    PaperOrderAttemptResponse,
    PaperOrderListResponse,
    PaperTradingStatusResponse,
    PredictionPageResponse,
    RiskDecisionPageResponse,
)
from spy_market_agent.api.services import (
    MAX_PAGE_LIMIT,
    ExecutionReadRepository,
    ReadRepository,
    ReadService,
)
from spy_market_agent.config import Settings
from spy_market_agent.execution.errors import (
    PaperExecutionError,
    PaperExecutionInputError,
    PaperExecutionNotFoundError,
)
from spy_market_agent.execution.repository import SQLitePaperExecutionRepository
from spy_market_agent.persistence.models import (
    PersistenceError,
    PersistenceInputError,
    PersistenceNotFoundError,
)
from spy_market_agent.persistence.repositories import SQLiteArtifactRepository
from spy_market_agent.run_ids import RUN_ID_PATTERN

DEFAULT_SQLITE_DATABASE_PATH = "./spy_market_agent.sqlite3"
RunIdParam = Annotated[str, Path(pattern=RUN_ID_PATTERN, min_length=1, max_length=128)]
LimitParam = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetParam = Annotated[int, Query(ge=0)]


def create_app(
    *,
    repository: ReadRepository | None = None,
    execution_repository: ExecutionReadRepository | None = None,
    service: ReadService | None = None,
    database_path: str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    read_service = service
    if read_service is None:
        sqlite_path = database_path or DEFAULT_SQLITE_DATABASE_PATH
        read_repository = repository or SQLiteArtifactRepository(sqlite_path)
        read_execution_repository = execution_repository or SQLitePaperExecutionRepository(
            sqlite_path
        )
        read_service = ReadService(
            read_repository,
            execution_repository=read_execution_repository,
            settings=settings,
        )

    app = FastAPI(title="SPY Market Agent Read API", version="1.0.0")

    @app.exception_handler(PersistenceNotFoundError)
    async def _not_found_handler(
        _request: Request,
        exc: PersistenceNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ApiErrorResponse(code=exc.code, message=str(exc)).model_dump(),
        )

    @app.exception_handler(PersistenceInputError)
    async def _persistence_input_handler(
        _request: Request,
        exc: PersistenceInputError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiErrorResponse(
                code=exc.code,
                message="Request parameters are invalid.",
            ).model_dump(),
        )

    @app.exception_handler(PersistenceError)
    async def _persistence_handler(_request: Request, exc: PersistenceError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ApiErrorResponse(
                code=exc.code,
                message="Persisted research data is unavailable or invalid.",
            ).model_dump(),
        )

    @app.exception_handler(PaperExecutionNotFoundError)
    async def _paper_not_found_handler(
        _request: Request,
        exc: PaperExecutionNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ApiErrorResponse(code=exc.code, message=str(exc)).model_dump(),
        )

    @app.exception_handler(PaperExecutionInputError)
    async def _paper_input_handler(
        _request: Request,
        exc: PaperExecutionInputError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiErrorResponse(
                code=exc.code,
                message="Request parameters are invalid.",
            ).model_dump(),
        )

    @app.exception_handler(PaperExecutionError)
    async def _paper_execution_handler(
        _request: Request,
        exc: PaperExecutionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ApiErrorResponse(
                code=exc.code,
                message="Persisted paper-execution data is unavailable or invalid.",
            ).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def _value_error_handler(_request: Request, _exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiErrorResponse(
                code="invalid_request",
                message="Request parameters are invalid.",
            ).model_dump(),
        )

    def service_dependency() -> ReadService:
        return read_service

    service_dependency_marker = Depends(service_dependency)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/data/status", response_model=DataStatusResponse)
    def data_status(reads: ReadService = service_dependency_marker) -> DataStatusResponse:
        return reads.data_status()

    @app.get("/api/v1/model-runs", response_model=ModelRunListResponse)
    def model_runs(reads: ReadService = service_dependency_marker) -> ModelRunListResponse:
        return reads.model_runs()

    @app.get("/api/v1/model-runs/{run_id}", response_model=ModelRunDetailResponse)
    def model_run_detail(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
    ) -> ModelRunDetailResponse:
        return reads.model_run_detail(run_id)

    @app.get("/api/v1/model-runs/{run_id}/predictions", response_model=PredictionPageResponse)
    def model_predictions(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> PredictionPageResponse:
        return reads.model_predictions(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/backtests", response_model=BacktestRunListResponse)
    def backtests(reads: ReadService = service_dependency_marker) -> BacktestRunListResponse:
        return reads.backtests()

    @app.get("/api/v1/backtests/{run_id}", response_model=BacktestDetailResponse)
    def backtest_detail(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
    ) -> BacktestDetailResponse:
        return reads.backtest_detail(run_id)

    @app.get("/api/v1/backtests/{run_id}/equity", response_model=EquityPageResponse)
    def backtest_equity(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> EquityPageResponse:
        return reads.equity(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/backtests/{run_id}/orders", response_model=OrderPageResponse)
    def backtest_orders(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> OrderPageResponse:
        return reads.orders(run_id, limit=limit, offset=offset)

    @app.get(
        "/api/v1/backtests/{run_id}/risk-decisions",
        response_model=RiskDecisionPageResponse,
    )
    def backtest_risk_decisions(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> RiskDecisionPageResponse:
        return reads.risk_decisions(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/backtests/{run_id}/fills", response_model=FillPageResponse)
    def backtest_fills(
        run_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> FillPageResponse:
        return reads.fills(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/paper-trading/status", response_model=PaperTradingStatusResponse)
    def paper_trading_status(
        reads: ReadService = service_dependency_marker,
    ) -> PaperTradingStatusResponse:
        return reads.paper_trading_status()

    @app.get("/api/v1/paper-orders", response_model=PaperOrderListResponse)
    def paper_orders(
        reads: ReadService = service_dependency_marker,
        limit: LimitParam = 100,
        offset: OffsetParam = 0,
    ) -> PaperOrderListResponse:
        return reads.paper_orders(limit=limit, offset=offset)

    @app.get("/api/v1/paper-orders/{client_order_id}", response_model=PaperOrderAttemptResponse)
    def paper_order_detail(
        client_order_id: RunIdParam,
        reads: ReadService = service_dependency_marker,
    ) -> PaperOrderAttemptResponse:
        return reads.paper_order_detail(client_order_id)

    _assert_read_only_routes(app.routes)
    return app


def _assert_read_only_routes(routes: Sequence[object]) -> None:
    state_changing = {"POST", "PUT", "PATCH", "DELETE"}
    for route in routes:
        methods = getattr(route, "methods", None)
        if methods is not None and state_changing.intersection(set(methods)):
            raise RuntimeError("Phase 8 API may only expose read-only application routes.")


__all__ = ["DEFAULT_SQLITE_DATABASE_PATH", "create_app"]
