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
    const mid = canvas.width / 2, radius = mid - 14;
    function radii() {
        const {w,h} = nailField(form);
        return [radius*w/Math.max(w,h),radius*h/Math.max(w,h)];
    }
    function outside(x,y) {
        return nailField(form).rectangular ? Math.max(Math.abs(x),Math.abs(y)) : Math.hypot(x,y);
    }
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
        const [rx,ry] = radii();
        if (nailField(form).rectangular) ctx.rect(mid-rx,mid-ry,2*rx,2*ry);
        else ctx.arc(mid, mid, radius, 0, 2 * Math.PI);
        ctx.fillStyle = "white";
        ctx.fill();
        ctx.clip();
        if (tone) {
            ctx.drawImage(tone, mid-rx, mid-ry, rx*2, ry*2);
        } else if (photo) {
            const [sw,sh] = photoCrop(form,photo.width,photo.height);
            const offset = (length, percent, side) => {
                const pos = Math.floor(length * Number(percent) / 100 - side/2);
                return hidden.dataset.pad === "1" || Number(form.elements.zoom.value) < 1
                    ? pos : Math.max(0, Math.min(length-side, pos));
            };
            ctx.drawImage(photo, offset(photo.width, form.elements.center_x.value,sw),
                offset(photo.height, form.elements.center_y.value,sh), sw, sh,
                mid-rx, mid-ry, rx*2, ry*2);
        }
        ctx.restore();
        ctx.strokeStyle = "#84918c";
        ctx.stroke();
        points.forEach(([x,y], i) => {
            const px = mid + x*rx, py = mid + y*ry;
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
        mmX.value = Number((x * nailField(form).w/2).toFixed(6));
        mmY.value = Number((y * nailField(form).h/2).toFixed(6));
    }
    function placeAt(add) {
        const p = [mmX.valueAsNumber * 2/nailField(form).w, mmY.valueAsNumber * 2/nailField(form).h];
        if (!p.every(Number.isFinite) || outside(...p) > 1 + 1e-12) {
            status.textContent = "Gib eine Position innerhalb des Nagelfelds ein.";
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
    form.addEventListener("input", ev => { if (["diameter","canvas_format","canvas_width","canvas_height"].includes(ev.target.name)) updateCoordinates(); });
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
        const {w,h,rectangular} = nailField(form);
        if (rectangular) {
            const sides = [1,1,1,1], lengths = [w,h,w,h];
            for (let i=4;i<ringCount;i++) {
                let k=0;
                for (let j=1;j<4;j++) if (lengths[j]/sides[j]>lengths[k]/sides[k]) k=j;
                sides[k]++;
            }
            points=sides.flatMap((n,j)=>Array.from({length:n},(_,i)=>{
                const t=2*i/n-1;
                return [[t,-1],[1,t],[-t,1],[-1,-t]][j];
            }));
        } else points = Array.from({length:ringCount}, (_,i) => {
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
        const [rx,ry] = radii();
        return [((ev.clientX-r.left)*canvas.width/r.width-mid)/rx,
                ((ev.clientY-r.top)*canvas.height/r.height-mid)/ry];
    }
    canvas.addEventListener("pointerdown", ev => {
        if (ev.button !== 0 || pointer !== null) return;
        const p = location(ev);
        const [rx,ry] = radii();
        let hit = -1, distance = 9*canvas.width/canvas.getBoundingClientRect().width;
        points.forEach(([x,y], i) => {
            const d = Math.hypot((x-p[0])*rx, (y-p[1])*ry);
            if (d <= distance) { hit = i; distance = d; }
        });
        if (hit < 0) {
            if (outside(...p) > 1 || points.length >= 720) return;
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
        const [x,y] = location(ev), d = Math.max(1, outside(x,y));
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
            status.textContent = "Platziere mindestens drei Nägel oder wähle den Randplan.";
            editor.closest("details")?.setAttribute("open","");
            mode.focus();
        }
    });
    sync();
    loadPhoto();
}
document.addEventListener("DOMContentLoaded", () => document.querySelectorAll(".nail-editor").forEach(initNailEditor));
