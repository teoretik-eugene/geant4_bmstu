from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple, Optional, Any
import json
from dotenv import load_dotenv
import geant4_pybind as g4
from langchain_gigachat.chat_models import GigaChat
from DataServer import DataServer
from TrimParser import TrimParser
import atexit
import gc
import os
import datetime
import random
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult, SimulationGigaConfig
from giga_tools import ScreenInfo
from utils import compute_layout, is_primary
from vis_graph import plot_energy_analysis, get_particle_color, visualize_multi_particle_results, visualize_single_particle_results

import logging

# Настройка логгера
logging.basicConfig(
    filename='app.log',
    filemode='w',  # 'w' - перезапись, 'a' - добавление (по умолчанию)
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

_geant4_initialized = False

# ---------------------------------------------------------------------------
# Список известных префиксов имён материалов Geant4 NIST.
# Если имя материала (или поле NistName) начинается с одного из них,
# материал берётся напрямую из базы NIST — без ручного задания состава.
# ---------------------------------------------------------------------------
_GEANT4_NIST_PREFIXES = ("G4_", "G4_NIST_")


def _build_g4material(mat_name: str, mat: dict, nist) -> "g4.G4Material":
    """Создаёт G4Material из словаря описания слоя экрана.

    Поддерживает три режима (в порядке приоритета):

    Режим 1 — NIST-материал.
        Срабатывает если:
        - задано поле ``NistName`` (например ``"G4_POLYETHYLENE"``), ИЛИ
        - ``mat_name`` начинается с ``"G4_"`` или ``"G4_NIST_"``.
        Материал берётся напрямую из базы NIST Geant4.

    Режим 2 — соединение (``isCompound: true``).
        Срабатывает если:
        - ``isCompound == true``,
        - задана ``Density`` на уровне материала,
        - у каждого элемента есть поле ``NAtoms`` (число атомов в молекуле).
        Массовые доли вычисляются автоматически из стехиометрии.
        Если хотя бы одно условие нарушено — используется режим 3.

    Режим 3 — обычный материал / сплав (обратная совместимость).
        Плотность берётся из поля ``Density`` материала (если задана)
        или вычисляется как средневзвешенная по долям элементов.
    """
    # ------------------------------------------------------------------
    # Режим 1: NIST-материал
    # ------------------------------------------------------------------
    nist_name: Optional[str] = mat.get("NistName")
    if nist_name:
        g4mat = nist.FindOrBuildMaterial(str(nist_name))
        if g4mat is not None:
            logging.info("[Material] '%s': using NIST material '%s'", mat_name, nist_name)
            return g4mat
        logging.warning(
            "[Material] '%s': NistName '%s' not found in NIST database, "
            "falling back to element-based construction.",
            mat_name, nist_name
        )

    if any(mat_name.startswith(prefix) for prefix in _GEANT4_NIST_PREFIXES):
        g4mat = nist.FindOrBuildMaterial(mat_name)
        if g4mat is not None:
            logging.info(
                "[Material] '%s': name matches NIST prefix, using NIST material.", mat_name
            )
            return g4mat
        logging.warning(
            "[Material] '%s': looks like a NIST name but was not found; "
            "falling back to element-based construction.",
            mat_name
        )

    elems: list = mat.get("Elements", [])
    if not elems:
        raise ValueError(
            f"Material '{mat_name}': no elements defined and no valid NIST name found."
        )

    # Плотность на уровне материала (опциональна)
    mat_density_raw = mat.get("Density")
    mat_density: Optional[float] = (
        float(mat_density_raw) if mat_density_raw is not None else None
    )

    is_compound: bool = bool(mat.get("isCompound", False))

    # ------------------------------------------------------------------
    # Режим 2: соединение — стехиометрия через NAtoms
    # ------------------------------------------------------------------
    if is_compound and mat_density is not None:
        n_atoms_list = []
        compound_ok = True
        for elem in elems:
            n_atoms_raw = elem.get("NAtoms")
            if n_atoms_raw is None:
                logging.warning(
                    "[Material] '%s': isCompound=true but element '%s' "
                    "has no NAtoms field; falling back to Mode 3.",
                    mat_name, elem.get("Symbol", "?")
                )
                compound_ok = False
                break
            try:
                n_atoms_list.append(int(n_atoms_raw))
            except (TypeError, ValueError):
                logging.warning(
                    "[Material] '%s': NAtoms='%s' for element '%s' "
                    "is not an integer; falling back to Mode 3.",
                    mat_name, n_atoms_raw, elem.get("Symbol", "?")
                )
                compound_ok = False
                break

        if compound_ok:
            element_objs = []
            atomic_masses = []
            for elem, n_atoms in zip(elems, n_atoms_list):
                symbol = str(elem.get("Symbol"))
                g4elem = nist.FindOrBuildElement(symbol)
                if g4elem is None:
                    raise ValueError(
                        f"Material '{mat_name}': element '{symbol}' not found in Geant4."
                    )
                atomic_masses.append(g4elem.GetAtomicMassAmu())
                element_objs.append(g4elem)

            contributions = [n * m for n, m in zip(n_atoms_list, atomic_masses)]
            total_molar_mass = sum(contributions)
            fractions = [c / total_molar_mass for c in contributions]

            g4mat = g4.G4Material(
                mat_name,
                mat_density * g4.g / g4.cm3,
                len(element_objs)
            )
            for g4elem, frac in zip(element_objs, fractions):
                g4mat.AddElement(g4elem, frac=frac)

            logging.info(
                "[Material] '%s' (compound): density=%.4f g/cm3, elements=%s, fractions=%s",
                mat_name,
                mat_density,
                [e.GetSymbol() for e in element_objs],
                [f"{f:.4f}" for f in fractions]
            )
            return g4mat

    # ------------------------------------------------------------------
    # Режим 3: обычный материал / сплав (обратная совместимость)
    # ------------------------------------------------------------------
    el_defs = []
    for elem in elems:
        symbol = str(elem.get("Symbol"))
        fraction = float(elem.get("Percentage", 100.0))
        elem_density_raw = elem.get("Density")
        elem_density = float(elem_density_raw) if elem_density_raw is not None else 0.0
        g4elem = nist.FindOrBuildElement(symbol)
        if g4elem is None:
            raise ValueError(
                f"Material '{mat_name}': element '{symbol}' not found in Geant4."
            )
        el_defs.append((g4elem, fraction, elem_density))

    total_fraction = sum(fr for _, fr, _ in el_defs) or 1.0

    if mat_density is not None:
        density_g_cm3 = mat_density
        logging.info(
            "[Material] '%s' (mode 3, explicit density): density=%.4f g/cm3",
            mat_name, density_g_cm3
        )
    else:
        density_g_cm3 = sum(
            (fr / total_fraction) * rho for _, fr, rho in el_defs
        )
        logging.info(
            "[Material] '%s' (mode 3, computed density): density=%.4f g/cm3",
            mat_name, density_g_cm3
        )

    g4mat = g4.G4Material(mat_name, density_g_cm3 * g4.g / g4.cm3, len(el_defs))
    for g4elem, fr, _ in el_defs:
        g4mat.AddElement(g4elem, frac=(fr / total_fraction))

    return g4mat


def _configure_geant4_data_env() -> None:
    """Populate required Geant4 dataset environment variables when possible."""
    dataset_map = {
        "G4ENSDFSTATEDATA": "G4ENSDFSTATE",
        "G4LEVELGAMMADATA": "PhotonEvaporation",
        "G4RADIOACTIVEDATA": "RadioactiveDecay",
        "G4PARTICLEXSDATA": "G4PARTICLEXS",
        "G4NEUTRONHPDATA": "G4NDL",
        "G4LEDATA": "G4EMLOW",
        "G4SAIDXSDATA": "G4SAIDDATA",
        "G4REALSURFACEDATA": "RealSurface",
        "G4ABLADATA": "G4ABLA",
        "G4INCLDATA": "G4INCL",
        "G4PIIDATA": "G4PII",
        "G4TENDLDATA": "G4TENDL",
    }

    candidate_roots = []
    geant4_data_env = os.getenv("GEANT4_DATA")
    if geant4_data_env:
        candidate_roots.append(Path(geant4_data_env))

    candidate_roots.extend(
        [
            Path("/app/geant4/data"),
            Path("/usr/local/share/Geant4/data"),
            Path("/usr/local/share/geant4/data"),
        ]
    )

    # Keep order while removing duplicates
    unique_roots = []
    seen = set()
    for root in candidate_roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)

    for env_name, dir_prefix in dataset_map.items():
        if os.getenv(env_name):
            continue

        for root in unique_roots:
            if not root.exists():
                continue
            matches = sorted(root.glob(f"{dir_prefix}*"))
            if matches:
                os.environ[env_name] = str(matches[-1])
                break

    # Fail fast with actionable diagnostics for the one dataset that triggered your error.
    if not os.getenv("G4ENSDFSTATEDATA"):
        roots_str = ", ".join(str(r) for r in unique_roots)
        raise RuntimeError(
            "Geant4 dataset path is not configured: G4ENSDFSTATEDATA is missing. "
            f"Searched roots: {roots_str}. "
            "Set GEANT4_DATA or G4ENSDFSTATEDATA explicitly."
        )

class TrackCollector:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._data: Dict[Tuple[int, int], List[Tuple[float, float, float]]] = {}
        self._particle_types: Dict[Tuple[int, int], str] = {}  # Новое: храним типы частиц

        self._energy_profiles = {}
        # key = (event_id, track_id)
        # value = {
        #     "parent_id": int,
        #     "particle": str,
        #     "points": [(z, E)]
        # }
        self._exit_energies = []    # энергии на выходе: список dict {energy_mev, is_primary, particle_type}
        self._electronics_hits: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._electronics_dose_gy_total: float = 0.0
        self._electronics_dose_events: int = 0
    
    def add(self, event_id: int, track_id: int, pos: g4.G4ThreeVector, particle_type: str = None) -> None:
        if not self.enabled:
            return
        self._data.setdefault((event_id, track_id), []).append((pos.x, pos.y, pos.z))
        if particle_type and (event_id, track_id) not in self._particle_types:
            self._particle_types[(event_id, track_id)] = particle_type

    def add_energy(self, event_id, track_id, parent_id, particle_type, z, energy):
        key = (event_id, track_id)

        if key not in self._energy_profiles:
            self._energy_profiles[key] = {
                "parent_id": parent_id,
                "particle": particle_type,
                "points": []
            }

        self._energy_profiles[key]["points"].append((z, energy))

    def add_exit_energy(self, energy: float, is_primary: bool = True, particle_type: str = "unknown") -> None:
        self._exit_energies.append({
            "energy_mev": float(energy),
            "is_primary": is_primary,
            "particle_type": particle_type
        })

    def add_electronics_step(
        self,
        event_id,
        track_id,
        parent_id,
        particle_type,
        edep_mev,
        step_length_mm,
        pre_energy_mev,
        post_energy_mev,
        let_mev_cm2_mg=None
    ):
        key = (event_id, track_id)
        if key not in self._electronics_hits:
            self._electronics_hits[key] = {
                "event_id": event_id,
                "track_id": track_id,
                "parent_id": parent_id,
                "particle": particle_type,
                "edep_mev": 0.0,
                "track_length_mm": 0.0,
                "entry_energy_mev": float(pre_energy_mev),
                "exit_energy_mev": float(post_energy_mev),
                "steps": 0,
                "let_sum_weighted": 0.0,
                "let_samples": 0,
                "mean_let_mev_cm2_mg": None,
                "max_let_mev_cm2_mg": None
            }

        hit = self._electronics_hits[key]
        hit["edep_mev"] += float(edep_mev)
        hit["track_length_mm"] += float(step_length_mm)
        hit["exit_energy_mev"] = float(post_energy_mev)
        hit["steps"] += 1
        if let_mev_cm2_mg is not None:
            let_value = float(let_mev_cm2_mg)
            hit["let_sum_weighted"] += let_value * max(float(step_length_mm), 0.0)
            hit["let_samples"] += 1
            if hit["track_length_mm"] > 0:
                hit["mean_let_mev_cm2_mg"] = hit["let_sum_weighted"] / hit["track_length_mm"]
            current_max = hit.get("max_let_mev_cm2_mg")
            hit["max_let_mev_cm2_mg"] = let_value if current_max is None else max(current_max, let_value)

    def add_electronics_dose_event(self, dose_gy: float) -> None:
        try:
            dose_value = float(dose_gy)
        except (TypeError, ValueError):
            return
        if dose_value < 0:
            return
        self._electronics_dose_gy_total += dose_value
        self._electronics_dose_events += 1

    def get_particle_type(self, event_id: int, track_id: int) -> str:
        return self._particle_types.get((event_id, track_id), "unknown")
    
    @property
    def data(self):
        return self._data
    
    @property
    def particle_types(self):
        return self._particle_types
    
    @property
    def energy_profiles(self):
        return self._energy_profiles

    @property
    def exit_energies(self):
        return self._exit_energies

    @property
    def electronics_hits(self):
        return list(self._electronics_hits.values())

    @property
    def electronics_dose_gy_total(self):
        return self._electronics_dose_gy_total

    @property
    def electronics_dose_events(self):
        return self._electronics_dose_events

