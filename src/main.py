from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple, Optional, Any
import json
import geant4_pybind as g4
from DataServer import DataServer
from TrimParser import TrimParser
import atexit
import gc
import os
import datetime
import random
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
from utils import compute_layout

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

        self._energy_profiles = {}   # (event_id, track_id) -> [(z, E)]
        self._exit_energies = []    # энергии на выходе
    
    def add(self, event_id: int, track_id: int, pos: g4.G4ThreeVector, particle_type: str = None) -> None:
        if not self.enabled:
            return
        self._data.setdefault((event_id, track_id), []).append((pos.x, pos.y, pos.z))
        if particle_type and (event_id, track_id) not in self._particle_types:
            self._particle_types[(event_id, track_id)] = particle_type

    def add_energy(self, event_id, track_id, z, energy):
        if not self.enabled:
            return
        self._energy_profiles.setdefault((event_id, track_id), []).append((z, energy))
    
    def add_exit_energy(self, energy):
        if not self.enabled:
            return
        self._exit_energies.append(energy)

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
    def data(self):
        return self._data

    @property
    def particle_types(self):
        return self._particle_types

# -----------------------------
# Геометрия
# -----------------------------
class ScreenGeometry(g4.G4VUserDetectorConstruction):
    def __init__(self, data: dict, screen_info: Dict[str, Any], cfg: SimulationConfig) -> None:
        super().__init__()
        self.tp = TrimParser(data)
        self.screen_info = screen_info
        self.cfg = cfg
        self.logic_world = None
        self.screen_logicals = []
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
        return phys_world

    def ConstructSDandField(self):
        sdm = g4.G4SDManager.GetSDMpointer()
        for idx, sc_log in enumerate(self.screen_logicals):
            sd = ScreenSensitiveDetector(f"ScreenDetector_{idx}", self.screen_info)
            sdm.AddNewDetector(sd)
            sc_log.SetSensitiveDetector(sd)

