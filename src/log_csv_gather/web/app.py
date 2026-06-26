from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from log_csv_gather.active_config import ActiveConfigError, ActiveConfigManager
from log_csv_gather.config import AppConfig, ConfigError, update_scheduler_settings
from log_csv_gather.scheduler import SchedulerError, WindowsTaskScheduler
from log_csv_gather.web.actions import (
    ActionRunner,
    DefaultActionRunner,
    allowed_actions_for_role,
    local_status_payload,
    record_action_result,
    tail_log_lines,
)
from log_csv_gather.web.jobs import JobAlreadyRunning, JobManager
from log_csv_gather.web.local_browser import list_drives, list_folders, validate_local_path
from log_csv_gather.web.runtime import build_url

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class SchedulerRequest(BaseModel):
    interval_minutes: int | None = Field(default=None, ge=1, le=1439)
    enabled: bool | None = None
    task_name: str | None = None


class RoleSwitchRequest(BaseModel):
    role: str


class PathValidationRequest(BaseModel):
    role: str
    path: str


class SetupRequest(BaseModel):
    role: str
    pc_id: str
    drive_root_folder_id: str
    machine_id: str | None = None
    source_root: str | None = None
    download_root: str | None = None


def create_app(
    config: AppConfig,
    config_path: Path,
    port: int,
    job_manager: JobManager | None = None,
    action_runner: ActionRunner | None = None,
    scheduler_service_factory: Callable[[AppConfig], WindowsTaskScheduler] | None = None,
) -> FastAPI:
    app = FastAPI(title="Log CSV Gather Local Dashboard", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.config_path = Path(config_path)
    app.state.port = port
    app.state.job_manager = job_manager or JobManager()
    app.state.job_manager.set_completion_callback(lambda job: record_action_result(current_config(), job))
    app.state.action_runner = action_runner or DefaultActionRunner(config, Path(config_path))
    app.state.scheduler_service_factory = scheduler_service_factory or (
        lambda current_config: WindowsTaskScheduler(current_config, config_path=current_config_path())
    )
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def current_config() -> AppConfig:
        return app.state.config

    def current_config_path() -> Path:
        return app.state.config_path

    def apply_config(updated_config: AppConfig, updated_config_path: str | Path | None = None) -> None:
        app.state.config = updated_config
        if updated_config_path is not None:
            app.state.config_path = Path(updated_config_path)
        if action_runner is None:
            app.state.action_runner = DefaultActionRunner(updated_config, current_config_path())

    def scheduler_service():
        return app.state.scheduler_service_factory(current_config())

    def active_config_manager() -> ActiveConfigManager:
        return ActiveConfigManager(current_config_path())

    @app.middleware("http")
    async def no_store_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        cfg = current_config()
        return {
            "ok": True,
            "app": "log-csv-gather",
            "role": cfg.role,
            "pc_id": cfg.pc_id,
            "host": cfg.web.host,
            "port": port,
            "url": build_url(cfg.web.host, port),
            "config_path": str(current_config_path()),
            "state_dir": str(cfg.state_dir),
        }

    @app.post("/api/actions/{action}", status_code=202)
    async def start_action(action: str) -> dict[str, object]:
        cfg = current_config()
        if action not in allowed_actions_for_role(cfg.role):
            raise HTTPException(status_code=403, detail=f"{action} is not allowed for role {cfg.role}")
        manager: JobManager = app.state.job_manager
        runner: Callable = app.state.action_runner
        try:
            job = manager.start(action, lambda progress: runner(action, progress))
        except JobAlreadyRunning as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "existing_job_id": exc.existing_job_id},
            ) from exc
        return {"job_id": job.id, "action": job.action, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        manager: JobManager = app.state.job_manager
        job = manager.get_or_none(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job.to_dict()

    @app.get("/api/feed")
    async def get_feed(limit: int = Query(50, ge=1, le=200)) -> dict[str, object]:
        manager: JobManager = app.state.job_manager
        return {"events": manager.feed(limit=limit)}

    @app.get("/api/status")
    async def get_status(details: bool = False) -> dict[str, object]:
        return local_status_payload(current_config(), details=details)

    @app.get("/api/logs/tail")
    async def get_log_tail(lines: int = Query(100, ge=1, le=1000)) -> dict[str, object]:
        return {"lines": tail_log_lines(current_config(), lines=lines)}

    @app.get("/api/local/drives")
    async def get_local_drives() -> dict[str, object]:
        return {"drives": list_drives()}

    @app.get("/api/local/folders")
    async def get_local_folders(path: str = Query(..., min_length=1)) -> dict[str, object]:
        return list_folders(path)

    @app.post("/api/local/validate-path")
    async def validate_path(payload: PathValidationRequest) -> dict[str, object]:
        return validate_local_path(payload.role, payload.path)

    @app.get("/api/scheduler")
    async def get_scheduler() -> dict[str, object]:
        return scheduler_service().status().to_dict()

    @app.post("/api/scheduler/register")
    async def register_scheduler(payload: SchedulerRequest | None = None) -> dict[str, object]:
        request_payload = payload or SchedulerRequest()
        try:
            updated_config = update_scheduler_settings(
                current_config_path(),
                enabled=request_payload.enabled,
                interval_minutes=request_payload.interval_minutes,
                task_name=request_payload.task_name,
            )
            apply_config(updated_config)
            status = scheduler_service().register_or_update(
                interval_minutes=updated_config.scheduler.interval_minutes,
                enabled=updated_config.scheduler.enabled,
            )
        except (ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return status.to_dict()

    @app.post("/api/scheduler/unregister")
    async def unregister_scheduler() -> dict[str, object]:
        try:
            scheduler_service().unregister()
            updated_config = update_scheduler_settings(current_config_path(), enabled=False)
            apply_config(updated_config)
            status = scheduler_service().status()
        except (ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return status.to_dict()

    @app.post("/api/scheduler/enable")
    async def enable_scheduler() -> dict[str, object]:
        try:
            updated_config = update_scheduler_settings(current_config_path(), enabled=True)
            apply_config(updated_config)
            status = scheduler_service().set_enabled(True)
        except (ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return status.to_dict()

    @app.post("/api/scheduler/disable")
    async def disable_scheduler() -> dict[str, object]:
        try:
            updated_config = update_scheduler_settings(current_config_path(), enabled=False)
            apply_config(updated_config)
            status = scheduler_service().set_enabled(False)
        except (ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return status.to_dict()

    @app.get("/api/config/active")
    async def get_active_config() -> dict[str, object]:
        return active_config_manager().status(current_config(), current_config_path())

    @app.post("/api/config/role")
    async def switch_active_role(payload: RoleSwitchRequest) -> dict[str, object]:
        try:
            scheduler_status = scheduler_service().status()
            scheduler_unregistered = False
            if scheduler_status.registered:
                scheduler_service().unregister()
                scheduler_unregistered = True
            selection = active_config_manager().switch_role(payload.role)
            apply_config(selection.config, selection.config_path)
        except (ActiveConfigError, ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = active_config_manager().status(current_config(), current_config_path())
        result["scheduler_unregistered"] = scheduler_unregistered
        result["restart_recommended"] = False
        return result

    @app.post("/api/config/active/reset")
    async def reset_active_config() -> dict[str, object]:
        try:
            result = active_config_manager().reset()
        except (ActiveConfigError, ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["restart_recommended"] = True
        result["role"] = current_config().role
        return result

    @app.post("/api/config/setup")
    async def save_setup(payload: SetupRequest) -> dict[str, object]:
        try:
            scheduler_status = scheduler_service().status()
            scheduler_unregistered = False
            if scheduler_status.registered:
                scheduler_service().unregister()
                scheduler_unregistered = True
            values = payload.model_dump(exclude_none=True)
            selection = active_config_manager().save_setup(values)
            apply_config(selection.config, selection.config_path)
        except (ActiveConfigError, ConfigError, SchedulerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cfg = current_config()
        validation_path = cfg.source_root if cfg.role == "uploader" else cfg.download_root
        path_validation = (
            validate_local_path(cfg.role, validation_path)
            if validation_path is not None
            else {"role": cfg.role, "status": "error", "message": "path is not configured"}
        )
        result = active_config_manager().status(cfg, current_config_path())
        result.update(
            {
                "scheduler_unregistered": scheduler_unregistered,
                "doctor_verification_required": True,
                "restart_recommended": False,
                "path_validation": path_validation,
            }
        )
        return result

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        cfg = current_config()
        active_status = active_config_manager().status(cfg, current_config_path())
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "role": cfg.role,
                "pc_id": cfg.pc_id,
                "host": cfg.web.host,
                "port": port,
                "url": build_url(cfg.web.host, port),
                "config_path": str(current_config_path()),
                "state_dir": str(cfg.state_dir),
                "source_root": str(cfg.source_root) if cfg.source_root else "-",
                "download_root": str(cfg.download_root) if cfg.download_root else "-",
                "drive_root_folder_id": cfg.drive_root_folder_id,
                "group_name": cfg.group_name or "-",
                "machine_id": cfg.machine_id or "-",
                "scheduler_interval": cfg.scheduler.interval_minutes,
                "scheduler_enabled": cfg.scheduler.enabled,
                "task_name": cfg.scheduler.task_name or f"LogCsvGather-{cfg.pc_id}-{cfg.role}",
                "active_config_path": active_status["active_path"],
                "active_config_exists": active_status["active_exists"],
                "available_roles": active_status["available_roles"],
                "setup_required": active_status["setup_required"],
            },
        )

    return app
