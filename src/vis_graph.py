from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
import logging
from utils import compute_layout
import os, datetime

# Настройка логгера
logging.basicConfig(
    filename='app.log',
    filemode='w',  # 'w' - перезапись, 'a' - добавление (по умолчанию)
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_particle_color(particle_name):
        """Возвращает цвет для конкретного типа частицы"""
        color_map = {
            # Первичные частицы
            "he3": "blue",
            "alpha": "darkblue",
            "proton": "red",
            "neutron": "gray",
            "e-": "green",
            "e+": "lightgreen",
            "gamma": "yellow",
            "mu-": "purple",
            "mu+": "violet",
            "pi+": "orange",
            "pi-": "darkorange",
            "kaon+": "brown",
            "kaon-": "sandybrown",
            "deuteron": "cyan",
            "triton": "darkcyan",
            # По умолчанию
            "primary": "blue",
            "unknown": "black"
        }
        particle_name = str(particle_name).lower()

        for key, color in color_map.items():
            if key.lower() == particle_name:
                return color
            
        for key, color in color_map.items():
            if key.lower() in particle_name or particle_name in key.lower():
                return color
            
        return color_map["unknown"]

def plot_energy_analysis(result: SimulationResult, cfg: SimulationConfig, data: dict, dir: str):
    import matplotlib.pyplot as plt
    import os

    # Создаем папку
    out_dir = f"{dir}/energy_analysis"
    os.makedirs(out_dir, exist_ok=True)
    layout = compute_layout(cfg=cfg, data=data)
    # =============================
    # Energy vs Depth
    # =============================
    if hasattr(result, "energy_profiles") and result.energy_profiles:
        plt.figure()
        first_z = None
        end_z = None

        logging.info(f'plot result screen info: {result}')

        
        first_z = layout["first_screen_front_z_mm"]
        end_z = layout["screens_end_z_mm"]
        screen_thickness = end_z - first_z if end_z else 0

        logging.info(f'first_z :{first_z}\tend_z: {end_z}')

        logging.info(f'first screen coord: {first_z}')

        if first_z is None:
            first_z = 0

        for track_key, track_data in result.energy_profiles.items():
            points = track_data["points"]
            if len(points) < 2:
                continue

            z_abs = [p[0] for p in points]
            E = [p[1] for p in points]

            z_rel = [zi - first_z for zi in z_abs]

            # фильтр: внутри экрана + немного после
            filtered = [
                (zr, e) for zr, e in zip(z_rel, E)
                if -5 <= zr <= (end_z - first_z + 10 if end_z else 100)
            ]

            if len(filtered) < 2:
                continue
            
            zf, Ef = zip(*filtered)

            if track_data["parent_id"] == 0:
                # первичные
                plt.plot(zf, Ef, color="blue", alpha=0.6, linewidth=1.5, label="Primary" 
                         if track_key == list(result.energy_profiles.keys())[0] else "")
            else:
                # вторичные
                plt.plot(zf, Ef, color="red", alpha=0.3, linewidth=1, label="Secondary" 
                         if track_key == list(result.energy_profiles.keys())[0] else "")

        plt.xlabel("Depth relative to shield start (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth")
        plt.grid(True, alpha=0.3)
        plt.axvline(0, linestyle="--", color="green", linewidth=1.5, label="Shield start")
        plt.axvline(screen_thickness, linestyle="--", color="orange", linewidth=1.5, label="Shield end")
        plt.legend(loc="best")
        plt.xlim(-10, screen_thickness + 20)
        
        filename = os.path.join(out_dir, "energy_vs_depth.png")
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # Energy spectrum (exit)
    # =============================
    if hasattr(result, "exit_energies") and result.exit_energies:
        plt.figure()

        plt.hist(result.exit_energies, bins=30)

        plt.xlabel("Energy (MeV)")
        plt.ylabel("Counts")
        plt.title("Exit Energy Spectrum")
        plt.grid()

        filename = os.path.join(out_dir, "exit_energy_spectrum.png")
        plt.savefig(filename, dpi=300)
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # Energy deposition per layer
    # =============================
    if result.screen_info and "Materials" in result.screen_info:
        materials = result.screen_info["Materials"]

        names = []
        edep = []

        for mat in materials:
            names.append(mat["Name"])
            edep.append(mat.get("Edep", 0))

        plt.figure()

        plt.bar(names, edep)

        plt.xlabel("Material")
        plt.ylabel("Deposited Energy (MeV)")
        plt.title("Energy Deposition per Layer")
        plt.xticks(rotation=30)
        plt.grid(axis='y')

        filename = os.path.join(out_dir, "energy_deposition.png")
        plt.savefig(filename, dpi=300)
        plt.close()
        print(f"Saved: {filename}")

    print(f"\nAll plots saved in: {out_dir}")

def visualize_multi_particle_results(cfg: SimulationConfig, result: SimulationResult, input_data: dict, dir: str):
    """Визуализация результатов для мульти-частичного режима"""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista не установлен. Визуализация невозможна.")
        return
    
    try:
        pv.start_xvfb()
    except:
        print("Предупреждение: Xvfb не запущен, используем offscreen режим")
    
    data = input_data
    layout = compute_layout(cfg, data)
    
    plotter = pv.Plotter()
    
    # Рисуем экраны
    z_cursor = float(layout["first_screen_front_z_mm"])
    for i, mat in enumerate(data.get('Screen', {}).get('Materials', [])):
        th = float(mat.get("Width", 0)) / 1000.0  # мкм → мм
        center_z = z_cursor + 0.5 * th
        cube = pv.Cube(
            center=(0, 0, center_z),
            x_length=cfg.screen_xy_mm * 0.6,
            y_length=cfg.screen_xy_mm * 0.6,
            z_length=th
        )
        plotter.add_mesh(cube, opacity=0.3, color='lightblue', name=f"Screen_{i}")
        z_cursor += th

    # Рисуем источник
    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color='red', name="Source")

    # Собираем статистику по типам частиц
    particle_counts = {}
    
    # Визуализируем треки из всех particle_results
    if result.particle_results:
        for key, particle_result in result.particle_results.items():
            if particle_result.tracks:
                for track_key, pts in particle_result.tracks.items():
                    if len(pts) >= 2:
                        event_id, track_id = track_key
                        particle_name = particle_result.particle
                        
                        # Считаем статистику
                        particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1
                        
                        # Получаем цвет
                        color = get_particle_color(particle_name)
                        
                        # Толщина линии: первичные частицы толще
                        line_width = 2 if track_id == 1 else 1
                        
                        # Создаем линию
                        line = pv.lines_from_points(pts)
                        plotter.add_mesh(
                            line, 
                            color=color, 
                            line_width=line_width,
                            name=f"{particle_name}_{event_id}_{track_id}"
                        )

    # Добавляем информационную панель
    legend_text = "Particle Types:\n"
    for particle_name, count in particle_counts.items():
        color = get_particle_color(particle_name)
        legend_text += f"{particle_name}: {count}\n"
    
    plotter.add_text(legend_text, position='upper_right', font_size=8)
    plotter.add_text(f"Geant4 Multi-Particle Simulation", 
                    position='upper_edge', font_size=10)
    plotter.add_axes()
    
    # Создаем директорию для результатов    
    os.makedirs(dir, exist_ok=True)
    
    # Сохраняем HTML
    html_filename = os.path.join(dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Визуализация экспортирована в {html_filename}")
    
    # Выводим статистику
    print("\nParticle type statistics:")
    for particle_name, count in sorted(particle_counts.items()):
        print(f"  {particle_name}: {count} tracks")

def visualize_single_particle_results(cfg: SimulationConfig, result: SimulationResult, input_data: dict, dir: str):
    """Визуализация для одиночной частицы"""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista не установлен. Визуализация невозможна.")
        return
    
    try:
        pv.start_xvfb()
    except:
        print("Предупреждение: Xvfb не запущен, используем offscreen режим")
    
    # Загружаем данные для вычисления layout
    layout = compute_layout(cfg, input_data)
    
    plotter = pv.Plotter()
    
    # Рисуем экраны
    z_cursor = float(layout["first_screen_front_z_mm"])
    for i, mat in enumerate(input_data.get('Screen', {}).get('Materials', [])):
        th = float(mat.get("Width", 0)) / 1000.0  # мкм → мм
        center_z = z_cursor + 0.5 * th
        cube = pv.Cube(
            center=(0, 0, center_z),
            x_length=cfg.screen_xy_mm * 0.6,
            y_length=cfg.screen_xy_mm * 0.6,
            z_length=th
        )
        plotter.add_mesh(cube, opacity=0.3, color='lightblue', name=f"Screen_{i}")
        z_cursor += th

    # Рисуем источник
    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color='red', name="Source")

    # Визуализируем треки
    particle_counts = {}
    if result.tracks:
        for track_key, pts in result.tracks.items():
            if len(pts) >= 2:
                event_id, track_id = track_key
                particle_name = cfg.particles[0].name if cfg.particles else cfg.particle
                
                # Считаем статистику
                particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1
                
                # Получаем цвет
                color = get_particle_color(particle_name)
                
                # Толщина линии
                line_width = 2 if track_id == 1 else 1
                
                # Создаем линию
                line = pv.lines_from_points(pts)
                plotter.add_mesh(
                    line, 
                    color=color, 
                    line_width=line_width,
                    name=f"{particle_name}_{event_id}_{track_id}"
                )

    # Добавляем информационную панель
    legend_text = f"Particle: {cfg.particles[0].name if cfg.particles else cfg.particle}\n"
    legend_text += f"Energy: {cfg.particles[0].energy_mev if cfg.particles else cfg.energy_mev} MeV\n"
    legend_text += f"Total tracks: {len(result.tracks) if result.tracks else 0}"
    
    plotter.add_text(legend_text, position='upper_right', font_size=8)
    plotter.add_text(f"Geant4 Simulation", 
                    position='upper_edge', font_size=10)
    plotter.add_axes()
    
    # Создаем директорию для результатов
    os.makedirs(dir, exist_ok=True)
    
    # Сохраняем HTML
    html_filename = os.path.join(dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Визуализация экспортирована в {html_filename}")