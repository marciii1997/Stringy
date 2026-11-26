(() => {
    const status = document.querySelector("#step-status");
    let saving = false;
    document.addEventListener("submit", async event => {
        const form = event.target;
        if (!form.matches("form[data-step]") || !event.submitter) return;
        event.preventDefault();
        if (saving) return;
        saving = true;
        const data = new FormData(form);
        data.set("action", event.submitter.value);
        const buttons = [...form.querySelectorAll("button")];
        buttons.forEach(button => button.disabled = true);
        try {
            const response = await fetch(form.getAttribute("action"), {method: "POST", body: data,
                headers: {Accept: "application/json"}});
            if (!response.ok) throw Error();
            const result = await response.json();
            if (result.reload) return location.reload();
            document.querySelector("#faedeln").outerHTML = result.html;
            const slider = document.querySelector(".player input[type=range]");
            slider.value = result.step;
            slider.dispatchEvent(new Event("input"));
            status.textContent = "Schritt " + result.step + " gespeichert.";
            document.querySelector("#faedeln button")?.focus({preventScroll:true});
        } catch {
            status.textContent = "Speichern konnte nicht bestätigt werden. Bitte erneut versuchen.";
            buttons.forEach(button => button.disabled = false);
        } finally {
            saving = false;
        }
    });
    window.addEventListener("offline", () => status.textContent = "Du bist offline. Zum Speichern brauchst du eine Verbindung.");
})();
