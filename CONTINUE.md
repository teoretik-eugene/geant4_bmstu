# CONTINUE.md — Проектное руководство

> **Руководство для AI-ассистента и команды разработчиков**  
> Проект: Программный модуль оценки радиационной защиты электронной аппаратуры  
> Дата последнего обновления: актуализировать при изменениях архитектуры

---

## 1. Обзор проекта

### Назначение

Программный модуль предназначен для **численной оценки степени защиты электронной аппаратуры космических аппаратов (и аналогичных применений) с помощью защитных экранов (радиационных щитов)**.

Основные задачи модуля:
- Моделирование **многослойного защитного экрана** произвольной конфигурации (материалы, толщины, порядок слоёв)
- **Облучение экрана** потоками заряженных и незаряженных частиц (протоны, электроны, альфа-частицы, гамма-кванты, нейтроны и т.д.)
- Расчёт **переданной (прошедшей) энергии**, поглощённой дозы и пространственного распределения энерговыделения
- Определение **количества прошедших и задержанных частиц** (эффективность экранирования)
- Анализ **вторичных частиц**, возникающих в слоях экрана
- Генерация отчётов и визуализация результатов

### Область применения

- Радиационная стойкость бортовой электроники космических аппаратов
- Проектирование радиационной защиты для ядерных установок
- Медицинская физика (расчёт защиты в лучевой терапии)
- Исследовательские применения (дозиметрия)

---

## 2. Физическая модель

### Типы частиц

| Тип частицы | Обозначение | Основные процессы взаимодействия |
|---|---|---|
| Протоны | p | Ионизация, упругое рассеяние, ядерные реакции |
| Электроны | e⁻ | Ионизация, тормозное излучение (Брагг), многократное рассеяние |
| Альфа-частицы | α | Ионизация (высокая ЛПЭ), упругое рассеяние |
| Гамма-кванты | γ | Фотоэффект, комптоновское рассеяние, рождение пар |
| Нейтроны | n | Упругое/неупругое рассеяние, захват, деление |
| Позитроны | e⁺ | Аннигиляция, ионизация |

### Ключевые физические величины

- **Линейная передача энергии (ЛПЭ / LET)** — энергия, передаваемая веществу на единицу пути, [МэВ/мм]
- **Пробег (Range)** — средний путь частицы до полной остановки, [мм] или [г/см²]
- **Поглощённая доза** — [Гр = Дж/кг]
- **Эффективная доза** — [Зв = Гр × коэффициент качества]
- **Коэффициент ослабления** — для фотонов, [1/см] или [см²/г]
- **Сечение реакции** — [барн = 10⁻²⁴ см²]

### Метод моделирования

Проект реализует **метод Монте-Карло** (или детерминистический подход) для трассировки частиц через слои экрана:

```
Источник частиц
      │
      ▼
┌─────────────┐     Случайное число → длина свободного пробега
│  Слой 1     │     → Тип взаимодействия (ионизация/рассеяние/...)
│  материал,  │     → Изменение энергии и направления
│  толщина    │     → Генерация вторичных частиц
└──────┬──────┘
       │
      ▼
┌─────────────┐
│  Слой 2     │  ... повторить для каждого слоя
└──────┬──────┘
       │
      ▼
   Детектор: анализ прошедших частиц,
   энергетических спектров, доз
```

---

## 3. Архитектура программного модуля

### Структура проекта (целевая)

