import os
import requests
import json

class DataServer:

    def __init__(self) -> None:
        # адрес для получения данных на задачу
        self.current_task_url = 'http://trim.kv-projects.ru/PHP_back/GetCurrentTaskFromPool.php'
        self.current_task_url_json = \
        'http://trim.kv-projects.ru/PHP_back/GetCurrentJSONTaskFromPool.php?ID='
        self.current_directory = os.getcwd()



    def get_current_task_text(self, id: int) -> str:
        response = requests.get(f'{self.current_task_url}?ID={id}')
        if response.status_code != 200:
            return 'ERROR'
        # TODO сделать обработку исключения
        return response.content.decode('utf-8')
    
    def get_current_task_to_json(self, id: int) -> dict:
        response = requests.get(f'{self.current_task_url_json}{id}')
        task_data = None
        if response.status_code == 200:
            task_data = response.json()
            return task_data
        else:
            # Подумать как тут реализовать обработку ошибки запроса
            return None   

    def to_json(self, screen_dict: dict):
        r = json.dumps(screen_dict)
        load = json.loads(r)
        return load        