# -----------------------------
# Геометрия
# -----------------------------
class ScreenGeometry(g4.G4VUserDetectorConstruction):
    def __init__(self, data: dict, screen_info: Dict[str, Any], cfg: SimulationConfig, tracks: Optional[TrackCollector] = None) -> None:
        super().__init__()
        self.tp = TrimParser(data)
        self.screen_info = screen_info
        self.cfg = cfg
        self.tracks = tracks
        self.logic_world = None
        self.screen_logicals = []
        self.electronics_logical = None
        self.electronics_dose_collection_name = "ElectronicsMFD/DoseDeposit"
        self.screens_end_z_mm = cfg.first_screen_z_mm
        self._precomputed_layout: Optional[dict] = None

    def Construct(self):
        nist = g4.G4NistManager.Instance()
        check_overlaps = True
        materials = self.tp.readMaterials()
        self.screen_info["Materials"] = []
        material_defs = []
        layout = self._precomputed_layout or compute_layout(self.cfg, {"Materials": materials})
        thicknesses_mm = layout["thicknesses_mm"]
        total_thickness_mm = layout["total_thickness_mm"]
        world_z_mm_local = layout["world_z_mm_local"]
        half_world_z_mm = layout["half_world_z_mm"]
        first_screen_front_z_mm = layout["first_screen_front_z_mm"]
        electronics_gap_mm = layout.get("electronics_gap_mm", getattr(self.cfg, "electronics_gap_mm", 0.1))
        electronics_thickness_mm_parameter = 0.5
        electronics_thickness_mm = layout.get("electronics_thickness_mm", getattr(self.cfg, "electronics_thickness_mm", 
                                                                                  electronics_thickness_mm_parameter))

        for mat, thickness_mm in zip(materials, thicknesses_mm):
            mat_name = str(mat.get("Name"))
            self.screen_info["Materials"].append({
                "Name": mat_name,
                "Primary_stuck_count": 0,
                "Secondary_stuck_count": 0,
                "Thickness_mm": thickness_mm,
            })
            g4mat = _build_g4material(mat_name, mat, nist)
            material_defs.append((g4mat, thickness_mm * g4.mm, mat_name))
        logging.info(f"Screen info: {self.screen_info}")

        world_xy_mm_local = max(self.cfg.world_xy_mm, self.cfg.screen_xy_mm + 50.0)
        solid_world = g4.G4Box("World", 0.5 * world_xy_mm_local * g4.mm, 0.5 * world_xy_mm_local * g4.mm, 0.5 * world_z_mm_local * g4.mm)
        self.logic_world = g4.G4LogicalVolume(solid_world, nist.FindOrBuildMaterial("G4_Galactic"), "World")
        phys_world = g4.G4PVPlacement(None, g4.G4ThreeVector(), self.logic_world, "World", None, False, 0, check_overlaps)
        z_cursor = first_screen_front_z_mm * g4.mm
        screen_xy = self.cfg.screen_xy_mm * g4.mm

        for idx, (g4mat, thickness, _) in enumerate(material_defs):
            center_z = z_cursor + 0.5 * thickness
            solid = g4.G4Box(f"Screen_{idx}", 0.5 * screen_xy, 0.5 * screen_xy, 0.5 * thickness)
            logical = g4.G4LogicalVolume(solid, g4mat, f"Screen_{idx}")
            g4.G4PVPlacement(None, g4.G4ThreeVector(0, 0, center_z), logical, f"Screen_{idx}", self.logic_world, False, 0, check_overlaps)
            self.screen_logicals.append(logical)
            z_cursor += thickness
        self.screens_end_z_mm = first_screen_front_z_mm + total_thickness_mm

        if electronics_thickness_mm > 0:
            electronics_material_name = getattr(self.cfg, "electronics_material", "G4_Si")
            electronics_size_x_mm = getattr(self.cfg, "electronics_size_x_mm", 50.0)
            electronics_size_y_mm = getattr(self.cfg, "electronics_size_y_mm", 50.0)
            electronics_material = nist.FindOrBuildMaterial(electronics_material_name)
            electronics_center_z = (
                self.screens_end_z_mm + electronics_gap_mm + 0.5 * electronics_thickness_mm
            ) * g4.mm
            electronics_center_y = 0.0
            electronics_solid = g4.G4Box(
                "Electronics",
                0.5 * electronics_size_x_mm * g4.mm,
                0.5 * electronics_size_y_mm * g4.mm,
                0.5 * electronics_thickness_mm * g4.mm
            )
            self.electronics_logical = g4.G4LogicalVolume(
                electronics_solid,
                electronics_material,
                "Electronics"
            )
            # user_limits = g4.G4UserLimits(
            #     0.001 * g4.mm,
            #     1.0 * g4.s,
            #     1.0 * g4.s,
            #     0.001 * g4.mm,
            #     0.001 * g4.MeV
            # )
            # self.electronics_logical.SetUserLimits(user_limits)
            g4.G4PVPlacement(
                None,
                g4.G4ThreeVector(0, electronics_center_y, electronics_center_z),
                self.electronics_logical,
                "Electronics",
                self.logic_world,
                False,
                0,
                check_overlaps
            )
            # Исправление #3: масса берётся из Geant4 через GetMass() (результат в г, переводим в мг),
            # а не вычисляется вручную через плотность × объём.
            # GetMass() вызывается ПОСЛЕ G4PVPlacement — том уже размещён,
            # Geant4 знает реальную геометрию и учитывает возможные дочерние объёмы.
            electronics_mass_mg = self.electronics_logical.GetMass() / g4.g * 1000.0
            logging.info(f"mass of electronics (G4): {electronics_mass_mg} mg")
            self.screen_info["Electronics"] = {
                "material": electronics_material_name,
                "density_g_cm3": electronics_material.GetDensity() / (g4.g / g4.cm3),
                "gap_mm": electronics_gap_mm,
                "size_x_mm": electronics_size_x_mm,
                "size_y_mm": electronics_size_y_mm,
                "thickness_mm": electronics_thickness_mm,
                "xy_size_mm": max(electronics_size_x_mm, electronics_size_y_mm),
                "volume_mm3": electronics_size_x_mm * electronics_size_y_mm * electronics_thickness_mm,
                "mass_mg": electronics_mass_mg,
                "z_start_mm": self.screens_end_z_mm + electronics_gap_mm,
                "z_end_mm": self.screens_end_z_mm + electronics_gap_mm + electronics_thickness_mm
            }
            logging.info(f"mass of electronics: {self.screen_info['Electronics']['mass_mg']}")
        return phys_world

    def ConstructSDandField(self):
        sdm = g4.G4SDManager.GetSDMpointer()
        for idx, sc_log in enumerate(self.screen_logicals):
            sd = ScreenSensitiveDetector(f"ScreenDetector_{idx}", self.screen_info)
            sdm.AddNewDetector(sd)
            sc_log.SetSensitiveDetector(sd)
        if self.electronics_logical is not None and self.tracks is not None:
            electronics_sd = ElectronicsSensitiveDetector(
                "ElectronicsDetector",
                self.screen_info,
                self.tracks
            )
            electronics_mfd = g4.G4MultiFunctionalDetector("ElectronicsMFD")
            electronics_mfd.RegisterPrimitive(g4.G4PSDoseDeposit("DoseDeposit", "Gy"))

            electronics_multi_sd = g4.G4MultiSensitiveDetector("ElectronicsMultiSD")
            electronics_multi_sd.AddSD(electronics_sd)
            electronics_multi_sd.AddSD(electronics_mfd)

            sdm.AddNewDetector(electronics_sd)
            sdm.AddNewDetector(electronics_mfd)
            sdm.AddNewDetector(electronics_multi_sd)
            self.electronics_logical.SetSensitiveDetector(electronics_multi_sd)

# -----------------------------
# Сенсоры
# -----------------------------
class ScreenSensitiveDetector(g4.G4VSensitiveDetector):

    def __init__(self, name, screen_info):
        super().__init__(name)
        self.screen_info = screen_info
        # Хранит (event_id, track_id) только для ТЕКУЩЕГО события.
        # Очищается в Initialize(), который Geant4 вызывает в начале каждого события.
        self.stopped_tracks = set()

    def Initialize(self, hit_collection_of_this_event):
        # Исправление #9: сбрасываем множество в начале каждого события.
        # Без этого stopped_tracks растёт бесконечно (утечка памяти) и при
        # совпадении ключей (event_id, track_id) между событиями остановка
        # частицы не регистрировалась бы повторно.
        self.stopped_tracks.clear()

    def ProcessHits(self, aStep: g4.G4Step, _hist):
        track: g4.G4Track = aStep.GetTrack()
        track_id = track.GetTrackID()
        # Получаем event_id из G4EventManager
        try:
            event_mgr = g4.G4EventManager.GetEventManager()
            event_id = event_mgr.GetConstCurrentEvent().GetEventID() if event_mgr else None
        except Exception:
            event_id = None

        kin_energy = track.GetKineticEnergy() / g4.MeV

        track_status = track.GetTrackStatus()

        pos = aStep.GetPostStepPoint().GetPosition()
        z_mm = pos.z / g4.mm

        # энергия, оставленная в шаге
        edep = aStep.GetTotalEnergyDeposit() / g4.MeV

        vol_name = track.GetVolume().GetName() if track.GetVolume() else ""

        is_stopped = False

        # Кинетическая энергия близка к 0 (с порогом)
        if kin_energy < 1e-6:  # 1 эВ порог
            is_stopped = True

        if track_status == g4.fStopAndKill or track_status == g4.fStopButAlive:
            is_stopped = True

        # Используем (event_id, track_id) для уникальной идентификации трека
        track_key = (event_id, track_id)
        particle = track.GetDynamicParticle().GetDefinition().GetParticleName()
        if is_stopped and track_key not in self.stopped_tracks:
            # Проверяем, что внутри экрана
            if "Screen" in vol_name:
                try:
                    idx = int(str(vol_name).split("_")[-1])
                    # Добавляем в статистику
                    if track.GetParentID() == 0:
                        self.screen_info["Materials"][idx]["Primary_stuck_count"] += 1
                        # logging.info(f"Primary track {particle} {track_id} (event {event_id}) stopped in {vol_name} at Z={z_mm:.2f} mm")
                    else:
                        self.screen_info["Materials"][idx]["Secondary_stuck_count"] += 1
                        # logging.info(f"Secondary track {track_id} (event {event_id}) stopped in {vol_name} at Z={z_mm:.2f} mm")

                    # Помечаем трек как обработанный
                    self.stopped_tracks.add(track_key)
                    
                except Exception as e:
                    logging.error(f"Error processing stopped track: {e}")
            
        edep = aStep.GetTotalEnergyDeposit() / g4.MeV
        if edep > 0:
            try:
                idx = int(str(vol_name).split("_")[-1])
                if "Edep" not in self.screen_info["Materials"][idx]:
                    self.screen_info["Materials"][idx]["Edep"] = 0.0
                self.screen_info["Materials"][idx]["Edep"] += edep
            except:
                pass

        return True


