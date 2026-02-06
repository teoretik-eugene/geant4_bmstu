"""
Geant4 (geant4_pybind) simulation module with optional PyVista visualization.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any
import json
import geant4_pybind as g4
from DataServer import DataServer
from TrimParser import TrimParser
import atexit
import gc
import os
import datetime
from dotenv import load_dotenv
from giga_tools import *
from langchain_gigachat.chat_models import GigaChat
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_geant4_initialized = False

# -----------------------------
# Конфиги и результаты
# -----------------------------
@dataclass
class SimulationConfig:
    task_id: Optional[int] = None
    input_data: Optional[dict] = None
    particle: str = "He3"
    energy_mev: float = 40.0
    events: int = 10
    world_xy_mm: float = 500.0
    world_z_mm: float = 500.0
    screen_xy_mm: float = 250.0
    first_screen_z_mm: float = 15.0
    collect_tracks: bool = False
    visualize: bool = False

@dataclass
class SimulationGigaConfig:
    prompt: str = "Помоги составить экран"
    input_data: Optional[dict] = None
    particle: str = "He3"
    energy_mev: float = 40.0
    events: int = 10
    world_xy_mm: float = 500.0
    world_z_mm: float = 500.0
    screen_xy_mm: float = 250.0
    first_screen_z_mm: float = 15.0
    collect_tracks: bool = False
    visualize: bool = False

@dataclass
class SimulationResult:
    screen_info: Dict[str, Any]
    total_particles: int
    total_out_primary_particles: int
    total_out_secondary_particles: int
    tracks: Optional[Dict[Tuple[int, int], List[Tuple[float, float, float]]]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.tracks is not None:
            d["tracks"] = {f"{k[0]}:{k[1]}": v for k, v in self.tracks.items()}
        return d

def compute_layout(cfg: SimulationConfig, data: dict) -> dict:
    tp = TrimParser(data)
    mats = tp.readMaterials()
    thicknesses = [float(m.get("Width")) / 1000.0 for m in mats]  # мкм → мм
    total_thickness_mm = sum(thicknesses)
    world_z_mm_needed = cfg.first_screen_z_mm + total_thickness_mm + 50.0
    world_z_mm_local = max(cfg.world_z_mm, world_z_mm_needed)
    half_world_z_mm = 0.5 * world_z_mm_local
    first_screen_front_z_mm = -half_world_z_mm + cfg.first_screen_z_mm
    centers = []
    z_cursor = first_screen_front_z_mm
    for th in thicknesses:
        centers.append(z_cursor + 0.5 * th)
        z_cursor += th
    screens_end_z_mm = first_screen_front_z_mm + total_thickness_mm

    return dict(
        thicknesses_mm=thicknesses,
        total_thickness_mm=total_thickness_mm,
        world_z_mm_local=world_z_mm_local,
        half_world_z_mm=half_world_z_mm,
        first_screen_front_z_mm=first_screen_front_z_mm,
        first_screen_centers_mm=centers,
        screens_end_z_mm=screens_end_z_mm,
    )

class TrackCollector:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._data: Dict[Tuple[int, int], List[Tuple[float, float, float]]] = {}
        self._particle_types: Dict[Tuple[int, int], str] = {}  # Новое: храним типы частиц
    
    def add(self, event_id: int, track_id: int, pos: g4.G4ThreeVector, particle_type: str = None) -> None:
        if not self.enabled:
            return
        self._data.setdefault((event_id, track_id), []).append((pos.x, pos.y, pos.z))
        if particle_type and (event_id, track_id) not in self._particle_types:
            self._particle_types[(event_id, track_id)] = particle_type
    
    def get_particle_type(self, event_id: int, track_id: int) -> str:
        return self._particle_types.get((event_id, track_id), "unknown")
    
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
    def ProcessHits(self, aStep, _hist):
        track = aStep.GetTrack()
        if track.GetKineticEnergy() == 0:
            vol_name = track.GetVolume().GetName()
            try:
                idx = int(str(vol_name).split("_")[-1])
            except Exception:
                return True
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
    def __init__(self, primary_out, secondary_out, screens_end_z_mm, event_action, tracks):
        super().__init__()
        self.primary_out = primary_out
        self.secondary_out = secondary_out
        self.screens_end_z_mm = screens_end_z_mm
        self.event_action = event_action
        self.tracks = tracks
    
    def UserSteppingAction(self, step):
        post = step.GetPostStepPoint()
        pos = post.GetPosition()
        track = step.GetTrack()
        
        # Получаем тип частицы из Geant4 - ИСПРАВЛЕННАЯ ВЕРСИЯ
        particle_name = "unknown"
        try:
            # Попробуем разные способы получения определения частицы
            particle_def = track.GetDefinition()  # Основной метод в pybind
            if particle_def:
                particle_name = particle_def.GetParticleName()
        except AttributeError:
            try:
                # Альтернативный способ
                particle_def = track.GetDynamicParticle().GetDefinition()
                if particle_def:
                    particle_name = particle_def.GetParticleName()
            except:
                # Если не получается, используем track_id для определения
                if track.GetTrackID() == 1:
                    particle_name = "primary"
                else:
                    particle_name = "secondary"
        
        if self.event_action.event_id is not None:
            self.tracks.add(self.event_action.event_id, track.GetTrackID(), pos, particle_name)
        
        if (pos.z / g4.mm) > self.screens_end_z_mm:
            key = (self.event_action.event_id or -1, track.GetTrackID())
            if track.GetTrackID() == 1:
                if key not in self.primary_out:
                    self.primary_out.append(key)
            else:
                if key not in self.secondary_out:
                    self.secondary_out.append(key)

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

class ActionInitialization(g4.G4VUserActionInitialization):
    def __init__(self, data, primary_out, secondary_out, particle, energy_mev, layout, tracks):
        super().__init__()
        self.data = data
        self.primary_out = primary_out
        self.secondary_out = secondary_out
        self.particle = particle
        self.energy_mev = energy_mev
        self.layout = layout
        self.tracks = tracks
    def Build(self):
        half_world_z_mm = self.layout["half_world_z_mm"]
        first_front_mm = self.layout["first_screen_front_z_mm"]
        source_z_mm = max(first_front_mm - 10.0, -half_world_z_mm + 1.0)
        self.SetUserAction(PrimaryGeneration(self.particle, self.energy_mev, source_z_mm))
        event_action = ScreenEventAction()
        self.SetUserAction(event_action)
        self.SetUserAction(ScreenSteppingAction(
            self.primary_out,
            self.secondary_out,
            self.layout["screens_end_z_mm"],
            event_action,
            self.tracks,
        ))

# -----------------------------
# Запуск симуляции
# -----------------------------
class SimulationRunner:
    def __init__(self):
        self._last_tracks = None
    
    def _load_input(self, cfg):
        if cfg.input_data is not None:
            return cfg.input_data
        if cfg.task_id is not None:
            return DataServer().get_current_task_to_json(cfg.task_id)
        raise ValueError("Either task_id or input_data must be provided")
    
    def run(self, cfg):
        global _geant4_initialized
        
        try:
            data = self._load_input(cfg)
            layout = compute_layout(cfg, data)
            screen_info = {}
            primary_out = []
            secondary_out = []
            tracks = TrackCollector(enabled=cfg.collect_tracks)
            self._last_tracks = tracks  # Сохраняем для доступа к типам частиц
            
            run_manager = g4.G4RunManagerFactory.CreateRunManager(g4.G4RunManagerType.Serial)
            _geant4_initialized = True
            
            geom = ScreenGeometry(data=data, screen_info=screen_info, cfg=cfg)
            geom._precomputed_layout = layout
            run_manager.SetUserInitialization(geom)
            run_manager.SetUserInitialization(g4.FTFP_BERT())
            run_manager.SetUserInitialization(ActionInitialization(
                data=data,
                primary_out=primary_out,
                secondary_out=secondary_out,
                particle=cfg.particle,
                energy_mev=cfg.energy_mev,
                layout=layout,
                tracks=tracks,
            ))
            
            run_manager.Initialize()
            run_manager.BeamOn(cfg.events)
            
            result = SimulationResult(
                screen_info=screen_info,
                total_particles=cfg.events,
                total_out_primary_particles=len(primary_out),
                total_out_secondary_particles=len(secondary_out),
                tracks=tracks.data if cfg.collect_tracks else None,
            )
            
            return result
            
        finally:
            # Всегда выполняем очистку
            try:
                if 'run_manager' in locals():
                    del run_manager
                _cleanup_geant4()
                gc.collect()
            except:
                pass
        
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

def convert_screen_info_to_data(screen: ScreenInfo) -> dict:
    return {
        "Screen": screen.to_dict()
    }

def run_simulation_with_giga(cfg: SimulationGigaConfig):
    logger.info('run simulation with giga main')
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
    logger.info('request for llm')
    result = structed_llm.invoke(cfg.prompt)
    logger.info(result)

    data = convert_screen_info_to_data(result)
    logger.info(f'data: {data}')

    run_config = SimulationConfig(input_data=data, 
                                  particle=cfg.particle, 
                                  energy_mev=cfg.energy_mev,
                                  events=cfg.events)
    
    runner = SimulationRunner()
    res = runner.run(run_config)
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))

    return res


if __name__ == "__main__":
    ds = DataServer()
    task_id = 103
    data = ds.get_current_task_to_json(task_id)
    cfg = SimulationConfig(
        task_id=task_id,
        input_data=data,
        particle="He3",
        energy_mev=60.0,
        events=50,
        collect_tracks=True,
        visualize=True,
    )
    runner = SimulationRunner()
    res = runner.run(cfg)
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    if cfg.visualize and res.tracks:
        import pyvista as pv
        
        try:
            pv.start_xvfb()
        except:
            print("Предупреждение: Xvfb не запущен, используем offscreen режим")
    
        layout = compute_layout(cfg, runner._load_input(cfg))
        plotter = pv.Plotter()
        
        # draw screens
        z_cursor = float(layout["first_screen_front_z_mm"])
        for i, mat in enumerate(res.screen_info["Materials"]):
            th = float(mat["Thickness_mm"])
            center_z = z_cursor + 0.5 * th
            cube = pv.Cube(
                center=(0, 0, center_z),
                x_length=cfg.screen_xy_mm * 0.6,
                y_length=cfg.screen_xy_mm * 0.6,
                z_length=th
            )
            plotter.add_mesh(cube, opacity=0.3, color='lightblue', name=f"Screen_{i}")
            z_cursor += th

        # draw source
        source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
        sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
        plotter.add_mesh(sphere, color='red', name="Source")

        # draw tracks with particle-type coloring
        particle_counts = {}
        
        for track_key, pts in res.tracks.items():
            if len(pts) >= 2:
                event_id, track_id = track_key
                
                # Получаем тип частицы из TrackCollector
                # Для этого нужно сохранить tracks объект и передать его
                particle_name = "unknown"
                if hasattr(runner, '_last_tracks'):
                    particle_name = runner._last_tracks.get_particle_type(event_id, track_id)
                else:
                    # Альтернатива: определяем по track_id
                    particle_name = "primary" if track_id == 1 else "secondary"
                
                # Считаем статистику по типам частиц
                particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1
                
                # Получаем цвет для данного типа частицы
                color = get_particle_color(particle_name)
                
                # Определяем толщину линии: первичные частицы толще
                line_width = 2 if track_id == 1 else 1
                
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
        plotter.add_text(f"Geant4 Simulation: {cfg.particle} at {cfg.energy_mev} MeV", 
                        position='upper_edge', font_size=10)
        plotter.add_axes()
        # Создаем имя папки на основе текущей даты и времени
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = f"out/simulation_visualization_{timestamp}"
        
        # Создаем директорию если она не существует
        os.makedirs(export_dir, exist_ok=True)
        
        # Полный путь к HTML файлу
        html_filename = os.path.join(export_dir, f"simulation_task_{cfg.task_id}.html")

        # Экспорт в HTML
        plotter.export_html(html_filename)
        print(f"Визуализация экспортирована в {html_filename}")
        
        # Выводим статистику в консоль
        print("\nParticle type statistics:")
        for particle_name, count in sorted(particle_counts.items()):
            print(f"  {particle_name}: {count} tracks")