// Millimetres between outer nail centres. Rectangle presets refer to the whole board.
function nailField(form) {
    const format = form.elements.canvas_format.value;
    if (format === "circle" || !format) {
        const d = Number(form.elements.diameter.value) || 600;
        return {w: d, h: d, rectangular: false};
    }
    const dims = format === "a3_portrait" ? [297, 420] : format === "a3_landscape" ? [420, 297] :
        [Number(form.elements.canvas_width.value) || 297, Number(form.elements.canvas_height.value) || 420];
    return {w: Math.max(1, dims[0]-10), h: Math.max(1, dims[1]-10), rectangular: true};
}
function photoCrop(form, width, height) {
    const {w, h} = nailField(form);
    const scale = Math.min(width/w, height/h) / (Number(form.elements.zoom.value) || 1);
    return [Math.max(1, Math.round(w*scale)), Math.max(1, Math.round(h*scale))];
}
document.addEventListener("DOMContentLoaded", () => {
    for (const shape of document.querySelectorAll("select[name=canvas_format]")) {
        const form = shape.form;
        function update() {
            const circle = shape.value === "circle", custom = shape.value === "custom";
            for (const [name, visible] of [["diameter",circle],["shaft_mm",!circle],["canvas_width",custom],["canvas_height",custom]]) {
                const el = form.elements[name];
                const wrapper = el.closest("[data-format-field], .form-row, p") || el;
                wrapper.hidden = !visible;
                el.disabled = !visible;
                el.required = visible;
            }
        }
        update();
        shape.addEventListener("change", update);
    }
});
