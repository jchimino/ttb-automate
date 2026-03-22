/**
 * Regulations page logic
 */

const searchInput = document.getElementById('search-input');

searchInput.addEventListener('input', (e) => {
    const query = e.target.value.toLowerCase();
    const container = document.getElementById('regulations-container');
    const items = container.querySelectorAll('> div');

    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        item.style.display = text.includes(query) ? 'block' : 'none';
    });
});