```
shield-protection-module/
│
├── src/                          # Основной исходный код
│   ├── core/                     # Ядро физической модели
│   │   ├── particle.py           # Классы частиц (Particle, ParticleType)
│   │   ├── material.py           # Материалы и их физические свойства
│   │   ├── layer.py              # Слой экрана (материал + геометрия)
│   │   ├── shield.py             # Многослойный экран (Stack of layers)
│   │   ├── physics/              # Физические процессы
│   │   │   ├── ionization.py     # Формула Бете-Блоха, ионизация
│   │   │   ├── scattering.py     # Кулоновское и ядерное рассеяние
│   │   │   ├── photon.py         # Взаимодействие гамма-квантов
│   │   │   └── nuclear.py        # Ядерные реакции
│   │   └── cross_sections/       # Таблицы сечений / NIST данные
│   │       ├── proton_data.py
│   │       ├── electron_data.py
│   │       └── photon_data.py
│   │
│   ├── simulation/               # Движок моделирования
│   │   ├── monte_carlo.py        # Основной цикл Монте-Карло
│   │   ├── source.py             # Источники частиц (моноэнерг., спектр)
│   │   ├── detector.py           # Детекторы (регистрация частиц и доз)
│   │   ├── geometry.py           # Геометрия (плоская/цилиндрическая)
│   │   └── runner.py             # Управление запуском симуляции
│   │
│   ├── analysis/                 # Анализ результатов
│   │   ├── spectrum.py           # Энергетические спектры
│   │   ├── dose_map.py           # Дозовые карты
│   │   ├── statistics.py         # Статистика (среднее, дисперсия, CI)
│   │   └── report.py             # Генерация отчётов
│   │
│   ├── ui/                       # Пользовательский интерфейс
│   │   ├── cli.py                # CLI-интерфейс (argparse)
│   │   ├── gui/                  # GUI (PyQt5/tkinter, опционально)
│   │   │   ├── main_window.py
│   │   │   ├── shield_editor.py  # Редактор конфигурации экрана
│   │   │   └── plot_widget.py    # Виджеты для графиков
│   │   └── api.py                # REST API (FastAPI, опционально)
│   │
│   ├── io/                       # Ввод/вывод данных
│   │   ├── config_reader.py      # Чтение конфигурации (YAML/JSON/TOML)
│   │   ├── results_writer.py     # Запись результатов (CSV, HDF5, JSON)
│   │   └── visualizer.py         # Графики (matplotlib, plotly)
│   │
│   └── utils/                    # Вспомогательные утилиты
│       ├── constants.py          # Физические константы
│       ├── units.py              # Конвертация единиц
│       ├── validators.py         # Валидация входных данных
│       └── logger.py             # Система логирования
│
├── data/                         # Данные и таблицы
│   ├── materials/                # Свойства материалов (Al, Fe, Pb, ...)
│   │   └── materials_db.json
│   ├── cross_sections/           # Сечения взаимодействия (NIST, ENDF/B)
│   └── spectra/                  # Типовые спектры частиц (AE8, AP8, ...)
│
├── config/                       # Конфигурации симуляций
│   ├── default_shield.yaml       # Пример конфигурации экрана
│   ├── proton_beam.yaml          # Пример конфигурации пучка протонов
│   └── settings.yaml             # Глобальные настройки
│
├── tests/                        # Тесты
│   ├── unit/
│   │   ├── test_particle.py
│   │   ├── test_material.py
│   │   ├── test_physics.py
│   │   └── test_shield.py
│   ├── integration/
│   │   ├── test_simulation.py
│   │   └── test_benchmark.py     # Сравнение с GEANT4/SRIM/NIST
│   └── fixtures/                 # Эталонные данные для тестов
│
├── notebooks/                    # Jupyter-ноутбуки для исследований
│   ├── demo_proton_shielding.ipynb
│   ├── material_comparison.ipynb
│   └── dose_depth_profile.ipynb
│
├── docs/                         # Документация
│   ├── physics_model.md          # Описание физической модели
│   ├── api_reference.md          # API справочник
│   ├── user_guide.md             # Руководство пользователя
│   └── validation_report.md     # Отчёт верификации/валидации
│
├── results/                      # Выходные результаты (gitignored)
│   └── .gitkeep
│
├── main.py                       # Точка входа приложения
├── requirements.txt              # Python зависимости
├── pyproject.toml                # Конфигурация проекта (setuptools/poetry)
├── setup.py                      # Установка пакета
├── Makefile                      # Команды сборки/запуска/тестирования
└── README.md                     # Краткое описание для GitHub
```

---

## 4. Ключевые классы и модули

