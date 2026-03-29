from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
from main import compute_layout

import logging

# Настройка логгера
logging.basicConfig(
    filename='app.log',
    filemode='w',  # 'w' - перезапись, 'a' - добавление (по умолчанию)
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

