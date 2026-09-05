/* Индекс фактов текущего прогона: каждое утверждение отчёта ссылается на факт. */

let factIndex = {};

export function setFacts(blocks) {
  factIndex = {};
  (blocks || []).forEach((block) => (block.facts || []).forEach((fact) => {
    factIndex[fact.id] = fact;
  }));
}

export const factOf = (id) => factIndex[id];
export const valueOf = (id) => (factIndex[id] ? factIndex[id].value : undefined);
