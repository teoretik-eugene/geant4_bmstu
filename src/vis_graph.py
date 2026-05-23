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
    import numpy as np
    grouped_tracks = {}
    particle_counts = {}
    total_tracks = 0
    shown_tracks = 0
    total_points = 0

    for particle_name, track_id, pts in track_iterable:
        if len(pts) < 2:
            continue

        total_tracks += 1
        particle_counts[particle_name] = particle_counts.get(particle_name, 0) + 1

        color = get_particle_color(particle_name)
        line_width = 2 if track_id == 1 else 1
        original_points = [tuple(map(float, p)) for p in pts]
        grouped_tracks.setdefault((color, line_width), []).append(original_points)
        total_points += len(original_points)

    for (color, line_width), tracks in grouped_tracks.items():
        mesh = _build_polyline_mesh(pv, tracks)
        if mesh is None:
            continue
        plotter.add_mesh(mesh, color=color, line_width=line_width)

    return {
        "particle_counts": particle_counts,
        "total_tracks": total_tracks,
        "shown_tracks": total_tracks,
        "shown_points": total_points,
        "skipped_tracks": 0,
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
    import numpy as np

    out_dir = f"{dir}/energy_analysis"
    os.makedirs(out_dir, exist_ok=True)
    layout = compute_layout(cfg=cfg, data=data)

    if hasattr(result, "energy_profiles") and result.energy_profiles:
        plt.figure(figsize=(10, 6))
        first_z = layout.get("first_screen_front_z_mm", 0) or 0
        end_z = layout.get("screens_end_z_mm", 0) or 0
        screen_thickness = end_z - first_z if end_z else 0
        profiles = list(result.energy_profiles.values())
        logging.info("plot result screen info: %s", result)
        logging.info("first_z: %s end_z: %s", first_z, end_z)

        z_data, e_data, colors, alphas = [], [], [], []

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

        for track_data in profiles:
            points = track_data.get("points", [])
            if len(points) < 2: continue

            z_abs = np.array([p[0] for p in points], dtype=np.float32)
            energies = np.array([p[1] for p in points], dtype=np.float32)
            z_rel = z_abs - first_z

            mask = (z_rel >= -5) & (z_rel <= screen_thickness + 10)
            zf, ef = z_rel[mask], energies[mask]
            if len(zf) < 2: continue

            p_type = track_data.get("particle", "unknown").lower()
            is_primary = track_data.get("parent_id", 0) == 0
            base_color = particle_colors.get(p_type, "gray" if not is_primary else "blue")

            z_data.extend(zf.tolist())
            e_data.extend(ef.tolist())
            colors.extend([base_color] * len(zf))
            alphas.extend([0.5 if is_primary else 0.3] * len(zf))

        if z_data:
            plt.scatter(z_data, e_data, c=colors, s=2, alpha=np.array(alphas), edgecolors='none')

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
        # --- разбираем на первичные / вторичные / по типам ---
        primary_exit:   list = []
        secondary_exit: list = []
        by_type: dict = {}

        for item in result.exit_energies:
            if isinstance(item, dict):
                e_mev  = float(item.get("energy_mev", 0.0))
                is_pri = bool(item.get("is_primary", True))
                ptype  = str(item.get("particle_type", "unknown"))
            else:
                e_mev, is_pri, ptype = float(item), True, "unknown"

            if is_pri:
                primary_exit.append(e_mev)
            else:
                secondary_exit.append(e_mev)
            by_type.setdefault(ptype, []).append(e_mev)

        # --- график 1: первичные vs вторичные ---
        fig, ax = plt.subplots(figsize=(9, 5))
        all_vals = primary_exit + secondary_exit
        if all_vals:
            import numpy as np
            bins = np.linspace(0, max(all_vals) * 1.05 + 1e-9, 31)
            if primary_exit:
                ax.hist(primary_exit,   bins=bins, alpha=0.75,
                        color="steelblue", label=f"Primary ({len(primary_exit)})")
            if secondary_exit:
                ax.hist(secondary_exit, bins=bins, alpha=0.60,
                        color="tomato",    label=f"Secondary ({len(secondary_exit)})")
        ax.set_xlabel("Exit Energy (MeV)")
        ax.set_ylabel("Counts")
        ax.set_title("Exit Energy Spectrum (Primary vs Secondary)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        filename = os.path.join(out_dir, "exit_energy_spectrum.png")
        plt.savefig(filename, dpi=300, bbox_inches="tight")
        plt.close()
        print(f"Saved: {filename}")

        # --- график 2: по типам частиц ---
        if len(by_type) > 1 or (len(by_type) == 1 and list(by_type.keys())[0] != "unknown"):
            fig, ax = plt.subplots(figsize=(9, 5))
            import numpy as np
            all_vals2 = [e for lst in by_type.values() for e in lst]
            bins2 = np.linspace(0, max(all_vals2) * 1.05 + 1e-9, 31) if all_vals2 else 30
            for ptype, energies in sorted(by_type.items()):
                color = get_particle_color(ptype)
                ax.hist(energies, bins=bins2, alpha=0.65,
                        color=color, label=f"{ptype} ({len(energies)})")
            ax.set_xlabel("Exit Energy (MeV)")
            ax.set_ylabel("Counts")
            ax.set_title("Exit Energy Spectrum by Particle Type")
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)
            filename = os.path.join(out_dir, "exit_energy_by_type.png")
            plt.savefig(filename, dpi=300, bbox_inches="tight")
            plt.close()
            print(f"Saved: {filename}")

    # --- график: сводная диаграмма защиты (Protection Summary) ---
    energy_summary = getattr(result, "energy_summary", None) or {}
    if energy_summary:
        _plot_protection_summary(energy_summary, out_dir)

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


def _plot_protection_summary(energy_summary: dict, out_dir: str) -> None:
    """Рисует сводную диаграмму защиты экрана из данных energy_summary."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    report     = energy_summary.get("screen_protection_report", {})
    fluence    = energy_summary.get("fluence_attenuation", {})
    bragg      = energy_summary.get("bragg_peak", {})
    elec_let   = energy_summary.get("electronics_let", {})
    primary    = energy_summary.get("primary_particles", {})
    secondary  = energy_summary.get("secondary_particles", {})
    residual   = energy_summary.get("residual_energy", {})
    criteria   = report.get("criteria", {})

    is_protected = report.get("is_protected", False)
    verdict_color = "#2ecc71" if is_protected else "#e74c3c"
    verdict_label = "ЗАЩИЩАЕТ" if is_protected else "НЕ ЗАЩИЩАЕТ"

    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor("#f8f9fa")

    # ── Заголовок / вердикт ─────────────────────────────────────────────
    ax_title = fig.add_axes([0.0, 0.88, 1.0, 0.12])
    ax_title.set_axis_off()
    ax_title.add_patch(plt.Rectangle((0, 0), 1, 1,
                                     facecolor=verdict_color, alpha=0.15,
                                     transform=ax_title.transAxes))
    ax_title.text(0.5, 0.65, f"Экран: {verdict_label}",
                  ha="center", va="center", fontsize=20, fontweight="bold",
                  color=verdict_color, transform=ax_title.transAxes)
    reason = report.get("reason", "")
    ax_title.text(0.5, 0.20, reason,
                  ha="center", va="center", fontsize=8, color="#444",
                  wrap=True, transform=ax_title.transAxes)

    # ── 1. Круговая диаграмма: судьба первичных частиц ──────────────────
    ax1 = fig.add_axes([0.02, 0.48, 0.28, 0.38])
    stopped    = primary.get("stopped_in_screen", 0)
    exited     = primary.get("exited_screen", 0)
    backsc     = primary.get("backscattered", 0)
    other      = max(0, primary.get("total", 0) - stopped - exited - backsc)
    pie_vals   = [stopped, exited, backsc, other]
    pie_labels = ["Остановились\nв экране", "Прошли\nнасквозь",
                  "Обратно\nрассеяны", "Прочее"]
    pie_colors = ["#2ecc71", "#e74c3c", "#f39c12", "#95a5a6"]
    non_zero   = [(v, l, c) for v, l, c in zip(pie_vals, pie_labels, pie_colors) if v > 0]
    if non_zero:
        vals, lbls, cols = zip(*non_zero)
        wedges, texts, autotexts = ax1.pie(
            vals, labels=lbls, colors=cols,
            autopct="%1.1f%%", startangle=90,
            textprops={"fontsize": 7}
        )
        for at in autotexts:
            at.set_fontsize(7)
    ax1.set_title("Судьба первичных частиц", fontsize=9, fontweight="bold")

    # ── 2. Барплот: частицы по слоям ────────────────────────────────────
    ax2 = fig.add_axes([0.35, 0.48, 0.30, 0.38])
    stopped_by_mat   = primary.get("stopped_by_material", {})
    stopped_sec_mat  = secondary.get("stopped_by_material", {})
    all_layers = sorted(set(list(stopped_by_mat.keys()) + list(stopped_sec_mat.keys())))
    if all_layers:
        x       = np.arange(len(all_layers))
        w       = 0.35
        prim_v  = [stopped_by_mat.get(i, 0)    for i in all_layers]
        sec_v   = [stopped_sec_mat.get(i, 0)   for i in all_layers]
        ax2.bar(x - w/2, prim_v, w, label="Первичные", color="#3498db", alpha=0.85)
        ax2.bar(x + w/2, sec_v,  w, label="Вторичные", color="#e67e22", alpha=0.85)
        ax2.set_xticks(x)
        ax2.set_xticklabels([f"Слой {i}" for i in all_layers], fontsize=8)
        ax2.set_ylabel("Кол-во частиц", fontsize=8)
        ax2.legend(fontsize=7)
    ax2.set_title("Остановки по слоям", fontsize=9, fontweight="bold")
    ax2.grid(axis="y", alpha=0.3)

    # ── 3. Gauge: коэффициент ослабления флюенса ─────────────────────────
    ax3 = fig.add_axes([0.68, 0.48, 0.30, 0.38])
    faf_pct   = fluence.get("fluence_attenuation_factor", 0.0) * 100.0
    stop_pct  = fluence.get("stopping_efficiency_percent", 0.0)
    bar_color = "#2ecc71" if faf_pct <= 5 else ("#f39c12" if faf_pct <= 20 else "#e74c3c")
    ax3.barh(["Прошли сквозь", "Задержано"],
             [faf_pct, stop_pct],
             color=["#e74c3c", "#2ecc71"], height=0.4, alpha=0.85)
    ax3.set_xlim(0, 100)
    ax3.set_xlabel("%", fontsize=8)
    for spine in ["top", "right"]:
        ax3.spines[spine].set_visible(False)
    ax3.text(faf_pct + 1,  0, f"{faf_pct:.1f}%",  va="center", fontsize=9, color="#e74c3c")
    ax3.text(stop_pct + 1, 1, f"{stop_pct:.1f}%", va="center", fontsize=9, color="#2ecc71")
    ax3.set_title("Ослабление флюенса первичных", fontsize=9, fontweight="bold")
    ax3.grid(axis="x", alpha=0.3)

    # ── 4. Таблица критериев ─────────────────────────────────────────────
    ax4 = fig.add_axes([0.02, 0.02, 0.60, 0.42])
    ax4.set_axis_off()
    ax4.set_title("Критерии защиты", fontsize=9, fontweight="bold", loc="left")
    rows = []
    # C1–C6 в единой таблице; C6 не дублируется в отдельном блоке
    crit_order = ["C1_fluence_attenuation", "C2_bragg_peak_inside",
                  "C3_dose", "C4_let", "C5_event_upset", "C6_secondary_fluence"]

    # Короткие метки критериев — чтобы не вылезали за рамки
    crit_short = {
        "C1_fluence_attenuation": "C1: Флюенс первичных",
        "C2_bragg_peak_inside":   "C2: Bragg внутри",
        "C3_dose":                "C3: Доза в электронике",
        "C4_let":                 "C4: LET макс.",
        "C5_event_upset":         "C5: Event upset",
        "C6_secondary_fluence":   "C6: Вторичный флюенс",
    }

    for k in crit_order:
        v      = criteria.get(k, {})
        passed = v.get("passed", False)
        status = "✓" if passed else "✗"
        val    = v.get("value")
        thr    = v.get("threshold")
        val_str = f"{val:.3g}" if isinstance(val, float) else str(val)
        thr_str = f"{thr:.3g}" if isinstance(thr, float) else str(thr)
        label   = crit_short.get(k, k)
        rows.append([status, label, val_str, thr_str])

    if rows:
        col_labels = ["✓/✗", "Критерий", "Значение", "Порог"]
        col_widths  = [0.06, 0.46, 0.24, 0.24]   # относительная ширина колонок
        tbl = ax4.table(
            cellText=rows,
            colLabels=col_labels,
            cellLoc="left",
            loc="upper left",
            bbox=[0, 0.25, 1, 0.75]            # оставляем 25% снизу под текст провалов
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#cccccc")
            # Задаём ширину колонок вручную
            cell.set_width(col_widths[c] if c < len(col_widths) else 0.1)
            if r == 0:
                cell.set_facecolor("#dde3ea")
                cell.set_text_props(fontweight="bold")
            elif r > 0:
                passed_row = rows[r - 1][0] == "✓"
                if c == 0:
                    cell.set_facecolor("#d5f5e3" if passed_row else "#fadbd8")
                    cell.set_text_props(
                        fontweight="bold",
                        color="#1a7a3a" if passed_row else "#c0392b"
                    )

        # Текст с причинами провала под таблицей
        failed_keys = [k for k in crit_order if not criteria.get(k, {}).get("passed", False)]
        if failed_keys:
            fail_lines = []
            for k in failed_keys:
                desc = criteria[k].get("description", "")
                # Обрезаем до 90 символов с переносом
                short = desc[:90] + ("…" if len(desc) > 90 else "")
                fail_lines.append(f"• {short}")
            fail_text = "\n".join(fail_lines)
            ax4.text(
                0.01, 0.22, fail_text,
                transform=ax4.transAxes,
                fontsize=6.5, color="#c0392b", va="top",
                wrap=False, linespacing=1.4
            )

        # ── 5. Блок: LET и доза ──────────────────────────────────────────────
    ax5 = fig.add_axes([0.65, 0.24, 0.33, 0.22])
    ax5.set_axis_off()
    ax5.set_title("Электроника", fontsize=9, fontweight="bold", loc="left")
    let_max  = elec_let.get("max_let_mev_cm2_mg")
    let_avg  = elec_let.get("avg_let_mev_cm2_mg")
    dose_gy  = elec_let.get("absorbed_dose_gy")
    dose_per_hit = elec_let.get("dose_per_hit_event_gy")
    edep_mev = elec_let.get("deposited_energy_mev")
    residual_primary = residual.get("primary_exit", {})
    lines = [
        ("Max LET (MeV·cm²/mg)",  f"{let_max:.4f}"     if let_max      is not None else "—"),
        ("Avg LET (MeV·cm²/mg)",  f"{let_avg:.4f}"     if let_avg      is not None else "—"),
        ("Доза суммарная (Gy)",    f"{dose_gy:.4e}"     if dose_gy      is not None else "—"),
        ("Доза/хит-событие (Gy)",  f"{dose_per_hit:.4e}" if dose_per_hit is not None else "—"),
        ("Edep (MeV)",             f"{edep_mev:.4f}"    if edep_mev     is not None else "—"),
        ("Прошло первичных",       str(residual_primary.get("count", 0))),
        ("Bragg peak внутри",      f"{bragg.get('inside_percent', 0.0):.1f}%"),
    ]
    y = 0.93
    for label, value in lines:
        ax5.text(0.02, y, label + ":", fontsize=7.5, color="#555",
                 transform=ax5.transAxes, va="top")
        ax5.text(0.65, y, value, fontsize=7.5, fontweight="bold", color="#222",
                 transform=ax5.transAxes, va="top")
        y -= 0.135

    # ── 6. Блок: вторичное излучение ─────────────────────────────────────
    # ── 6. Блок: вторичное излучение (числовые данные + барплот по типам) ──
    fa        = fluence
    spr_pct   = fa.get("secondary_production_percent", 0.0) or 0.0
    spr_thr_p = fa.get("secondary_fluence_threshold_pct", 10.0) or 10.0
    n_sec     = fa.get("secondary_exited", 0) or 0
    n_ev      = fa.get("events_total", 0) or 0
    by_type   = residual.get("by_particle_type") or {}

    # Если есть данные по типам — рисуем барплот внизу и текст над ним
    if by_type:
        type_names       = list(by_type.keys())[:6]
        type_counts_vals = [by_type[t].get("count", 0) for t in type_names]
        has_bar = any(v > 0 for v in type_counts_vals)
    else:
        has_bar = False

    # Позиции: если есть барплот — текстовый блок выше
    ax6_y     = 0.13 if has_bar else 0.02
    ax6_h     = 0.20
    ax6 = fig.add_axes([0.65, ax6_y, 0.33, ax6_h])
    ax6.set_axis_off()
    ax6.set_title("Вторичное излучение за экраном", fontsize=9, fontweight="bold", loc="left")

    sec_lines = [
        ("Вторичных за экраном", f"{n_sec} шт."),
        ("SPR (от событий)",      f"{spr_pct:.2f}%  (порог {spr_thr_p:.0f}%)"),
        ("Всего событий",         str(n_ev)),
    ]
    y6 = 0.78
    for label, value in sec_lines:
        ax6.text(0.02, y6, label + ":", fontsize=7.5, color="#555",
                 transform=ax6.transAxes, va="top")
        ax6.text(0.62, y6, value, fontsize=7.5, fontweight="bold",
                 color="#222", transform=ax6.transAxes, va="top")
        y6 -= 0.24

    # Барплот типов частиц под текстовым блоком
    if has_bar:
        ax6_bar = fig.add_axes([0.65, 0.02, 0.33, 0.10])
        bar_colors = [get_particle_color(t) for t in type_names]
        ax6_bar.barh(type_names, type_counts_vals,
                     color=bar_colors, alpha=0.80, height=0.5)
        ax6_bar.set_xlabel("Кол-во", fontsize=7)
        ax6_bar.tick_params(axis="y", labelsize=7)
        ax6_bar.tick_params(axis="x", labelsize=7)
        ax6_bar.set_title("Типы частиц за экраном", fontsize=7, loc="left")
        ax6_bar.grid(axis="x", alpha=0.3)

    plt.savefig(
        os.path.join(out_dir, "protection_summary.png"),
        dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    plt.close()
    print(f"Saved: {os.path.join(out_dir, 'protection_summary.png')}")


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
                pt_map = getattr(particle_result, "particle_types", None) or {}
                for track_key, pts in particle_result.tracks.items():
                    # Берём реальный тип частицы из particle_types.
                    # Если не найден — используем имя первичной частицы подпрогона.
                    particle_name = pt_map.get(track_key, particle_result.particle)
                    yield particle_name, track_key[1], pts

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
        f"({stats['shown_points']} original points)"
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
            pt_map = getattr(result, "particle_types", None) or {}
            for track_key, pts in result.tracks.items():
                # Берём реальный тип частицы из particle_types.
                # Если не найден — используем имя первичной частицы.
                pname = pt_map.get(track_key, particle_name)
                yield pname, track_key[1], pts

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