### `Particle` — класс частицы

```python
# src/core/particle.py
class Particle:
    particle_type: ParticleType   # PROTON, ELECTRON, ALPHA, GAMMA, NEUTRON
    energy: float                 # кинетическая энергия [МэВ]
    position: np.ndarray          # координаты [x, y, z] в мм
    direction: np.ndarray         # единичный вектор направления
    charge: int                   # заряд в единицах e
    mass: float                   # масса в МэВ/c²
    is_alive: bool                # флаг: частица ещё движется
```

### `Material` — класс материала

```python
# src/core/material.py
class Material:
    name: str                     # название (Aluminum, Lead, ...)
    symbol: str                   # химический символ / формула
    Z: int                        # атомный номер
    A: float                      # атомная масса [а.е.м.]
    density: float                # плотность [г/см³]
    radiation_length: float       # длина радиационных потерь X₀ [г/см²]
    nuclear_interaction_length: float  # длина ядерного взаимодействия [г/см²]
    ionization_potential: float   # потенциал ионизации I [эВ]
```

### `Layer` — слой экрана

```python
# src/core/layer.py
class Layer:
    material: Material            # материал слоя
    thickness: float              # толщина [мм]
    area: float                   # площадь [мм²] (опционально)

    def areal_density(self) -> float:  # поверхностная плотность [г/см²]
        return self.thickness * self.material.density / 10
```

### `Shield` — многослойный экран

```python
# src/core/shield.py
class Shield:
    layers: List[Layer]           # список слоёв (в порядке прохождения)
    
    def total_thickness(self) -> float       # суммарная толщина [мм]
    def total_areal_density(self) -> float   # суммарная поверхностная плотность [г/см²]
    def add_layer(self, layer: Layer)        # добавить слой
    def remove_layer(self, index: int)       # удалить слой по индексу
```

### `MonteCarloEngine` — движок моделирования

```python
# src/simulation/monte_carlo.py
class MonteCarloEngine:
    shield: Shield
    source: ParticleSource
    detector: Detector
    n_particles: int              # количество частиц для моделирования
    
    def run(self) -> SimulationResults
    def _transport_particle(self, particle: Particle) -> TrackRecord
    def _sample_interaction(self, particle: Particle, material: Material) -> Interaction
    def _apply_physics(self, particle: Particle, interaction: Interaction)
```

### `SimulationResults` — результаты

```python
# src/simulation/runner.py
@dataclass
class SimulationResults:
    n_transmitted: int            # прошло сквозь экран
    n_absorbed: int               # поглощено в экране
    n_backscattered: int          # отражено назад
    transmitted_energies: np.ndarray      # энергии прошедших частиц [МэВ]
    dose_per_layer: np.ndarray            # доза в каждом слое [Гр]
    energy_deposition_profile: np.ndarray # профиль энерговыделения
    secondary_particles: List[Particle]   # вторичные частицы
```

---

## 5. Физические алгоритмы

### Торможение заряженных частиц (формула Бете-Блоха)

```
-dE/dx = K · z² · Z/A · 1/β² · [½ ln(2mₑc²β²γ²Tmax/I²) - β² - δ/2]

где:
  K = 4πNAr²ₑmₑc² = 0.307 МэВ·см²/г
  z — заряд налетающей частицы
  Z, A — атомный номер и масса среды
  β = v/c, γ = 1/√(1-β²)
  I — средний потенциал ионизации среды
  δ — поправка на плотностный эффект
```

### Кулоновское многократное рассеяние (Highland/Molière)

```
θ₀ = (13.6 МэВ / βcp) · z · √(x/X₀) · [1 + 0.038 · ln(x/X₀)]

где:
  p — импульс частицы
  x/X₀ — толщина в единицах длины радиационных потерь
```

### Ослабление гамма-квантов

```
I(x) = I₀ · exp(-μ · x)

где:
  μ — линейный коэффициент ослабления [1/см]
  x — толщина вещества [см]
  μ/ρ — массовый коэффициент ослабления [см²/г]
```