class ElectronicsSensitiveDetector(g4.G4VSensitiveDetector):
    def __init__(self, name, screen_info, tracks: TrackCollector):
        super().__init__(name)
        self.screen_info = screen_info
        self.tracks = tracks
        self.em_calculator = g4.G4EmCalculator()

    def ProcessHits(self, aStep: g4.G4Step, _hist):
        track: g4.G4Track = aStep.GetTrack()
        try:
            event_mgr = g4.G4EventManager.GetEventManager()
            event_id = event_mgr.GetConstCurrentEvent().GetEventID() if event_mgr else None
        except Exception:
            event_id = None

        edep_mev = aStep.GetTotalEnergyDeposit() / g4.MeV
        step_length_mm = aStep.GetStepLength() / g4.mm

        pre_step = aStep.GetPreStepPoint()
        post_step = aStep.GetPostStepPoint()
        pre_energy_mev = pre_step.GetKineticEnergy() / g4.MeV
        post_energy_mev = post_step.GetKineticEnergy() / g4.MeV
        particle_def = track.GetDefinition()
        particle_name = particle_def.GetParticleName() if particle_def else "unknown"
        material = pre_step.GetMaterial()
        density_g_cm3 = material.GetDensity() / (g4.g / g4.cm3) if material else 0.0

        if step_length_mm > 1e-4:  # отсечка микрошагов
            let_step_mev_cm2_mg = (edep_mev / step_length_mm) / (density_g_cm3 * 100.0)
        else:
            let_step_mev_cm2_mg = 0.0

                # Исправление #8: LET вычисляется на СРЕДНЕЙ энергии шага (pre + post) / 2,
        # а не только на pre_energy. Это физически корректнее, особенно в области
        # пика Брэгга, где энергия резко падает на протяжении одного шага.
        mid_energy_mev = (pre_energy_mev + post_energy_mev) / 2.0

        if (
            particle_def is not None
            and abs(particle_def.GetPDGCharge()) > 0
            and material is not None
            and mid_energy_mev > 0
            and density_g_cm3 > 0
        ):
            # Заряженные частицы (протоны, ионы, электроны):
            # LET считается через ComputeElectronicDEDX — наиболее точный метод.
            try:
                dedx_internal = self.em_calculator.ComputeElectronicDEDX(
                    mid_energy_mev * g4.MeV,
                    particle_def,
                    material
                )
                dedx_mev_per_mm = dedx_internal / (g4.MeV / g4.mm)
                let_step_mev_cm2_mg = dedx_mev_per_mm / (density_g_cm3 * 100.0)
            except Exception as exc:
                logging.warning(
                    "G4EmCalculator LET failed for particle=%s, mid_energy=%.6f MeV: %s",
                    particle_name,
                    mid_energy_mev,
                    exc
                )
                # Fallback: считаем через edep/step если шаг ненулевой
                if step_length_mm > 1e-4 and density_g_cm3 > 0:
                    let_step_mev_cm2_mg = (edep_mev / step_length_mm) / (density_g_cm3 * 100.0)
                else:
                    let_step_mev_cm2_mg = None
        elif edep_mev > 0 and step_length_mm > 1e-4 and density_g_cm3 > 0:
            # Нейтральные частицы (гаммы, нейтроны):
            # ComputeElectronicDEDX не применим (нет заряда).
            # Используем косвенную оценку: edep / step_length / density.
            # Для гамм — это энергия Комптон-электронов и фотоэффекта, осаждённая локально.
            # Для нейтронов — энергия ядер отдачи.
            # Физический смысл: эффективный LET от вторичных ионизирующих частиц.
            let_step_mev_cm2_mg = (edep_mev / step_length_mm) / (density_g_cm3 * 100.0)
            logging.debug(
                "Neutral particle LET (edep/step): particle=%s, edep=%.4e MeV, "
                "step=%.4e mm, let=%.4e MeV*cm2/mg",
                particle_name, edep_mev, step_length_mm, let_step_mev_cm2_mg
            )
        elif edep_mev > 0:
            # Нейтральная частица с edep > 0 но шагом == 0
            # (ядерное взаимодействие нейтрона — виртуальный шаг).
            # LET не определён геометрически, но факт осаждения энергии регистрируем.
            # Оставляем let = None, но хит всё равно попадёт в статистику дозы
            # через edep_mev (который учитывается независимо от LET).
            let_step_mev_cm2_mg = None
        else:
            let_step_mev_cm2_mg = None

        if edep_mev <= 0 and step_length_mm <= 0:
            return True

        self.tracks.add_electronics_step(
            event_id=event_id,
            track_id=track.GetTrackID(),
            parent_id=track.GetParentID(),
            particle_type=particle_name,
            edep_mev=edep_mev,
            step_length_mm=step_length_mm,
            pre_energy_mev=pre_energy_mev,
            post_energy_mev=post_energy_mev,
            let_mev_cm2_mg=let_step_mev_cm2_mg
        )

        electronics_info: Dict = self.screen_info.setdefault("Electronics", {})
        electronics_info["deposited_energy_mev"] = (
            electronics_info.get("deposited_energy_mev", 0.0) + float(edep_mev)
        )
        electronics_info["track_length_mm"] = (
            electronics_info.get("track_length_mm", 0.0) + float(step_length_mm)
        )
        electronics_info["hit_count"] = electronics_info.get("hit_count", 0) + 1
        if let_step_mev_cm2_mg is not None:
            electronics_info["let_max_mev_cm2_mg"] = max(
                electronics_info.get("let_max_mev_cm2_mg", 0.0),
                float(let_step_mev_cm2_mg)
            )
        return True

class ScreenEventAction(g4.G4UserEventAction):
    def __init__(self, tracks: Optional[TrackCollector] = None, dose_collection_name: Optional[str] = None):
        super().__init__()
        self.event_id = None
        self.tracks = tracks
        self.dose_collection_name = dose_collection_name
        self._dose_collection_id = None
    def BeginOfEventAction(self, anEvent):
        self.event_id = anEvent.GetEventID()
    def EndOfEventAction(self, anEvent):
        if self.tracks is not None and self.dose_collection_name:
            try:
                if self._dose_collection_id is None or self._dose_collection_id < 0:
                    new_id = g4.G4SDManager.GetSDMpointer().GetCollectionID(
                        self.dose_collection_name
                    )
                    # Исправление #5: при нерабочем scorer предупреждаем явно,
                    # чтобы не было молчаливого fallback на manual_edep_over_mass.
                    if new_id < 0:
                        logging.warning(
                            "Dose scorer collection '%s' not found (id=%d). "
                            "absorbed_dose_gy will be estimated from edep/mass fallback.",
                            self.dose_collection_name, new_id
                        )
                    self._dose_collection_id = new_id

                if self._dose_collection_id is not None and self._dose_collection_id >= 0:
                    hce = anEvent.GetHCofThisEvent()
                    if hce is not None:
                        hc = hce.GetHC(self._dose_collection_id)
                        dose_event_gy = 0.0
                        if hc is not None:
                            for _, value in hc:
                                # Исправление #2: G4PSDoseDeposit возвращает значение
                                # во внутренних единицах Geant4 (MeV/kg * system factor).
                                # Необходимо явно делить на g4.gray для перевода в Гр.
                                dose_event_gy += float(value) / g4.gray
                        self.tracks.add_electronics_dose_event(dose_event_gy)
            except Exception as exc:
                logging.warning("Failed to read dose scorer collection '%s': %s", self.dose_collection_name, exc)
        self.event_id = None

class ScreenSteppingAction(g4.G4UserSteppingAction):
    def __init__(self, primary_out, secondary_out, screens_end_z_mm, event_action, tracks: TrackCollector, current_particle=None):
        super().__init__()
        self.primary_out = primary_out
        self.secondary_out = secondary_out
        self.screens_end_z_mm = screens_end_z_mm
        self.event_action = event_action
        self.tracks = tracks
        self.current_particle = current_particle
    
    def UserSteppingAction(self, step: g4.G4Step):
        pre = step.GetPreStepPoint()
        post = step.GetPostStepPoint()
        pos = post.GetPosition()
        track = step.GetTrack()
        parent_id = track.GetParentID()

        kin_energy = track.GetKineticEnergy() / g4.MeV
        z_pos = pos.z / g4.mm
        pre_z_pos = pre.GetPosition().z / g4.mm
        # print("EVENT ID:", self.event_action.event_id)
        # print(f'EVENT ID: {self.event_action.event_id}\tTRACK ID: {track.GetTrackID()}\tkinenergy: {kin_energy}')
        
        # Получаем тип частицы из Geant4
        particle_name = "unknown"
        try:
            particle_def = track.GetDefinition()
            if particle_def:
                particle_name = particle_def.GetParticleName()
        except AttributeError:
            try:
                particle_def = track.GetDynamicParticle().GetDefinition()
                if particle_def:
                    particle_name = particle_def.GetParticleName()
            except:
                if track.GetTrackID() == 1:
                    particle_name = "primary"
                else:
                    particle_name = "secondary"
        
        # Для первичных частиц в смешанном пучке добавляем информацию о типе
        if track.GetTrackID() == 1 and self.current_particle and self.current_particle != "mixed":
            particle_name = self.current_particle

        if self.event_action.event_id is not None:
            self.tracks.add_energy(
                event_id=self.event_action.event_id,
                track_id=track.GetTrackID(),
                particle_type=particle_name,
                parent_id=parent_id,
                z=z_pos,
                energy=kin_energy
            )

        if self.event_action.event_id is not None:
            self.tracks.add(self.event_action.event_id, track.GetTrackID(), pos, particle_name)

        if pre_z_pos <= self.screens_end_z_mm < z_pos:
            key = (self.event_action.event_id or -1, track.GetTrackID())
            exit_energy_mev = track.GetKineticEnergy() / g4.MeV

            if track.GetTrackID() == 1:
                if key not in self.primary_out:
                    self.tracks.add_exit_energy(
                        exit_energy_mev,
                        is_primary=True,
                        particle_type=particle_name
                    )
                    self.primary_out.append(key)
            else:
                if key not in self.secondary_out:
                    self.tracks.add_exit_energy(
                        exit_energy_mev,
                        is_primary=False,
                        particle_type=particle_name
                    )
                    self.secondary_out.append(key)

class ActionInitialization(g4.G4VUserActionInitialization):
    def __init__(
        self,
        data,
        primary_out,
        secondary_out,
        particle_config,
        layout,
        tracks,
        current_particle,
        generator,
        electronics_dose_collection_name: Optional[str] = None
    ):
        super().__init__()
        self.data = data
        self.primary_out = primary_out
        self.secondary_out = secondary_out
        self.particle_config = particle_config
        self.layout = layout
        self.tracks = tracks
        self.current_particle = current_particle
        self.generator = generator
        self.electronics_dose_collection_name = electronics_dose_collection_name
    
    def Build(self):
        self.SetUserAction(self.generator)
        
        event_action = ScreenEventAction(
            tracks=self.tracks,
            dose_collection_name=self.electronics_dose_collection_name
        )
        self.SetUserAction(event_action)
        self.SetUserAction(ScreenSteppingAction(
            self.primary_out,
            self.secondary_out,
            self.layout["screens_end_z_mm"],
            event_action,
            self.tracks,
            current_particle=self.current_particle
        ))

# -----------------------------
# Генераторы частиц
# -----------------------------

class SingleParticlePrimaryGenerator(g4.G4VUserPrimaryGeneratorAction):
    """Генератор для одиночного типа частиц"""
    def __init__(self, particle_name: str, energy_mev: float, source_z_mm: float):
        super().__init__()
        self.fParticleGun = g4.G4ParticleGun(1)
        particle_table = g4.G4ParticleTable.GetParticleTable()
        particle = particle_table.FindParticle(particle_name)
        if particle is None:
            raise ValueError(f"Unknown particle: {particle_name}")
        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(g4.G4ThreeVector(0.0, 0.0, 1.0))
        self.fParticleGun.SetParticleEnergy(energy_mev * g4.MeV)
        self.source_z = source_z_mm * g4.mm

    def GeneratePrimaries(self, anEvent):
        self.fParticleGun.SetParticlePosition(g4.G4ThreeVector(0, 0, self.source_z))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

class MixedParticlePrimaryGenerator(g4.G4VUserPrimaryGeneratorAction):
    """Генератор для смешанного пучка частиц"""
    def __init__(self, particles: List[ParticleConfig], source_z_mm: float):
        super().__init__()
        self.particles = particles
        self.source_z = source_z_mm * g4.mm
        self.fParticleGun = g4.G4ParticleGun(1)
        
        # Нормализуем веса
        total_weight = sum(p.weight for p in particles)
        self.normalized_weights = [p.weight / total_weight for p in particles]
        
        # Предварительно находим определения частиц
        self.particle_defs = []
        particle_table = g4.G4ParticleTable.GetParticleTable()
        
        for p_config in particles:
            particle = particle_table.FindParticle(p_config.name)
            if particle is None:
                raise ValueError(f"Unknown particle: {p_config.name}")
            self.particle_defs.append((particle, p_config.energy_mev))

    def GeneratePrimaries(self, anEvent):
        # Выбираем случайную частицу согласно весам
        r = random.random()
        cumulative = 0
        selected_particle = None
        selected_energy = 0
        
        for (particle, energy), weight in zip(self.particle_defs, self.normalized_weights):
            cumulative += weight
            if r <= cumulative:
                selected_particle = particle
                selected_energy = energy
                break
        
        if selected_particle is None:
            selected_particle, selected_energy = self.particle_defs[-1]
        
        # Настраиваем пушку
        self.fParticleGun.SetParticleDefinition(selected_particle)
        self.fParticleGun.SetParticleMomentumDirection(g4.G4ThreeVector(0.0, 0.0, 1.0))
        self.fParticleGun.SetParticleEnergy(selected_energy * g4.MeV)
        self.fParticleGun.SetParticlePosition(g4.G4ThreeVector(0, 0, self.source_z))
        
        # Генерируем первичную вершину
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

