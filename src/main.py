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
        self._exit_energies = []    # энергии на выходе
        self._electronics_hits: Dict[Tuple[int, int], Dict[str, Any]] = {}
    
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
    
    def add_exit_energy(self, energy):
        self._exit_energies.append(energy)

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
    def data(self):
        return self._data

    @property
    def particle_types(self):
        return self._particle_types

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
        electronics_thickness_mm_parameter = 0.05
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
            
            elems = mat.get("Elements", [])
            el_defs = []
            for elem in elems:
                symbol = str(elem.get("Symbol"))
                fraction = float(elem.get("Percentage"))
                density = float(elem.get("Density"))
                el_defs.append((nist.FindOrBuildElement(symbol), fraction, density))
            total_fraction = sum(fr for _, fr, _ in el_defs) or 1.0
            avg_density = sum((fr / total_fraction) * rho for _, fr, rho in el_defs)
            g4mat = g4.G4Material(mat_name, avg_density * g4.g / g4.cm3, len(el_defs))
            for element, fr, _ in el_defs:
                g4mat.AddElement(element, frac=(fr / total_fraction))
            material_defs.append((g4mat, thickness_mm * g4.mm, mat_name))
        logging.info(f"Screen info: {self.screen_info}")

        world_xy_mm_local = max(self.cfg.world_xy_mm, self.cfg.screen_xy_mm + 50.0)
        solid_world = g4.G4Box("World", 0.5 * world_xy_mm_local * g4.mm, 0.5 * world_xy_mm_local * g4.mm, 0.5 * world_z_mm_local * g4.mm)
        
        self.logic_world = g4.G4LogicalVolume(solid_world, nist.FindOrBuildMaterial("G4_AIR"), "World")
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
            electronics_material = nist.FindOrBuildMaterial(electronics_material_name)
            electronics_center_z = (
                self.screens_end_z_mm + electronics_gap_mm + 0.5 * electronics_thickness_mm
            ) * g4.mm
            electronics_solid = g4.G4Box(
                "Electronics",
                0.5 * screen_xy,
                0.5 * screen_xy,
                0.5 * electronics_thickness_mm * g4.mm
            )
            self.electronics_logical = g4.G4LogicalVolume(
                electronics_solid,
                electronics_material,
                "Electronics"
            )
            g4.G4PVPlacement(
                None,
                g4.G4ThreeVector(0, 0, electronics_center_z),
                self.electronics_logical,
                "Electronics",
                self.logic_world,
                False,
                0,
                check_overlaps
            )
            self.screen_info["Electronics"] = {
                "material": electronics_material_name,
                "density_g_cm3": electronics_material.GetDensity() / (g4.g / g4.cm3),
                "gap_mm": electronics_gap_mm,
                "thickness_mm": electronics_thickness_mm,
                "xy_size_mm": self.cfg.screen_xy_mm,
                "volume_mm3": self.cfg.screen_xy_mm * self.cfg.screen_xy_mm * electronics_thickness_mm,
                "mass_mg": (
                    (self.cfg.screen_xy_mm / 10.0)
                    * (self.cfg.screen_xy_mm / 10.0)
                    * (electronics_thickness_mm / 10.0)
                    * (electronics_material.GetDensity() / (g4.g / g4.cm3))
                    * 1000.0
                ),
                "z_start_mm": self.screens_end_z_mm + electronics_gap_mm,
                "z_end_mm": self.screens_end_z_mm + electronics_gap_mm + electronics_thickness_mm
            }
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
            sdm.AddNewDetector(electronics_sd)
            self.electronics_logical.SetSensitiveDetector(electronics_sd)

