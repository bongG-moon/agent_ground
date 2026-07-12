(() => {
  const navLinks = [...document.querySelectorAll('.training-toc a[href^="#"]')];
  const sections = navLinks.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);

  const setActiveNav = () => {
    if (!sections.length) return;
    const current = sections.filter((section) => section.getBoundingClientRect().top <= 170).pop() || sections[0];
    navLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${current.id}`));
  };
  window.addEventListener("scroll", setActiveNav, { passive: true });
  setActiveNav();

  document.querySelectorAll(".copy-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const code = button.closest(".code-card")?.querySelector("code")?.innerText;
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code);
        const original = button.innerHTML;
        button.textContent = "복사됨";
        setTimeout(() => { button.innerHTML = original; window.lucide?.createIcons(); }, 1300);
      } catch {
        button.textContent = "복사 실패";
      }
    });
  });

  document.querySelectorAll("[data-pattern-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.patternTarget;
      const group = button.closest("[data-search-block]") || document;
      group.querySelectorAll("[data-pattern-target]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      group.querySelectorAll(".pattern").forEach((pattern) => pattern.classList.toggle("visible", pattern.dataset.pattern === target));
    });
  });

  const searchInput = document.getElementById("searchInput");
  searchInput?.addEventListener("input", () => {
    const query = searchInput.value.trim().toLocaleLowerCase("ko");
    document.querySelectorAll(".training-article [data-search-block]").forEach((block) => {
      block.classList.toggle("hidden-by-search", Boolean(query) && !block.innerText.toLocaleLowerCase("ko").includes(query));
    });
  });

  document.getElementById("printPage")?.addEventListener("click", () => window.print());
  document.getElementById("expandCode")?.addEventListener("click", () => {
    document.querySelector("[data-code-collapsible]")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  document.querySelectorAll("[data-check]").forEach((checkbox) => {
    const key = `langflow-training-check-${checkbox.dataset.check}`;
    checkbox.checked = localStorage.getItem(key) === "true";
    checkbox.addEventListener("change", () => localStorage.setItem(key, String(checkbox.checked)));
  });

  window.lucide?.createIcons();
})();
