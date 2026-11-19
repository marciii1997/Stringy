function initNailEditor(editor) {
    const hidden = editor.querySelector("input[type=hidden]");
    const mode = editor.querySelector("[name=nail_mode]");
    const board = editor.querySelector("[data-board]");
    const canvas = board.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    const selection = board.querySelector("[data-selection]");
    const remove = board.querySelector("[data-remove]");
    const clear = board.querySelector("[data-clear]");
    let cleared = [];
    const seed = board.querySelector("[data-ring]");
    const status = board.querySelector("[role=status]");
    const form = hidden.form;
    const count = form.elements.anchors;
    const tone = document.getElementById("tone");
    const mmX = board.querySelector("[data-x]"), mmY = board.querySelector("[data-y]");
    const addAt = board.querySelector("[data-add-at]"), moveTo = board.querySelector("[data-move-to]");
    const radiusMm = () => Number(form.elements.diameter.value) / 2;
    const mid = canvas.width / 2, radius = mid - 14;
    let points = [], active = -1, pointer = null, photo = null, sourceUrl = null;
    try {
        const saved = JSON.parse(hidden.value || "[]");
        if (Array.isArray(saved)) points = saved.filter(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite));
    } catch { /* Validation errors from a submitted layout are displayed by the form. */ }
    let ringCount = Math.max(24, Math.min(720, Number(count.value) || 360));
    mode.value = hidden.dataset.nailMode || (points.length ? "free" : "ring");

    function draw() {
        if (board.hidden) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.save();
        ctx.beginPath();
        ctx.arc(mid, mid, radius, 0, 2 * Math.PI);
        ctx.fillStyle = "white";
        ctx.fill();
        ctx.clip();
        if (tone) {
            ctx.drawImage(tone, mid-radius, mid-radius, radius*2, radius*2);
        } else if (photo) {
            const side = Math.max(1, Math.round(Math.min(photo.width, photo.height) / (Number(form.elements.zoom.value) || 1)));
            const offset = (length, percent) => {
                const pos = Math.floor(length * Number(percent) / 100 - side/2);
                return hidden.dataset.pad === "1" || side > Math.min(photo.width, photo.height)
                    ? pos : Math.max(0, Math.min(length-side, pos));
            };
            ctx.drawImage(photo, offset(photo.width, form.elements.center_x.value),
                offset(photo.height, form.elements.center_y.value), side, side,
                mid-radius, mid-radius, radius*2, radius*2);
        }
        ctx.restore();
        ctx.strokeStyle = "#84918c";
        ctx.stroke();
        points.forEach(([x,y], i) => {
            const px = mid + x*radius, py = mid + y*radius;
            ctx.beginPath();
            ctx.arc(px, py, i === active ? 6 : 4, 0, 2*Math.PI);
            ctx.fillStyle = i === active ? "#ac3653" : "#146664";
            ctx.strokeStyle = "white";
            ctx.lineWidth = 1.5;
            ctx.fill(); ctx.stroke();
            if (points.length <= 45 || i === active) {
                ctx.font = "bold 12px system-ui";
                ctx.textAlign = x > .85 ? "right" : "left";
                const labelX = px + (x > .85 ? -7 : 7);
                ctx.lineWidth = 3;
                ctx.strokeText(String(i), labelX, py+4);
                ctx.fillStyle = "#142e2c";
                ctx.fillText(String(i), labelX, py+4);
            }
        });
    }

    function sync() {
        const free = mode.value === "free";
        board.hidden = !free;
        count.readOnly = free;
        count.value = free ? points.length : ringCount;
        hidden.value = JSON.stringify(free ? points : []);
        selection.replaceChildren(...points.map((_, i) => new Option(String(i), String(i))));
        selection.selectedIndex = active;
        remove.disabled = moveTo.disabled = active < 0;
        addAt.disabled = points.length >= 720;
        updateCoordinates();
        clear.disabled = !points.length && !cleared.length;
        clear.textContent = !points.length && cleared.length ? "Plan wiederherstellen" : "Plan leeren";
        seed.disabled = points.length > 0;
        seed.textContent = ringCount + " Randnägel übernehmen";
        status.textContent = points.length + " von 720 Nägeln gesetzt. Mindestens 3 erforderlich.";
        draw();
    }
    async function loadPhoto() {
        if (tone || board.hidden) return;
        const file = form.elements.photo?.files[0];
        const source = file || hidden.dataset.photoUrl;
        if (source === sourceUrl) return;
        sourceUrl = source;
        photo = null;
        draw();
        if (!source) return;
        const img = new Image();
        const url = file ? URL.createObjectURL(file) : source;
        try {
            img.src = url;
            await img.decode();
            if (source === sourceUrl) photo = img;
        } catch {
            if (source === sourceUrl) status.textContent = "Das Foto konnte nicht angezeigt werden.";
        } finally {
            if (file) URL.revokeObjectURL(url);
        }
        draw();
    }
    mode.addEventListener("change", () => {
        if (mode.value === "free") ringCount = Math.max(24, Math.min(720, Number(count.value) || 360));
        sync(); loadPhoto();
    });
    function updateCoordinates() {
        const [x, y] = points[active] || [0, 0];
        mmX.value = Number((x * radiusMm()).toFixed(6));
        mmY.value = Number((y * radiusMm()).toFixed(6));
    }
    function placeAt(add) {
        const p = [mmX.valueAsNumber / radiusMm(), mmY.valueAsNumber / radiusMm()];
        if (!p.every(Number.isFinite) || Math.hypot(...p) > 1 + 1e-12) {
            status.textContent = "Gib eine Position innerhalb des Kreises ein.";
            return;
        }
        if (points.some(([x,y], i) => (add || i !== active) && Math.hypot(x-p[0], y-p[1]) < 1e-6)) {
            status.textContent = "An dieser Position liegt bereits ein Nagel.";
            return;
        }
        if (add) active = points.length;
        points[active] = p;
        sync();
    }
    addAt.addEventListener("click", () => placeAt(true));
    moveTo.addEventListener("click", () => placeAt(false));
    form.elements.diameter.addEventListener("input", updateCoordinates);
    clear.addEventListener("click", () => {
        if (points.length) {
            cleared = points;
            points = [];
        } else {
            points = cleared;
            cleared = [];
        }
        active = points.length ? 0 : -1;
        sync();
    });
    seed.addEventListener("click", () => {
        points = Array.from({length:ringCount}, (_,i) => {
            const angle = 2*Math.PI*i/ringCount - Math.PI/2;
            return [Math.cos(angle), Math.sin(angle)];
        });
        active = 0; sync();
    });
    selection.addEventListener("change", () => { active = selection.selectedIndex; sync(); });
    remove.addEventListener("click", () => {
        if (active < 0) return;
        points.splice(active,1);
        active = Math.min(active, points.length-1);
        sync();
    });
    function location(ev) {
        const r = canvas.getBoundingClientRect();
        return [((ev.clientX-r.left)*canvas.width/r.width-mid)/radius,
                ((ev.clientY-r.top)*canvas.height/r.height-mid)/radius];
    }
    canvas.addEventListener("pointerdown", ev => {
        if (ev.button !== 0 || pointer !== null) return;
        const p = location(ev);
        let hit = -1, distance = 9*canvas.width/canvas.getBoundingClientRect().width/radius;
        points.forEach(([x,y], i) => {
            const d = Math.hypot(x-p[0], y-p[1]);
            if (d <= distance) { hit = i; distance = d; }
        });
        if (hit < 0) {
            if (Math.hypot(...p) > 1 || points.length >= 720) return;
            hit = points.length;
            points.push(p);
        }
        active = hit;
        pointer = ev.pointerId;
        canvas.setPointerCapture(pointer);
        sync();
    });
    canvas.addEventListener("pointermove", ev => {
        if (ev.pointerId !== pointer) return;
        const [x,y] = location(ev), d = Math.max(1, Math.hypot(x,y));
        points[active] = [x/d, y/d];
        hidden.value = JSON.stringify(points);
        draw();
    });
    canvas.addEventListener("lostpointercapture", () => { pointer = null; updateCoordinates(); });
    form.elements.photo?.addEventListener("change", loadPhoto);
    form.addEventListener("input", draw);
    tone?.addEventListener("tone-ready", draw);
    form.addEventListener("submit", ev => {
        if (mode.value === "free" && points.length < 3) {
            ev.preventDefault();
            status.textContent = "Platziere mindestens drei Nägel oder wähle den Randkreis.";
            editor.closest("details")?.setAttribute("open","");
            mode.focus();
        }
    });
    sync();
    loadPhoto();
}
document.addEventListener("DOMContentLoaded", () => document.querySelectorAll(".nail-editor").forEach(initNailEditor));
