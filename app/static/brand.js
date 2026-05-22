const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
      }
    });
  },
  { threshold: 0.18 },
);

document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));

const navLinks = document.querySelector(".nav-links");
if (navLinks && !navLinks.querySelector('[href="/logout"]')) {
  const logoutLink = document.createElement("a");
  logoutLink.href = "/logout";
  logoutLink.textContent = "Выйти";
  navLinks.appendChild(logoutLink);
}
