def plot_energy_analysis(result: SimulationResult, cfg: SimulationConfig):
    import matplotlib.pyplot as plt
    import os
    import datetime

    # Создаем папку
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = f"out/energy_analysis_{timestamp}"
    os.makedirs(out_dir, exist_ok=True)

    # =============================
    # 📈 1. Energy vs Depth
    # =============================
    if hasattr(result, "energy_profiles") and result.energy_profiles:
        plt.figure()

        for track_key, data in result.energy_profiles.items():
            if len(data) < 2:
                continue

            z = [p[0] for p in data]
            E = [p[1] for p in data]

            plt.plot(z, E, alpha=0.3)

        plt.xlabel("Depth (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth")
        plt.grid()

        filename = os.path.join(out_dir, "energy_vs_depth.png")
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # 📊 2. Energy spectrum (exit)
    # =============================
    if hasattr(result, "exit_energies") and result.exit_energies:
        plt.figure()

        plt.hist(result.exit_energies, bins=30)

        plt.xlabel("Energy (MeV)")
        plt.ylabel("Counts")
        plt.title("Exit Energy Spectrum")
        plt.grid()

        filename = os.path.join(out_dir, "exit_energy_spectrum.png")
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    # =============================
    # 📦 3. Energy deposition per layer
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
        plt.savefig(filename)
        plt.close()
        print(f"Saved: {filename}")

    print(f"\n📁 All plots saved in: {out_dir}")