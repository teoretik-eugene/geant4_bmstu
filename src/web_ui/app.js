const defaultLayers = [
  {
    name: "Ti layer",
    description: "Titanium layer",
    width_um: 3000,
    material_type: "metal",
    density_g_cm3: null,
    elements: [
      {
        name: "Titanium",
        symbol: "Ti",
        atomic_number: 22,
        standard_atomic_weight: 47.87,
        density_g_cm3: 4.5,
        percentage: 100,
        n_atoms: null,
      },
    ],
  },
  {
    name: "Kapton",
    description: "Polyimide compound layer",
    width_um: 1000,
    material_type: "compound",
    density_g_cm3: 1.42,
    elements: [
      { name: "Carbon", symbol: "C", atomic_number: 6, standard_atomic_weight: 12.011, density_g_cm3: null, percentage: null, n_atoms: 22 },
      { name: "Hydrogen", symbol: "H", atomic_number: 1, standard_atomic_weight: 1.008, density_g_cm3: null, percentage: null, n_atoms: 10 },
      { name: "Nitrogen", symbol: "N", atomic_number: 7, standard_atomic_weight: 14.007, density_g_cm3: null, percentage: null, n_atoms: 2 },
      { name: "Oxygen", symbol: "O", atomic_number: 8, standard_atomic_weight: 15.999, density_g_cm3: null, percentage: null, n_atoms: 5 },
    ],
  },
];

let layerCounter = 0;
let availableParticles = ["He3", "e-", "proton", "alpha", "neutron", "gamma"];
let defaultParticle = "He3";
const defaultBeamParticles = [{ name: "He3", energy_mev: 40, weight: 1 }];

function setStatus(text, ok = true) {
  const s = document.getElementById("status");
  s.className = "status " + (ok ? "ok" : "err");
  s.textContent = text;
}

function getDefaultElement(materialType) {
  if (materialType === "compound") {
    return {
      name: "",
      symbol: "C",
      atomic_number: 6,
      standard_atomic_weight: 12.011,
      density_g_cm3: null,
      percentage: null,
      n_atoms: 1,
    };
  }

  return {
    name: "",
    symbol: "Al",
    atomic_number: 13,
    standard_atomic_weight: 26.98,
    density_g_cm3: 2.7,
    percentage: 100,
    n_atoms: null,
  };
}

function addElementRow(tableBody, materialType, element = null) {
  const el = element || getDefaultElement(materialType);
  const row = document.createElement("tr");
  row.className = "element-row";
  row.innerHTML = `
    <td><input data-field="name" value="${el.name ?? ""}"></td>
    <td><input data-field="symbol" value="${el.symbol ?? ""}"></td>
    <td><input data-field="atomic_number" type="number" value="${el.atomic_number ?? 0}" min="0" step="1"></td>
    <td><input data-field="standard_atomic_weight" type="number" value="${el.standard_atomic_weight ?? 0}" min="0" step="0.0001"></td>
    <td data-col="density"><input data-field="density_g_cm3" type="number" value="${el.density_g_cm3 ?? ""}" min="0" step="0.0001"></td>
    <td data-col="percentage"><input data-field="percentage" type="number" value="${el.percentage ?? ""}" min="0.0001" step="0.1"></td>
    <td data-col="n_atoms"><input data-field="n_atoms" type="number" value="${el.n_atoms ?? ""}" min="1" step="1"></td>
  `;
  tableBody.appendChild(row);
}

function updateElementRowsForType(card, materialType) {
  const rows = card.querySelectorAll(".element-row");
  rows.forEach((row) => {
    const densityInput = row.querySelector('[data-field="density_g_cm3"]');
    const percentageInput = row.querySelector('[data-field="percentage"]');
    const nAtomsInput = row.querySelector('[data-field="n_atoms"]');

    if (materialType === "compound") {
      densityInput.value = "";
      percentageInput.value = "";
      nAtomsInput.value = nAtomsInput.value || "1";
    } else {
      densityInput.value = densityInput.value || "1";
      percentageInput.value = percentageInput.value || "100";
      nAtomsInput.value = "";
    }
  });
}

