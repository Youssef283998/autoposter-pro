// Character counter for composer
document.addEventListener('DOMContentLoaded', () => {
    const body = document.getElementById('body');
    const counter = document.getElementById('char-count');
    if (body && counter) {
        body.addEventListener('input', () => {
            counter.textContent = body.value.length + ' characters';
        });
    }
});
