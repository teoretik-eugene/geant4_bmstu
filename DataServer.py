import os
import requests

class DataServer:

    def __init__(self) -> None:
        self.current_task_url = 'http://trim.kv-projects.ru/PHP_back/GetCurrentTaskFromPool.php'
        self.current_directory = os.getcwd()
        