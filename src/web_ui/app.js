const defaultLayers = [
  {
    name: "Ti layer",
    description: "Titanium layer",
    width_um: 3000,
    elements: [
      {
        name: "Titanium",
        symbol: "Ti",
        atomic_number: 22,
        standard_atomic_weight: 47.87,
        density_g_cm3: 4.5,
        percentage: 100,
      },
    ],
  },
  {
    name: "W layer",
    description: "Tungsten layer",
    width_um: 2000,
    elements: [
      {
        name: "Tungsten",
        symbol: "W",
        atomic_number: 74,
        standard_atomic_weight: 183.84,
        density_g_cm3: 19.25,
        percentage: 100,
      },
    ],
  },
];

let layerCounter = 0;

function setStatus(text, ok = true) {
  const s = document.getElementById("status");
  s.className = "status " + (ok ? "ok" : "err");
  s.textContent = text;
}

function addElementRow(tableBody, element = null) {
  const el = element || {
    name: "",
    symbol: "Al",
    atomic_number: 13,
    standard_atomic_weight: 26.98,
    density_g_cm3: 2.7,
    percentage: 100,
  };
  const row = document.createElement("tr");
  row.className = "element-row";
  row.innerHTML = `
    <td><input data-field="name" value="${el.name ?? ""}"></td>
    <td><input data-field="symbol" value="${el.symbol ?? ""}"></td>
    <td><input data-field="atomic_number" type="number" value="${el.atomic_number ?? 0}" min="0" step="1"></td>
    <td><input data-field="standard_atomic_weight" type="number" value="${el.standard_atomic_weight ?? 0}" min="0" step="0.0001"></td>
    <td><input data-field="density_g_cm3" type="number" value="${el.density_g_cm3 ?? 0}" min="0" step="0.0001"></td>
    <td><input data-field="percentage" type="number" value="${el.percentage ?? 0}" min="0.0001" step="0.1"></td>
  `;
  tableBody.appendChild(row);
}

function addLayer(layer = null) {
  const lay = layer || {
    name: "Layer",
    description: "",
    width_um: 1000,
    elements: [
      {
        name: "",
        symbol: "Al",
        atomic_number: 13,
        standard_atomic_weight: 26.98,
        density_g_cm3: 2.7,
        percentage: 100,
      },
    ],
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
    </div>
    <table class="elements">
      <thead>
        <tr>
          <th>Name</th>
          <th>Symbol</th>
          <th>Atomic number</th>
          <th>Std atomic weight</th>
          <th>Density (g/cm3)</th>
          <th>Percentage</th>
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
  (lay.elements || []).forEach((el) => addElementRow(body, el));
  if (!body.children.length) {
    addElementRow(body);
  }
  document.getElementById("layersContainer").appendChild(card);
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
  addElementRow(body);
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
    const name = card.querySelector('[data-layer-field="name"]').value.trim();
    const description = card.querySelector('[data-layer-field="description"]').value.trim();
    const width_um = Number(card.querySelector('[data-layer-field="width_um"]').value);
    const elements = [...card.querySelectorAll(".element-row")].map((row) => {
      const getVal = (field) => row.querySelector(`[data-field="${field}"]`).value;
      return {
        name: getVal("name").trim(),
        symbol: getVal("symbol").trim(),
        atomic_number: Number(getVal("atomic_number")),
        standard_atomic_weight: Number(getVal("standard_atomic_weight")),
        density_g_cm3: Number(getVal("density_g_cm3")),
        percentage: Number(getVal("percentage")),
      };
    });
    return { name, description, width_um, elements };
  });
}

function renderKpi(report) {
  const kpis = document.getElementById("kpis");
  const pct = (value) => (value * 100).toFixed(2) + "%";
  kpis.innerHTML = `
    <div class="kpi">Total particles<strong>${report.total_particles}</strong></div>
    <div class="kpi">Primary out<strong>${report.out_primary_particles}</strong></div>
    <div class="kpi">Primary stopped<strong>${report.stopped_primary_particles}</strong></div>
    <div class="kpi">Secondary out<strong>${report.out_secondary_particles}</strong></div>
    <div class="kpi">Transmission<strong>${pct(report.transmission_rate)}</strong></div>
    <div class="kpi">Stopping efficiency<strong>${pct(report.stopping_efficiency)}</strong></div>
  `;
}

function renderLayerReport(report) {
  const lines = report.layers.map(
    (x) =>
      `${x.index}. ${x.name}: ${x.thickness_mm.toFixed(3)} mm | primary_stuck=${x.primary_stuck}, secondary_stuck=${x.secondary_stuck}, Edep=${x.edep_mev.toFixed(4)} MeV`,
  );
  document.getElementById("layerReport").textContent = lines.join("\n") || "No data";
}

function renderFiles(files) {
  const holder = document.getElementById("files");
  if (!files || !files.length) {
    holder.innerHTML = "<span class='muted'>No generated images.</span>";
    return;
  }
  holder.innerHTML = files
    .map(
      (f) =>
        `<a href="${f.web_url}" target="_blank">${f.name}</a><div class="muted">${f.abs_path}</div>`,
    )
    .join("");
}

async function loadParticles() {
  const res = await fetch("/api/particles");
  const data = await res.json();
  const select = document.getElementById("particle");
  select.innerHTML = "";
  data.particles.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    if (p === data.default) opt.selected = true;
    select.appendChild(opt);
  });
}

async function runSimulation() {
  const payload = {
    particle: document.getElementById("particle").value,
    energy_mev: Number(document.getElementById("energy").value),
    events: Number(document.getElementById("events").value),
    collect_tracks: document.getElementById("tracks").value === "true",
    world_xy_mm: Number(document.getElementById("world_xy").value),
    world_z_mm: Number(document.getElementById("world_z").value),
    screen_xy_mm: Number(document.getElementById("screen_xy").value),
    first_screen_z_mm: Number(document.getElementById("first_z").value),
    build_energy_plots: true,
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

document.getElementById("addLayerBtn").addEventListener("click", () => addLayer());
document.getElementById("removeLayerBtn").addEventListener("click", removeLayer);
document.getElementById("runBtn").addEventListener("click", runSimulation);
document.getElementById("layersContainer").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains("add-element-btn")) addElement(target);
  if (target.classList.contains("remove-element-btn")) removeElement(target);
});

defaultLayers.forEach((layer) => addLayer(layer));
loadParticles();
