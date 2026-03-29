import json
from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
from DataServer import DataServer
from TrimParser import TrimParser

def read_task_json(file_name: str) -> dict:
    try:
        with open(file_name, 'r', encoding='utf-8') as file:
            data = json.load(file)
        return data
    except FileNotFoundError:
        print("Файл не найден")
    except json.JSONDecodeError:
        print("Ошибка в формате JSON")

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

def is_primary(track_data):
    return track_data["parent_id"] == 0

def get_particle_color(particle_name):
        """Возвращает цвет для конкретного типа частицы"""
        color_map = {
            # Первичные частицы
            "he3": "blue",
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
        particle_name = str(particle_name).lower()

        for key, color in color_map.items():
            if key.lower() == particle_name:
                return color
            
        for key, color in color_map.items():
            if key.lower() in particle_name or particle_name in key.lower():
                return color
            
        return color_map["unknown"]