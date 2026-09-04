/* Лендинг: проверка ИНН и примеры компаний из загруженной выгрузки. */

const form = document.getElementById('search-form');
const input = document.getElementById('company-search');
const errorBox = document.getElementById('error');
const errorText = document.getElementById('error-text');

function showError(text) {
  errorText.textContent = text;
  errorBox.hidden = false;
}

form.addEventListener('submit', (event) => {
  const inn = input.value.trim();
  if (!/^\d{10,12}$/.test(inn)) {
    event.preventDefault();
    showError('ИНН состоит из 10 или 12 цифр');
    return;
  }
  errorBox.hidden = true;
  input.value = inn;
});

input.addEventListener('input', () => { errorBox.hidden = true; });

/* Примеры: берём карточки с заполненными блоками, среди них одну с
   негативными метками и одну без — чтобы на демо было что показать. */
(async function loadExamples() {
  try {
    const resp = await fetch('/api/v1/companies?limit=60&min_filled_blocks=5');
    const rows = await resp.json();
    if (!Array.isArray(rows) || !rows.length) return;
    const withNeg = rows.filter((r) => r.negative_count > 0).slice(0, 2);
    const clean = rows.filter((r) => r.negative_count === 0).slice(0, 1);
    const box = document.getElementById('chips');
    [...withNeg, ...clean].forEach((row) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'demo-chip';
      chip.innerHTML = `${row.short_name || row.inn} <small>${row.inn}</small>`;
      chip.addEventListener('click', () => {
        window.location.href = `/report?inn=${encodeURIComponent(row.inn)}`;
      });
      box.appendChild(chip);
    });
  } catch (e) {
    /* примеры необязательны, лендинг работает и без них */
  }
})();