# -----------------------------
# Сенсоры
# -----------------------------
class ScreenSensitiveDetector(g4.G4VSensitiveDetector):

    def __init__(self, name, screen_info):
        super().__init__(name)
        self.screen_info = screen_info
        self.stopped_tracks = set()  # Хранит (event_id, track_id)

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
                        logging.info(f"Primary track {particle} {track_id} (event {event_id}) stopped in {vol_name} at Z={z_mm:.2f} mm")
                    else:
                        self.screen_info["Materials"][idx]["Secondary_stuck_count"] += 1
                        logging.info(f"Secondary track {track_id} (event {event_id}) stopped in {vol_name} at Z={z_mm:.2f} mm")

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

        if (
            particle_def is not None
            and abs(particle_def.GetPDGCharge()) > 0
            and material is not None
            and pre_energy_mev > 0
            and density_g_cm3 > 0
        ):
            try:
                dedx_internal = self.em_calculator.ComputeElectronicDEDX(
                    pre_energy_mev * g4.MeV,
                    particle_def,
                    material
                )
                dedx_mev_per_mm = dedx_internal / (g4.MeV / g4.mm)
                logging.info(f"dedx_mev_per_mm: {dedx_mev_per_mm}")
                let_step_mev_cm2_mg = dedx_mev_per_mm / (density_g_cm3 * 100.0)
                logging.info(f"let_step_mev_cm2_mg: {let_step_mev_cm2_mg}")
            except Exception as exc:
                logging.warning(
                    "G4EmCalculator LET failed for particle=%s, energy=%.6f MeV: %s",
                    particle_name,
                    pre_energy_mev,
                    exc
                )
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
    def __init__(self):
        super().__init__()
        self.event_id = None
    def BeginOfEventAction(self, anEvent):
        self.event_id = anEvent.GetEventID()
    def EndOfEventAction(self, anEvent):
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

            if track.GetTrackID() == 1:
                if key not in self.primary_out:
                    self.tracks.add_exit_energy(track.GetKineticEnergy() / g4.MeV)
                    self.primary_out.append(key)
            else:
                if key not in self.secondary_out:
                    self.tracks.add_exit_energy(track.GetKineticEnergy() / g4.MeV)
                    self.secondary_out.append(key)

class ActionInitialization(g4.G4VUserActionInitialization):
    def __init__(self, data, primary_out, secondary_out, particle_config, layout, tracks, current_particle, generator):
        super().__init__()
        self.data = data
        self.primary_out = primary_out
        self.secondary_out = secondary_out
        self.particle_config = particle_config
        self.layout = layout
        self.tracks = tracks
        self.current_particle = current_particle
        self.generator = generator
    
    def Build(self):
        self.SetUserAction(self.generator)
        
        event_action = ScreenEventAction()
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
        self.fParticleGun = g4.G4ParticleGun(100)
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