class PrimaryGeneration(g4.G4VUserPrimaryGeneratorAction):
    def __init__(self, particle_name, energy_mev, source_z_mm):
        super().__init__()
        self.fParticleGun = g4.G4ParticleGun(1)
        particle_table = g4.G4ParticleTable.GetParticleTable()
        particle = particle_table.FindParticle(particle_name)
        if particle is None:
            raise ValueError(f"Unknown particle: {particle_name}")
        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(g4.G4ThreeVector(0.0, 0.0, 1.0))
        self.fParticleGun.SetParticleEnergy(energy_mev * g4.MeV)
        self.source_z = source_z_mm * g4.mm
    def GeneratePrimaries(self, anEvent):
        self.fParticleGun.SetParticlePosition(g4.G4ThreeVector(0, 0, self.source_z))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

# -----------------------------
# Вспомогательные функции для многопроцессного запуска
# -----------------------------

def run_single_simulation_in_process(config_dict: dict) -> dict:
    import json
    import geant4_pybind as g4
    import gc
    
    # Восстанавливаем конфиг с правильной десериализацией ParticleConfig
    config = SimulationConfig(**config_dict)
    
    # Запускаем симуляцию
    runner = SingleProcessSimulationRunner()
    result = runner.run_single(config)
    logging.info(f"run_single_simulation_in_process: {result}")
    
    # Преобразуем треки в сериализуемый формат
    result_dict = result.to_dict()
    
    # Дополнительная обработка для треков
    if result.tracks:
        # Преобразуем ключи треков в строки для JSON сериализации
        tracks_serializable = {}
        for key, points in result.tracks.items():
            str_key = f"{key[0]}_{key[1]}"
            tracks_serializable[str_key] = points
        result_dict["tracks"] = tracks_serializable
    
    return result_dict

def convert_screen_info_to_data(screen: ScreenInfo) -> dict:
    return {
        "Screen": screen.to_dict()
    }


def _create_screen_generation_llm() -> GigaChat:
    load_dotenv()
    giga_credentials = os.getenv("GIGACHAT_CREDENTIALS")
    return GigaChat(
        model="GigaChat-2-Max",
        credentials=giga_credentials,
        scope="GIGACHAT_API_PERS",
        top_p=0,
        timeout=120,
        ca_bundle_file="russian_trusted_root_ca_pem.crt",
    )


def generate_screen_config_with_giga(prompt: str) -> dict:
    llm = _create_screen_generation_llm()
    structured_llm = llm.with_structured_output(ScreenInfo)
    full_prompt = (
        "Сформируй конфигурацию защитного экрана для моделирования взаимодействия заряженных частиц с веществом. "
        "Верни структуру экрана с явным составом каждого слоя. "
        "Для металлов и сплавов укажи Elements с Percentage и Density у элементов. "
        "Для химических соединений укажи Density на уровне материала, isCompound=true и NAtoms у каждого элемента. "
        "Не используй NistName как единственный способ задания материала, если можно явно описать состав. "
        "Толщину слоя указывай в микронах в поле Width. "
        f"\n\nЗапрос пользователя:\n{prompt}"
    )
    logging.info("request for llm screen config")
    result = structured_llm.invoke(full_prompt)
    logging.info("llm screen config result: %s", result)
    return convert_screen_info_to_data(result)

def run_simulation_with_giga(cfg: SimulationGigaConfig):
    logging.info('run simulation with giga main')
    data = generate_screen_config_with_giga(cfg.prompt)
    logging.info(f'data: {data}')

    run_config = SimulationConfig(input_data=data, 
                                  particle=cfg.particle, 
                                  energy_mev=cfg.energy_mev,
                                  events=cfg.events)
    
    runner = SimulationRunner()
    res = runner.run(run_config)
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))

    return res

class SingleProcessSimulationRunner:
    """Runner для одного процесса (без многократного создания RunManager)"""
    
    def run_single(self, cfg: SimulationConfig) -> SimulationResult:
        """Запускает одну симуляцию в отдельном процессе"""
        _configure_geant4_data_env()
        data = self._load_input(cfg)
        layout = compute_layout(cfg, data)
        screen_info = {}
        primary_out = []
        secondary_out = []
        tracks = TrackCollector(enabled=cfg.collect_tracks)
        
        run_manager = g4.G4RunManagerFactory.CreateRunManager(g4.G4RunManagerType.Serial)
        global _geant4_initialized
        _geant4_initialized = True
        
        geom = ScreenGeometry(data=data, screen_info=screen_info, cfg=cfg, tracks=tracks)
        geom._precomputed_layout = layout
        run_manager.SetUserInitialization(geom)
        physics_list = g4.FTFP_BERT()
        # physics_list = g4.FTFP_BERT_HP()
        run_manager.SetUserInitialization(physics_list)
        # run_manager.SetUserInitialization(g4.QGSP_BERT())
        # Определяем тип генератора
        if cfg.use_mixed_beam and len(cfg.particles) > 1:
            # Смешанный пучок
            half_world_z_mm = layout["half_world_z_mm"]
            first_front_mm = layout["first_screen_front_z_mm"]
            source_z_mm = max(first_front_mm - 10.0, -half_world_z_mm + 1.0)
            
            generator = MixedParticlePrimaryGenerator(cfg.particles, source_z_mm)
            current_particle = "mixed"
        else:
            # Одиночная частица
            p_config = cfg.particles[0]
            half_world_z_mm = layout["half_world_z_mm"]
            first_front_mm = layout["first_screen_front_z_mm"]
            source_z_mm = max(first_front_mm - 10.0, -half_world_z_mm + 1.0)
            
            generator = SingleParticlePrimaryGenerator(p_config.name, p_config.energy_mev, source_z_mm)
            current_particle = p_config.name
        
        run_manager.SetUserInitialization(ActionInitialization(
            data=data,
            primary_out=primary_out,
            secondary_out=secondary_out,
            particle_config=cfg.particles,
            layout=layout,
            tracks=tracks,
            current_particle=current_particle,
            generator=generator,
            electronics_dose_collection_name=geom.electronics_dose_collection_name
        ))
        
        run_manager.Initialize()
        run_manager.BeamOn(cfg.events)
        
        # Для смешанного пучка анализируем статистику
        mixed_result = None
        if cfg.use_mixed_beam and len(cfg.particles) > 1:
            mixed_result = self._analyze_mixed_beam_results(tracks, cfg.events, cfg.particles)
        logging.info(f"simulation result screen info: {screen_info}")
        logging.info(f"simulation tracks: {tracks.exit_energies}")
        # logging.info(f"sum energy {sum(tracks.exit_energies) / len(tracks.exit_energies)}")

        energy_summary = _compute_energy_summary(
            tracks.energy_profiles,
            tracks.exit_energies,
            layout,
            events = cfg.events,
            electronics_hits=tracks.electronics_hits,
            electronics_info=screen_info.get("Electronics"),
            let_threshold_mev_cm2_mg=cfg.electronics_let_threshold_mev_cm2_mg,
            dose_threshold_gy=cfg.electronics_dose_threshold_gy,
            absorbed_dose_gy_override=(
                tracks.electronics_dose_gy_total
                if tracks.electronics_dose_events > 0 else None
            ),
            secondary_fluence_threshold=cfg.secondary_fluence_threshold
        )
        logging.info(f'energy summary: {energy_summary}')

        # Обновляем screen_info корректными данными из energy_summary
        # (ScreenSensitiveDetector ненадёжен для вторичных частиц)
        if "Materials" in screen_info:
            stopped_by_mat = energy_summary.get("primary_particles", {}).get("stopped_by_material", {})
            for mat_idx, count in stopped_by_mat.items():
                if mat_idx < len(screen_info["Materials"]):
                    screen_info["Materials"][mat_idx]["Primary_stuck_count"] = count

            stopped_sec_by_mat = energy_summary.get("secondary_particles", {}).get("stopped_by_material", {})
            for mat_idx, count in stopped_sec_by_mat.items():
                if mat_idx < len(screen_info["Materials"]):
                    screen_info["Materials"][mat_idx]["Secondary_stuck_count"] = count

        logging.info(f'updated screen info: {screen_info}')

        result = SimulationResult(
            screen_info=screen_info,
            total_particles=cfg.events,
            total_out_primary_particles=len(primary_out),
            total_out_secondary_particles=len(secondary_out),
            tracks=tracks.data if cfg.collect_tracks else None,
            mixed_beam_result=mixed_result,
            energy_profiles=tracks.energy_profiles,
            exit_energies=tracks.exit_energies,
            electronics_hits=tracks.electronics_hits,
            energy_summary=energy_summary
    )

        # result.energy_profiles = tracks.energy_profiles
        # result.exit_energies = tracks.exit_energies

        logging.info(f"tracks: {tracks.energy_profiles}")
        logging.info(f"result energy_profiles: {result.energy_profiles}")
        logging.info(f"run single result: {result}")
        
        # Очистка
        del run_manager
        gc.collect()
        
        return result
    
    def _load_input(self, cfg):
        if cfg.input_data is not None:
            return cfg.input_data
        if cfg.task_id is not None:
            return DataServer().get_current_task_to_json(cfg.task_id)
        raise ValueError("Either task_id or input_data must be provided")
    
    def _analyze_mixed_beam_results(self, tracks: TrackCollector, total_events: int, particles: List[ParticleConfig]) -> Dict[str, Any]:
        """Анализирует результаты смешанного пучка"""
        particle_counts = {}
        
        for track_key, particle_type in tracks.particle_types.items():
            if track_key[1] == 1:  # Первичные частицы
                particle_counts[particle_type] = particle_counts.get(particle_type, 0) + 1
        
        # Статистика по типам частиц
        stats = {
            "particle_composition": particle_counts,
            "total_primary_particles": sum(particle_counts.values()),
            "expected_composition": {p.name: p.weight for p in particles}
        }
        
        return stats
    
def _find_material_index(z_pos_mm: float, first_z: float, thicknesses: list) -> int:
    """Определяет индекс материала по Z-координате. Возвращает -1 если не найден."""
    if not thicknesses:
        return -1
    end_z = first_z + sum(thicknesses)
    
    # Проверяем, попадает ли точка в диапазон экрана
    z_cursor = first_z
    for idx, th in enumerate(thicknesses):
        layer_end = z_cursor + th
        # Используем <= для обеих границ, чтобы покрыть пограничные случаи
        if z_cursor <= z_pos_mm <= layer_end:
            return idx
        z_cursor = layer_end
    return -1

def _find_stopped_particle_material(points: list, first_z: float, thicknesses: list) -> int:
    """Определяет материал, в котором остановилась/застряла частица.
    
    Для частиц, остановившихся ВНУТРИ экрана — по последней Z.
    Для частиц, остановившихся ДО экрана (обратное рассеяние) — по первой точке внутри экрана.
    Для частиц, остановившихся ПОСЛЕ экрана — по последней точке внутри экрана.
    """
    if not thicknesses or not points:
        return -1
    
    end_z = first_z + sum(thicknesses)
    z_last = float(points[-1][0])
    
    # 1. Частица остановилась ВНУТРИ экрана — используем последнюю Z
    mat_idx = _find_material_index(z_last, first_z, thicknesses)
    if mat_idx >= 0:
        return mat_idx
    
    # 2. Частица остановилась ДО экрана (z_last < first_z) — обратное рассеяние
    #    Ищем первую точку внутри экрана (ближе к месту рождения)
    if z_last < first_z:
        for z, e in points:
            z_float = float(z)
            mat_idx = _find_material_index(z_float, first_z, thicknesses)
            if mat_idx >= 0:
                return mat_idx
        return -1  # Вся траектория вне экрана
    
    # 3. Частица остановилась ПОСЛЕ экрана (z_last > end_z) — пролетела экран
    #    Ищем последнюю точку внутри экрана
    if z_last > end_z:
        for z, e in reversed(points):
            z_float = float(z)
            mat_idx = _find_material_index(z_float, first_z, thicknesses)
            if mat_idx >= 0:
                return mat_idx
        return -1
    
    return -1