### Сечение фотоэффекта, комптоновского рассеяния, рождения пар

Используются табличные данные NIST XCOM или аналитические формулы Клейна-Нишины.

---

## 6. Конфигурационный файл (пример)

```yaml
# config/simulation_example.yaml

simulation:
  n_particles: 100000           # количество частиц
  random_seed: 42               # для воспроизводимости
  parallel: true                # многопоточность
  n_threads: 4

source:
  particle_type: proton         # proton | electron | alpha | gamma | neutron
  energy_mode: monoenergetic    # monoenergetic | spectrum | gaussian
  energy_MeV: 10.0              # для monoenergetic
  # spectrum_file: data/spectra/ap8_max.dat  # для spectrum
  beam_type: parallel           # parallel | isotropic | pencil

shield:
  layers:
    - material: aluminum
      thickness_mm: 2.0
    - material: polyethylene
      thickness_mm: 10.0
    - material: lead
      thickness_mm: 1.5
    - material: aluminum
      thickness_mm: 1.0

output:
  results_dir: results/
  format: [csv, json, hdf5]
  plots: true
  plot_format: png
  verbose: true
```

---

## 7. Материалы базы данных (пример)

```json
{
  "materials": {
    "aluminum": {
      "Z": 13, "A": 26.98, "density": 2.70,
      "radiation_length_g_cm2": 24.01,
      "nuclear_interaction_length_g_cm2": 106.4,
      "ionization_potential_eV": 166.0
    },
    "lead": {
      "Z": 82, "A": 207.2, "density": 11.35,
      "radiation_length_g_cm2": 6.37,
      "nuclear_interaction_length_g_cm2": 199.6,
      "ionization_potential_eV": 823.0
    },
    "polyethylene": {
      "formula": "CH2", "density": 0.94,
      "radiation_length_g_cm2": 44.77,
      "ionization_potential_eV": 57.4
    },
    "silicon": {
      "Z": 14, "A": 28.09, "density": 2.33,
      "radiation_length_g_cm2": 21.82,
      "ionization_potential_eV": 173.0
    },
    "titanium": {
      "Z": 22, "A": 47.87, "density": 4.51,
      "radiation_length_g_cm2": 16.17,
      "ionization_potential_eV": 233.0
    },
    "water": {
      "formula": "H2O", "density": 1.00,
      "radiation_length_g_cm2": 36.08,
      "ionization_potential_eV": 75.0
    }
  }
}
```

---

## 8. Зависимости и технологический стек

### Основные зависимости (Python)

```
# requirements.txt

# Научные вычисления
numpy>=1.24.0           # массивы, линейная алгебра, случайные числа
scipy>=1.10.0           # специальные функции, интеграция, интерполяция
numba>=0.57.0           # JIT-компиляция критичных вычислений (опционально)

# Анализ и визуализация данных
pandas>=2.0.0           # работа с табличными данными / результатами
matplotlib>=3.7.0       # построение графиков (спектры, дозовые профили)
plotly>=5.15.0          # интерактивная визуализация (опционально)

# Хранение данных
h5py>=3.9.0             # формат HDF5 для больших результатов
pyyaml>=6.0             # чтение конфигурационных YAML файлов

# Параллельные вычисления
multiprocessing         # стандартная библиотека
joblib>=1.3.0           # параллелизация циклов (опционально)

# GUI (опционально)
PyQt5>=5.15.0           # или tkinter (стандартная библиотека)

# API (опционально)
fastapi>=0.100.0
uvicorn>=0.23.0

# Тестирование
pytest>=7.4.0
pytest-cov>=4.1.0

# Форматирование и линтинг
black>=23.0.0
flake8>=6.0.0
mypy>=1.4.0
```

### Внешние инструменты (опционально, для верификации)