function applyLayerTypeState(card) {
  const materialType = card.querySelector('[data-layer-field="material_type"]').value;
  const isCompound = materialType === "compound";
  card.dataset.materialType = materialType;

  card.querySelectorAll('[data-col="density"]').forEach((cell) => cell.classList.toggle("hidden-col", isCompound));
  card.querySelectorAll('[data-col="percentage"]').forEach((cell) => cell.classList.toggle("hidden-col", isCompound));
  card.querySelectorAll('[data-col="n_atoms"]').forEach((cell) => cell.classList.toggle("hidden-col", !isCompound));

  const materialDensityBlock = card.querySelector(".material-density-block");
  if (materialDensityBlock) {
    materialDensityBlock.classList.toggle("hidden-block", !isCompound);
  }

  const hint = card.querySelector(".layer-type-hint");
  if (hint) {
    hint.textContent = isCompound
      ? "Compound: set material density and NAtoms for each element. Percentages are computed automatically."
      : "Metal/alloy: set element density and percentage for each element.";
  }

  updateElementRowsForType(card, materialType);
}

function addLayer(layer = null) {
  const lay = layer || {
    name: "Layer",
    description: "",
    width_um: 1000,
    material_type: "metal",
    density_g_cm3: null,
    elements: [getDefaultElement("metal")],
  };

  layerCounter += 1;
  const card = document.createElement("div");
  card.className = "layer-card";
  card.dataset.layerId = String(layerCounter);
  card.innerHTML = `
    <div class="layer-head">
      <div>
        <label>Layer name</label>
        <input data-layer-field="name" value="${lay.name}">
      </div>
      <div>
        <label>Description</label>
        <input data-layer-field="description" value="${lay.description || ""}">
      </div>
      <div>
        <label>Width (um)</label>
        <input data-layer-field="width_um" type="number" value="${lay.width_um}" min="0.0001" step="1">
      </div>
      <div>
        <label>Material type</label>
        <select data-layer-field="material_type">
          <option value="metal" ${lay.material_type === "metal" ? "selected" : ""}>Metal</option>
          <option value="alloy" ${lay.material_type === "alloy" ? "selected" : ""}>Alloy</option>
          <option value="compound" ${lay.material_type === "compound" ? "selected" : ""}>Compound</option>
        </select>
      </div>
      <div class="material-density-block">
        <label>Material density (g/cm3)</label>
        <input data-layer-field="density_g_cm3" type="number" value="${lay.density_g_cm3 ?? ""}" min="0.0001" step="0.0001">
      </div>
    </div>
    <div class="muted layer-type-hint"></div>
    <table class="elements">
      <thead>
        <tr>
          <th>Name</th>
          <th>Symbol</th>
          <th>Atomic number</th>
          <th>Std atomic weight</th>
          <th data-col="density">Element density (g/cm3)</th>
          <th data-col="percentage">Percentage</th>
          <th data-col="n_atoms">NAtoms</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
    <div class="btns">
      <button class="add add-element-btn" type="button">+ Add element</button>
      <button class="remove remove-element-btn" type="button">- Remove last element</button>
    </div>
  `;

  const body = card.querySelector("tbody");
  (lay.elements || []).forEach((el) => addElementRow(body, lay.material_type, el));
  if (!body.children.length) {
    addElementRow(body, lay.material_type);
  }

  card.querySelector('[data-layer-field="material_type"]').addEventListener("change", () => {
    applyLayerTypeState(card);
  });

  document.getElementById("layersContainer").appendChild(card);
  applyLayerTypeState(card);
}

function removeLayer() {
  const container = document.getElementById("layersContainer");
  const cards = container.querySelectorAll(".layer-card");
  if (cards.length > 0) {
    container.removeChild(cards[cards.length - 1]);
  }
}

function addElement(button) {
  const card = button.closest(".layer-card");
  const body = card.querySelector("tbody");
  const materialType = card.querySelector('[data-layer-field="material_type"]').value;
  addElementRow(body, materialType);
  applyLayerTypeState(card);
}

function removeElement(button) {
  const card = button.closest(".layer-card");
  const body = card.querySelector("tbody");
  const rows = body.querySelectorAll(".element-row");
  if (rows.length > 0) {
    body.removeChild(rows[rows.length - 1]);
  }
}

function readLayers() {
  const cards = [...document.querySelectorAll(".layer-card")];
  return cards.map((card) => {
    const getLayerValue = (field) => card.querySelector(`[data-layer-field="${field}"]`).value;
    const material_type = getLayerValue("material_type");
    const densityValue = getLayerValue("density_g_cm3");
    const elements = [...card.querySelectorAll(".element-row")].map((row) => {
      const getVal = (field) => row.querySelector(`[data-field="${field}"]`).value;
      const base = {
        name: getVal("name").trim(),
        symbol: getVal("symbol").trim(),
        atomic_number: Number(getVal("atomic_number")),
        standard_atomic_weight: Number(getVal("standard_atomic_weight")),
      };

      if (material_type === "compound") {
        return {
          ...base,
          n_atoms: Number(getVal("n_atoms")),
        };
      }

      return {
        ...base,
        density_g_cm3: Number(getVal("density_g_cm3")),
        percentage: Number(getVal("percentage")),
      };
    });

    return {
      name: getLayerValue("name").trim(),
      description: getLayerValue("description").trim(),
      width_um: Number(getLayerValue("width_um")),
      material_type,
      density_g_cm3: material_type === "compound" && densityValue !== "" ? Number(densityValue) : null,
      elements,
    };
  });
}

