(() => {
  const search = document.getElementById("slot-search");
  const cells = Array.from(document.querySelectorAll(".cell"));
  const filters = {
    blocked: document.getElementById("filter-blocked"),
    free: document.getElementById("filter-free"),
    other: document.getElementById("filter-other"),
  };

  let activeFilters = new Set(["blocked", "free", "other"]);

  function toggleChip(chip) {
    const key = chip.dataset.filter;
    if (!key) return;
    if (activeFilters.has(key)) {
      activeFilters.delete(key);
      chip.classList.remove("active");
    } else {
      activeFilters.add(key);
      chip.classList.add("active");
    }
    applyView();
  }

  Object.values(filters).forEach((chip) => {
    if (!chip) return;
    chip.classList.add("active");
    chip.addEventListener("click", () => toggleChip(chip));
  });

  function cellKind(cell) {
    if (cell.classList.contains("blocked")) return "blocked";
    if (cell.classList.contains("free")) return "free";
    return "other";
  }

  function applyView() {
    const q = (search?.value || "").trim().toUpperCase();
    cells.forEach((cell) => {
      const label = (cell.dataset.label || cell.textContent || "").toUpperCase();
      const kind = cellKind(cell);
      const filterOk = activeFilters.has(kind);
      const searchOk = !q || label.includes(q);
      cell.classList.toggle("dimmed", !(filterOk && searchOk));
      cell.classList.toggle("match", !!q && label === q);
    });
  }

  search?.addEventListener("input", applyView);

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== search) {
      e.preventDefault();
      search?.focus();
    }
  });
})();