| Инструмент | Назначение |
|---|---|
| **GEANT4** | Эталонный Monte Carlo код (CERN) для верификации |
| **SRIM/TRIM** | Таблицы пробегов ионов в веществе (Ziegler) |
| **NIST PSTAR/ESTAR** | Онлайн-таблицы пробегов протонов/электронов |
| **NIST XCOM** | Онлайн-таблицы сечений гамма-квантов |
| **FLUKA** | Альтернативный Monte Carlo код |
| **MCNP** | Код нейтронного транспорта (LANL) |

---

## 9. Запуск и использование

### Установка

```bash
# Клонировать репозиторий
git clone https://github.com/your-org/shield-protection-module.git
cd shield-protection-module

# Создать виртуальное окружение
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Установить зависимости
pip install -r requirements.txt
pip install -e .                 # установка в режиме разработки
```

### Запуск симуляции (CLI)

```bash
# Базовый запуск с конфигурационным файлом
python main.py --config config/simulation_example.yaml

# Быстрый запуск через аргументы командной строки
python main.py \
  --particle proton \
  --energy 10.0 \
  --shield "aluminum:2.0,lead:1.5,aluminum:1.0" \
  --n-particles 100000 \
  --output results/my_simulation/

# Запуск с визуализацией результатов
python main.py --config config/default_shield.yaml --plot

# Запуск набора тестов
pytest tests/ -v --cov=src --cov-report=html
```

### Использование как библиотеки (Python API)

```python
from src.core.material import Material, MaterialDatabase
from src.core.layer import Layer
from src.core.shield import Shield
from src.simulation.source import MonoenergeticSource, ParticleType
from src.simulation.monte_carlo import MonteCarloEngine

# Загрузить базу материалов
db = MaterialDatabase.load("data/materials/materials_db.json")

# Создать многослойный экран
shield = Shield([
    Layer(material=db["aluminum"],    thickness_mm=2.0),
    Layer(material=db["polyethylene"], thickness_mm=10.0),
    Layer(material=db["lead"],        thickness_mm=1.5),
    Layer(material=db["aluminum"],    thickness_mm=1.0),
])

# Настроить источник частиц
source = MonoenergeticSource(
    particle_type=ParticleType.PROTON,
    energy_MeV=10.0,
    n_particles=100_000,
)

# Запустить симуляцию
engine = MonteCarloEngine(shield=shield, source=source)
results = engine.run()

# Анализ результатов
print(f"Прошло частиц:    {results.n_transmitted} ({results.transmission_rate:.1%})")
print(f"Поглощено:         {results.n_absorbed}    ({results.absorption_rate:.1%})")
print(f"Средняя переданная энергия: {results.mean_transmitted_energy:.3f} МэВ")
print(f"Суммарная доза за экраном:  {results.total_dose_behind:.3e} Гр")

# Визуализация
results.plot_energy_spectrum(save_to="results/energy_spectrum.png")
results.plot_dose_profile(save_to="results/dose_profile.png")
results.save("results/simulation_output.hdf5")
```

---

## 10. Тестирование и верификация

### Стратегия тестирования

```
Уровни тестирования:
1. Unit-тесты       → отдельные физические формулы, классы
2. Интеграционные   → полный цикл симуляции
3. Верификация      → сравнение с аналитическими решениями
4. Валидация        → сравнение с GEANT4, SRIM, NIST данными
```

### Ключевые проверки (benchmark cases)

| Тест | Описание | Эталон |
|---|---|---|
| Пробег протона в Al | Протон 10 МэВ в алюминии | NIST PSTAR |
| Пробег электрона в Pb | Электрон 1 МэВ в свинце | NIST ESTAR |
| Ослабление гамма в Fe | Гамма 1 МэВ в железе | NIST XCOM |
| Пик Брэгга | Профиль дозы протонов в воде | GEANT4 |
| Кривая ослабления | Экспоненциальное ослабление гамма | Аналитика |

### Запуск тестов

```bash
# Все тесты
pytest tests/ -v

# Только физические unit-тесты
pytest tests/unit/test_physics.py -v

# Только benchmark-тесты с выводом таблицы
pytest tests/integration/test_benchmark.py -v --benchmark

# С отчётом покрытия
pytest tests/ --cov=src --cov-report=term-missing
```