function buildParticleOptions(selected) {
  return availableParticles
    .map((p) => `<option value="${p}" ${p === selected ? "selected" : ""}>${p}</option>`)
    .join("");
}

function addParticleRow(particle = null) {
  const p = particle || { name: defaultParticle, energy_mev: 40, weight: 1 };
  const row = document.createElement("tr");
  row.className = "particle-row";
  row.innerHTML = `
    <td><select data-particle-field="name">${buildParticleOptions(p.name)}</select></td>
    <td><input data-particle-field="energy_mev" type="number" value="${p.energy_mev}" min="0.0001" step="0.1"></td>
    <td><input data-particle-field="weight" type="number" value="${p.weight}" min="0.0001" step="0.1"></td>
  `;
  const tbody = document.querySelector("#particlesTable tbody");
  if (!tbody) return;
  tbody.appendChild(row);
}

function removeParticleRow() {
  const body = document.querySelector("#particlesTable tbody");
  if (!body) return;
  const rows = body.querySelectorAll(".particle-row");
  if (rows.length > 0) {
    body.removeChild(rows[rows.length - 1]);
  }
}

function readParticles() {
  return [...document.querySelectorAll("#particlesTable tbody .particle-row")].map((row) => {
    const get = (field) => row.querySelector(`[data-particle-field="${field}"]`);
    return {
      name: get("name").value,
      energy_mev: Number(get("energy_mev").value),
      weight: Number(get("weight").value),
    };
  });
}

function renderKpi(report) {
  const kpis = document.getElementById("kpis");
  const pct = (value) => (value * 100).toFixed(2) + "%";
  const num = (value, digits = 4) =>
    value === null || value === undefined || Number.isNaN(Number(value))
      ? "n/a"
      : Number(value).toFixed(digits);
  kpis.innerHTML = `
    <div class="kpi">Total particles<strong>${report.total_particles}</strong></div>
    <div class="kpi">Primary out<strong>${report.out_primary_particles}</strong></div>
    <div class="kpi">Primary stopped<strong>${report.stopped_primary_particles}</strong></div>
    <div class="kpi">Secondary out<strong>${report.out_secondary_particles}</strong></div>
    <div class="kpi">Transmission<strong>${pct(report.transmission_rate)}</strong></div>
    <div class="kpi">Stopping efficiency<strong>${pct(report.stopping_efficiency)}</strong></div>
    <div class="kpi">Dose (Gy)<strong>${num(report.dose_gy, 6)}</strong></div>
    <div class="kpi">LET (MeV*cm2/mg)<strong>${num(report.let_mev_cm2_mg, 4)}</strong></div>
  `;
}

function renderLayerReport(report) {
  const lines = report.layers.map(
    (x) =>
      `${x.index}. ${x.name}: ${x.thickness_mm.toFixed(3)} mm | primary_stuck=${x.primary_stuck}, secondary_stuck=${x.secondary_stuck}, Edep=${x.edep_mev.toFixed(4)} MeV`,
  );
  const particleLines = [];
  const particleResults = report.particle_results || {};
  for (const [key, value] of Object.entries(particleResults)) {
    const total = Number(value.total_particles || 0);
    const out = Number(value.total_out_primary_particles || 0);
    const rate = total > 0 ? ((out / total) * 100).toFixed(2) : "0.00";
    particleLines.push(`${key}: total=${total}, out_primary=${out}, transmission=${rate}%`);
  }
  const full = [...lines];
  if (particleLines.length) {
    full.push("");
    full.push("Per-particle:");
    full.push(...particleLines);
  }
  if (report.dose_gy !== undefined || report.let_mev_cm2_mg !== undefined) {
    full.push("");
    full.push("Electronics:");
    full.push(
      `Dose: ${
        report.dose_gy === null || report.dose_gy === undefined ? "n/a" : Number(report.dose_gy).toFixed(6)
      } Gy (threshold: ${
        report.dose_threshold_gy === null || report.dose_threshold_gy === undefined
          ? "n/a"
          : Number(report.dose_threshold_gy).toFixed(6)
      } Gy, verdict: ${report.dose_verdict || "unknown"})`,
    );
    full.push(
      `LET max: ${
        report.let_mev_cm2_mg === null || report.let_mev_cm2_mg === undefined
          ? "n/a"
          : Number(report.let_mev_cm2_mg).toFixed(4)
      } MeV*cm2/mg (threshold: ${
        report.let_threshold_mev_cm2_mg === null || report.let_threshold_mev_cm2_mg === undefined
          ? "n/a"
          : Number(report.let_threshold_mev_cm2_mg).toFixed(4)
      } MeV*cm2/mg, risk: ${report.let_verdict || "unknown"})`,
    );
  }
  document.getElementById("layerReport").textContent = full.join("\n") || "No data";
}

