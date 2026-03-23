import json

class Utils:

    @staticmethod
    def read_task_json(file_name: str) -> dict:
        try:
            with open(file_name, 'r', encoding='utf-8') as file:
                data = json.load(file)
            return data
        except FileNotFoundError:
            print("Файл не найден")
        except json.JSONDecodeError:
            print("Ошибка в формате JSON")
