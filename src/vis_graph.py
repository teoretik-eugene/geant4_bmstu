from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
import logging
from utils import compute_layout

# Настройка логгера
logging.basicConfig(
    filename='app.log',
    filemode='w',  # 'w' - перезапись, 'a' - добавление (по умолчанию)
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

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

        plt.xlabel("Depth relative to screen start (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth")
        plt.grid(True, alpha=0.3)
        plt.axvline(0, linestyle="--", color="green", linewidth=1.5, label="Screen start")
        plt.axvline(screen_thickness, linestyle="--", color="orange", linewidth=1.5, label="Screen end")
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