function renderFiles(files) {
  const holder = document.getElementById("files");
  if (!files || !files.length) {
    holder.innerHTML = "<span class='muted'>No generated images.</span>";
    return;
  }
  holder.innerHTML = files
    .map((f) => {
      const label = f.kind === "scene_3d" ? `3D scene: ${f.name}` : f.name;
      return `<a href="${f.web_url}" target="_blank">${label}</a><div class="muted">${f.abs_path}</div>`;
    })
    .join("");
}

async function loadParticles() {
  try {
    const res = await fetch("/api/particles");
    const data = await res.json();
    availableParticles = data.particles || availableParticles;
    defaultParticle = data.default || defaultParticle;
  } catch (e) {
    // fallback to built-in list
  }
  const body = document.querySelector("#particlesTable tbody");
  body.innerHTML = "";
  defaultBeamParticles.forEach((p) => addParticleRow(p));
}

async function runSimulation() {
  const particles = readParticles();
  if (!particles.length) {
    setStatus("Error: add at least one particle.", false);
    return;
  }

  const payload = {
    particle: particles[0].name,
    energy_mev: particles[0].energy_mev,
    particles,
    use_mixed_beam: document.getElementById("beamMode").value === "mixed",
    events: Number(document.getElementById("events").value),
    collect_tracks: document.getElementById("tracks").value === "true",
    world_xy_mm: Number(document.getElementById("world_xy").value),
    world_z_mm: Number(document.getElementById("world_z").value),
    screen_xy_mm: Number(document.getElementById("screen_xy").value),
    first_screen_z_mm: Number(document.getElementById("first_z").value),
    electronics_dose_threshold_gy: Number(document.getElementById("dose_threshold").value),
    electronics_let_threshold_mev_cm2_mg: Number(document.getElementById("let_threshold").value),
    build_energy_plots: true,
    build_3d_scene: true,
    layers: readLayers(),
  };

  setStatus("Simulation is running. Please wait...", true);
  document.getElementById("rawResult").textContent = "";

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "API error");
    }

    const data = await res.json();
    renderKpi(data.report);
    renderLayerReport(data.report);
    renderFiles(data.generated_files);
    document.getElementById("rawResult").textContent = JSON.stringify(data.result, null, 2);
    setStatus("Simulation completed.", true);
  } catch (e) {
    setStatus(`Error: ${e.message}`, false);
  }
}

function initUi() {
  const addLayerBtn = document.getElementById("addLayerBtn");
  const removeLayerBtn = document.getElementById("removeLayerBtn");
  const addParticleBtn = document.getElementById("addParticleBtn");
  const removeParticleBtn = document.getElementById("removeParticleBtn");
  const runBtn = document.getElementById("runBtn");
  const layersContainer = document.getElementById("layersContainer");

  if (addLayerBtn) addLayerBtn.addEventListener("click", () => addLayer());
  if (removeLayerBtn) removeLayerBtn.addEventListener("click", removeLayer);
  if (addParticleBtn) addParticleBtn.addEventListener("click", () => addParticleRow());
  if (removeParticleBtn) removeParticleBtn.addEventListener("click", removeParticleRow);
  if (runBtn) runBtn.addEventListener("click", runSimulation);

  if (layersContainer) {
    layersContainer.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.classList.contains("add-element-btn")) addElement(target);
      if (target.classList.contains("remove-element-btn")) removeElement(target);
    });
  }

  defaultLayers.forEach((layer) => addLayer(layer));
  loadParticles();
}

window.addParticleFromUi = () => addParticleRow();
window.removeParticleFromUi = () => removeParticleRow();

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initUi);
} else {
  initUi();
}
