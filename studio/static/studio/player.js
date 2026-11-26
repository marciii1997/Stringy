const player = document.querySelector(".player");
const {path, anchors, rectangle, positions, firstExtra, start, width, initial, opacity, sides, tangentRadius} = JSON.parse(document.getElementById("playback").textContent);
const canvas = player.querySelector("canvas");
const ctx = canvas.getContext("2d");
const slider = player.querySelector("input[type=range]");
const showNails = player.querySelector("[data-nails]");
const planToggle = player.querySelector("[data-plan-target]");
const target = new Image();
if (planToggle) {
    planToggle.addEventListener("change", () => {
        if (planToggle.checked) {
            showNails.checked = true;
            if (!target.src) target.src = planToggle.dataset.planTarget;
        }
        draw();
    });
    target.addEventListener("load", draw);
}
const output = player.querySelector("output");
const back = player.querySelector("[data-back]");
const next = player.querySelector("[data-next]");
const size = canvas.width;
const radius = (size - 16) / 2;
const [w,h] = rectangle || [1,1];
const rx = radius*w/Math.max(w,h), ry = radius*h/Math.max(w,h);
canvas.width=Math.round(2*rx+16); canvas.height=Math.round(2*ry+16);
const cx=canvas.width/2, cy=canvas.height/2;
const points = Array.from({length: anchors}, (_, index) => {
    if (positions) {
        const p = positions[index];
        return p ? [cx + rx*p[0], cy + ry*p[1]] : null;
    }
    const angle = 2 * Math.PI * index / anchors - Math.PI / 2;
    return [size / 2 + radius * Math.cos(angle), size / 2 + radius * Math.sin(angle)];
});
slider.max = path.length - 1 - start;
slider.value = initial ?? slider.max;

const segments = path.slice(1).map((pin, index) => {
    const a = points[path[index]], b = points[pin];
    if (!sides || !sides[index+1]) return [a, b];
    const r = tangentRadius * 2 * radius;
    const length = Math.hypot(b[0]-a[0], b[1]-a[1]);
    if (length <= 2*r) return [a, b];
    const dx = (b[0]-a[0])/length, dy = (b[1]-a[1])/length;
    const startSide = sides[index] === "ccw" ? 1 : -1;
    const endSide = sides[index+1] === "ccw" ? 1 : -1;
    const k = r*(startSide-endSide)/length, q = Math.sqrt(1-k*k);
    const nx = k*dx-q*dy, ny = k*dy+q*dx;
    return [[a[0]+startSide*r*nx, a[1]+startSide*r*ny],
            [b[0]+endSide*r*nx, b[1]+endSide*r*ny]];
});

function draw() {
    const step = Number(slider.value);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.beginPath();
    if (rectangle) ctx.rect(cx-rx,cy-ry,2*rx,2*ry);
    else ctx.arc(cx,cy,radius,0,2*Math.PI);
    ctx.strokeStyle = "#c3bdb1";
    ctx.lineWidth = 1;
    ctx.stroke();
    const plan = planToggle?.checked && target.naturalWidth > 0;
    if (plan) ctx.drawImage(target, cx-rx,cy-ry,2*rx,2*ry);
    ctx.lineWidth = width * radius * 2;
    ctx.globalAlpha = opacity ?? 1;
    ctx.strokeStyle = "#000";
    for (let i = 1; !plan && i <= start + step; i++) {
        ctx.beginPath();
        ctx.moveTo(...segments[i-1][0]);
        ctx.lineTo(...segments[i-1][1]);
        ctx.stroke();
    }
    if (showNails.checked) {
        ctx.globalAlpha = 1;
        const current = plan ? -1 : path[start + step];
        points.forEach((p, id) => {
            if (!p) return;
            const [x,y] = p;
            ctx.beginPath();
            const added = firstExtra != null && id >= firstExtra;
            ctx.arc(x, y, id === current || added ? 6 : 3, 0, 2*Math.PI);
            ctx.fillStyle = id === current ? "#a33153" : added ? "#bd5b08" : "#146664";
            ctx.strokeStyle = "white";
            ctx.lineWidth = 1.3;
            ctx.fill(); ctx.stroke();
            if (id === current || added || anchors <= 45) {
                ctx.font = "bold 14px system-ui";
                ctx.textAlign = x > cx ? "right" : "left";
                const dx = x > cx ? -7 : 7;
                ctx.lineWidth = 3;
                ctx.strokeText(String(id), x+dx, y+4);
                ctx.fillStyle = "#142e2c";
                ctx.fillText(String(id), x+dx, y+4);
            }
        });
    }
    back.disabled = step === 0;
    next.disabled = step === Number(slider.max);
    output.value = step === 0 ? `Start an Anker ${path[start]}` :
        `${step} / ${slider.max} · Anker ${path[start + step - 1]} → ${path[start + step]}`;
}
showNails.addEventListener("change", draw);
slider.addEventListener("input", draw);
back.addEventListener("click", () => { slider.stepDown(); draw(); });
next.addEventListener("click", () => { slider.stepUp(); draw(); });
draw();
