# Geant4 BMSTU — Проект моделирования взаимодействия частиц

## Обзор проекта

Проект представляет собой систему для моделирования прохождения частиц через многослойные материалы с использованием **Geant4** (инструмент моделирования прохождения частиц через вещество). Система включает:

- **Ядро моделирования** (`main.py`) — обёртка на Python над Geant4 через `geant4-pybind`, конфигурирование геометрии, материалов, генераторов частиц и сбор результатов.
- **Веб-API** (`web_controller.py`) — FastAPI-сервис для удалённого запуска симуляций с отслеживанием статуса.
- **GUI-клиент** (`gui_client.py`) — десктопное приложение на Tkinter для конфигурирования и визуализации результатов.
- **ИИ-агент** (`giga_geant.py`) — интеграция с GigaChat LLM для автоматического подбора конфигурации экранов из слоёв материалов.
- **Визуализация** (`vis_graph.py`, `energy_analysis.py`) — графики анализа энергии, треков и депозиции энергии.

### Технологии

- **Python 3** — основной язык
- **Geant4 v11.1.1** — через `geant4-pybind` и сборка из исходников в Docker
- **FastAPI + Uvicorn** — веб-API
- **Pydantic** — валидация данных
- **Matplotlib** — визуализация
- **Tkinter** — GUI-клиент
- **LangChain + GigaChat** — ИИ-агент для конфигурирования
- **Docker** — контейнеризация (Ubuntu 22.04)
- **Trame/VTK/PyVista** — 3D-визуализация

## Структура проекта

```
geant4_bmstu/
├── Dockerfile              # Многоэтапная сборка Geant4 + Python-окружение
├── docker-compose.yml      # Оркестрация контейнера
├── entry-point.sh          # Инициализация переменных окружения Geant4
├── requirements.txt        # Python-зависимости
├── build_root.sh           # Скрипт сборки (ROOT, если требуется)
├── README.md               # Краткие инструкции по запуску
├── src/
│   ├── main.py             # Ядро: геометрия, сенсоры, генераторы, раннеры
│   ├── web_controller.py   # FastAPI сервер (/simulate, /simulate/giga)
│   ├── gui_client.py       # Tkinter GUI-клиент
│   ├── simulations.py      # Модели данных (SimulationConfig, SimulationResult и др.)
│   ├── giga_geant.py       # Пример ИИ-агента с GigaChat
│   ├── giga_tools.py       # Утилиты для GigaChat
│   ├── energy_analysis.py  # Функции анализа и plotting энергии
│   ├── vis_graph.py        # Визуализация графов и результатов
│   ├── energy.py           # Расчёты энергии
│   ├── geant4_client.py    # Клиент для Geant4
│   ├── DataServer.py       # Сервер данных (задача по task_id)
│   ├── TrimParser.py       # Парсер входных данных
│   ├── utils.py            # Утилиты (compute_layout, is_primary)
│   ├── simulation_gui.py   # GUI-компоненты симуляции
│   ├── dto/                # DTO-модели
│   └── ...
└── screen_data/            # Данные экранов
```

## Сборка и запуск

### Сборка Docker-образа

```bash
docker build -t geant4-dev .
```

### Запуск контейнера

```bash
docker run -it --rm -p 8000:8000 -v $PWD/src:/app/src --name geant4-api geant4-dev
```

- Порт `8000` — FastAPI сервер
- Том `./src:/app/src` — монтирование исходников для разработки

### Запуск веб-API

Внутри контейнера:

```bash
python3 web_controller.py
```

API будет доступен по адресу `http://localhost:8000`.

### Запуск GUI-клиента

```bash
python3 gui_client.py
```

### Запуск ИИ-агента (GigaChat)

1. Скачайте сертификаты НУЦ Минцифры и поместите в `src/`: `russian_trusted_root_ca_pem.crt`
2. Установите `GIGACHAT_CREDENTIALS` в `.env`
3. Запустите:

```bash
python3 giga_geant.py
```

## API эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/simulate` | Запуск симуляции по task_id или input_data |
| POST | `/simulate/giga` | Запуск симуляции с GigaChat LLM |
| GET | `/simulations/{id}` | Статус симуляции |
| GET | `/simulations` | Список симуляций (limit, offset) |
| DELETE | `/simulations/{id}` | Удаление симуляции |
| GET | `/health` | Проверка здоровья сервиса |
| GET | `/particles` | Список доступных частиц |

### Доступные частицы

`He3`, `e-`, `proton`, `alpha`, `neutron`, `gamma`

## Ключевые классы

### main.py

