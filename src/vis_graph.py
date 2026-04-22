from simulations import ParticleConfig, SimulationConfig, SingleParticleResult, SimulationResult
import logging
from utils import compute_layout
import os
import datetime

# Logger setup
logging.basicConfig(
    filename="app.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

DEFAULT_MAX_TRACKS = int(os.getenv("VIS_MAX_TRACKS", "4000"))
DEFAULT_MAX_POINTS_PER_TRACK = int(os.getenv("VIS_MAX_POINTS_PER_TRACK", "120"))
DEFAULT_MAX_TOTAL_POINTS = int(os.getenv("VIS_MAX_TOTAL_POINTS", "250000"))


def get_particle_color(particle_name):
    """Returns a color for a particle type."""
    color_map = {
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
        "primary": "blue",
        "unknown": "black",
    }
    particle_name = str(particle_name).lower()

    for key, color in color_map.items():
        if key.lower() == particle_name:
            return color

    for key, color in color_map.items():
        if key.lower() in particle_name or particle_name in key.lower():
            return color

    return color_map["unknown"]


def _downsample_track_points(points, max_points):
    if not points or len(points) < 2:
        return []

    max_points = max(2, int(max_points))
    if len(points) <= max_points:
        return [tuple(map(float, p)) for p in points]

    last_index = len(points) - 1
    sampled_indices = []
    for i in range(max_points):
        idx = round(i * last_index / (max_points - 1))
        if not sampled_indices or idx != sampled_indices[-1]:
            sampled_indices.append(idx)

    if sampled_indices[-1] != last_index:
        sampled_indices[-1] = last_index

    return [tuple(map(float, points[idx])) for idx in sampled_indices]


def _build_polyline_mesh(pv, batched_tracks):
    import numpy as np

    all_points = []
    line_cells = []
    offset = 0

    for pts in batched_tracks:
        if len(pts) < 2:
            continue
        arr = np.asarray(pts, dtype=float)
        n_points = arr.shape[0]
        all_points.append(arr)
        line_cells.append(
            np.concatenate(([n_points], np.arange(offset, offset + n_points, dtype=np.int64)))
        )
        offset += n_points

    if not all_points:
        return None

    poly = pv.PolyData()
    poly.points = np.vstack(all_points)
    poly.lines = np.concatenate(line_cells)
    return poly


def _add_tracks_batched(plotter, pv, track_iterable):
    grouped_tracks = {}
    particle_counts = {}
    total_tracks = 0
    shown_tracks = 0
    shown_points = 0
    skipped_tracks = 0

    for particle_name, track_id, pts in track_iterable:
        if len(pts) < 2:
            continue

        total_tracks += 1
        particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1

        if shown_tracks >= DEFAULT_MAX_TRACKS or shown_points >= DEFAULT_MAX_TOTAL_POINTS:
            skipped_tracks += 1
            continue

        simplified = _downsample_track_points(pts, DEFAULT_MAX_POINTS_PER_TRACK)
        remaining_points = DEFAULT_MAX_TOTAL_POINTS - shown_points
        if remaining_points < 2:
            skipped_tracks += 1
            continue
        if len(simplified) > remaining_points:
            simplified = _downsample_track_points(simplified, remaining_points)
        if len(simplified) < 2:
            skipped_tracks += 1
            continue

        color = get_particle_color(particle_name)
        line_width = 2 if track_id == 1 else 1
        grouped_tracks.setdefault((color, line_width), []).append(simplified)
        shown_tracks += 1
        shown_points += len(simplified)

    for (color, line_width), tracks in grouped_tracks.items():
        mesh = _build_polyline_mesh(pv, tracks)
        if mesh is None:
            continue
        plotter.add_mesh(mesh, color=color, line_width=line_width)

    return {
        "particle_counts": particle_counts,
        "total_tracks": total_tracks,
        "shown_tracks": shown_tracks,
        "shown_points": shown_points,
        "skipped_tracks": skipped_tracks,
    }


def _add_screen_geometry(plotter, pv, cfg: SimulationConfig, layout: dict, screen_materials: list):
    z_cursor = float(layout["first_screen_front_z_mm"])
    for i, mat in enumerate(screen_materials):
        th = float(mat.get("Width", 0)) / 1000.0
        center_z = z_cursor + 0.5 * th
        cube = pv.Cube(
            center=(0, 0, center_z),
            x_length=cfg.screen_xy_mm * 0.6,
            y_length=cfg.screen_xy_mm * 0.6,
            z_length=th,
        )
        plotter.add_mesh(cube, opacity=0.3, color="lightblue", name=f"Screen_{i}")
        z_cursor += th


def plot_energy_analysis(result: SimulationResult, cfg: SimulationConfig, data: dict, dir: str):
    import matplotlib.pyplot as plt

    out_dir = f"{dir}/energy_analysis"
    os.makedirs(out_dir, exist_ok=True)
    layout = compute_layout(cfg=cfg, data=data)

    if hasattr(result, "energy_profiles") and result.energy_profiles:
        plt.figure()
        first_z = layout["first_screen_front_z_mm"]
        end_z = layout["screens_end_z_mm"]
        screen_thickness = end_z - first_z if end_z else 0

        logging.info("plot result screen info: %s", result)
        logging.info("first_z: %s end_z: %s", first_z, end_z)

        if first_z is None:
            first_z = 0

        particle_colors = {
            "he3": "#1f77b4",
            "alpha": "#0d47a1",
            "proton": "#d62728",
            "neutron": "#7f7f7f",
            "e-": "#2ca02c",
            "gamma": "#ff7f0e",
        }
        type_counts = {}

        for track_data in result.energy_profiles.values():
            points = track_data["points"]
            if len(points) < 2:
                continue

            z_abs = [p[0] for p in points]
            energies = [p[1] for p in points]
            z_rel = [zi - first_z for zi in z_abs]

            filtered = [
                (zr, e)
                for zr, e in zip(z_rel, energies)
                if -5 <= zr <= (end_z - first_z + 10 if end_z else 100)
            ]

            if len(filtered) < 2:
                continue

            zf, ef = zip(*filtered)
            p_type = track_data.get("particle", "unknown").lower()
            is_primary = track_data.get("parent_id", 0) == 0

            base_color = particle_colors.get(p_type, "gray" if not is_primary else "blue")
            alpha = 0.5 if is_primary else 0.3
            lw = 1.5 if is_primary else 1.0

            if is_primary:
                type_counts[p_type] = type_counts.get(p_type, 0) + 1

            label = (
                f"{p_type.capitalize()} ({type_counts[p_type]})"
                if is_primary and type_counts[p_type] == 1
                else ""
            )

            plt.plot(zf, ef, color=base_color, alpha=alpha, linewidth=lw, label=label)

            if ef[-1] < 0.01:
                plt.scatter(zf[-1], ef[-1], color=base_color, s=15, zorder=5)

        handles, labels = plt.gca().get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        plt.legend(by_label.values(), by_label.keys(), loc="best", fontsize=8)

        plt.xlabel("Depth relative to shield start (mm)")
        plt.ylabel("Energy (MeV)")
        plt.title("Energy vs Depth")
        plt.grid(True, alpha=0.3)
        plt.axvline(0, linestyle="--", color="green", linewidth=1.5, label="Shield start")
        plt.axvline(screen_thickness, linestyle="--", color="orange", linewidth=1.5, label="Screen end")
        plt.xlim(-10, screen_thickness + 20)

        filename = os.path.join(out_dir, "energy_vs_depth.png")
        plt.savefig(filename, dpi=300, bbox_inches="tight")
        plt.close()
        print(f"Saved: {filename}")

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
        plt.grid(axis="y")

        filename = os.path.join(out_dir, "energy_deposition.png")
        plt.savefig(filename, dpi=300)
        plt.close()
        print(f"Saved: {filename}")

    print(f"\nAll plots saved in: {out_dir}")


def visualize_multi_particle_results(cfg: SimulationConfig, result: SimulationResult, input_data: dict, dir: str):
    """Visualization for multi-particle mode."""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista is not installed. Visualization is unavailable.")
        return

    try:
        pv.start_xvfb()
    except Exception:
        print("Warning: Xvfb is not available, using offscreen rendering")

    data = input_data
    layout = compute_layout(cfg, data)
    plotter = pv.Plotter(off_screen=True)

    _add_screen_geometry(plotter, pv, cfg, layout, data.get("Screen", {}).get("Materials", []))

    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color="red", name="Source")

    stats = {
        "particle_counts": {},
        "total_tracks": 0,
        "shown_tracks": 0,
        "shown_points": 0,
        "skipped_tracks": 0,
    }

    if result.particle_results:
        def iter_tracks():
            for particle_result in result.particle_results.values():
                if not particle_result.tracks:
                    continue
                for track_key, pts in particle_result.tracks.items():
                    yield particle_result.particle, track_key[1], pts

        stats = _add_tracks_batched(plotter, pv, iter_tracks())

    legend_text = "Particle Types:\n"
    for particle_name, count in sorted(stats["particle_counts"].items()):
        legend_text += f"{particle_name}: {count}\n"
    legend_text += f"\nShown tracks: {stats['shown_tracks']} / {stats['total_tracks']}"
    legend_text += f"\nShown points: {stats['shown_points']}"
    if stats["skipped_tracks"]:
        legend_text += f"\nSkipped tracks: {stats['skipped_tracks']}"

    plotter.add_text(legend_text, position="upper_right", font_size=8)
    plotter.add_text("Geant4 Multi-Particle Simulation", position="upper_edge", font_size=10)
    plotter.add_axes()

    os.makedirs(dir, exist_ok=True)

    html_filename = os.path.join(dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Visualization exported to {html_filename}")

    print("\nParticle type statistics:")
    for particle_name, count in sorted(stats["particle_counts"].items()):
        print(f"  {particle_name}: {count} tracks")
    print(
        f"Displayed {stats['shown_tracks']} of {stats['total_tracks']} tracks "
        f"({stats['shown_points']} points after simplification)"
    )


def visualize_single_particle_results(cfg: SimulationConfig, result: SimulationResult, input_data: dict, dir: str):
    """Visualization for single-particle mode."""
    try:
        import pyvista as pv
    except ImportError:
        print("PyVista is not installed. Visualization is unavailable.")
        return

    try:
        pv.start_xvfb()
    except Exception:
        print("Warning: Xvfb is not available, using offscreen rendering")

    layout = compute_layout(cfg, input_data)
    plotter = pv.Plotter(off_screen=True)

    _add_screen_geometry(plotter, pv, cfg, layout, input_data.get("Screen", {}).get("Materials", []))

    source_z = max(layout["first_screen_front_z_mm"] - 10.0, -layout["half_world_z_mm"] + 1.0)
    sphere = pv.Sphere(center=(0, 0, source_z), radius=0.2)
    plotter.add_mesh(sphere, color="red", name="Source")

    particle_name = cfg.particles[0].name if cfg.particles else cfg.particle
    stats = {
        "particle_counts": {},
        "total_tracks": 0,
        "shown_tracks": 0,
        "shown_points": 0,
        "skipped_tracks": 0,
    }
    if result.tracks:
        def iter_tracks():
            for track_key, pts in result.tracks.items():
                yield particle_name, track_key[1], pts

        stats = _add_tracks_batched(plotter, pv, iter_tracks())

    legend_text = f"Particle: {particle_name}\n"
    legend_text += f"Energy: {cfg.particles[0].energy_mev if cfg.particles else cfg.energy_mev} MeV\n"
    legend_text += f"Shown tracks: {stats['shown_tracks']} / {stats['total_tracks']}\n"
    legend_text += f"Shown points: {stats['shown_points']}"
    if stats["skipped_tracks"]:
        legend_text += f"\nSkipped tracks: {stats['skipped_tracks']}"

    plotter.add_text(legend_text, position="upper_right", font_size=8)
    plotter.add_text("Geant4 Simulation", position="upper_edge", font_size=10)
    plotter.add_axes()

    os.makedirs(dir, exist_ok=True)

    html_filename = os.path.join(dir, f"simulation_task_{cfg.task_id}.html")
    plotter.export_html(html_filename)
    print(f"Visualization exported to {html_filename}")
