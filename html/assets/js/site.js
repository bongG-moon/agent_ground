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
  const libraryGroups = [...document.querySelectorAll("[data-library-group]")];
  let activeFilter = filterButtons.find((button) => button.getAttribute("aria-pressed") === "true")?.dataset.filter || "all";

  const applyFilters = () => {
    const query = (search?.value || "").trim().toLocaleLowerCase("ko");
    let visible = 0;
    cards.forEach((card) => {
      const text = (card.dataset.search || card.textContent).toLocaleLowerCase("ko");
      const family = card.dataset.family || "";
      const scope = card.dataset.scope || "";
      const matchesText = !query || text.includes(query);
      const matchesFilter = activeFilter === "all" || family === activeFilter || scope === activeFilter;
      card.hidden = !(matchesText && matchesFilter);
      if (!card.hidden) visible += 1;
    });
    const count = document.querySelector("[data-result-count]");
    if (count) count.textContent = `${visible}개 ${libraryGroups.length ? "Component" : "자산"}`;
    const empty = document.querySelector("[data-search-empty]");
    if (empty) empty.hidden = visible !== 0;
    document.querySelectorAll("[data-component-group]").forEach((group) => {
      const hasVisibleCard = [...group.querySelectorAll("[data-asset-card]")].some((card) => !card.hidden);
      group.hidden = !hasVisibleCard;
      if ((query || activeFilter !== "public_library") && hasVisibleCard) group.open = true;
    });
    const flowSection = document.querySelector("[data-flow-component-section]");
    if (flowSection) {
      flowSection.hidden = ![...flowSection.querySelectorAll("[data-component-group]")].some((group) => !group.hidden);
    }
    libraryGroups.forEach((group) => {
      group.hidden = ![...group.querySelectorAll("[data-asset-card]")].some((card) => !card.hidden);
    });
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

  const codeSource = document.querySelector("[data-code-source]");
  const copyCodeButton = document.querySelector("[data-copy-code]");
  const copyStatus = document.querySelector("[data-copy-status]");

  const fallbackCopy = (value) => {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("copy 명령을 사용할 수 없습니다.");
  };

  if (codeSource && copyCodeButton) {
    copyCodeButton.addEventListener("click", async () => {
      const code = [...codeSource.querySelectorAll("[data-code-text]")]
        .map((line) => line.textContent)
        .join("\n");
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(code);
        } else {
          fallbackCopy(code);
        }
        copyCodeButton.textContent = "복사 완료";
        if (copyStatus) copyStatus.textContent = `${code.split("\n").length.toLocaleString("ko-KR")}줄을 클립보드에 복사했습니다.`;
      } catch (_error) {
        try {
          fallbackCopy(code);
          copyCodeButton.textContent = "복사 완료";
          if (copyStatus) copyStatus.textContent = "코드를 클립보드에 복사했습니다.";
        } catch (_fallbackError) {
          copyCodeButton.textContent = "복사 실패";
          if (copyStatus) copyStatus.textContent = "브라우저가 복사를 막았습니다. 코드 영역에서 직접 선택해 주세요.";
        }
      }
      window.setTimeout(() => {
        copyCodeButton.textContent = "코드 복사";
      }, 2200);
    });
  }
})();