def _classify_let_risk(max_let_mev_cm2_mg: Optional[float], threshold_mev_cm2_mg: float) -> Dict[str, Any]:
    if max_let_mev_cm2_mg is None:
        return {
            "level": "unknown",
            "is_dangerous": False,
            "reason": "В слое электроники не зарегистрировано отложение энергии, LET не определен."
        }

    if max_let_mev_cm2_mg >= threshold_mev_cm2_mg:
        return {
            "level": "high",
            "is_dangerous": True,
            "reason": (
                f"Максимальный LET {max_let_mev_cm2_mg:.4f} MeV*cm^2/mg "
                f"превышает порог {threshold_mev_cm2_mg:.4f} MeV*cm^2/mg."
            )
        }

    if max_let_mev_cm2_mg >= 0.5 * threshold_mev_cm2_mg:
        return {
            "level": "moderate",
            "is_dangerous": False,
            "reason": (
                f"Максимальный LET {max_let_mev_cm2_mg:.4f} MeV*cm^2/mg "
                f"ниже порога {threshold_mev_cm2_mg:.4f} MeV*cm^2/mg, "
                "но находится близко к нему."
            )
        }

    return {
        "level": "low",
        "is_dangerous": False,
        "reason": (
            f"Максимальный LET {max_let_mev_cm2_mg:.4f} MeV*cm^2/mg "
            f"существенно ниже порога {threshold_mev_cm2_mg:.4f} MeV*cm^2/mg."
        )
    }


def _classify_dose_risk(absorbed_dose_gy: Optional[float], threshold_gy: float) -> Dict[str, Any]:
    if absorbed_dose_gy is None:
        return {
            "level": "unknown",
            "is_dangerous": False,
            "is_protected": False,
            "reason": "Поглощенная доза в электронике не определена."
        }

    if absorbed_dose_gy >= threshold_gy:
        return {
            "level": "high",
            "is_dangerous": True,
            "is_protected": False,
            "reason": (
                f"Поглощенная доза {absorbed_dose_gy:.6f} Gy превышает порог "
                f"{threshold_gy:.6f} Gy для чувствительной электроники."
            )
        }

    if absorbed_dose_gy >= 0.5 * threshold_gy:
        return {
            "level": "moderate",
            "is_dangerous": False,
            "is_protected": True,
            "reason": (
                f"Поглощенная доза {absorbed_dose_gy:.6f} Gy ниже порога "
                f"{threshold_gy:.6f} Gy, но находится близко к нему."
            )
        }

    return {
        "level": "low",
        "is_dangerous": False,
        "is_protected": True,
        "reason": (
            f"Поглощенная доза {absorbed_dose_gy:.6f} Gy значительно ниже порога "
            f"{threshold_gy:.6f} Gy."
        )
    }


def _compute_electronics_let_summary(
    electronics_hits: list,
    electronics_info: Optional[dict],
    threshold_mev_cm2_mg: float,
    dose_threshold_gy: float,
    events,
    absorbed_dose_gy_override: Optional[float] = None
) -> dict:
    if not electronics_info:
        return {}

    density_g_cm3 = float(electronics_info.get("density_g_cm3", 2.329))
    material_name = electronics_info.get("material", "G4_Si")
    valid_hits = []
    
    for hit in electronics_hits or []:
        try:
            track_length_mm = float(hit.get("track_length_mm", 0.0))
            edep_mev = float(hit.get("edep_mev", 0.0))
            mean_let_mev_cm2_mg = hit.get("mean_let_mev_cm2_mg")
            max_let_mev_cm2_mg = hit.get("max_let_mev_cm2_mg")

            # Включаем хит если:
            # 1. Есть LET (заряженные частицы и нейтральные с ненулевым шагом), ИЛИ
            # 2. Есть реальное осаждение энергии (гаммы/нейтроны с виртуальным шагом).
            # Ранее нейтральные частицы без LET выбрасывались — они не учитывались
            # в event_max_let и dose_per_hit_event_gy, хотя вносили вклад в дозу.
            has_let  = mean_let_mev_cm2_mg is not None or max_let_mev_cm2_mg is not None
            has_edep = edep_mev > 0
            if not has_let and not has_edep:
                continue

            valid_hits.append({
                "event_id": hit.get("event_id"),
                "track_id": hit.get("track_id"),
                "parent_id": hit.get("parent_id", 0),
                "particle": hit.get("particle", "unknown"),
                "edep_mev": edep_mev,
                "track_length_mm": track_length_mm,
                "entry_energy_mev": hit.get("entry_energy_mev"),
                "exit_energy_mev": hit.get("exit_energy_mev"),
                "steps": hit.get("steps", 0),
                "mean_let_mev_cm2_mg": float(mean_let_mev_cm2_mg) if mean_let_mev_cm2_mg is not None else None,
                "max_let_mev_cm2_mg": float(max_let_mev_cm2_mg) if max_let_mev_cm2_mg is not None else None
            })
        except (TypeError, ValueError):
            continue
    logging.info(f"valid hits len: {len(valid_hits)}")
    mean_let_values = [
        hit["mean_let_mev_cm2_mg"] for hit in valid_hits
        if hit["mean_let_mev_cm2_mg"] is not None
    ]
    max_let_values = [
        hit["max_let_mev_cm2_mg"] for hit in valid_hits
        if hit["max_let_mev_cm2_mg"] is not None
    ]
    event_max_let = {}
    for hit in valid_hits:
        event_id = hit.get("event_id")
        hit_max_let = hit.get("max_let_mev_cm2_mg")
        if event_id is None or hit_max_let is None:
            continue
        current = event_max_let.get(event_id)
        event_max_let[event_id] = hit_max_let if current is None else max(current, hit_max_let)

    upset_events_count = sum(
        1 for let_value in event_max_let.values()
        if let_value >= threshold_mev_cm2_mg
    )
    upset_events_fraction = (
        upset_events_count / len(event_max_let)
        if event_max_let else 0.0
    )

    deposited_energy_mev = sum(
        float(hit.get("edep_mev", 0.0) or 0.0)
        for hit in electronics_hits or []
    )
    logging.info(f"deposited_energy_mev: {deposited_energy_mev}")
    mass_mg = float(electronics_info.get("mass_mg", 0.0) or 0.0)
    mass_kg = mass_mg * 1e-6 if mass_mg > 0 else 0.0
    absorbed_dose_gy = (
        float(absorbed_dose_gy_override)
        if absorbed_dose_gy_override is not None
        else (
            deposited_energy_mev * 1.602176634e-13 / mass_kg
            if mass_kg > 0 else None
        )
    )
    # Исправление #4: два показателя нормировки дозы.
    # dose_per_primary_gy     — доза на ОДНО запущенное событие (для экстраполяции на флюенс).
    # dose_per_hit_event_gy   — доза на событие, реально достигшее электроники
    #                           (физически корректная характеристика проникающих частиц).
    n_hit_events = len(event_max_let)
    dose_per_primary_gy = (
        absorbed_dose_gy / events
        if (absorbed_dose_gy is not None and events > 0) else None
    )
    dose_per_hit_event_gy = (
        absorbed_dose_gy / n_hit_events
        if (absorbed_dose_gy is not None and n_hit_events > 0) else None
    )
    logging.info(f"dose_per_primary_gy (all events): {dose_per_primary_gy}")
    logging.info(f"dose_per_hit_event_gy (hit events only): {dose_per_hit_event_gy}")
    logging.info(f"deposited_energy_mev: {deposited_energy_mev}")
    logging.info(f"absorbed_dose_gy: {absorbed_dose_gy}")
    dose_risk = _classify_dose_risk(
        absorbed_dose_gy=absorbed_dose_gy,
        threshold_gy=dose_threshold_gy
    )

    max_hit = max(valid_hits, key=lambda hit: hit.get("max_let_mev_cm2_mg") or 0.0) if valid_hits else None
    above_threshold_count = sum(
        1 for let_value in max_let_values
        if let_value >= threshold_mev_cm2_mg
    )
    above_threshold_fraction = (
        above_threshold_count / len(max_let_values)
        if max_let_values else 0.0
    )
    risk = _classify_let_risk(
        max_let_mev_cm2_mg=max(max_let_values) if max_let_values else None,
        threshold_mev_cm2_mg=threshold_mev_cm2_mg
    )

    return {
        "material": material_name,
        "density_g_cm3": density_g_cm3,
        "thickness_mm": electronics_info.get("thickness_mm"),
        "mass_mg": mass_mg,
        "deposited_energy_mev": deposited_energy_mev,
        "absorbed_dose_gy": absorbed_dose_gy,
        "dose_per_primary_gy": dose_per_primary_gy,
        "dose_per_hit_event_gy": dose_per_hit_event_gy,
        "n_hit_events": n_hit_events,
        "dose_source": "g4_ps_dose_deposit" if absorbed_dose_gy_override is not None else "manual_edep_over_mass",
        "dose_threshold_gy": dose_threshold_gy,
        "dose_assessment": {
            "threshold_gy": dose_threshold_gy,
            "threshold_rad_si": dose_threshold_gy * 100.0,
            "threshold_note": (
                "По умолчанию используется консервативный порог 5 Gy "
                "(около 500 rad(Si)) для чувствительной электроники; "
                "для конкретной ЭКБ порог следует задавать отдельно."
            ),
            "is_dangerous": dose_risk["is_dangerous"],
            "is_protected": dose_risk["is_protected"],
            "risk_level": dose_risk["level"],
            "verdict": "protected" if dose_risk["is_protected"] else "not_protected",
            "reason": dose_risk["reason"]
        },
        "threshold_mev_cm2_mg": threshold_mev_cm2_mg,
        "hits_total": len(electronics_hits or []),
        "tracks_with_deposition": len(valid_hits),
        "events_with_hits": len(event_max_let),
        "avg_let_mev_cm2_mg": sum(mean_let_values) / len(mean_let_values) if mean_let_values else None,
        "max_let_mev_cm2_mg": max(max_let_values) if max_let_values else None,
        "min_let_mev_cm2_mg": min(mean_let_values) if mean_let_values else None,
        "above_threshold_count": above_threshold_count,
        "above_threshold_fraction": above_threshold_fraction,
        "above_threshold_percent": above_threshold_fraction * 100.0,
        "event_upset_risk": {
            "threshold_mev_cm2_mg": threshold_mev_cm2_mg,
            "upset_events_count": upset_events_count,
            "events_with_hits": len(event_max_let),
            "upset_fraction": upset_events_fraction,
            "upset_percent": upset_events_fraction * 100.0
        },
        "is_dangerous": risk["is_dangerous"],
        "risk_level": risk["level"],
        "reason": risk["reason"],
        "worst_case_track": max_hit
    }

