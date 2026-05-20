(function () {
    const storageKey = 'marketplace-theme';
    const savedTheme = localStorage.getItem(storageKey);
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const initialTheme = savedTheme || (prefersDark ? 'dark' : 'light');

    function applyTheme(theme) {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem(storageKey, theme);
        const toggle = document.querySelector('[data-theme-toggle]');
        if (toggle) {
            toggle.textContent = theme === 'dark' ? '☀' : '☾';
            toggle.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
            toggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
        }
    }

    applyTheme(initialTheme);

    document.addEventListener('DOMContentLoaded', () => {
        if (!document.querySelector('[data-theme-toggle]')) {
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'theme-toggle';
            toggle.dataset.themeToggle = 'true';
            toggle.setAttribute('aria-label', 'Toggle dark mode');
            toggle.setAttribute('title', 'Toggle dark mode');
            document.body.appendChild(toggle);
        }

        applyTheme(document.documentElement.dataset.theme || initialTheme);

        document.querySelector('[data-theme-toggle]')?.addEventListener('click', () => {
            const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
            applyTheme(nextTheme);
        });

        const navbar = document.querySelector('.navbar');
        let lastScrollY = window.scrollY;

        window.addEventListener('scroll', () => {
            if (!navbar) {
                return;
            }

            const currentScrollY = window.scrollY;
            const scrollingDown = currentScrollY > lastScrollY && currentScrollY > 90;
            navbar.classList.toggle('nav-hidden', scrollingDown);
            lastScrollY = currentScrollY;
        }, { passive: true });
    });
})();
