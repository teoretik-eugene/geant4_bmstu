### Собрать контейнер:

```docker build -t geant4-dev .```


### Запустить контейнер

```docker run -it --rm -p 8000:8000 -v $PWD/src:/app/src --name geant4-api geant4-dev```

### Запуск моделирования

Для ручного конфигурирования и запуска моделирования - скрипт `chtmain.py`

Для запуска веб контроллера - `web_controller.py`

Пример ИИ агента - `giga_geant.py`
- Перед запуском ИИ агента с использованием GigaChat LLM необходимо скачать сертификаты НУЦ Минцифры и поместить в папку `src/`. Документация: https://developers.sber.ru/docs/ru/gigachat/certificates

 docker build -t geant4-dev .