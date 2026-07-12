(() => {
  const menuButton = document.querySelector("[data-menu-button]");
  const nav = document.querySelector("[data-site-nav]");
  if (menuButton && nav) {
    menuButton.addEventListener("click", () => {
      const open = nav.dataset.open !== "true";
      nav.dataset.open = String(open);
      menuButton.setAttribute("aria-expanded", String(open));
    });
    nav.addEventListener("click", (event) => {
      if (event.target.closest("a")) {
        nav.dataset.open = "false";
        menuButton.setAttribute("aria-expanded", "false");
      }
    });
  }

  document.querySelectorAll("[data-back]").forEach((button) => {
    button.addEventListener("click", () => {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = button.dataset.fallback || "index.html";
      }
    });
  });

  const search = document.querySelector("[data-asset-search]");
  const cards = [...document.querySelectorAll("[data-asset-card]")];
  const filterButtons = [...document.querySelectorAll("[data-filter]")];
  let activeFilter = "all";

  const applyFilters = () => {
    const query = (search?.value || "").trim().toLocaleLowerCase("ko");
    let visible = 0;
    cards.forEach((card) => {
      const text = (card.dataset.search || card.textContent).toLocaleLowerCase("ko");
      const family = card.dataset.family || "";
      const matchesText = !query || text.includes(query);
      const matchesFilter = activeFilter === "all" || family === activeFilter;
      card.hidden = !(matchesText && matchesFilter);
      if (!card.hidden) visible += 1;
    });
    const count = document.querySelector("[data-result-count]");
    if (count) count.textContent = `${visible}개 자산`;
    const empty = document.querySelector("[data-search-empty]");
    if (empty) empty.hidden = visible !== 0;
  };

  search?.addEventListener("input", applyFilters);
  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.filter;
      filterButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      applyFilters();
    });
  });
  if (cards.length) applyFilters();
})();
