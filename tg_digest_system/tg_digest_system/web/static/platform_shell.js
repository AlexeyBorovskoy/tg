(function () {
    function isActive(pathname, href) {
        if (href === '/') return pathname === '/';
        return pathname === href || pathname.startsWith(href + '/');
    }

    function buildShell() {
        var path = window.location.pathname;
        if (path === '/login' || path === '/register') {
            return;
        }
        if (document.querySelector('.platform-shell')) {
            return;
        }

        var links = [
            { href: '/', label: 'Главная' },
            { href: '/setup', label: 'Настройка Telegram' },
            { href: '/channels', label: 'Мои каналы' },
            { href: '/prompts', label: 'Промпты' },
            { href: '/instructions', label: 'Инструкции' },
            { href: '/users', label: 'Пользователи' },
            { href: '/logout', label: 'Выход' }
        ];

        var shell = document.createElement('div');
        shell.className = 'platform-shell';

        var nav = document.createElement('aside');
        nav.className = 'platform-nav';

        var title = document.createElement('div');
        title.className = 'platform-nav-title';
        title.innerHTML = '<span class="dot"></span><span>Платформа TG Digest</span>';
        nav.appendChild(title);

        links.forEach(function (item) {
            var a = document.createElement('a');
            a.className = 'platform-nav-link' + (isActive(path, item.href) ? ' active' : '');
            a.href = item.href;
            a.textContent = item.label;
            nav.appendChild(a);
        });

        var main = document.createElement('div');
        main.className = 'platform-main-wrap';

        while (document.body.firstChild) {
            main.appendChild(document.body.firstChild);
        }

        shell.appendChild(nav);
        shell.appendChild(main);
        document.body.appendChild(shell);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', buildShell);
    } else {
        buildShell();
    }
})();
