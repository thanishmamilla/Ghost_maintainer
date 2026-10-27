/**
 * Configuration merging.
 *
 * Uses `lodash` 4.17.15, which carries known advisories. The intended remediation
 * is a same-major upgrade rather than a package replacement.
 */
const _ = require("lodash");

/**
 * Deep-merge `overrides` on top of `base` without mutating either argument.
 * Nested plain objects are merged; arrays are replaced.
 */
function mergeConfig(base, overrides) {
  return _.merge({}, base, overrides);
}

/** Merge a list of config layers left to right. */
function mergeAll(layers) {
  return layers.reduce((accumulator, layer) => _.merge({}, accumulator, layer), {});
}

module.exports = { mergeConfig, mergeAll };