---

## 11. Выходные данные и визуализация

### Форматы выходных файлов

| Формат | Содержимое | Использование |
|---|---|---|
| `results.json` | Итоговая статистика симуляции | Быстрый просмотр |
| `results.csv` | Энергетические спектры, профили | Сторонний анализ |
| `results.hdf5` | Полные треки частиц, все гистограммы | Детальный анализ |
| `report.pdf` | Сводный отчёт с графиками | Передача заказчику |

### Типовые графики

1. **Энергетический спектр прошедших частиц** — гистограмма E, [МэВ]
2. **Профиль поглощённой дозы по глубине** — D(x), [Гр/частицу]
3. **Зависимость коэффициента пропускания от энергии** — T(E), [%]
4. **Зависимость от толщины экрана** — T(d) при фиксированном E
5. **Сравнение материалов** — T(материал) при одинаковых условиях
6. **Пространственное распределение дозы** — 2D карта D(x,y)

---

## 12. Рабочий процесс разработки

### Соглашения по коду

- **Язык:** Python 3.10+
- **Форматирование:** `black` (строки до 88 символов)
- **Линтинг:** `flake8`, `mypy` (строгая типизация)
- **Именование:**
  - `snake_case` для функций и переменных
  - `PascalCase` для классов
  - `UPPER_SNAKE_CASE` для констант
  - Физические единицы **обязательно** в docstring и именах (`energy_MeV`, `thickness_mm`, `dose_Gy`)
- **Docstrings:** NumPy-стиль

```python
def bethe_bloch(particle: Particle, material: Material) -> float:
    """
    Вычислить тормозную способность по формуле Бете-Блоха.

    Parameters
    ----------
    particle : Particle
        Налетающая частица с заданной энергией и зарядом.
    material : Material
        Вещество, через которое проходит частица.

    Returns
    -------
    float
        Тормозная способность -dE/dx в единицах МэВ/(г/см²).

    References
    ----------
    Particle Data Group, Review of Particle Physics, 2022.
    Раздел 34: Passage of Particles through Matter.
    """
```

### Добавление нового материала

1. Найти физические параметры в NIST или PDG (Z, A, ρ, I, X₀, λ_I)
2. Добавить запись в `data/materials/materials_db.json`
3. Добавить unit-тест в `tests/unit/test_material.py`
4. Обновить `docs/api_reference.md`

### Добавление нового типа частицы

1. Добавить значение в `ParticleType` enum в `src/core/particle.py`
2. Реализовать физические процессы для этой частицы в `src/core/physics/`
3. Добавить сечения в `data/cross_sections/`
4. Обновить `MonteCarloEngine._sample_interaction()`
5. Написать тест `tests/unit/test_physics_<particle>.py`
6. Верифицировать по NIST/GEANT4

### Добавление нового физического процесса

1. Создать модуль в `src/core/physics/`
2. Реализовать интерфейс `PhysicsProcess` с методами:
   - `mean_free_path(particle, material) -> float`
   - `apply(particle, material) -> List[Particle]`
3. Зарегистрировать процесс в `MonteCarloEngine`
4. Добавить тесты и верификацию

---

## 13. Возможные проблемы и их решение

### Медленная симуляция (производительность)

- Использовать `numba` JIT для циклов трассировки частиц (`@numba.jit`)
- Включить параллельный режим (`parallel: true` в конфиге)
- Уменьшить `n_particles` для предварительного тестирования
- Использовать векторизацию NumPy вместо Python-циклов

### Статистические флуктуации в результатах

- Увеличить `n_particles` (минимум 10⁴, для точных результатов 10⁵–10⁶)
- Зафиксировать `random_seed` для воспроизводимости
- Оценить статистическую погрешность (σ ∝ 1/√N)

### Физически нереалистичные результаты

- Проверить единицы измерения (мм vs см, МэВ vs эВ)
- Убедиться в корректности потенциала ионизации I для материала
- Сравнить пробег частицы с таблицами NIST
- Включить `verbose: true` для детального логирования

