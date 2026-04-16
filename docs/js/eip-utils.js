/**
 * Pure utility functions for EIP data processing.
 * No DOM dependencies -- safe for Deno tests.
 */

import { ALL_FORK_ORDER } from './constants.js';

/**
 * Group EIPs by their introduction fork, sorted by EIP number within each group.
 */
export function getEipsByFork(eipIndex) {
  const byFork = {};
  for (const [num, eip] of Object.entries(eipIndex)) {
    const fork = eip.fork;
    if (!byFork[fork]) byFork[fork] = [];
    byFork[fork].push(eip);
  }
  for (const fork of Object.keys(byFork)) {
    byFork[fork].sort((a, b) => a.number - b.number);
  }
  return byFork;
}

/**
 * Group an EIP's touched items by spec.
 */
export function getItemsBySpec(eip) {
  const bySpec = {};
  for (const item of eip.items) {
    if (!bySpec[item.spec]) bySpec[item.spec] = [];
    bySpec[item.spec].push(item);
  }
  return bySpec;
}

/**
 * Get unique forks from EIP index, ordered by FORK_ORDER.
 */
export function getUniqueForks(eipIndex) {
  const forks = new Set();
  for (const eip of Object.values(eipIndex)) {
    forks.add(eip.fork);
  }
  const ordered = ALL_FORK_ORDER.filter(f => forks.has(f));
  for (const f of forks) {
    if (!ordered.includes(f)) ordered.push(f);
  }
  return ordered;
}
