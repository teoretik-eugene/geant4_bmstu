from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple, Optional, Any

from pydantic import BaseModel

# -----------------------------
# Конфиги и результаты
# -----------------------------
@dataclass
class ParticleConfig:
    name: str
    energy_mev: float
    weight: float = 1.0  # Для смешанного пучка

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

class SimulationGigaRequest(BaseModel):
    particle: str = "He3"
    energy_mev: float = 40.0
    events: int = 100
    collect_tracks: bool = False
    prompt: str

@dataclass
class SimulationConfig:
    task_id: Optional[int] = None
    input_data: Optional[dict] = None
    # Одиночная частица (обратная совместимость)
    particle: str = "He3"
    energy_mev: float = 40.0
    # Мульти-частичный режим
    particles: List[ParticleConfig] = field(default_factory=list)
    use_mixed_beam: bool = False  # True - смешанный пучок, False - последовательные запуски
    events: int = 10
    world_xy_mm: float = 500.0
    world_z_mm: float = 500.0
    screen_xy_mm: float = 250.0
    first_screen_z_mm: float = 15.0
    electronics_gap_mm: float = 0.1
    # Размер и толщина чувствительного объёма электроники.
    # 20×20 мм — достаточно для захвата рассеянных частиц.
    # 1.0 мм толщина — компромисс:
    #   - заряженные частицы (He3/alpha/proton после экрана) останавливаются внутри
    #   - гаммы 1 МэВ имеют вероятность взаимодействия ~1.6% (против 0.08% при 0.05 мм)
    #   - не искажает геометрию экрана
    # Для задач где нужна только доза от гамм — увеличить до 3–5 мм.
    electronics_size_x_mm: float = 20.0
    electronics_size_y_mm: float = 20.0
    electronics_thickness_mm: float = 1.0
    electronics_material: str = "G4_Si"
    electronics_let_threshold_mev_cm2_mg: float = 1.0
    electronics_dose_threshold_gy: float = 5.0
    # Критерий C6: порог вторичного флюенса.
    # secondary_fluence_threshold — максимально допустимая доля вторичных частиц,
    # прошедших экран, от общего числа событий (SPR = n_secondary_exited / events).
    # Например, 0.10 означает: если более 10% событий породили вторичную частицу
    # за экраном — экран не защищает.
    secondary_fluence_threshold: float = 0.10
    collect_tracks: bool = False
    visualize: bool = False

    def __post_init__(self):
        # Автоматическое создание particles из устаревших полей
        if not self.particles and self.particle:
            self.particles = [ParticleConfig(name=self.particle, energy_mev=self.energy_mev)]
        
        # Преобразуем словари обратно в ParticleConfig если нужно
        converted_particles = []
        for p in self.particles:
            if isinstance(p, dict):
                converted_particles.append(ParticleConfig(**p))
            else:
                converted_particles.append(p)
        self.particles = converted_particles

@dataclass
class SingleParticleResult:
    particle: str
    energy_mev: float
    screen_info: Dict[str, Any]
    total_particles: int
    total_out_primary_particles: int
    total_out_secondary_particles: int
    tracks: Optional[Dict[Tuple[int, int], List[Tuple[float, float, float]]]] = None
    # Словарь {(event_id, track_id): particle_name} для правильной раскраски треков
    particle_types: Optional[Dict[Tuple[int, int], str]] = None

    def to_dict(self) -> dict:
        # asdict() не умеет сериализовать Dict с кортежами-ключами.
        # Временно подменяем tracks и particle_types на None, потом восстанавливаем.
        _tracks_backup = self.tracks
        _pt_backup = self.particle_types
        self.tracks = None
        self.particle_types = None
        d = asdict(self)
        self.tracks = _tracks_backup
        self.particle_types = _pt_backup

        if _tracks_backup is not None:
            d["tracks"] = {f"{k[0]}_{k[1]}": v for k, v in _tracks_backup.items()}
        if _pt_backup is not None:
            d["particle_types"] = {f"{k[0]}_{k[1]}": v for k, v in _pt_backup.items()}
        return d