def _compute_energy_summary(
    energy_profiles: dict,
    exit_energies: list,
    layout: dict,
    events: int,
    electronics_hits: Optional[list] = None,
    electronics_info: Optional[dict] = None,
    let_threshold_mev_cm2_mg: float = 1.0,
    dose_threshold_gy: float = 5.0,
    absorbed_dose_gy_override: Optional[float] = None,
    secondary_fluence_threshold: float = 0.10
) -> dict:
    """Вычисляет сводную статистику по энергии.

    Ключевые определения:
    - exited_screen    : частица вышла ЗА ЗАДНЮЮ границу экрана (last_z > end_z)
    - stopped_in_screen: остановилась ВНУТРИ материала экрана (e_end < 1 кэВ и first_z <= last_z <= end_z)
    - backscattered    : улетела НАЗАД (last_z < first_z)
    - absorbed_before  : вторичная, остановившаяся ДО экрана (в вакууме)

    exit_energies — список dict {energy_mev, is_primary, particle_type},
    что позволяет раздельно анализировать первичные и вторичные.
    """
    if not energy_profiles:
        energy_profiles = {}

    first_z    = layout.get("first_screen_front_z_mm", 0)
    end_z      = layout.get("screens_end_z_mm", 0)
    screen_thickness = end_z - first_z
    thicknesses = layout.get("thicknesses_mm", [])

    # ------------------------------------------------------------------ #
    # 1. Разбор exit_energies на первичные / вторичные                    #
    # ------------------------------------------------------------------ #
    primary_exit_energies: List[float]     = []
    secondary_exit_energies: List[float]   = []
    exit_by_particle_type: Dict[str, List[float]] = {}

    for item in (exit_energies or []):
        try:
            if isinstance(item, dict):
                e_mev        = float(item["energy_mev"])
                is_prim      = bool(item.get("is_primary", True))
                ptype        = str(item.get("particle_type", "unknown"))
            else:
                # обратная совместимость: просто число
                e_mev   = float(item)
                is_prim = True
                ptype   = "unknown"
        except (TypeError, ValueError, KeyError):
            logging.warning(f"Invalid exit_energy item: {item}, skipping")
            continue

        if is_prim:
            primary_exit_energies.append(e_mev)
        else:
            secondary_exit_energies.append(e_mev)
        exit_by_particle_type.setdefault(ptype, []).append(e_mev)

    all_exit_energies_flat = primary_exit_energies + secondary_exit_energies

    # ------------------------------------------------------------------ #
    # 2. Коэффициент ослабления флюенса (Fluence Attenuation Factor)      #
    #    FAF = N_primary_exited / N_events                                #
    #    Чем меньше FAF, тем лучше экран.                                 #
    # ------------------------------------------------------------------ #
    n_primary_exited   = len(primary_exit_energies)
    n_secondary_exited = len(secondary_exit_energies)
    fluence_attenuation_factor   = n_primary_exited / events if events > 0 else 0.0
    fluence_attenuation_coeff    = 1.0 - fluence_attenuation_factor   # == stopping efficiency

    # ------------------------------------------------------------------ #
    # 3. Остаточная энергия прошедших первичных частиц                    #
    # ------------------------------------------------------------------ #
    mean_primary_exit_energy   = (
        sum(primary_exit_energies) / len(primary_exit_energies)
        if primary_exit_energies else None
    )
    max_primary_exit_energy    = max(primary_exit_energies)  if primary_exit_energies else None
    min_primary_exit_energy    = min(primary_exit_energies)  if primary_exit_energies else None

    mean_secondary_exit_energy = (
        sum(secondary_exit_energies) / len(secondary_exit_energies)
        if secondary_exit_energies else None
    )

    # Разбивка по типу частицы
    exit_by_type_summary: Dict[str, Any] = {}
    for ptype, energies in exit_by_particle_type.items():
        exit_by_type_summary[ptype] = {
            "count":      len(energies),
            "mean_mev":   sum(energies) / len(energies),
            "max_mev":    max(energies),
            "min_mev":    min(energies),
        }

    # ------------------------------------------------------------------ #
    # 4. Анализ треков: остановки, выходы, пик Брэгга                     #
    # ------------------------------------------------------------------ #
    primary_profiles   = [p for p in energy_profiles.values() if p.get("parent_id", 0) == 0]
    secondary_profiles = [p for p in energy_profiles.values() if p.get("parent_id", 0) != 0]

    # --- первичные ---
    stopped_primary             = 0
    exited_primary              = 0
    backscattered_primary       = 0
    stopped_primary_by_material: Dict[int, int] = {}
    exited_primary_initial_energies: List[float] = []
    exited_primary_final_energies:   List[float] = []
    energy_loss_primary:             List[float] = []
    # Bragg peak analysis
    bragg_stopped_inside  = 0   # пик внутри экрана — хорошо
    bragg_stopped_after   = 0   # пик ЗА экраном — экран тонкий
    bragg_stopped_before  = 0   # обратное рассеяние
    bragg_stop_z_list:    List[float] = []

    for profile in primary_profiles:
        points = profile.get("points", [])
        if len(points) < 2:
            continue
        try:
            e_start = float(points[0][1])
            e_end   = float(points[-1][1])
            z_last  = float(points[-1][0])
            energy_loss_primary.append(e_start - e_end)

            if z_last > end_z:
                exited_primary += 1
                exited_primary_initial_energies.append(e_start)
                exited_primary_final_energies.append(e_end)
                bragg_stopped_after += 1          # дошла до конца экрана и пошла дальше
            elif z_last < first_z:
                backscattered_primary += 1
                bragg_stopped_before += 1
            else:   # first_z <= z_last <= end_z
                if e_end < 0.001:   # остановилась (< 1 кэВ)
                    stopped_primary += 1
                    bragg_stopped_inside += 1
                    bragg_stop_z_list.append(z_last)
                    mat_idx = _find_stopped_particle_material(points, first_z, thicknesses)
                    if mat_idx >= 0:
                        stopped_primary_by_material[mat_idx] = (
                            stopped_primary_by_material.get(mat_idx, 0) + 1
                        )
        except (ValueError, TypeError, IndexError):
            continue

    # ------------------------------------------------------------------ #
    # Исправление #7: проверка рассогласования двух счётчиков.           #
    # n_primary_exited — из exit_energies (ScreenSteppingAction):        #
    #   фиксирует реальное пересечение задней границы экрана.            #
    # exited_primary   — из energy_profiles (финальная z > end_z):       #
    #   анализирует последнюю точку трека.                               #
    # Авторитетный источник для FAF — exit_energies.                     #
    # ------------------------------------------------------------------ #
    if abs(n_primary_exited - exited_primary) > max(1, int(0.05 * events)):
        logging.warning(
            "Counter mismatch: exit_energies primary exited=%d vs "
            "energy_profiles exited_primary=%d (events=%d). "
            "FAF computed from exit_energies (boundary crossing).",
            n_primary_exited, exited_primary, events
        )

    # --- вторичные ---
    stopped_secondary             = 0
    exited_secondary              = 0
    backscattered_secondary       = 0
    absorbed_before_secondary     = 0
    stopped_secondary_by_material: Dict[int, int] = {}
    energy_loss_secondary:         List[float] = []

    for profile in secondary_profiles:
        points = profile.get("points", [])
        if len(points) < 2:
            continue
        try:
            e_start = float(points[0][1])
            e_end   = float(points[-1][1])
            z_last  = float(points[-1][0])
            energy_loss_secondary.append(e_start - e_end)

            if z_last > end_z:
                exited_secondary += 1
            elif z_last < first_z:
                backscattered_secondary += 1
                if e_end < 0.001:
                    absorbed_before_secondary += 1
            else:
                if e_end < 0.001:
                    stopped_secondary += 1
                    mat_idx = _find_stopped_particle_material(points, first_z, thicknesses)
                    if mat_idx >= 0:
                        stopped_secondary_by_material[mat_idx] = (
                            stopped_secondary_by_material.get(mat_idx, 0) + 1
                        )
        except (ValueError, TypeError, IndexError):
            continue

    # ------------------------------------------------------------------ #
    # 5. Энергетическое ослабление первичных (из треков)                  #
    # ------------------------------------------------------------------ #
    mean_initial_exit_primary_energy = (
        sum(exited_primary_initial_energies) / len(exited_primary_initial_energies)
        if exited_primary_initial_energies else None
    )
    mean_final_exit_primary_energy = (
        sum(exited_primary_final_energies) / len(exited_primary_final_energies)
        if exited_primary_final_energies else None
    )
    # Коэффициент ослабления энергии прошедших частиц
    # (насколько экран снизил энергию тех, кто всё-таки прошёл)
    energy_attenuation_fraction = (
        1.0 - (mean_final_exit_primary_energy / mean_initial_exit_primary_energy)
        if mean_initial_exit_primary_energy and mean_initial_exit_primary_energy > 0
           and mean_final_exit_primary_energy is not None
        else None
    )

    # ------------------------------------------------------------------ #
    # 6. Bragg Peak summary                                               #
    # ------------------------------------------------------------------ #
    total_classified = bragg_stopped_inside + bragg_stopped_after + bragg_stopped_before
    bragg_inside_fraction = (
        bragg_stopped_inside / total_classified if total_classified > 0 else 0.0
    )
    mean_bragg_stop_z = (
        sum(bragg_stop_z_list) / len(bragg_stop_z_list) if bragg_stop_z_list else None
    )
    # Глубина пика Брэгга относительно начала экрана
    mean_bragg_depth_mm = (
        (mean_bragg_stop_z - first_z) if mean_bragg_stop_z is not None else None
    )

    # ------------------------------------------------------------------ #
    # 7. Электроника: LET и доза                                          #
    # ------------------------------------------------------------------ #
    electronics_let = _compute_electronics_let_summary(
        electronics_hits=electronics_hits or [],
        electronics_info=electronics_info,
        threshold_mev_cm2_mg=let_threshold_mev_cm2_mg,
        dose_threshold_gy=dose_threshold_gy,
        events=events,
        absorbed_dose_gy_override=absorbed_dose_gy_override
    )

    # ------------------------------------------------------------------ #
    # 8. Итоговый вердикт: защитил / не защитил                           #
    #                                                                     #
    # Критерии (все должны быть выполнены для "protected"):               #
    #   C1. Коэффициент ослабления флюенса < 5%                           #
    #       (менее 5% первичных частиц прошли насквозь)                   #
    #   C2. Пик Брэгга: >= 90% первичных остановились ВНУТРИ экрана      #
    #   C3. Поглощённая доза в электронике < порога                       #
    #   C4. Максимальный LET в электронике < порога                       #
    #   C5. Event upset risk = 0                                          #
    # ------------------------------------------------------------------ #
    dose_assessment  = electronics_let.get("dose_assessment", {})
    event_upset_risk = electronics_let.get("event_upset_risk", {})

    FAF_THRESHOLD          = 0.05   # <= 5% первичных прошли
    BRAGG_INSIDE_THRESHOLD = 0.90   # >= 90% остановились внутри

    # Исправление #6: если electronics_let пуст — электроника не задана в конфиге.
    # В этом случае критерии C3/C4/C5 не применимы и считаются пройденными,
    # чтобы не давать ложный вердикт "not_protected" при отсутствии электроники.
    electronics_absent = not bool(electronics_let)

    # ------------------------------------------------------------------ #
    # Критерий C6: Secondary Production Ratio (SPR)                      #
    # SPR = n_secondary_exited / events                                  #
    # Показывает долю событий, породивших хотя бы одну вторичную         #
    # частицу ЗА экраном. Высокий SPR означает, что экран сам является   #
    # источником вторичного излучения (тормозное, нейтроны, гаммы и т.д) #
    # даже при хорошем задержании первичных частиц.                      #
    # ------------------------------------------------------------------ #
    secondary_production_ratio = n_secondary_exited / events if events > 0 else 0.0

    c1_fluence_ok = fluence_attenuation_factor <= FAF_THRESHOLD
    c2_bragg_ok   = bragg_inside_fraction >= BRAGG_INSIDE_THRESHOLD
    c3_dose_ok    = electronics_absent or dose_assessment.get("is_protected", False)
    c4_let_ok     = electronics_absent or (not electronics_let.get("is_dangerous", False))
    c5_upset_ok   = electronics_absent or (event_upset_risk.get("upset_events_count", 0) == 0)
    c6_secondary_ok = secondary_production_ratio <= secondary_fluence_threshold

    screen_protected = c1_fluence_ok and c2_bragg_ok and c3_dose_ok and c4_let_ok and c5_upset_ok and c6_secondary_ok

    criteria_details = {
        "C1_fluence_attenuation": {
            "passed": c1_fluence_ok,
            "value": fluence_attenuation_factor,
            "threshold": FAF_THRESHOLD,
            "description": f"Доля первичных, прошедших экран: "
                           f"{fluence_attenuation_factor * 100:.2f}% "
                           f"(порог <= {FAF_THRESHOLD * 100:.0f}%)"
        },
        "C2_bragg_peak_inside": {
            "passed": c2_bragg_ok,
            "value": bragg_inside_fraction,
            "threshold": BRAGG_INSIDE_THRESHOLD,
            "description": f"Доля первичных, остановившихся внутри экрана: "
                           f"{bragg_inside_fraction * 100:.1f}% "
                           f"(порог >= {BRAGG_INSIDE_THRESHOLD * 100:.0f}%)"
        },
                "C3_dose": {
            "passed": c3_dose_ok,
            "value": electronics_let.get("absorbed_dose_gy"),
            "threshold": dose_threshold_gy,
            "description": (
                "Электроника не задана — критерий не применяется."
                if electronics_absent
                else dose_assessment.get("reason", "Нет данных о дозе")
            )
        },
        "C4_let": {
            "passed": c4_let_ok,
            "value": electronics_let.get("max_let_mev_cm2_mg"),
            "threshold": let_threshold_mev_cm2_mg,
            "description": (
                "Электроника не задана — критерий не применяется."
                if electronics_absent
                else electronics_let.get("reason", "Нет данных о LET")
            )
        },
                "C5_event_upset": {
            "passed": c5_upset_ok,
            "value": event_upset_risk.get("upset_events_count", 0),
            "threshold": 0,
            "description": (
                "Электроника не задана — критерий не применяется."
                if electronics_absent
                else f"Событий с опасным LET: {event_upset_risk.get('upset_events_count', 0)}"
            )
        },
        "C6_secondary_fluence": {
            "passed": c6_secondary_ok,
            "value": secondary_production_ratio,
            "threshold": secondary_fluence_threshold,
            "description": (
                f"Доля событий с вторичными частицами за экраном: "
                f"{secondary_production_ratio * 100:.2f}% "
                f"(порог <= {secondary_fluence_threshold * 100:.0f}%, "
                f"абс.: {n_secondary_exited} из {events} событий)"
            )
        },
    }

    failed = [k for k, v in criteria_details.items() if not v["passed"]]
    if screen_protected:
        protection_verdict = "protected"
        protection_reason  = (
            "Экран признан защитным: все критерии выполнены — "
            "коэффициент ослабления флюенса в норме, пик Брэгга внутри экрана, "
            "доза и LET в электронике ниже порогов, event upset не выявлен, "
            f"вторичный флюенс за экраном в норме ({secondary_production_ratio * 100:.2f}% "
            f"<= {secondary_fluence_threshold * 100:.0f}%)."
        )
    else:
        protection_verdict = "not_protected"
        failed_desc = "; ".join(
            criteria_details[k]["description"] for k in failed
        )
        protection_reason = f"Экран не признан защитным. Нарушены критерии: {failed_desc}"

    logging.info(
        "Shield verdict: %s | FAF=%.3f | BraggInside=%.2f | Dose=%s | "
        "LET_max=%s | UpsetEvents=%d",
        protection_verdict,
        fluence_attenuation_factor,
        bragg_inside_fraction,
        electronics_let.get("absorbed_dose_gy"),
        electronics_let.get("max_let_mev_cm2_mg"),
        event_upset_risk.get("upset_events_count", 0),
    )

    return {
        # --- первичные частицы ---
        "primary_particles": {
            "total": len(primary_profiles),
            "stopped_in_screen": stopped_primary,
            "stopped_by_material": stopped_primary_by_material,
            "exited_screen": exited_primary,
            "backscattered": backscattered_primary,
            "stopping_fraction": (
                stopped_primary / len(primary_profiles) if primary_profiles else 0.0
            ),
            "mean_initial_energy_exited_mev": mean_initial_exit_primary_energy,
            "mean_exit_energy_mev": mean_final_exit_primary_energy,
            "energy_attenuation_fraction": energy_attenuation_fraction,
            "energy_attenuation_percent": (
                energy_attenuation_fraction * 100.0
                if energy_attenuation_fraction is not None else None
            ),
            "avg_energy_loss_mev": (
                sum(energy_loss_primary) / len(energy_loss_primary)
                if energy_loss_primary else 0.0
            ),
            "max_energy_loss_mev": max(energy_loss_primary) if energy_loss_primary else 0.0,
            "min_energy_loss_mev": min(energy_loss_primary) if energy_loss_primary else 0.0,
        },
        # --- вторичные частицы ---
        "secondary_particles": {
            "total": len(secondary_profiles),
            "stopped_in_screen": stopped_secondary,
            "stopped_by_material": stopped_secondary_by_material,
            "exited_screen": exited_secondary,
            "backscattered": backscattered_secondary,
            "absorbed_before_screen": absorbed_before_secondary,
            "stopping_fraction": (
                stopped_secondary / len(secondary_profiles) if secondary_profiles else 0.0
            ),
            "avg_energy_loss_mev": (
                sum(energy_loss_secondary) / len(energy_loss_secondary)
                if energy_loss_secondary else 0.0
            ),
        },
        # --- пик Брэгга ---
        "bragg_peak": {
            "stopped_inside_screen": bragg_stopped_inside,
            "stopped_after_screen":  bragg_stopped_after,
            "stopped_before_screen": bragg_stopped_before,
            "inside_fraction":       bragg_inside_fraction,
            "inside_percent":        bragg_inside_fraction * 100.0,
            "mean_stop_z_mm":        mean_bragg_stop_z,
            "mean_stop_depth_mm":    mean_bragg_depth_mm,
            "screen_thick_enough":   bragg_stopped_after == 0,
            "comment": (
                "Пик Брэгга внутри экрана — толщина достаточна."
                if bragg_stopped_after == 0 and bragg_stopped_inside > 0
                else (
                    f"{bragg_stopped_after} первичных частиц остановились ЗА экраном — "
                    "рекомендуется увеличить толщину."
                    if bragg_stopped_after > 0
                    else "Нет данных о траекториях первичных частиц."
                )
            ),
        },
        # --- коэффициент ослабления флюенса ---
            "fluence_attenuation": {
            "events_total":                   events,
            "primary_exited":                 n_primary_exited,
            "secondary_exited":               n_secondary_exited,
            "fluence_attenuation_factor":      fluence_attenuation_factor,
            "fluence_attenuation_percent":     fluence_attenuation_factor * 100.0,
            "fluence_attenuation_coeff":       fluence_attenuation_coeff,
            "stopping_efficiency_percent":     fluence_attenuation_coeff * 100.0,
            "secondary_production_ratio":      secondary_production_ratio,
            "secondary_production_percent":    secondary_production_ratio * 100.0,
            "secondary_fluence_threshold":     secondary_fluence_threshold,
            "secondary_fluence_threshold_pct": secondary_fluence_threshold * 100.0,
            "comment": (
                f"Экран задержал {fluence_attenuation_coeff * 100:.1f}% первичных частиц "
                f"({events - n_primary_exited} из {events}). "
                f"Вторичных за экраном: {n_secondary_exited} "
                f"({secondary_production_ratio * 100:.2f}% от событий)."
            ),
        },
        # --- остаточная энергия ---
        "residual_energy": {
            "primary_exit": {
                "count":    n_primary_exited,
                "mean_mev": mean_primary_exit_energy,
                "max_mev":  max_primary_exit_energy,
                "min_mev":  min_primary_exit_energy,
            },
            "secondary_exit": {
                "count":    n_secondary_exited,
                "mean_mev": mean_secondary_exit_energy,
            },
            "by_particle_type": exit_by_type_summary,
        },
        # --- геометрия экрана ---
        "screen": {
            "thickness_mm": screen_thickness,
            "first_z_mm":   first_z,
            "end_z_mm":     end_z,
        },
        # --- электроника: LET и доза ---
        "electronics_let": electronics_let,
        # --- итоговый вердикт ---
        "screen_protection_report": {
            "is_protected": screen_protected,
            "verdict":      protection_verdict,
            "reason":       protection_reason,
            "criteria":     criteria_details,
        },
    }
