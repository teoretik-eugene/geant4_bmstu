# web_controller.py
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import uvicorn
import logging
import json
import multiprocessing
import tempfile
import os
import subprocess
import shutil
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid
import signal
import time

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Geant4 Simulation API")

# Глобальный словарь для хранения статусов симуляций
simulation_statuses: Dict[str, Dict[str, Any]] = {}

# Модели для запросов и ответов
class SimulationRequest(BaseModel):
    task_id: int
    particle: str = "He3"
    energy_mev: float = 40.0
    events: int = 100
    collect_tracks: bool = False
    input_data: Optional[dict] = None

class SimulationResponse(BaseModel):
    simulation_id: str
    status: str
    message: str
    created_at: str

class SimulationStatus(BaseModel):
    simulation_id: str = Field(..., description="ID симуляции")
    status: str = Field(..., description="Статус выполнения")
    result: Optional[Dict[str, Any]] = Field(None, description="Результаты симуляции")
    error: Optional[str] = Field(None, description="Ошибка выполнения")
    created_at: str = Field(..., description="Время создания")
    completed_at: Optional[str] = Field(None, description="Время завершения")

class SimulationResult(BaseModel):
    simulation_id: str
    status: str
    result: Dict[str, Any]
    created_at: str
    completed_at: str

# Функция для запуска симуляции в отдельном процессе
def run_simulation_process(simulation_id: str, config_dict: dict, result_dir: str):
    """Запускает симуляцию в полностью изолированном процессе"""
    try:
        # Создаем временный файл для конфигурации
        config_file = os.path.join(result_dir, 'config.json')
        with open(config_file, 'w') as f:
            json.dump(config_dict, f)
        
        # Создаем файл для результата
        result_file = os.path.join(result_dir, 'result.json')
        
        # Команда для запуска симуляции
        cmd = [
            'python3', '-c', 
            f'''
import json
import sys
sys.path.insert(0, ".")
from chtmain import run_simulation, SimulationConfig

with open("{config_file}", "r") as f:
    config_dict = json.load(f)

cfg = SimulationConfig(**config_dict)
result = run_simulation(cfg)

with open("{result_file}", "w") as f:
    json.dump(result.to_dict(), f, indent=2)
            '''
        ]
        
        # Запускаем процесс
        process = subprocess.Popen(
            cmd,
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Ждем завершения с таймаутом (10 минут)
        try:
            stdout, stderr = process.communicate(timeout=600)
            if process.returncode == 0:
                # Читаем результат
                if os.path.exists(result_file):
                    with open(result_file, 'r') as f:
                        result_data = json.load(f)
                    return {
                        'status': 'completed',
                        'result': result_data,
                        'error': None
                    }
                else:
                    return {
                        'status': 'failed',
                        'result': None,
                        'error': 'Result file not found'
                    }
            else:
                return {
                    'status': 'failed',
                    'result': None,
                    'error': f'Process failed: {stderr}'
                }
        except subprocess.TimeoutExpired:
            process.kill()
            return {
                'status': 'timeout',
                'result': None,
                'error': 'Simulation timed out after 10 minutes'
            }
            
    except Exception as e:
        return {
            'status': 'error',
            'result': None,
            'error': f'Unexpected error: {str(e)}'
        }
    finally:
        # Очищаем временные файлы
        try:
            if os.path.exists(config_file):
                os.remove(config_file)
            if os.path.exists(result_file):
                os.remove(result_file)
            os.rmdir(result_dir)
        except:
            pass

# Фоновая задача для мониторинга симуляции
async def monitor_simulation(simulation_id: str, config_dict: dict):
    """Мониторит выполнение симуляции в фоне"""
    try:
        # Создаем временную директорию для результатов
        result_dir = tempfile.mkdtemp(prefix=f'sim_{simulation_id}_')
        
        # Обновляем статус
        simulation_statuses[simulation_id].update({
            'status': 'running',
            'result_dir': result_dir
        })
        
        # Запускаем симуляцию в отдельном процессе
        result = run_simulation_process(simulation_id, config_dict, result_dir)
        
        # Обновляем статус
        simulation_statuses[simulation_id].update({
            'status': result['status'],
            'result': result['result'],
            'error': result['error'],
            'completed_at': datetime.now().isoformat()
        })
        
        logger.info(f"Simulation {simulation_id} completed with status: {result['status']}")
        
    except Exception as e:
        simulation_statuses[simulation_id].update({
            'status': 'error',
            'error': f"Monitoring error: {str(e)}",
            'completed_at': datetime.now().isoformat()
        })
        logger.error(f"Error monitoring simulation {simulation_id}: {str(e)}")

# Эндпоинты
@app.post("/simulate", response_model=SimulationResponse)
async def simulate(request: SimulationRequest, background_tasks: BackgroundTasks):
    """Запускает новую симуляцию"""
    simulation_id = str(uuid.uuid4())
    
    logger.info(f"Starting simulation {simulation_id} for task_id: {request.task_id}")
    
    # Сохраняем информацию о симуляции (включая simulation_id)
    simulation_statuses[simulation_id] = {
        'simulation_id': simulation_id,  # Добавляем simulation_id в данные
        'status': 'pending',
        'request': request.model_dump(),  # Используем model_dump() вместо dict()
        'created_at': datetime.now().isoformat(),
        'completed_at': None,
        'result': None,
        'error': None
    }
    
    # Запускаем мониторинг в фоне
    background_tasks.add_task(monitor_simulation, simulation_id, request.model_dump())  # Используем model_dump()
    
    return SimulationResponse(
        simulation_id=simulation_id,
        status='started',
        message=f"Simulation {simulation_id} started successfully",
        created_at=simulation_statuses[simulation_id]['created_at']
    )

@app.get("/simulations/{simulation_id}", response_model=SimulationStatus)
async def get_simulation_status(simulation_id: str):
    """Возвращает статус симуляции"""
    if simulation_id not in simulation_statuses:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Создаем копию данных и добавляем simulation_id если его нет
    status_data = simulation_statuses[simulation_id].copy()
    if 'simulation_id' not in status_data:
        status_data['simulation_id'] = simulation_id
    
    return SimulationStatus(**status_data)

@app.get("/simulations", response_model=List[SimulationStatus])
async def list_simulations(limit: int = 10, offset: int = 0):
    """Возвращает список симуляций"""
    result = []
    simulations = list(simulation_statuses.items())[offset:offset + limit]
    
    for sim_id, sim_data in simulations:
        # Создаем копию данных и добавляем simulation_id если его нет
        status_data = sim_data.copy()
        if 'simulation_id' not in status_data:
            status_data['simulation_id'] = sim_id
        result.append(SimulationStatus(**status_data))
    
    return result

@app.delete("/simulations/{simulation_id}")
async def delete_simulation(simulation_id: str):
    """Удаляет информацию о симуляции"""
    if simulation_id not in simulation_statuses:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Очищаем временные файлы, если они есть
    sim_data = simulation_statuses[simulation_id]
    if 'result_dir' in sim_data and os.path.exists(sim_data['result_dir']):
        try:
            shutil.rmtree(sim_data['result_dir'])
        except:
            pass
    
    del simulation_statuses[simulation_id]
    return {"message": f"Simulation {simulation_id} deleted"}

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_simulations": len([s for s in simulation_statuses.values() if s['status'] in ['pending', 'running']]),
        "total_simulations": len(simulation_statuses)
    }

@app.get("/particles")
async def get_particles():
    """Возвращает список доступных частиц"""
    return {
        "particles": ["He3", "e-", "proton", "alpha", "neutron", "gamma"],
        "default": "He3"
    }

# Очистка при запуске
@app.on_event("startup")
async def startup_event():
    """Очистка старых временных файлов при запуске"""
    temp_dir = tempfile.gettempdir()
    for item in os.listdir(temp_dir):
        if item.startswith('sim_') and os.path.isdir(os.path.join(temp_dir, item)):
            try:
                shutil.rmtree(os.path.join(temp_dir, item))
            except:
                pass

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        timeout_keep_alive=60
    )