### Ошибки при загрузке конфигурации

- Проверить синтаксис YAML (`yamllint config/`)
- Убедиться, что все указанные материалы есть в базе данных
- Убедиться, что толщины > 0 и энергия > 0

### Ошибки численной нестабильности

- При `energy < 0.01 МэВ` применить cutoff (частица остановлена)
- Проверить обработку граничных случаев (частица на границе слоёв)
- Использовать `np.float64` (не `float32`) для энергий

---

## 14. Справочные ресурсы

### Физика частиц

| Ресурс | URL / Ссылка |
|---|---|
| Particle Data Group (PDG) | https://pdg.lbl.gov |
| PDG: Passage of Particles through Matter | PDG Review, Chapter 34 |
| NIST PSTAR (протоны) | https://physics.nist.gov/PhysRefData/Star/Text/PSTAR.html |
| NIST ESTAR (электроны) | https://physics.nist.gov/PhysRefData/Star/Text/ESTAR.html |
| NIST XCOM (фотоны) | https://physics.nist.gov/PhysRefData/Xcom/html/xcom1.html |
| SRIM (ионы) | http://www.srim.org |
| ENDF/B ядерные данные | https://www.nndc.bnl.gov/endf |

### Симуляционные инструменты для верификации

| Инструмент | URL |
|---|---|
| GEANT4 | https://geant4.org |
| FLUKA | https://fluka.cern |
| MCNP | https://mcnp.lanl.gov |
| OpenMC | https://openmc.org |

### Книги и статьи

- Leo W.R. *Techniques for Nuclear and Particle Physics Experiments*. Springer, 1994
- Knoll G.F. *Radiation Detection and Measurement*. Wiley, 4th ed., 2010
- Agostinelli S. et al. *GEANT4 — a simulation toolkit*. NIM A 506 (2003) 250–303
- Ziegler J.F. *SRIM-2013*. NIM B 268 (2010) 1818–1823

### Радиационная стойкость электроники (применение)

- ECSS-E-ST-10-12C: *Methods for the Calculation of Radiation Received and its Effects*
- NASA SP-8116: *Radiation Design Margins for Electronic Parts*
- ESA SHIELDOSE-2: расчёт доз в экранированных объёмах

---

## 15. Глоссарий

| Термин | Определение |
|---|---|
| **ЛПЭ (LET)** | Линейная передача энергии — энергия, выделяемая частицей на единицу пути [МэВ·см²/г] |
| **Пробег (Range)** | Средняя длина пути частицы до полной остановки |
| **Поглощённая доза** | Энергия ионизирующего излучения, поглощённая единицей массы вещества [Гр = Дж/кг] |
| **Тормозная способность** | -dE/dx — потери энергии частицы на единицу пути [МэВ·см²/г] |
| **Коэффициент пропускания** | Доля частиц, прошедших через экран насквозь |
| **Поверхностная плотность** | Произведение плотности на толщину [г/см²] |
| **Длина радиационных потерь X₀** | Толщина, на которой электрон теряет 1/e энергии на тормозное излучение |
| **Пик Брэгга** | Максимум энерговыделения заряженной частицы вблизи конца пробега |
| **Вторичные частицы** | Частицы, рождённые в результате взаимодействий первичной частицы с веществом |
| **Метод Монте-Карло** | Численный метод случайного моделирования физических процессов |
| **SEE (Single Event Effect)** | Единичный эффект — сбой в микросхеме от одной частицы |
| **TID (Total Ionizing Dose)** | Полная ионизирующая доза, накопленная элементом за срок службы |
| **DDD (Displacement Damage Dose)** | Доза смещений — повреждения кристаллической решётки от нейтронов и тяжёлых ионов |

---

> **Примечание:** Этот файл должен обновляться по мере развития проекта.  
> При изменении архитектуры — обновить раздел 3.  
> При добавлении новых частиц/материалов — обновить разделы 4, 7, 15.  
> При изменении зависимостей — обновить раздел 8.