@dataclass
class SimulationResult:
    # Для одиночной частицы
    screen_info: Optional[Dict[str, Any]] = None
    total_particles: Optional[int] = None
    total_out_primary_particles: Optional[int] = None
    total_out_secondary_particles: Optional[int] = None
    tracks: Optional[Dict[Tuple[int, int], List[Tuple[float, float, float]]]] = None
    
    # Для мульти-частичного режима
    particle_results: Optional[Dict[str, SingleParticleResult]] = None
    mixed_beam_result: Optional[Dict[str, Any]] = None
    comparison: Optional[Dict[str, Any]] = None

    energy_profiles: Optional[Dict] = None
    exit_energies: Optional[List[float]] = None
    electronics_hits: Optional[List[Dict[str, Any]]] = None
    energy_summary: Optional[Dict] = None
    # Словарь {(event_id, track_id): particle_name} для правильной раскраски треков
    particle_types: Optional[Dict[Tuple[int, int], str]] = None
    
    def to_dict(self) -> dict:
        # asdict() не умеет сериализовать Dict с кортежами-ключами (tracks, particle_types,
        # а также вложенные SingleParticleResult.tracks/particle_types).
        # Временно подменяем проблемные поля на None, потом восстанавливаем вручную.
        _tracks_backup = self.tracks
        _pt_backup = self.particle_types
        _pr_backup = self.particle_results
        self.tracks = None
        self.particle_types = None
        self.particle_results = None
        d = asdict(self)
        self.tracks = _tracks_backup
        self.particle_types = _pt_backup
        self.particle_results = _pr_backup

        # Треки одиночной частицы: {(ev, tr): pts} → {"ev_tr": pts}
        if _tracks_backup is not None:
            d["tracks"] = {f"{k[0]}_{k[1]}": v for k, v in _tracks_backup.items()}

        # Типы частиц: {(ev, tr): name} → {"ev_tr": name}
        if _pt_backup is not None:
            d["particle_types"] = {f"{k[0]}_{k[1]}": v for k, v in _pt_backup.items()}

        # particle_results сериализуем через собственный to_dict() каждого элемента
        if _pr_backup is not None:
            d["particle_results"] = {
                key: value.to_dict() for key, value in _pr_backup.items()
            }

        if self.exit_energies is not None:
            serialized = []
            for e in self.exit_energies:
                if isinstance(e, dict):
                    serialized.append({
                        "energy_mev":    float(e.get("energy_mev", 0.0)),
                        "is_primary":    bool(e.get("is_primary", True)),
                        "particle_type": str(e.get("particle_type", "unknown")),
                        "event_id":      e.get("event_id"),
                    })
                else:
                    serialized.append({
                        "energy_mev":    float(e),
                        "is_primary":    True,
                        "particle_type": "unknown",
                        "event_id":      None,
                    })
            d["exit_energies"] = serialized

        if self.electronics_hits is not None:
            d["electronics_hits"] = self.electronics_hits

        if self.energy_profiles is not None:
            energy_profiles_json = {}
            for key, profile in self.energy_profiles.items():
                str_key = f"{key[0]}_{key[1]}" if isinstance(key, tuple) else str(key)
                points_serializable = [
                    list(point) if isinstance(point, tuple) else point
                    for point in profile.get("points", [])
                ]
                energy_profiles_json[str_key] = {
                    "parent_id":    profile.get("parent_id", 0),
                    "particle":     profile.get("particle", "unknown"),
                    "points":       points_serializable,
                    "n_points":     len(points_serializable),
                    "energy_start": points_serializable[0][1] if points_serializable else None,
                    "energy_end":   points_serializable[-1][1] if points_serializable else None,
                    "energy_loss":  (points_serializable[0][1] - points_serializable[-1][1])
                                    if len(points_serializable) >= 2 else None,
                    "z_start":      points_serializable[0][0] if points_serializable else None,
                    "z_end":        points_serializable[-1][0] if points_serializable else None,
                    "is_stopped":   points_serializable[-1][1] < 0.001 if points_serializable else False,
                }
            d["energy_profiles"] = energy_profiles_json

        if self.energy_summary is not None:
            d["energy_summary"] = self.energy_summary

        return d
