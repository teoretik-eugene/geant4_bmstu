import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any

@dataclass
class EnergyRecord:
    """Запись об энергетическом событии"""
    particle_name: str
    energy_before_mev: float
    energy_after_mev: float
    energy_loss_mev: float
    layer_index: int
    layer_name: str
    event_id: int
    track_id: int
    position_z_mm: float


@dataclass
class EnergyAnalysisResult:
    """Результаты анализа энергии"""
    # Для каждого слоя: список энергетических потерь
    energy_loss_per_layer: Dict[int, List[float]] = field(default_factory=dict)
    # Энергия на входе в экран
    initial_energies: List[float] = field(default_factory=list)
    # Энергия на выходе из экрана
    final_energies: List[float] = field(default_factory=list)
    # Полные энергетические потери для каждой частицы
    total_energy_loss: List[float] = field(default_factory=list)
    # Детальные записи по каждому треку
    detailed_records: List[EnergyRecord] = field(default_factory=list)
    # Энергетические спектры
    energy_spectrum_before: List[float] = field(default_factory=list)
    energy_spectrum_after: List[float] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Сериализация результатов"""
        # Вычисляем статистику только если есть данные
        stats = {
            "mean_initial_energy": 0,
            "mean_final_energy": 0,
            "mean_energy_loss": 0,
            "std_initial_energy": 0,
            "std_final_energy": 0,
            "std_energy_loss": 0,
            "max_energy_loss": 0,
            "min_energy_loss": 0,
        }
        
        if self.initial_energies:
            import numpy as np
            stats["mean_initial_energy"] = np.mean(self.initial_energies)
            stats["std_initial_energy"] = np.std(self.initial_energies)
        
        if self.final_energies:
            import numpy as np
            stats["mean_final_energy"] = np.mean(self.final_energies)
            stats["std_final_energy"] = np.std(self.final_energies)
        
        if self.total_energy_loss:
            import numpy as np
            stats["mean_energy_loss"] = np.mean(self.total_energy_loss)
            stats["std_energy_loss"] = np.std(self.total_energy_loss)
            stats["max_energy_loss"] = max(self.total_energy_loss)
            stats["min_energy_loss"] = min(self.total_energy_loss)
        
        return {
            "energy_loss_per_layer": {
                str(k): v for k, v in self.energy_loss_per_layer.items()
            },
            "initial_energies": self.initial_energies,
            "final_energies": self.final_energies,
            "total_energy_loss": self.total_energy_loss,
            "energy_spectrum_before": self.energy_spectrum_before,
            "energy_spectrum_after": self.energy_spectrum_after,
            "statistics": stats
        }
