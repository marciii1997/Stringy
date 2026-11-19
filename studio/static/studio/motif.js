(async () => {
  const byId = (id) => document.getElementById(id);
  const cfg = JSON.parse(byId("motif-config").textContent);
  const brush = byId("brush"),
    view = byId("tone"),
    tool = byId("tool");
  const load = async (bytes) => {
    const img = new Image();
    img.src = "data:image/png;base64," + bytes;
    await img.decode();
    return img;
  };
  const [photo, mask] = await Promise.all([load(cfg.photo), load(cfg.masks)]);
  const w = (brush.width = photo.width),
    h = (brush.height = photo.height);
  const layers = [
    "subject",
    "background",
    "focus",
    "unfocus",
    "clothing",
    "unclothe",
  ];
  function buffer() {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    return canvas.getContext("2d", { willReadFrequently: true });
  }
  const ctx = brush.getContext("2d"),
    base = buffer(),
    processed = buffer();
  const samples = {};
  await Promise.all(Object.entries(cfg.styles).map(async ([name, bytes]) => {
    const layer = buffer();
    layer.drawImage(await load(bytes), 0, 0);
    samples[name] = layer.getImageData(0, 0, w, h).data;
  }));
  base.fillStyle = "white";
  base.fillRect(0, 0, w, h);
  base.drawImage(photo, 0, 0);
  const colors = base.getImageData(0, 0, w, h).data;
  base.drawImage(mask, 0, 0);
  const channels = base.getImageData(0, 0, w, h).data;
  const masks = [buffer(), buffer(), buffer()];
  let strokes;
  try {
    strokes = JSON.parse(byId("id_strokes").value || "[]") || [];
  } catch {
    strokes = [];
  }
  if (
    !Array.isArray(strokes) ||
    strokes.some(
      (s) => !s || !layers.includes(s.layer) || !Array.isArray(s.points),
    )
  )
    strokes = [];
  const form = byId("motif-form");
  const num = (name) => Number(form.elements[name].value);
  let active = null,
    queued = false;
  function frame() {
    const [width, height] = cfg.dimensions;
    const side = Math.max(
      1,
      Math.round(Math.min(width, height) / Math.max(0.01, num("zoom"))),
    );
    const offset = (d, center) => {
      const start = Math.floor((d * center) / 100 - side / 2);
      return form.elements.pad.checked || num("zoom") < 1
        ? start
        : Math.max(0, Math.min(d - side, start));
    };
    return [
      (offset(width, num("center_x")) * w) / width,
      (offset(height, num("center_y")) * h) / height,
      (side * w) / width,
      (side * h) / height,
    ];
  }
  function applyStroke(s) {
    const index = layers.indexOf(s.layer);
    const layer = masks[Math.floor(index / 2)];
    const radius = Math.max(1, s.radius * Math.min(w, h));
    layer.save();
    layer.filter = "blur(" + Math.max(0.5, radius * 0.12) + "px)";
    layer.fillStyle = layer.strokeStyle = index % 2 ? "black" : "white";
    layer.lineWidth = 2 * radius;
    layer.lineJoin = layer.lineCap = "round";
    layer.beginPath();
    s.points.forEach(([x, y], i) =>
      i
        ? layer.lineTo(x * (w - 1), y * (h - 1))
        : layer.moveTo(x * (w - 1), y * (h - 1)),
    );
    layer.stroke();
    if (s.points.length === 1) {
      const [x, y] = s.points[0];
      layer.beginPath();
      layer.arc(x * (w - 1), y * (h - 1), radius, 0, 2 * Math.PI);
      layer.fill();
    }
    layer.restore();
  }
  function rebuild() {
    masks.forEach((layer, k) => {
      const im = layer.createImageData(w, h);
      for (let i = 0; i < channels.length; i += 4) {
        im.data[i] = im.data[i + 1] = im.data[i + 2] = channels[i + k];
        im.data[i + 3] = 255;
      }
      layer.putImageData(im, 0, 0);
    });
    strokes.forEach(applyStroke);
  }
  function paint() {
    queued = false;
    const maps = masks.map((m) => m.getImageData(0, 0, w, h).data);
    const tone = processed.createImageData(w, h),
      overlay = ctx.createImageData(w, h);
    const bg = num("background") / 100,
      cloth = num("clothing") / 100,
      gamma = num("gamma");
    const color = form.elements.background_color.value;
    const style = form.elements.style.value;
    const styled = samples[style];
    const [x, y, sw, sh] = frame();
    const [center, rim] = cfg.gradient;
    const layer = Math.floor(layers.indexOf(tool.value) / 2);
    const tint = [
      [20, 182, 114],
      [244, 150, 34],
      [98, 146, 238],
    ][layer];
    for (let i = 0; i < colors.length; i += 4) {
      const subject = maps[0][i] / 255;
      const lum =
        (0.2126 * colors[i] + 0.7152 * colors[i + 1] + 0.0722 * colors[i + 2]) /
        255;
      const blend = bg * (1 - subject);
      let backdrop = color === "black" ? 1 : 0;
      if (color === "gradient") {
        const index = i / 4;
        const dx = 2 * (index % w + 0.5 - x) / sw - 1;
        const dy = 2 * (Math.floor(index / w) + 0.5 - y) / sh - 1;
        backdrop = center + (rim - center) * Math.min(1, Math.hypot(dx, dy));
      }
      const weight = styled ? subject * (style === "natural" ? bg : 1) : 0;
      const styledInk = styled ? weight * (1 - (styled[i] / 255) ** gamma) : 0;
      const dark =
        ((1 - lum ** gamma) * (1 - blend - weight) + styledInk + backdrop * blend) *
        (1 - (cloth * subject * maps[2][i]) / 255);
      tone.data[i] = tone.data[i + 1] = tone.data[i + 2] = 255 * (1 - dark);
      tone.data[i + 3] = overlay.data[i + 3] = 255;
      const a = tint ? (0.55 * maps[layer][i]) / 255 : 0;
      for (let c = 0; c < 3; c++)
        overlay.data[i + c] = tint
          ? colors[i + c] * (1 - a) + tint[c] * a
          : colors[i + c];
    }
    ctx.putImageData(overlay, 0, 0);
    processed.putImageData(tone, 0, 0);
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    ctx.ellipse(x + sw / 2, y + sh / 2, sw / 2, sh / 2, 0, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,0,0,.50)";
    ctx.fill("evenodd");
    ctx.beginPath();
    ctx.ellipse(x + sw / 2, y + sh / 2, sw / 2, sh / 2, 0, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "white";
    ctx.stroke();
    const v = view.getContext("2d");
    v.clearRect(0, 0, 256, 256);
    v.save();
    v.beginPath();
    v.arc(128, 128, 128, 0, Math.PI * 2);
    v.clip();
    const paddingColor = darkness => {
      const value = Math.round(255 * (1 - bg * darkness));
      return `rgb(${value}, ${value}, ${value})`;
    };
    if (color === "gradient") {
      const gradient = v.createRadialGradient(128, 128, 0, 128, 128, 128);
      gradient.addColorStop(0, paddingColor(center));
      gradient.addColorStop(1, paddingColor(rim));
      v.fillStyle = gradient;
    } else {
      v.fillStyle = paddingColor(color === "black" ? 1 : 0);
    }
    v.fillRect(0, 0, 256, 256);
    v.drawImage(processed.canvas, x, y, sw, sh, 0, 0, 256, 256);
    v.restore();
    view.dispatchEvent(new Event("tone-ready"));
    document
      .querySelectorAll("output[for]")
      .forEach((o) => (o.value = byId(o.htmlFor.value).value + " %"));
    byId("undo").disabled = !strokes.length;
  }
  function schedule() {
    if (!queued) {
      queued = true;
      requestAnimationFrame(paint);
    }
  }
  const count = cfg.faces.filter((f) => f.kind === "person").length;
  const cats = cfg.faces.length - count;
  const detected = [
    count && (count === 1 ? "1 Gesicht" : count + " Gesichter"),
    cats && (cats === 1 ? "1 Katzenkopf" : cats + " Katzenköpfe"),
  ].filter(Boolean);
  byId("detection").textContent =
    (detected.length
      ? detected.join(" und ") + " erkannt."
      : "Kein Gesicht erkannt.") +
    " Mit dem Fokuspinsel kannst du weitere Details markieren.";
  function point(ev) {
    const r = brush.getBoundingClientRect();
    const clamp = (v) =>
      Math.max(0, Math.min(1, Math.round(v * 10000) / 10000));
    return [
      clamp((ev.clientX - r.left) / r.width),
      clamp((ev.clientY - r.top) / r.height),
    ];
  }
  function save() {
    byId("id_strokes").value = JSON.stringify(strokes);
  }
  brush.addEventListener("pointerdown", (ev) => {
    if (active || ev.button !== 0) return;
    const p = point(ev);
    if (tool.value === "center") {
      form.elements.center_x.value = (p[0] * 100).toFixed(2);
      form.elements.center_y.value = (p[1] * 100).toFixed(2);
    } else {
      if (
        strokes.length >= 128 ||
        strokes.reduce((n, s) => n + s.points.length, 0) >= 15500
      ) {
        byId("brush-status").textContent =
          "Pinselgrenze erreicht. Entferne bei Bedarf den letzten Strich.";
        return;
      }
      brush.setPointerCapture(ev.pointerId);
      active = {
        layer: tool.value,
        radius: Number(byId("radius").value) / 100,
        points: [p],
      };
      strokes.push(active);
      applyStroke(active);
    }
    schedule();
  });
  brush.addEventListener("pointermove", (ev) => {
    if (
      !active ||
      !brush.hasPointerCapture(ev.pointerId) ||
      active.points.length >= 500
    )
      return;
    const p = point(ev),
      last = active.points.at(-1);
    if (Math.hypot((last[0] - p[0]) * w, (last[1] - p[1]) * h) < 2) return;
    active.points.push(p);
    applyStroke({ ...active, points: [last, p] });
    schedule();
  });
  brush.addEventListener("lostpointercapture", () => {
    active = null;
    save();
  });
  byId("undo").onclick = () => {
    strokes.pop();
    rebuild();
    save();
    schedule();
  };
  byId("frame").onclick = () => {
    form.elements.pad.checked = true;
    for (const [k, v] of Object.entries(cfg.framing))
      form.elements[k].value = v;
    schedule();
  };
  byId("cutout").onclick = () => {
    form.elements.background.value = 100;
    schedule();
  };
  form.oninput = schedule;
  form.addEventListener("submit", save);
  rebuild();
  save();
  paint();
})().catch(() => {
  document.getElementById("brush-status").textContent =
    "Die Bildvorschau konnte nicht geladen werden. Bitte lade die Seite neu.";
});