# -----------------------------
# Запуск симуляции
# -----------------------------
class SimulationRunner:
    def __init__(self):
        self._last_tracks = None
    
    def run_sequential_multiprocess(self, cfg: SimulationConfig) -> SimulationResult:
        """Запускает последовательные симуляции в отдельных процессах"""
        logging.info("Запускает последовательные симуляции в отдельных процессах")
        if cfg.input_data is not None:
            data = cfg.input_data
        elif cfg.task_id is not None:
            data = DataServer().get_current_task_to_json(cfg.task_id)
        else:
            raise ValueError("Either task_id or input_data must be provided")
        particle_results = {}

        all_energy_profiles  = {}
        all_exit_energies    = []
        all_electronics_hits = []
        all_screen_info      = None
        all_energy_summary   = {}
        all_absorbed_dose_gy = 0.0
        has_absorbed_dose_gy = False
        # Суммируем реально выполненные события по всем подпрогонам.
        # cfg.events — события одного прогона; при N частицах суммарно N×events.
        # Используем фактическое число из result_dict["total_particles"],
        # чтобы корректно обработать прогоны, завершившиеся с ошибкой.
        all_total_events = 0

        logging.info(f"simulation config: {cfg}")
        # Создаем конфиги для каждой частицы
        configs = []
        for p_config in cfg.particles:
            particle_cfg = SimulationConfig(
                task_id=cfg.task_id,
                input_data=cfg.input_data,
                particles=[p_config],
                events=cfg.events,
                world_xy_mm=cfg.world_xy_mm,
                world_z_mm=cfg.world_z_mm,
                screen_xy_mm=cfg.screen_xy_mm,
                first_screen_z_mm=cfg.first_screen_z_mm,
                electronics_gap_mm=cfg.electronics_gap_mm,
                electronics_size_x_mm=cfg.electronics_size_x_mm,
                electronics_size_y_mm=cfg.electronics_size_y_mm,
                electronics_thickness_mm=cfg.electronics_thickness_mm,
                electronics_material=cfg.electronics_material,
                electronics_let_threshold_mev_cm2_mg=cfg.electronics_let_threshold_mev_cm2_mg,
                electronics_dose_threshold_gy=cfg.electronics_dose_threshold_gy,
                secondary_fluence_threshold=cfg.secondary_fluence_threshold,
                collect_tracks=cfg.collect_tracks,
                visualize=False,
                use_mixed_beam=False
            )
            configs.append((p_config, asdict(particle_cfg)))
        
        # Запускаем в параллельных процессах
        with ProcessPoolExecutor(max_workers=min(len(configs), mp.cpu_count())) as executor:
            future_to_config = {
                executor.submit(run_single_simulation_in_process, config_dict): (p_config, config_dict)
                for p_config, config_dict in configs
            }
            
            for future in as_completed(future_to_config):
                p_config, config_dict = future_to_config[future]
                key = f"{p_config.name}_{p_config.energy_mev}MeV"
                
                try:
                    result_dict = future.result()
    
                    # Восстанавливаем треки из сериализованного формата
                    tracks = None
                    if "tracks" in result_dict and result_dict["tracks"]:
                        tracks = {}
                        for str_key, points in result_dict["tracks"].items():
                            try:
                                event_id, track_id = map(int, str_key.split('_'))
                                tracks[(event_id, track_id)] = points
                            except:
                                continue
                    
                    # Создаем SingleParticleResult из словаря
                    result = SingleParticleResult(
                        particle=p_config.name,
                        energy_mev=p_config.energy_mev,
                        screen_info=result_dict["screen_info"],
                        total_particles=result_dict["total_particles"],
                        total_out_primary_particles=result_dict["total_out_primary_particles"],
                        total_out_secondary_particles=result_dict["total_out_secondary_particles"],
                        tracks=tracks
                    )
                    particle_results[key] = result
                    # Накапливаем реально выполненные события этого подпрогона
                    all_total_events += int(result_dict.get("total_particles") or 0)
                    # logging.info(f"cfg result: {particle_results[key]}")
                    print(f"Completed: {key}")
                    logging.info(f"Completed: {key}")                                    

                    if "energy_profiles" in result_dict and result_dict["energy_profiles"]:
                        for track_key, profiles in result_dict["energy_profiles"].items():
                            # Добавляем префикс с типом частицы для уникальности
                            new_key = f"{p_config.name}_{track_key}"
                            all_energy_profiles[new_key] = profiles
                    else :
                        logging.info("no energy profiles")

                    # logging.info(f"all energy profiles: {all_energy_profiles}")

                    if "exit_energies" in result_dict and result_dict["exit_energies"]:
                        logging.info(f'ex: {result_dict["exit_energies"]}')
                        all_exit_energies.extend(result_dict["exit_energies"])

                    if "electronics_hits" in result_dict and result_dict["electronics_hits"]:
                        all_electronics_hits.extend(result_dict["electronics_hits"])

                    if "energy_summary" in result_dict and result_dict["energy_summary"]:
                        logging.info(f'energy_summary: {result_dict["energy_summary"]}')
                        electronics_let = (result_dict.get("energy_summary") or {}).get("electronics_let") or {}
                        # Исправление #1: берём дозу ТОЛЬКО если источник — G4PSDoseDeposit
                        # (dose_source == "g4_ps_dose_deposit"), т.е. scorer реально сработал.
                        # Дозу из "manual_edep_over_mass" НЕ суммируем здесь:
                        # all_electronics_hits уже содержит все edep, и финальный
                        # _compute_energy_summary сам пересчитает дозу через edep/mass
                        # без двойного счёта.
                        dose_source = electronics_let.get("dose_source")
                        dose_value  = electronics_let.get("absorbed_dose_gy")
                        if dose_value is not None and dose_source == "g4_ps_dose_deposit":
                            try:
                                all_absorbed_dose_gy += float(dose_value)
                                has_absorbed_dose_gy = True
                            except (TypeError, ValueError):
                                pass
                        elif dose_source == "manual_edep_over_mass":
                            logging.info(
                                "Skipping manual_edep_over_mass dose %.6f Gy for particle '%s': "
                                "will be recomputed from all_electronics_hits in final summary.",
                                dose_value or 0.0, p_config.name
                            )

                    res_screen = result_dict.get("screen_info", {})
                    res_mats = res_screen.get("Materials", [])

                    if all_screen_info is None and res_mats:
                        all_screen_info = {"Materials": []}
                        for mat in res_mats:
                            all_screen_info["Materials"].append({
                                "Name": mat.get("Name", "Unknown"),
                                "Thickness_mm": mat.get("Thickness_mm", 0),
                                "Primary_stuck_count": 0,
                                "Secondary_stuck_count": 0,
                                "Edep": 0.0
                            })
                        if res_screen.get("Electronics"):
                            electronics = dict(res_screen.get("Electronics"))
                            electronics["deposited_energy_mev"] = float(electronics.get("deposited_energy_mev", 0.0) or 0.0)
                            electronics["track_length_mm"] = float(electronics.get("track_length_mm", 0.0) or 0.0)
                            electronics["hit_count"] = int(electronics.get("hit_count", 0) or 0)
                            all_screen_info["Electronics"] = electronics

                    if all_screen_info.get("Materials"):
                        for i, mat in enumerate(res_mats):
                            if i < len(all_screen_info["Materials"]):
                                target = all_screen_info["Materials"][i]
                                target["Primary_stuck_count"] += mat.get("Primary_stuck_count", 0)
                                target["Secondary_stuck_count"] += mat.get("Secondary_stuck_count", 0)
                                target["Edep"] = target.get("Edep", 0.0) + mat.get("Edep", 0.0)

                    if all_screen_info and res_screen.get("Electronics"):
                        target_electronics = all_screen_info.setdefault("Electronics", dict(res_screen.get("Electronics")))
                        res_electronics = res_screen.get("Electronics", {})
                        target_electronics["deposited_energy_mev"] = (
                            float(target_electronics.get("deposited_energy_mev", 0.0) or 0.0)
                            + float(res_electronics.get("deposited_energy_mev", 0.0) or 0.0)
                        )
                        target_electronics["track_length_mm"] = (
                            float(target_electronics.get("track_length_mm", 0.0) or 0.0)
                            + float(res_electronics.get("track_length_mm", 0.0) or 0.0)
                        )
                        target_electronics["hit_count"] = (
                            int(target_electronics.get("hit_count", 0) or 0)
                            + int(res_electronics.get("hit_count", 0) or 0)
                        )
                        if res_electronics.get("let_max_mev_cm2_mg") is not None:
                            target_electronics["let_max_mev_cm2_mg"] = max(
                                float(target_electronics.get("let_max_mev_cm2_mg", 0.0) or 0.0),
                                float(res_electronics.get("let_max_mev_cm2_mg", 0.0) or 0.0)
                            )

                except Exception as e:
                    print(f"Failed: {key} - {e}")
                    # Создаем результат с ошибкой
                    result = SingleParticleResult(
                        particle=p_config.name,
                        energy_mev=p_config.energy_mev,
                        screen_info={},
                        total_particles=0,
                        total_out_primary_particles=0,
                        total_out_secondary_particles=0,
                        tracks=None
                    )
                    particle_results[key] = result

        # Создаем сравнительный отчет
        logging.info(f'partivle results :{particle_results}')
        comparison = self._create_comparison(particle_results)
        logging.info(f'all_exit_energies: {all_exit_energies}')

        layout = compute_layout(cfg=cfg, data=data)
        energy_summary = _compute_energy_summary(
            energy_profiles=all_energy_profiles,
            exit_energies=all_exit_energies,
            layout=layout,
            electronics_hits=all_electronics_hits,
            electronics_info=(all_screen_info or {}).get("Electronics"),
            let_threshold_mev_cm2_mg=cfg.electronics_let_threshold_mev_cm2_mg,
            dose_threshold_gy=cfg.electronics_dose_threshold_gy,
            # all_total_events — сумма реально выполненных событий по всем подпрогонам
            # (N_частиц × cfg.events при успешном завершении всех прогонов).
            # Используем cfg.events * len(cfg.particles) как запасной вариант
            # если all_total_events не накопился (все прогоны упали с ошибкой).
            events=all_total_events if all_total_events > 0 else cfg.events * len(cfg.particles),
            absorbed_dose_gy_override=(all_absorbed_dose_gy if has_absorbed_dose_gy else None),
            secondary_fluence_threshold=cfg.secondary_fluence_threshold
        )
        logging.info(
            f'res energy summary (total_events={all_total_events}, '
            f'particles={len(cfg.particles)}, events_per_run={cfg.events})'
        )

        # Обновляем all_screen_info корректными данными из energy_summary
        if all_screen_info and "Materials" in all_screen_info:
            stopped_by_mat = energy_summary.get("primary_particles", {}).get("stopped_by_material", {})
            for mat_idx, count in stopped_by_mat.items():
                if mat_idx < len(all_screen_info["Materials"]):
                    all_screen_info["Materials"][mat_idx]["Primary_stuck_count"] = count

            stopped_sec_by_mat = energy_summary.get("secondary_particles", {}).get("stopped_by_material", {})
            for mat_idx, count in stopped_sec_by_mat.items():
                if mat_idx < len(all_screen_info["Materials"]):
                    all_screen_info["Materials"][mat_idx]["Secondary_stuck_count"] = count

        sim_res = SimulationResult(
            particle_results=particle_results,
            comparison=comparison,
            screen_info=all_screen_info,
            total_out_primary_particles=sum(
                r.total_out_primary_particles for r in particle_results.values()
            ),
            total_out_secondary_particles=sum(
                r.total_out_secondary_particles for r in particle_results.values()
            ),
            energy_profiles=all_energy_profiles,
            exit_energies=all_exit_energies,
            electronics_hits=all_electronics_hits,
            energy_summary=energy_summary
        )
        logging.info(f"final simulation results: {sim_res}")
        return sim_res
    
    def run(self, cfg: SimulationConfig) -> SimulationResult:
        """Основной метод запуска симуляции"""
        
        # Автоматическое определение режима
        if len(cfg.particles) == 1:
            # Одиночная частица - запускаем в текущем процессе
            runner = SingleProcessSimulationRunner()
            return runner.run_single(cfg)
        elif cfg.use_mixed_beam:
            # Смешанный пучок - запускаем в текущем процессе
            runner = SingleProcessSimulationRunner()
            return runner.run_single(cfg)
        else:
            # Последовательные запуски - используем отдельные процессы
            return self.run_sequential_multiprocess(cfg)
    
    def _create_comparison(self, particle_results: Dict[str, SingleParticleResult]) -> Dict[str, Any]:
        """Создает сравнительный отчет по всем частицам"""
        comparison = {}
        
        for key, result in particle_results.items():
            total_primary = result.total_out_primary_particles
            total_events = result.total_particles
            
            comparison[key] = {
                "transmission_rate": total_primary / total_events if total_events > 0 else 0,
                "stopping_efficiency": 1 - (total_primary / total_events) if total_events > 0 else 0,
                "secondary_production": result.total_out_secondary_particles,
                "material_interactions": result.screen_info.get("Materials", [])
            }
        
        return comparison
        