def run_simulation_with_giga(cfg: SimulationGigaConfig):
    logging.info('run simulation with giga main')
    load_dotenv()
    GIGACHAT_CREDENTIALS = os.getenv('GIGACHAT_CREDENTIALS')

    llm = GigaChat(
        model="GigaChat-2-Max",
        credentials=GIGACHAT_CREDENTIALS,
        scope="GIGACHAT_API_PERS",
        top_p=0, 
        timeout=120, 
        ca_bundle_file='russian_trusted_root_ca_pem.crt'
    )

    structed_llm = llm.with_structured_output(ScreenInfo)
    logging.info('request for llm')
    result = structed_llm.invoke(cfg.prompt)
    logging.info(result)

    data = convert_screen_info_to_data(result)
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
        run_manager.SetUserInitialization(g4.FTFP_BERT())
        
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
            generator=generator
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
            electronics_hits=tracks.electronics_hits,
            electronics_info=screen_info.get("Electronics"),
            let_threshold_mev_cm2_mg=cfg.electronics_let_threshold_mev_cm2_mg,
            dose_threshold_gy=cfg.electronics_dose_threshold_gy
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
    dose_threshold_gy: float
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
            if mean_let_mev_cm2_mg is None and max_let_mev_cm2_mg is None:
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
    mass_mg = float(electronics_info.get("mass_mg", 0.0) or 0.0)
    mass_kg = mass_mg * 1e-6 if mass_mg > 0 else 0.0
    absorbed_dose_gy = (
        deposited_energy_mev * 1.602176634e-13 / mass_kg
        if mass_kg > 0 else None
    )
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
    electronics_hits: Optional[list] = None,
    electronics_info: Optional[dict] = None,
    let_threshold_mev_cm2_mg: float = 1.0,
    dose_threshold_gy: float = 5.0
) -> dict:
        """Вычисляет сводную статистику по энергии.
        
        Ключевые определения:
        - exited_screen: частица вышла ЗА ЗАДНЮЮ границу экрана (last_z > screens_end_z_mm)
        - stopped_in_screen: остановилась ВНУТРИ материала экрана
        - backscattered: улетела НАЗАД (last_z < first_screen_front_z_mm)
        - absorbed_before_screen: вторичная частица, остановившаяся ДО экрана (в вакууме)
        """
        if not energy_profiles:
            energy_profiles = {}

        first_z = layout.get("first_screen_front_z_mm", 0)
        end_z = layout.get("screens_end_z_mm", 0)
        screen_thickness = end_z - first_z
        thicknesses = layout.get("thicknesses_mm", [])

        exit_energies_float = []
        if exit_energies:
            for e in exit_energies:
                try:
                    exit_energies_float.append(float(e))
                except (ValueError, TypeError):
                    logging.warning(f"Invalid energy value: {e}, skipping")

        assessment_energy_threshold_mev = 10.0
        assessment_max_allowed_fraction = 0.1
        exit_above_threshold_count = sum(
            1 for energy in exit_energies_float
            if energy >= assessment_energy_threshold_mev
        )
        exit_total_count = len(exit_energies_float)
        exit_above_threshold_fraction = (
            exit_above_threshold_count / exit_total_count
            if exit_total_count > 0 else 0.0
        )
        screen_ineffective = exit_above_threshold_fraction > assessment_max_allowed_fraction

        if exit_total_count == 0:
            assessment_verdict = "effective"
            assessment_reason = (
                "Выходное излучение за экраном не зарегистрировано, "
                "по текущему критерию экран считается эффективным."
            )
        elif screen_ineffective:
            assessment_verdict = "ineffective"
            assessment_reason = (
                f"Доля выходных частиц с энергией не ниже {assessment_energy_threshold_mev:.1f} МэВ "
                f"составляет {exit_above_threshold_fraction * 100:.1f}%, что превышает порог "
                f"{assessment_max_allowed_fraction * 100:.1f}%."
            )
        else:
            assessment_verdict = "effective"
            assessment_reason = (
                f"Доля выходных частиц с энергией не ниже {assessment_energy_threshold_mev:.1f} МэВ "
                f"составляет {exit_above_threshold_fraction * 100:.1f}%, что не превышает порог "
                f"{assessment_max_allowed_fraction * 100:.1f}%."
            )

        electronics_let = _compute_electronics_let_summary(
            electronics_hits=electronics_hits or [],
            electronics_info=electronics_info,
            threshold_mev_cm2_mg=let_threshold_mev_cm2_mg,
            dose_threshold_gy=dose_threshold_gy
        )

        # Статистика по первичным частицам
        primary_profiles = [
            p for p in energy_profiles.values()
            if p.get("parent_id", 0) == 0
        ]

        # Статистика по вторичным частицам
        secondary_profiles = [
            p for p in energy_profiles.values()
            if p.get("parent_id", 0) != 0
        ]

        # Первичные
        stopped_primary = 0
        exited_primary = 0
        backscattered_primary = 0
        energy_loss_primary = []
        stopped_primary_by_material = {}
        exited_primary_initial_energies = []
        exited_primary_final_energies = []

        # Вторичные
        stopped_secondary = 0
        exited_secondary = 0
        backscattered_secondary = 0
        absorbed_before_secondary = 0
        energy_loss_secondary = []
        stopped_secondary_by_material = {}

        for profile in primary_profiles:
            points = profile.get("points", [])
            if len(points) >= 2:
                try:
                    e_start = float(points[0][1])
                    e_end = float(points[-1][1])
                    z_last = float(points[-1][0])
                    energy_loss_primary.append(e_start - e_end)

                    # 1. Вышла за экран (Z > end_z)
                    if z_last > end_z:
                        exited_primary += 1
                        exited_primary_initial_energies.append(e_start)
                        exited_primary_final_energies.append(e_end)
                    # 2. Улетела назад (Z < first_z)
                    elif z_last < first_z:
                        backscattered_primary += 1
                    # 3. Остановилась ВНУТРИ экрана
                    elif e_end < 0.001:
                        stopped_primary += 1
                        mat_idx = _find_stopped_particle_material(points, first_z, thicknesses)
                        if mat_idx >= 0:
                            stopped_primary_by_material[mat_idx] = stopped_primary_by_material.get(mat_idx, 0) + 1
                except (ValueError, TypeError, IndexError):
                    continue

        for profile in secondary_profiles:
            points = profile.get("points", [])
            if len(points) >= 2:
                try:
                    e_start = float(points[0][1])
                    e_end = float(points[-1][1])
                    z_last = float(points[-1][0])
                    energy_loss_secondary.append(e_start - e_end)

                    # 1. Вышла за экран (Z > end_z)
                    if z_last > end_z:
                        exited_secondary += 1
                    # 2. Улетела назад (Z < first_z)
                    elif z_last < first_z:
                        backscattered_secondary += 1
                        # Если остановилась — считаем как absorbed_before
                        if e_end < 0.001:
                            absorbed_before_secondary += 1
                    # 3. Остановилась ВНУТРИ экрана
                    elif e_end < 0.001:
                        stopped_secondary += 1
                        mat_idx = _find_stopped_particle_material(points, first_z, thicknesses)
                        if mat_idx >= 0:
                            stopped_secondary_by_material[mat_idx] = stopped_secondary_by_material.get(mat_idx, 0) + 1
                except (ValueError, TypeError, IndexError):
                    continue

        mean_initial_exit_primary_energy = (
            sum(exited_primary_initial_energies) / len(exited_primary_initial_energies)
            if exited_primary_initial_energies else None
        )
        mean_final_exit_primary_energy = (
            sum(exited_primary_final_energies) / len(exited_primary_final_energies)
            if exited_primary_final_energies else None
        )
        transmission_energy_attenuation = (
            1.0 - (mean_final_exit_primary_energy / mean_initial_exit_primary_energy)
            if mean_initial_exit_primary_energy and mean_initial_exit_primary_energy > 0
            and mean_final_exit_primary_energy is not None
            else None
        )
        dose_assessment = electronics_let.get("dose_assessment", {})
        event_upset_risk = electronics_let.get("event_upset_risk", {})
        screen_protected = (
            assessment_verdict == "effective"
            and dose_assessment.get("is_protected", False)
            and not electronics_let.get("is_dangerous", False)
            and event_upset_risk.get("upset_events_count", 0) == 0
        )
        if screen_protected:
            protection_reason = (
                "Экран признан защитным: доля опасного выходного излучения ниже порога, "
                "поглощенная доза в электронике ниже порога, опасный LET не зарегистрирован, "
                "event upset risk не выявлен."
            )
        else:
            failed_criteria = []
            if assessment_verdict != "effective":
                failed_criteria.append("по доле выходного излучения")
            if not dose_assessment.get("is_protected", False):
                failed_criteria.append("по поглощенной дозе")
            if electronics_let.get("is_dangerous", False):
                failed_criteria.append("по LET")
            if event_upset_risk.get("upset_events_count", 0) > 0:
                failed_criteria.append("по event upset risk")
            protection_reason = (
                "Экран не признан защитным " + ", ".join(failed_criteria) + "."
                if failed_criteria else
                "Экран не признан защитным."
            )

        return {
            "primary_particles": {
                "total": len(primary_profiles),
                "stopped_in_screen": stopped_primary,
                "stopped_by_material": stopped_primary_by_material,
                "exited_screen": exited_primary,
                "backscattered": backscattered_primary,
                "stopping_fraction": stopped_primary / len(primary_profiles) if primary_profiles else 0,
                "mean_initial_energy_exited_mev": mean_initial_exit_primary_energy,
                "mean_exit_energy_mev": mean_final_exit_primary_energy,
                "energy_attenuation_fraction": transmission_energy_attenuation,
                "energy_attenuation_percent": (
                    transmission_energy_attenuation * 100.0
                    if transmission_energy_attenuation is not None else None
                ),
                "avg_energy_loss": sum(energy_loss_primary) / len(energy_loss_primary) if energy_loss_primary else 0,
                "max_energy_loss": max(energy_loss_primary) if energy_loss_primary else 0,
                "min_energy_loss": min(energy_loss_primary) if energy_loss_primary else 0
            },
            "secondary_particles": {
                "total": len(secondary_profiles),
                "stopped_in_screen": stopped_secondary,
                "stopped_by_material": stopped_secondary_by_material,
                "exited_screen": exited_secondary,
                "backscattered": backscattered_secondary,
                "absorbed_before_screen": absorbed_before_secondary,
                "stopping_fraction": stopped_secondary / len(secondary_profiles) if secondary_profiles else 0,
                "avg_energy_loss": sum(energy_loss_secondary) / len(energy_loss_secondary) if energy_loss_secondary else 0
            },
            "screen": {
                "thickness_mm": screen_thickness,
                "first_z_mm": first_z,
                "end_z_mm": end_z
            },
            "exit_energies": {
                "count": len(exit_energies_float),
                "min": min(exit_energies_float) if exit_energies_float else None,
                "max": max(exit_energies_float) if exit_energies_float else None,
                "mean": sum(exit_energies_float) / len(exit_energies_float) if exit_energies_float else None
            },
            "shield_assessment": {
                "criterion": "exit_radiation_fraction_above_energy_threshold",
                "threshold_energy_mev": assessment_energy_threshold_mev,
                "max_allowed_fraction": assessment_max_allowed_fraction,
                "max_allowed_percent": assessment_max_allowed_fraction * 100,
                "exit_particles_total": exit_total_count,
                "exit_particles_above_threshold": exit_above_threshold_count,
                "fraction_above_threshold": exit_above_threshold_fraction,
                "percent_above_threshold": exit_above_threshold_fraction * 100,
                "is_effective": not screen_ineffective,
                "is_ineffective": screen_ineffective,
                "verdict": assessment_verdict,
                "reason": assessment_reason
            },
            "electronics_let": electronics_let,
            "screen_protection_report": {
                "is_protected": screen_protected,
                "verdict": "protected" if screen_protected else "not_protected",
                "reason": protection_reason,
                "criteria": {
                    "exit_radiation": assessment_verdict,
                    "dose": dose_assessment.get("verdict", "unknown"),
                    "let": "safe" if not electronics_let.get("is_dangerous", False) else "dangerous",
                    "event_upset_risk": (
                        "safe" if event_upset_risk.get("upset_events_count", 0) == 0 else "dangerous"
                    )
                }
            }
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

        all_energy_profiles = {}
        all_exit_energies = []
        all_electronics_hits = []
        all_screen_info = None
        all_energy_summary = {}

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
                electronics_thickness_mm=cfg.electronics_thickness_mm,
                electronics_material=cfg.electronics_material,
                electronics_let_threshold_mev_cm2_mg=cfg.electronics_let_threshold_mev_cm2_mg,
                electronics_dose_threshold_gy=cfg.electronics_dose_threshold_gy,
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
            dose_threshold_gy=cfg.electronics_dose_threshold_gy
        )
        logging.info(f'res emergy summary: {energy_summary}')

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
                    "Name": "Al",
                    "Description": "Алюминий (Al) толщиной 1000 мкм",
                    "Width": 3000.0,
                    "Elements": [
                        {
                            "Name": "Титан",
                            "Symbol": "Ti",
                            "Atomic_number": 22,
                            "Standard_atomic_weight": 47.87,
                            "Density": 4.5,
                            "Percentage": 100.0
                        }
                    ]
                },
                {
                    "Name": "W",
                    "Description": "Вольфрам (W) толщиной 2000 мкм",
                    "Width": 2000.0,
                    "Elements": [
                        {
                            "Name": "Вольфрам",
                            "Symbol": "W",
                            "Atomic_number": 74,
                            "Standard_atomic_weight": 183.84,
                            "Density": 19.25,
                            "Percentage": 100.0
                        }
                    ]
                }
            ]
        }
    }
    '''
    Использовать для получения данных по task_id с сайта (раскоментировать строку)
    '''
    # data = ds.get_current_task_to_json(task_id)
    events = 100
    # Пример: Мульти-частичный последовательный режим
    cfg_multi = SimulationConfig(
        task_id=task_id,
        input_data=data,
        particles=[
            ParticleConfig(name="He3", energy_mev=70.0),
            ParticleConfig(name="alpha", energy_mev=70.0),
            ParticleConfig(name="proton", energy_mev=70.0)
            # ParticleConfig(name="neutron", energy_mev=50.0)
            # ParticleConfig(name="e-", energy_mev=60.0)
        ],
        events=events,
        collect_tracks=True,
        visualize=True,
        use_mixed_beam=False  # Последовательные запуски в отдельных процессах
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
