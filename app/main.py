from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.errors import InventoryDenied, ZtpError
from app.inventory.factory import build_provider
from app.models.contracts import DeviceEvent, ObservedDevice, RegistrationResponse, StatusResponse
from app.persistence.database import build_engine
from app.persistence.models import Attempt, ValidationJob
from app.persistence.repository import Repository
from app.services.authorization import authorize_identity, authorize_intent
from app.services.hashing import stable_hash
from app.services.rendering import configuration_manifest
from app.services.workflow import device_event
from app.settings import Settings
from app.vendors.cisco_nxos import CiscoNxosAdapter

bearer = HTTPBearer(auto_error=False)


def create_app(settings: Settings | None = None, provider=None, engine=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings()
        db = engine or build_engine(config.database_url.get_secret_value())
        inventory = provider or build_provider(config)
        app.state.config = config
        app.state.db = db
        app.state.inventory = inventory
        app.state.adapter = CiscoNxosAdapter(config.catalog_path)
        app.state.repository = Repository(db)
        try:
            yield
        finally:
            if provider is None:
                inventory.close()
            if engine is None:
                db.dispose()

    app = FastAPI(title="NX-OS POAP controller", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(ZtpError)
    async def domain_error(request: Request, exc: ZtpError):
        headers = {"Retry-After": "60"} if exc.status == 429 else {}
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "database_unavailable",
                    "message": "Workflow storage is unavailable",
                }
            },
        )

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health(request: Request):
        mode = request.app.state.config.execution_mode
        return {"status": "ok", "mode": mode, "execution_enabled": mode == "configuration-only"}

    @app.get("/ready")
    def ready(request: Request):
        with request.app.state.db.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(select(Attempt).limit(1))
            connection.execute(select(ValidationJob.attempt_id).limit(1))
        request.app.state.inventory.check_ready()
        return {"status": "ready"}

    @app.post("/api/v1/ztp/register", response_model=RegistrationResponse)
    def register(observed: ObservedDevice, request: Request):
        state = request.app.state
        source = request.client.host if request.client else "unknown"
        state.repository.rate_limit(source, state.config.registration_limit_per_minute)
        device = state.inventory.get_device_by_serial(observed.serial_number)
        authorize_identity(device, observed)
        intent = state.inventory.get_device_intent(device.id)
        if intent.device.id != device.id:
            raise InventoryDenied()
        authorize_intent(intent, observed)
        manifest = state.adapter.build_manifest(intent, observed)
        manifest, config_body = configuration_manifest(manifest, intent, state.config)
        return state.repository.register(
            intent,
            observed,
            manifest,
            state.config.status_token_ttl_seconds,
            config_body,
            state.config.validation_delay_seconds,
            state.config.validation_deadline_seconds,
        )

    @app.get("/api/v1/ztp/status/{provisioning_id}", response_model=StatusResponse)
    def status(
        provisioning_id: UUID,
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if credentials is None:
            raise ZtpError("invalid_status_token", 401, "Valid attempt status token required")
        attempt = request.app.state.repository.status(str(provisioning_id), credentials.credentials)
        # Recheck current policy before releasing operational state.
        intent = request.app.state.inventory.get_device_intent(attempt.device_id)
        authorize_intent(intent, ObservedDevice.model_validate(attempt.observed))
        return StatusResponse(
            provisioning_id=attempt.id,
            state=attempt.state,
            plan_hash=attempt.plan_hash,
            mode=attempt.manifest["mode"],
            failure_reason=attempt.failure_reason,
            validation=request.app.state.repository.validation_evidence(attempt.id),
        )

    def authorized_config_attempt(request, attempt_id, credentials):
        if credentials is None:
            raise ZtpError("invalid_status_token", 401, "Attempt token required")
        attempt = request.app.state.repository.status(str(attempt_id), credentials.credentials)
        if (
            request.app.state.config.execution_mode != "configuration-only"
            or attempt.manifest["mode"] != "configuration-only"
        ):
            raise ZtpError("execution_disabled", 403, "Configuration execution is disabled")
        intent = request.app.state.inventory.get_device_intent(attempt.device_id)
        authorize_intent(intent, ObservedDevice.model_validate(attempt.observed))
        if stable_hash(intent.model_dump(mode="json")) != attempt.manifest["intent_hash"]:
            raise ZtpError("intent_changed", 409, "Inventory intent changed")
        return attempt

    @app.get("/api/v1/ztp/config/{provisioning_id}")
    def configuration(
        provisioning_id: UUID,
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        attempt = authorized_config_attempt(request, provisioning_id, credentials)
        if attempt.state in {"FAILED", "VALIDATED"} or attempt.config_body is None:
            raise ZtpError("artifact_unavailable", 409, "Configuration is no longer available")
        return Response(
            content=attempt.config_body,
            media_type="text/plain",
            headers={"X-Content-SHA256": attempt.manifest["config"]["sha256"]},
        )

    @app.post("/api/v1/ztp/status/{provisioning_id}")
    def progress(
        provisioning_id: UUID,
        event: DeviceEvent,
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        authorized_config_attempt(request, provisioning_id, credentials)
        return device_event(request.app.state.db, str(provisioning_id), event)

    return app