- **`ScreenGeometry`** — геометрия многослойного экрана (`G4VUserDetectorConstruction`)
- **`ScreenSensitiveDetector`** — чувствительный детектор для экранов
- **`ScreenEventAction`** — действия в начале/конце события
- **`ScreenSteppingAction`** — действия на каждом шаге трека
- **`SingleParticlePrimaryGenerator`** — генератор одиночных частиц
- **`MixedParticlePrimaryGenerator`** — генератор смешанного пучка
- **`SingleProcessSimulationRunner`** — запуск одиночной симуляции
- **`TrackCollector`** — сборщик треков и энергий
- **`run_simulation_with_giga`** — запуск через GigaChat LLM

### simulations.py (модели данных)

- **`ParticleConfig`** — конфигурация частицы (имя, энергия, вес)
- **`SimulationConfig`** — конфигурация симуляции
- **`SimulationGigaConfig`** — конфиг для GigaChat-режима
- **`SimulationResult`** — результаты симуляции (screen_info, треки, энергия)
- **`SingleParticleResult`** — результат для одиночной частицы

## Конфигурация материалов

Материалы задаются через JSON-структуру:

```json
{
  "Screen": {
    "Materials": [
      {
        "Name": "Be",
        "Width": 1000,
        "Elements": [
          { "Symbol": "Be", "AtomicNumber": 4, "AtomicWeight": 9.012, "Density": 1.85 }
        ]
      }
    ]
  }
}
```

## Известные проблемы и исправления

### Баг с подсчётом застрявших частиц (исправлено 2026-04-12)

**Проблема 1:** `Primary_stuck_count` в `screen_info["Materials"]` показывал 1 вместо 30.

**Причина:** `ScreenSensitiveDetector.stopped_tracks` использовал только `track_id` без `event_id`. В Geant4 `track_id` перезапускается с 1 для каждого события.

**Решение:** Использовать кортеж `(event_id, track_id)` как ключ в `stopped_tracks`.

**Проблема 2:** `Secondary_stuck_count` показывал 1 вместо реального числа (61), хотя `energy_summary.stopped_in_screen` был правильным. `total_out_secondary_particles` всегда был 0, хотя `exited_screen` показывал 330.

**Причина:** 
1. `ScreenSensitiveDetector.ProcessHits` вызывается только когда трек делает шаг внутри объёма. Многие вторичные электроны рождаются и мгновенно поглощаются Geant4 без явного шага.
2. Исходная логика `exited_screen = total - stopped` считала все нe-остановившиеся частицы "вылетевшими", включая те, что улетели **назад** (обратное рассеяние, Z < first_z). Это некорректно — задача требует считать "вылетевшими" **только** частицы, пересёкшие **заднюю** границу экрана.

**Решение:** 
1. **Переопределена логика `_compute_energy_summary`**:
   - `exited_screen` — только частицы с `last_z > screens_end_z_mm` (физически вышли за заднюю границу экрана)
   - `stopped_in_screen` — остановились ВНУТРИ материала экрана (`e_end < 0.001` И `first_z <= last_z <= end_z`)
   - `backscattered` — улетели НАЗАД (`last_z < first_z`, обратное рассеяние в вакуум перед экраном)
   - `absorbed_before_screen` — вторичные, остановившиеся ДО экрана (в вакууме)

2. Добавлена функция `_find_stopped_particle_material(points, first_z, thicknesses)` — определяет материал остановившейся частицы. Если последняя Z вне экрана (обратное рассеяние), ищет первую точку **внутри** экрана (ближе к месту рождения частицы).

3. `_compute_energy_summary` возвращает `stopped_by_material` — словарь `{material_index: count}`. После вычисления `energy_summary` значения `screen_info["Materials"][idx]["Primary_stuck_count"]` и `Secondary_stuck_count` перезаписываются корректными данными.

4. `total_out_primary_particles` и `total_out_secondary_particles` берутся из `ScreenSteppingAction` — `len(primary_out)` и `len(secondary_out)`. Это списки частиц, которые **физически пересекли** заднюю границу экрана (`pos.z > screens_end_z_mm`), что является надёжным источником для подсчёта вылетевших.

**Затронутые файлы:**
- `src/main.py` — `ScreenSensitiveDetector`, `_find_material_index`, `_compute_energy_summary`, `SingleProcessSimulationRunner.run_single`, `SimulationRunner.run_sequential_multiprocess`

## Особенности разработки

- **Многопроцессность**: симуляции запускаются через `subprocess` для изоляции Geant4 RunManager
- **Сбор треков**: опциональный (`collect_tracks: bool`), собирает координаты и энергии треков
- **Смешанные пучки**: поддержка нескольких типов частиц с весами
- **Энергетический анализ**: `energy_summary` содержит статистику по первичным/вторичным частицам, депозиции энергии и выходам
- **Логирование**: в файл `app.log` с уровнями INFO/ERROR
