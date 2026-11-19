const player = document.querySelector(".player");
const {path, anchors, start, width} = JSON.parse(document.getElementById("playback").textContent);
const canvas = player.querySelector("canvas");
const ctx = canvas.getContext("2d");
const slider = player.querySelector("input");
const output = player.querySelector("output");
const back = player.querySelector("[data-back]");
const next = player.querySelector("[data-next]");
const size = canvas.width;
const radius = (size - 6) / 2;
const points = Array.from({length: anchors}, (_, index) => {
    const angle = 2 * Math.PI * index / anchors - Math.PI / 2;
    return [size / 2 + radius * Math.cos(angle), size / 2 + radius * Math.sin(angle)];
});
slider.max = path.length - 1 - start;
slider.value = slider.max;

function draw() {
    const step = Number(slider.value);
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, size, size);
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, radius, 0, 2 * Math.PI);
    ctx.strokeStyle = "#c3bdb1";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.lineWidth = width * radius * 2;
    ctx.strokeStyle = "#111";
    for (let i = 1; i <= start + step; i++) {
        ctx.beginPath();
        ctx.moveTo(...points[path[i - 1]]);
        ctx.lineTo(...points[path[i]]);
        ctx.stroke();
    }
    back.disabled = step === 0;
    next.disabled = step === Number(slider.max);
    output.value = step === 0 ? `Start an Anker ${path[start]}` :
        `${step} / ${slider.max} · Anker ${path[start + step - 1]} → ${path[start + step]}`;
}
slider.addEventListener("input", draw);
back.addEventListener("click", () => { slider.stepDown(); draw(); });
next.addEventListener("click", () => { slider.stepUp(); draw(); });
draw();