# -----------------------------
# Сенсоры
# -----------------------------
class ScreenSensitiveDetector(g4.G4VSensitiveDetector):

    def __init__(self, name, screen_info):
        super().__init__(name)
        self.screen_info = screen_info

    def ProcessHits(self, aStep: g4.G4Step, _hist):
        track = aStep.GetTrack()

        # энергия, оставленная в шаге
        edep = aStep.GetTotalEnergyDeposit() / g4.MeV

        vol_name = track.GetVolume().GetName()
        try:
            idx = int(str(vol_name).split("_")[-1])
        except Exception:
            return True

        # добавляем энергию в слой
        if "Edep" not in self.screen_info["Materials"][idx]:
            self.screen_info["Materials"][idx]["Edep"] = 0.0

        self.screen_info["Materials"][idx]["Edep"] += edep

        # старый функционал
        if track.GetKineticEnergy() == 0:
            if track.GetTrackID() == 1:
                self.screen_info["Materials"][idx]["Primary_stuck_count"] += 1
            else:
                self.screen_info["Materials"][idx]["Secondary_stuck_count"] += 1

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
    
    def UserSteppingAction(self, step):
        post = step.GetPostStepPoint()
        pos = post.GetPosition()
        track = step.GetTrack()

        kin_energy = track.GetKineticEnergy() / g4.MeV
        z_pos = pos.z / g4.mm
        # print("EVENT ID:", self.event_action.event_id)
        # print(f'EVENT ID: {self.event_action.event_id}\tTRACK ID: {track.GetTrackID()}\tkinenergy: {kin_energy}')
        if self.event_action.event_id is not None:
            self.tracks.add_energy(
                self.event_action.event_id,
                track.GetTrackID(),
                z_pos,
                kin_energy
            )
        
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
            self.tracks.add(self.event_action.event_id, track.GetTrackID(), pos, particle_name)
        
        if (pos.z / g4.mm) > self.screens_end_z_mm:
            key = (self.event_action.event_id or -1, track.GetTrackID())

            # сохраняем энергию на выходе
            self.tracks.add_exit_energy(track.GetKineticEnergy() / g4.MeV)

            if track.GetTrackID() == 1:
                if key not in self.primary_out:
                    self.primary_out.append(key)
            else:
                if key not in self.secondary_out:
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
        
        geom = ScreenGeometry(data=data, screen_info=screen_info, cfg=cfg)
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
        result = SimulationResult(
            screen_info=screen_info,
            total_particles=cfg.events,
            total_out_primary_particles=len(primary_out),
            total_out_secondary_particles=len(secondary_out),
            tracks=tracks.data if cfg.collect_tracks else None,
            mixed_beam_result=mixed_result,
            energy_profiles=tracks.energy_profiles,
            exit_energies=tracks.exit_energies
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

# -----------------------------
# Запуск симуляции
# -----------------------------
class SimulationRunner:
    def __init__(self):
        self._last_tracks = None
    
    def run_sequential_multiprocess(self, cfg: SimulationConfig) -> SimulationResult:
        """Запускает последовательные симуляции в отдельных процессах"""
        logging.info("Запускает последовательные симуляции в отдельных процессах")
        particle_results = {}

        all_energy_profiles = {}
        all_exit_energies = []
        all_screen_info = None

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
                    logging.info(f"cfg result: {particle_results[key]}")
                    print(f"Completed: {key}")
                    logging.info(f"Completed: {key}")                  

                    if "energy_profiles" in result_dict and result_dict["energy_profiles"]:
                        for track_key, profiles in result_dict["energy_profiles"].items():
                            # Добавляем префикс с типом частицы для уникальности
                            new_key = f"{p_config.name}_{track_key}"
                            all_energy_profiles[new_key] = profiles
                    else :
                        logging.info("no energy profiles")

                    logging.info(f"all energy profiles: {all_energy_profiles}")

                    if "exit_energies" in result_dict and result_dict["exit_energies"]:
                        all_exit_energies.extend(result_dict["exit_energies"])

                    if all_screen_info is None and result_dict.get("screen_info"):
                        all_screen_info = result_dict["screen_info"]

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
        comparison = self._create_comparison(particle_results)
        
        sim_res = SimulationResult(
            particle_results=particle_results,
            comparison=comparison,
            screen_info=all_screen_info,
            total_out_primary_particles=result_dict["total_out_primary_particles"],
            total_out_secondary_particles=result_dict["total_out_secondary_particles"],
            energy_profiles=all_energy_profiles,
            exit_energies=all_exit_energies
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

def get_particle_color(particle_name):
    """Возвращает цвет для конкретного типа частицы"""
    color_map = {
        # Первичные частицы
        "He3": "blue",
        "alpha": "darkblue",
        "proton": "red",
        "neutron": "gray",
        "e-": "green",
        "e+": "lightgreen",
        "gamma": "yellow",
        "mu-": "purple",
        "mu+": "violet",
        "pi+": "orange",
        "pi-": "darkorange",
        "kaon+": "brown",
        "kaon-": "sandybrown",
        "deuteron": "cyan",
        "triton": "darkcyan",
        
        # По умолчанию
        "primary": "blue",
        "unknown": "black"
    }
    
    # Нормализуем имя частицы
    particle_name = str(particle_name).lower()
    
    # Ищем точное совпадение
    for key, color in color_map.items():
        if key.lower() == particle_name:
            return color
    
    # Ищем частичное совпадение
    for key, color in color_map.items():
        if key.lower() in particle_name or particle_name in key.lower():
            return color
    
    return color_map["unknown"]

def export_to_html(plotter, filename="visualization.html"):
    """Экспортирует сцену PyVista в HTML файл"""
    try:
        # Используем экспорт в HTML
        plotter.export_html(filename)
        print(f"Визуализация экспортирована в {filename}")
    except Exception as e:
        print(f"Ошибка при экспорте в HTML: {e}")
        # Альтернативный способ через сохранение и встраивание
        plotter.show(screenshot=filename.replace('.html', '.png'))

def run_simulation(cfg: SimulationConfig) -> SimulationResult:
    runner = SimulationRunner()
    return runner.run(cfg)

def plot_energy_analysis(result: SimulationResult, cfg: SimulationConfig, data: dict, dir: str):
    import matplotlib.pyplot as plt
    import os
    import datetime

    # Создаем папку
    out_dir = f"{dir}/energy_analysis"
    os.makedirs(out_dir, exist_ok=True)
    layout = compute_layout(cfg=cfg, data=data)
    # =============================
    # Energy vs Depth
    # =============================
    if hasattr(result, "energy_profiles") and result.energy_profiles:
        plt.figure()
        first_z = None
        end_z = None

        logging.info(f'plot result screen info: {result}')

        if result.screen_info:
            first_z = layout["first_screen_front_z_mm"]
            end_z = layout["screens_end_z_mm"]

            logging.info(f'first_z :{first_z}\tend_z: {end_z}')

            logging.info(f'first screen coord: {first_z}')

        if first_z is None:
            first_z = 0

        for track_key, data in result.energy_profiles.items():
            if len(data) < 2:
                continue
            z = [p[0] for p in data]
            E = [p[1] for p in data]

            z_rel = [zi - first_z for zi in z]

            # фильтр: внутри экрана + немного после
            filtered = [
                (zr, e) for zr, e in zip(z_rel, E)
                if -5 <= zr <= (end_z - first_z + 10 if end_z else 100)
            ]

            if len(filtered) < 2:
                continue

            zf, Ef = zip(*filtered)

            plt.plot(zf, Ef, alpha=0.3)

            if end_z is not None:
                screen_thickness = end_z - first_z

        plt.xlabel("Z relative to screen (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth (relative to screen)")
        plt.grid()
        plt.legend()

        plt.axvline(0, linestyle="--", label="Screen start")
        plt.axvline(screen_thickness, linestyle="--", label="Screen end")

        plt.xlabel("Depth (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth")
        plt.grid()

        filename = os.path.join(out_dir, "energy_vs_depth.png")
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # Energy spectrum (exit)
    # =============================
    if hasattr(result, "exit_energies") and result.exit_energies:
        plt.figure()

        plt.hist(result.exit_energies, bins=30)

        plt.xlabel("Energy (MeV)")
        plt.ylabel("Counts")
        plt.title("Exit Energy Spectrum")
        plt.grid()

        filename = os.path.join(out_dir, "exit_energy_spectrum.png")
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # Energy deposition per layer
    # =============================
    if result.screen_info and "Materials" in result.screen_info:
        materials = result.screen_info["Materials"]

        names = []
        edep = []

        for mat in materials:
            names.append(mat["Name"])
            edep.append(mat.get("Edep", 0))

        plt.figure()

        plt.bar(names, edep)

        plt.xlabel("Material")
        plt.ylabel("Deposited Energy (MeV)")
        plt.title("Energy Deposition per Layer")
        plt.xticks(rotation=30)
        plt.grid(axis='y')

        filename = os.path.join(out_dir, "energy_deposition.png")
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    print(f"\nAll plots saved in: {out_dir}")

def visualize_multi_particle_results(cfg: SimulationConfig, result: SimulationResult, input_data: dict, dir: str):
    """Визуализация результатов для мульти-частичного режима"""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista не установлен. Визуализация невозможна.")
        return
    
    try:
        pv.start_xvfb()
    except:
        print("Предупреждение: Xvfb не запущен, используем offscreen режим")
    
    # Загружаем данные для вычисления layout
    # ds = DataServer()
    data = input_data
    layout = compute_layout(cfg, data)
    
    plotter = pv.Plotter()
    
    # Рисуем экраны
    z_cursor = float(layout["first_screen_front_z_mm"])
    for i, mat in enumerate(data.get('Screen', {}).get('Materials', [])):
        th = float(mat.get("Width", 0)) / 1000.0  # мкм → мм
        center_z = z_cursor + 0.5 * th
        cube = pv.Cube(
            center=(0, 0, center_z),
            x_length=cfg.screen_xy_mm * 0.6,
            y_length=cfg.screen_xy_mm * 0.6,
            z_length=th
        )
        plotter.add_mesh(cube, opacity=0.3, color='lightblue', name=f"Screen_{i}")
        z_cursor += th

    # Рисуем источник
    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color='red', name="Source")

    # Собираем статистику по типам частиц
    particle_counts = {}
    
    # Визуализируем треки из всех particle_results
    if result.particle_results:
        for key, particle_result in result.particle_results.items():
            if particle_result.tracks:
                for track_key, pts in particle_result.tracks.items():
                    if len(pts) >= 2:
                        event_id, track_id = track_key
                        particle_name = particle_result.particle
                        
                        # Считаем статистику
                        particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1
                        
                        # Получаем цвет
                        color = get_particle_color(particle_name)
                        
                        # Толщина линии: первичные частицы толще
                        line_width = 2 if track_id == 1 else 1
                        
                        # Создаем линию
                        line = pv.lines_from_points(pts)
                        plotter.add_mesh(
                            line, 
                            color=color, 
                            line_width=line_width,
                            name=f"{particle_name}_{event_id}_{track_id}"
                        )

    # Добавляем информационную панель
    legend_text = "Particle Types:\n"
    for particle_name, count in particle_counts.items():
        color = get_particle_color(particle_name)
        legend_text += f"{particle_name}: {count}\n"
    
    plotter.add_text(legend_text, position='upper_right', font_size=8)
    plotter.add_text(f"Geant4 Multi-Particle Simulation", 
                    position='upper_edge', font_size=10)
    plotter.add_axes()
    
    # Создаем директорию для результатов    
    os.makedirs(dir, exist_ok=True)
    
    # Сохраняем HTML
    html_filename = os.path.join(export_dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Визуализация экспортирована в {html_filename}")
    
    # Выводим статистику
    print("\nParticle type statistics:")
    for particle_name, count in sorted(particle_counts.items()):
        print(f"  {particle_name}: {count} tracks")

def visualize_single_particle_results(cfg: SimulationConfig, result: SimulationResult):
    """Визуализация для одиночной частицы"""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista не установлен. Визуализация невозможна.")
        return
    
    try:
        pv.start_xvfb()
    except:
        print("Предупреждение: Xvfb не запущен, используем offscreen режим")
    
    # Загружаем данные для вычисления layout
    ds = DataServer()
    data = ds.get_current_task_to_json(cfg.task_id) if cfg.task_id else cfg.input_data
    layout = compute_layout(cfg, data)
    
    plotter = pv.Plotter()
    
    # Рисуем экраны
    z_cursor = float(layout["first_screen_front_z_mm"])
    for i, mat in enumerate(data.get('Screen', {}).get('Materials', [])):
        th = float(mat.get("Width", 0)) / 1000.0  # мкм → мм
        center_z = z_cursor + 0.5 * th
        cube = pv.Cube(
            center=(0, 0, center_z),
            x_length=cfg.screen_xy_mm * 0.6,
            y_length=cfg.screen_xy_mm * 0.6,
            z_length=th
        )
        plotter.add_mesh(cube, opacity=0.3, color='lightblue', name=f"Screen_{i}")
        z_cursor += th

    # Рисуем источник
    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color='red', name="Source")

    # Визуализируем треки
    particle_counts = {}
    if result.tracks:
        for track_key, pts in result.tracks.items():
            if len(pts) >= 2:
                event_id, track_id = track_key
                particle_name = cfg.particles[0].name if cfg.particles else cfg.particle
                
                # Считаем статистику
                particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1
                
                # Получаем цвет
                color = get_particle_color(particle_name)
                
                # Толщина линии
                line_width = 2 if track_id == 1 else 1
                
                # Создаем линию
                line = pv.lines_from_points(pts)
                plotter.add_mesh(
                    line, 
                    color=color, 
                    line_width=line_width,
                    name=f"{particle_name}_{event_id}_{track_id}"
                )

    # Добавляем информационную панель
    legend_text = f"Particle: {cfg.particles[0].name if cfg.particles else cfg.particle}\n"
    legend_text += f"Energy: {cfg.particles[0].energy_mev if cfg.particles else cfg.energy_mev} MeV\n"
    legend_text += f"Total tracks: {len(result.tracks) if result.tracks else 0}"
    
    plotter.add_text(legend_text, position='upper_right', font_size=8)
    plotter.add_text(f"Geant4 Simulation", 
                    position='upper_edge', font_size=10)
    plotter.add_axes()
    
    # Создаем директорию для результатов
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = f"out/simulation_visualization_{timestamp}"
    os.makedirs(export_dir, exist_ok=True)
    
    # Сохраняем HTML
    html_filename = os.path.join(export_dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Визуализация экспортирована в {html_filename}")

# Обновите основную часть кода в конце файла:

if __name__ == "__main__":
    ds = DataServer()
    task_id = 103
    
    # Использовать подготовленные данные, сгенерированные LLM
    data = {
        "Screen": {
            "Name": "Экран из Al и ВТ5Л",
            "Description": "Экран состоит из двух слоев: Al и ВТ5Л. Первый слой толщиной 1000 мкм, второй слой толщиной 2000 мкм. В первом слое материал Be (Бериллий), во втором слое материал ВТ5Л (Титановый сплав ВТ5Л). В слое ВТ5Л содержится 90% Ti (Титан) и 10% Al (Алюминий).",
            "Materials": [
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
                },
                {
                    "Name": "ВТ5Л",
                    "Description": "Титановый сплав ВТ5Л толщиной 2000 мкм. Состоит из 90% Ti (Титан) и 10% Al (Алюминий).",
                    "Width": 2000.0,
                    "Elements": [
                        {
                            "Name": "Титан",
                            "Symbol": "Ti",
                            "Atomic_number": 22,
                            "Standard_atomic_weight": 47.867,
                            "Density": 4.51,
                            "Percentage": 90.0
                        },
                        {
                            "Name": "Алюминий",
                            "Symbol": "Al",
                            "Atomic_number": 13,
                            "Standard_atomic_weight": 26.982,
                            "Density": 2.7,
                            "Percentage": 10.0
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
    
    # Пример: Мульти-частичный последовательный режим
    cfg_multi = SimulationConfig(
        task_id=task_id,
        input_data=data,
        particles=[
            ParticleConfig(name="He3", energy_mev=30.0),
            ParticleConfig(name="proton", energy_mev=25.0)
            # ParticleConfig(name="e-", energy_mev=20.0)
        ],
        events=30,
        collect_tracks=True,
        visualize=True,
        use_mixed_beam=False  # Последовательные запуски в отдельных процессах
    )
    
    runner = SimulationRunner()
    result = runner.run(cfg_multi)
    logging.info(f"print results")
    logging.info(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

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
            visualize_single_particle_results(cfg_multi, result)
        else:
            print("Нет данных для визуализации (треки не собраны)")

        # Новый анализ энергии
        plot_energy_analysis(result, cfg_multi, data=data, dir=export_dir)