# Глобальная переменная для отслеживания состояния Geant4
_geant4_initialized = False

def _cleanup_geant4():
    """Функция для очистки ресурсов Geant4 при выходе"""
    global _geant4_initialized
    if _geant4_initialized:
        try:
            # Принудительная сборка мусора
            gc.collect()
            _geant4_initialized = False
        except:
            pass

atexit.register(_cleanup_geant4)

# Обновите основную часть кода в конце файла:

if __name__ == "__main__":
    ds = DataServer()
    task_id = 103
    
    # Использовать подготовленные данные, сгенерированные LLM
    data = \
    {
        "Screen": {
            "Name": "Экран из W и Ti",
            "Description": "Экран состоит из двух слоев: W и Ti.",
            "Materials": [
                                {
                    "Name": "POLYETHYLENE",
                    "Width": 5000.0,
                    "Density": 0.965,
                    "isCompound": True,
                    "Elements": [
                        {"Symbol": "C", "NAtoms": 2},
                        {"Symbol": "H", "NAtoms": 4}
                    ]
                },
                {
                    "Name": "Al",
                    "Description": "Алюминий (Al) толщиной 1000 мкм",
                    "Width": 3000.0,
                    "Elements": [
                        {
                            "Name": "Алюминий",
                            "Symbol": "Al",
                            "Atomic_number": 13,
                            "Standard_atomic_weight": 26.98,
                            "Density": 2.7,
                            "Percentage": 100.0
                        }
                    ]
                },

                # {
                #     "Name": "Титан",
                #     "Description": "Титан",
                #     "Width": 2000.0,
                #     "Elements": [
                #         {
                #             "Name": "Титан",
                #             "Symbol": "Ti",
                #             "Atomic_number": 22,
                #             "Standard_atomic_weight": 47.87,
                #             "Density": 4.5,
                #             "Percentage": 100.0
                #         }
                #     ]
                # },
                {
                    "Name": "Pb",
                    "Description": "Pb layer",
                    "Width": 4000.0,
                    "Elements": [
                        {
                            "Name": "Свинец",
                            "Symbol": "Pb",
                            "Atomic_number": 82,
                            "Standard_atomic_weight": 207.2,
                            "Density": 11.35,
                            "Percentage": 100.0
                        }
                    ]
                },
                {
                    "Name": "Al",
                    "Description": "Алюминий (Al) толщиной 1000 мкм",
                    "Width": 1000.0,
                    "Elements": [
                        {
                            "Name": "Алюминий",
                            "Symbol": "Al",
                            "Atomic_number": 13,
                            "Standard_atomic_weight": 26.98,
                            "Density": 2.7,
                            "Percentage": 100.0
                        }
                    ]
                }
                # {
                #     "Name": "Al",
                #     "Description": "Алюминий (Al) толщиной 1000 мкм",
                #     "Width": 2000.0,
                #     "Elements": [
                #         {
                #             "Name": "Алюминий",
                #             "Symbol": "Al",
                #             "Atomic_number": 13,
                #             "Standard_atomic_weight": 26.98,
                #             "Density": 2.7,
                #             "Percentage": 100.0
                #         }
                #     ]
                # }
                # {
                #     "Name": "W",
                #     "Description": "Вольфрам (W) толщиной 2000 мкм",
                #     "Width": 1000.0,
                #     "Elements": [
                #         {
                #             "Name": "Вольфрам",
                #             "Symbol": "W",
                #             "Atomic_number": 74,
                #             "Standard_atomic_weight": 183.84,
                #             "Density": 19.25,
                #             "Percentage": 100.0
                #         }
                #     ]
                # }
                # {
                #     "Name": "Al",
                #     "Description": "Алюминий (Al) толщиной 1000 мкм",
                #     "Width": 4000.0,
                #     "Elements": [
                #         {
                #             "Name": "Алюминий",
                #             "Symbol": "Al",
                #             "Atomic_number": 13,
                #             "Standard_atomic_weight": 26.98,
                #             "Density": 2.7,
                #             "Percentage": 100.0
                #         }
                #     ]
                # },
                # {
                #     "Name": "Be",
                #     "Description": "Be",
                #     "Width": 2000.0,
                #     "Elements": [
                #         {
                #             "Name": "Бериллий",
                #             "Symbol": "Be",
                #             "Atomic_number": 4,
                #             "Standard_atomic_weight": 9.012,
                #             "Density": 1.85,
                #             "Percentage": 100.0
                #         }
                #     ]
                # },
            ]
        }
    }
    '''
    Использовать для получения данных по task_id с сайта (раскоментировать строку)
    '''
    # data = ds.get_current_task_to_json(task_id)
    # events = 1_000_000
    events = 100_000
    # Пример: Мульти-частичный последовательный режим
    cfg_multi = SimulationConfig(
        screen_xy_mm=100,
        electronics_thickness_mm=0.5,
        task_id=task_id,
        input_data=data,
        particles=[
            ParticleConfig(name="He3", energy_mev=30.0),
            # ParticleConfig(name="e-", energy_mev=10.0),
            # ParticleConfig(name="gamma", energy_mev=20.0)
            # ParticleConfig(name="alpha", energy_mev=70.0),
            ParticleConfig(name="proton", energy_mev=30.0)
            # ParticleConfig(name="neutron", energy_mev=50.0)
        ],
        events=events,
        collect_tracks=True,
        visualize=True,
        use_mixed_beam=False  
    )
    
    runner = SimulationRunner()
    result = runner.run(cfg_multi)
    logging.info(f"print results")
    logging.info(f'result object: {result}')
    logging.info(f'exit energies: {result.exit_energies}')
    # print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    # logging.info(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    # print(json.dumps(result.to_dict()["energy_summary"], indent=2, ensure_ascii=False))

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = f"out/simulation_visualization_{timestamp}"
    tracks = result.tracks

    if cfg_multi.visualize:
        if result.particle_results:
            logging.info(f'visualize_multi_particle_results')
            # Визуализация для мульти-частичного режима
            visualize_multi_particle_results(cfg_multi, result, data, export_dir)
        elif result.tracks:
            logging.info(f'visualize_single_particle_results')
            # Визуализация для одиночной частицы
            visualize_single_particle_results(cfg_multi, result, data, export_dir)
        else:
            print("Нет данных для визуализации (треки не собраны)")

        # Новый анализ энергии
        plot_energy_analysis(result, cfg_multi, data=data, dir=export_dir)
