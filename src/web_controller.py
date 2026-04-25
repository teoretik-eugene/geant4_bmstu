from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from simulations import SimulationConfig
from vis_graph import plot_energy_analysis


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Geant4 Simulation API")
simulation_statuses: Dict[str, Dict[str, Any]] = {}


class SimulationRequest(BaseModel):
    task_id: int = 0
    particle: str = "He3"
    energy_mev: float = 40.0
    events: int = 100
    collect_tracks: bool = False
    input_data: Optional[dict] = None
    world_xy_mm: float = 500.0
    world_z_mm: float = 500.0
    screen_xy_mm: float = 250.0
    first_screen_z_mm: float = 15.0
    electronics_gap_mm: float = 0.1
    electronics_thickness_mm: float = 0.05
    electronics_material: str = "G4_Si"
    build_energy_plots: bool = True


class SimulationResponse(BaseModel):
    simulation_id: str
    status: str
    message: str
    created_at: str


class SimulationStatus(BaseModel):
    simulation_id: str = Field(..., description="Simulation ID")
    status: str = Field(..., description="Execution status")
    result: Optional[Dict[str, Any]] = Field(None, description="Simulation result payload")
    error: Optional[str] = Field(None, description="Execution error")
    created_at: str = Field(..., description="Creation timestamp")
    completed_at: Optional[str] = Field(None, description="Completion timestamp")


def _build_report(result_dict: Dict[str, Any]) -> Dict[str, Any]:
    total_particles = int(result_dict.get("total_particles") or 0)
    out_primary = int(result_dict.get("total_out_primary_particles") or 0)
    out_secondary = int(result_dict.get("total_out_secondary_particles") or 0)
    stopped_primary = max(total_particles - out_primary, 0)
    transmission = (out_primary / total_particles) if total_particles else 0.0
    stopping_eff = (stopped_primary / total_particles) if total_particles else 0.0

    screen_info = result_dict.get("screen_info") or {}
    materials = screen_info.get("Materials") or []
    layer_rows = []
    for idx, mat in enumerate(materials):
        layer_rows.append(
            {
                "index": idx + 1,
                "name": mat.get("Name", f"Layer_{idx + 1}"),
                "thickness_mm": float(mat.get("Thickness_mm", 0.0) or 0.0),
                "primary_stuck": int(mat.get("Primary_stuck_count", 0) or 0),
                "secondary_stuck": int(mat.get("Secondary_stuck_count", 0) or 0),
                "edep_mev": float(mat.get("Edep", 0.0) or 0.0),
            }
        )

    return {
        "total_particles": total_particles,
        "out_primary_particles": out_primary,
        "out_secondary_particles": out_secondary,
        "stopped_primary_particles": stopped_primary,
        "transmission_rate": transmission,
        "stopping_efficiency": stopping_eff,
        "layers": layer_rows,
        "electronics": screen_info.get("Electronics"),
        "energy_summary": result_dict.get("energy_summary"),
    }


def _build_generated_files(simulation_id: str, result_dir: str) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    analysis_dir = Path(result_dir) / "energy_analysis"
    if not analysis_dir.exists():
        return files

    for path in sorted(analysis_dir.glob("*.png")):
        files.append(
            {
                "name": path.name,
                "abs_path": str(path.resolve()),
                "url_path": f"/simulations/{simulation_id}/plots/{path.name}",
            }
        )
    return files


_SIM_CONFIG_FIELD_NAMES = {f.name for f in fields(SimulationConfig)}


def _to_simulation_config_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in payload.items() if k in _SIM_CONFIG_FIELD_NAMES}


def run_simulation_process(simulation_id: str, config_dict: dict, result_dir: str) -> Dict[str, Any]:
    config_file = os.path.join(result_dir, "config.json")
    result_file = os.path.join(result_dir, "result.json")
    try:
        runner_config = _to_simulation_config_dict(config_dict)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(runner_config, f)

        cmd = [
            sys.executable,
            "-c",
            f"""
import json
import sys
sys.path.insert(0, ".")
from main import SimulationRunner, SimulationConfig

with open(r\"{config_file}\", "r", encoding="utf-8") as f:
    config_dict = json.load(f)

cfg = SimulationConfig(**config_dict)
runner = SimulationRunner()
result = runner.run(cfg)

with open(r\"{result_file}\", "w", encoding="utf-8") as f:
    json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
""",
        ]

        process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(__file__),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=900)
        if process.returncode != 0:
            logger.error("Simulation subprocess failed. stdout=%s stderr=%s", stdout, stderr)
            return {"status": "failed", "result": None, "error": f"Process failed: {stderr}"}

        if not os.path.exists(result_file):
            return {"status": "failed", "result": None, "error": "Result file not found"}

        with open(result_file, "r", encoding="utf-8") as f:
            result_data = json.load(f)

        generated_files: List[Dict[str, str]] = []
        if bool(config_dict.get("build_energy_plots", True)):
            try:
                cfg = SimulationConfig(**_to_simulation_config_dict(config_dict))
                plot_result = SimpleNamespace(
                    energy_profiles=result_data.get("energy_profiles"),
                    exit_energies=result_data.get("exit_energies"),
                    screen_info=result_data.get("screen_info"),
                )
                plot_energy_analysis(
                    result=plot_result,
                    cfg=cfg,
                    data=config_dict.get("input_data") or {},
                    dir=result_dir,
                )
                generated_files = _build_generated_files(simulation_id, result_dir)
            except Exception as plot_exc:
                logger.warning("Failed to build plots for %s: %s", simulation_id, plot_exc)

        payload = {
            "result": result_data,
            "report": _build_report(result_data),
            "input_data": config_dict.get("input_data"),
            "generated_files": generated_files,
        }
        return {"status": "completed", "result": payload, "error": None}

    except subprocess.TimeoutExpired:
        return {"status": "timeout", "result": None, "error": "Simulation timed out after 900 seconds"}
    except Exception as exc:
        return {"status": "error", "result": None, "error": f"Unexpected error: {exc}"}
    finally:
        for tmp_file in (config_file, result_file):
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass


async def monitor_simulation(simulation_id: str, config_dict: dict) -> None:
    try:
        result_dir = tempfile.mkdtemp(prefix=f"sim_{simulation_id}_")
        simulation_statuses[simulation_id].update({"status": "running", "result_dir": result_dir})

        result = run_simulation_process(simulation_id, config_dict, result_dir)
        simulation_statuses[simulation_id].update(
            {
                "status": result["status"],
                "result": result["result"],
                "error": result["error"],
                "completed_at": datetime.now().isoformat(),
            }
        )
        logger.info("Simulation %s completed with status: %s", simulation_id, result["status"])
    except Exception as exc:
        simulation_statuses[simulation_id].update(
            {
                "status": "error",
                "error": f"Monitoring error: {exc}",
                "completed_at": datetime.now().isoformat(),
            }
        )
        logger.error("Error monitoring simulation %s: %s", simulation_id, exc)


@app.post("/simulate", response_model=SimulationResponse)
async def simulate(request: SimulationRequest, background_tasks: BackgroundTasks):
    simulation_id = str(uuid.uuid4())
    simulation_statuses[simulation_id] = {
        "simulation_id": simulation_id,
        "status": "pending",
        "request": request.model_dump(),
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    }
    background_tasks.add_task(monitor_simulation, simulation_id, request.model_dump())
    return SimulationResponse(
        simulation_id=simulation_id,
        status="started",
        message=f"Simulation {simulation_id} started successfully",
        created_at=simulation_statuses[simulation_id]["created_at"],
    )


@app.get("/simulations/{simulation_id}", response_model=SimulationStatus)
async def get_simulation_status(simulation_id: str):
    if simulation_id not in simulation_statuses:
        raise HTTPException(status_code=404, detail="Simulation not found")
    status_data = simulation_statuses[simulation_id].copy()
    if "simulation_id" not in status_data:
        status_data["simulation_id"] = simulation_id
    return SimulationStatus(**status_data)


@app.get("/simulations")
async def list_simulations(limit: int = 10, offset: int = 0):
    result: List[Dict[str, Any]] = []
    simulations = list(simulation_statuses.items())[offset : offset + limit]
    for sim_id, sim_data in simulations:
        status_data = sim_data.copy()
        if "simulation_id" not in status_data:
            status_data["simulation_id"] = sim_id
        result.append(status_data)
    return result


@app.get("/simulations/{simulation_id}/plots/{plot_name}")
async def get_simulation_plot(simulation_id: str, plot_name: str):
    sim_data = simulation_statuses.get(simulation_id)
    if not sim_data:
        raise HTTPException(status_code=404, detail="Simulation not found")
    result_dir = sim_data.get("result_dir")
    if not result_dir:
        raise HTTPException(status_code=404, detail="No artifacts for this simulation")

    safe_name = os.path.basename(plot_name)
    plot_path = Path(result_dir) / "energy_analysis" / safe_name
    if not plot_path.exists():
        raise HTTPException(status_code=404, detail="Plot not found")
    return FileResponse(str(plot_path))


@app.delete("/simulations/{simulation_id}")
async def delete_simulation(simulation_id: str):
    if simulation_id not in simulation_statuses:
        raise HTTPException(status_code=404, detail="Simulation not found")
    sim_data = simulation_statuses[simulation_id]
    if "result_dir" in sim_data and os.path.exists(sim_data["result_dir"]):
        try:
            shutil.rmtree(sim_data["result_dir"])
        except Exception:
            pass
    del simulation_statuses[simulation_id]
    return {"message": f"Simulation {simulation_id} deleted"}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_simulations": len(
            [s for s in simulation_statuses.values() if s["status"] in ["pending", "running"]]
        ),
        "total_simulations": len(simulation_statuses),
    }


@app.get("/particles")
async def get_particles():
    return {"particles": ["He3", "e-", "proton", "alpha", "neutron", "gamma"], "default": "He3"}


@app.on_event("startup")
async def startup_event():
    temp_dir = tempfile.gettempdir()
    for item in os.listdir(temp_dir):
        if item.startswith("sim_") and os.path.isdir(os.path.join(temp_dir, item)):
            try:
                shutil.rmtree(os.path.join(temp_dir, item))
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", timeout_keep_alive=60)
