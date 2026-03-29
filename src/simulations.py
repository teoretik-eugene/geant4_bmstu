from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple, Optional, Any

# -----------------------------
# Конфиги и результаты
# -----------------------------
@dataclass
class ParticleConfig:
    name: str
    energy_mev: float
    weight: float = 1.0  # Для смешанного пучка

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

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.tracks is not None:
            d["tracks"] = {f"{k[0]}:{k[1]}": v for k, v in self.tracks.items()}
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
    
    def to_dict(self) -> dict:
        d = asdict(self)
        
        # Обработка tracks для одиночной частицы
        if self.tracks is not None:
            d["tracks"] = {f"{k[0]}:{k[1]}": v for k, v in self.tracks.items()}
        
        # Обработка particle_results
        if self.particle_results is not None:
            d["particle_results"] = {
                key: value.to_dict() for key, value in self.particle_results.items()
            }
        
        return d