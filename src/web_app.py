from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

AVAILABLE_PARTICLES = ["He3", "e-", "proton", "alpha", "neutron", "gamma"]
WEB_CONTROLLER_URL = os.getenv("WEB_CONTROLLER_URL", "http://localhost:8000").rstrip("/")
SIMULATION_TIMEOUT_SEC = int(os.getenv("SIMULATION_TIMEOUT_SEC", "900"))
POLL_INTERVAL_SEC = float(os.getenv("SIMULATION_POLL_INTERVAL_SEC", "1.0"))
ELEMENT_HINTS = {
    "H": {"name": "Hydrogen", "z": 1, "a": 1.008},
    "Be": {"name": "Beryllium", "z": 4, "a": 9.0122},
    "C": {"name": "Carbon", "z": 6, "a": 12.011},
    "Al": {"name": "Aluminum", "z": 13, "a": 26.9815},
    "Si": {"name": "Silicon", "z": 14, "a": 28.085},
    "Ti": {"name": "Titanium", "z": 22, "a": 47.867},
    "Fe": {"name": "Iron", "z": 26, "a": 55.845},
    "Cu": {"name": "Copper", "z": 29, "a": 63.546},
    "W": {"name": "Tungsten", "z": 74, "a": 183.84},
    "Pb": {"name": "Lead", "z": 82, "a": 207.2},
}


class ElementInput(BaseModel):
    name: str = Field("", description="Element name")
    symbol: str = Field(..., min_length=1, description="Element symbol")
    atomic_number: int = Field(0, ge=0)
    standard_atomic_weight: float = Field(0.0, ge=0.0)
    density_g_cm3: float = Field(..., gt=0.0)
    percentage: float = Field(..., gt=0.0)


class LayerInput(BaseModel):
    name: str = Field(..., description="Layer name")
    description: str = Field("", description="Layer description")
    width_um: float = Field(..., gt=0.0, description="Layer thickness in micrometers")
    elements: List[ElementInput] = Field(default_factory=list, min_length=1)


class RunRequest(BaseModel):
    particle: str = Field("He3")
    energy_mev: float = Field(40.0, gt=0)
    events: int = Field(100, ge=1, le=1_000_000)
    collect_tracks: bool = False
    world_xy_mm: float = Field(500.0, gt=0)
    world_z_mm: float = Field(500.0, gt=0)
    screen_xy_mm: float = Field(250.0, gt=0)
    first_screen_z_mm: float = Field(15.0, gt=0)
    electronics_gap_mm: float = Field(0.1, ge=0)
    electronics_thickness_mm: float = Field(0.05, ge=0)
    electronics_material: str = Field("G4_Si")
    build_energy_plots: bool = True
    layers: List[LayerInput] = Field(default_factory=list, min_length=1)


app = FastAPI(title="Shield Config Web UI", version="1.1.0")
Path("out").mkdir(parents=True, exist_ok=True)
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web_ui"
app.mount("/out", StaticFiles(directory="out"), name="out")
app.mount("/ui", StaticFiles(directory=str(WEB_DIR)), name="ui")


def _element_to_input(el: ElementInput) -> Dict[str, Any]:
    symbol = el.symbol.strip()
    hint = ELEMENT_HINTS.get(symbol, {})
    element_name = (el.name or "").strip() or hint.get("name", symbol)
    atomic_number = int(el.atomic_number) if el.atomic_number > 0 else int(hint.get("z", 0))
    atomic_weight = (
        float(el.standard_atomic_weight)
        if el.standard_atomic_weight > 0
        else float(hint.get("a", 0.0))
    )
    return {
        "Name": element_name,
        "Symbol": symbol,
        "Atomic_number": atomic_number,
        "Standard_atomic_weight": atomic_weight,
        "Density": float(el.density_g_cm3),
        "Percentage": float(el.percentage),
    }


