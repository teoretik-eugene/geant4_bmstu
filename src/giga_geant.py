from langchain_gigachat.chat_models import GigaChat
from main import run_simulation_with_giga, SimulationConfig, SimulationGigaConfig
import os

prompt = '''
Помоги подготовить информацию для экрана. Помоги составить экран из слоев Be и Al и стекла. 
Первый слой толщиной 1000 мкм, а второй 1000 мкм, третий 1000 мкм. Найди необходимые данные. 
Укажи правильные символы элементов, атомный номер, стандартный атомный вес элементов, плотность.
'''

prompt2 = '''
Помоги подготовить информацию для экрана. Помоги составить экран из слоев Be и Al и ВТ5Л. 
Первый слой толщиной 1000 мкм, а второй 1000 мкм, третий 2000 мкм. Найди необходимые данные. 
Укажи правильные символы элементов, атомный номер, стандартный атомный вес элементов, плотность.
'''


cfg = SimulationGigaConfig(particle="He3", energy_mev=60, events=20, prompt=prompt2)

result = run_simulation_with_giga(cfg=cfg)

print(result)