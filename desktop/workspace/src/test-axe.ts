import axe from 'axe-core';
import {expect} from 'vitest';

/** Fails with the rule ids and offending nodes when axe finds a violation. jsdom has no layout, so colour contrast is skipped. */
export async function expectNoAxeViolations(container: Element) {
  const {violations} = await axe.run(container, {rules: {'color-contrast': {enabled: false}}});
  expect(violations.map(v => `${v.id}: ${v.nodes.map(n => n.html).join(' | ')}`)).toEqual([]);
}