def _layer_to_material(layer: LayerInput) -> Dict[str, Any]:
    return {
        "Name": layer.name.strip(),
        "Description": layer.description.strip(),
        "Width": float(layer.width_um),
        "Elements": [_element_to_input(el) for el in layer.elements],
    }


def _build_input_data(payload: RunRequest) -> Dict[str, Any]:
    materials = [_layer_to_material(layer) for layer in payload.layers]
    total_um = sum(layer.width_um for layer in payload.layers)
    return {
        "Screen": {
            "Name": "Web Configured Screen",
            "Description": f"Layers: {len(materials)}, total thickness: {total_um:.2f} um",
            "Materials": materials,
        }
    }


def _call_controller(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{WEB_CONTROLLER_URL}{path}"
    try:
        response = requests.request(method=method, url=url, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"web_controller is unavailable: {exc}") from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        message = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(
            status_code=502,
            detail=f"web_controller error {response.status_code}: {message or response.text}",
        )
    return data if isinstance(data, dict) else {}


@app.get("/api/particles")
def particles() -> Dict[str, Any]:
    try:
        data = _call_controller("GET", "/particles")
    except HTTPException:
        return {"particles": AVAILABLE_PARTICLES, "default": "He3"}
    if not data.get("particles"):
        return {"particles": AVAILABLE_PARTICLES, "default": "He3"}
    return data


@app.post("/api/run")
def run_simulation(payload: RunRequest) -> Dict[str, Any]:
    if payload.particle not in AVAILABLE_PARTICLES:
        raise HTTPException(status_code=400, detail=f"Unsupported particle: {payload.particle}")

    input_data = _build_input_data(payload)
    controller_payload: Dict[str, Any] = {
        "task_id": 0,
        "particle": payload.particle,
        "energy_mev": payload.energy_mev,
        "events": payload.events,
        "collect_tracks": payload.collect_tracks,
        "input_data": input_data,
        "world_xy_mm": payload.world_xy_mm,
        "world_z_mm": payload.world_z_mm,
        "screen_xy_mm": payload.screen_xy_mm,
        "first_screen_z_mm": payload.first_screen_z_mm,
        "electronics_gap_mm": payload.electronics_gap_mm,
        "electronics_thickness_mm": payload.electronics_thickness_mm,
        "electronics_material": payload.electronics_material,
        "build_energy_plots": payload.build_energy_plots,
    }

    started = _call_controller("POST", "/simulate", payload=controller_payload)
    simulation_id = started.get("simulation_id")
    if not simulation_id:
        raise HTTPException(status_code=502, detail="web_controller did not return simulation_id")

    deadline = time.time() + SIMULATION_TIMEOUT_SEC
    final_status: Dict[str, Any] = {}
    while time.time() < deadline:
        status = _call_controller("GET", f"/simulations/{simulation_id}")
        state = status.get("status")
        if state in {"completed", "failed", "error", "timeout"}:
            final_status = status
            break
        time.sleep(POLL_INTERVAL_SEC)
    else:
        raise HTTPException(status_code=504, detail="Timed out waiting for web_controller simulation result")

    if final_status.get("status") != "completed":
        raise HTTPException(
            status_code=500,
            detail=f"Simulation failed in web_controller: {final_status.get('error') or final_status.get('status')}",
        )

    payload_result = final_status.get("result") or {}
    result_dict = payload_result.get("result") or {}
    report = payload_result.get("report") or {}
    generated_files = payload_result.get("generated_files") or []
    for item in generated_files:
        url_path = item.get("url_path")
        if url_path:
            item["web_url"] = f"{WEB_CONTROLLER_URL}{url_path}"

    return {
        "status": "ok",
        "simulation_id": simulation_id,
        "report": report,
        "result": result_dict,
        "input_data": payload_result.get("input_data") or input_data,
        "generated_files": generated_files,
    }


@app.get("/", response_class=FileResponse)
def ui() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010, log_level="info")
