/**
 * Version comparison helpers.
 *
 * Uses `semver` 7.8.5, a healthy dependency that exists to show the baseline:
 * recent publish, active repository, no advisories, no major-version lag.
 */
const semver = require("semver");

/** True when `version` satisfies the given range. */
function satisfies(version, range) {
  return semver.satisfies(version, range);
}

/** Compare two versions: -1, 0 or 1. */
function compare(left, right) {
  return semver.compare(left, right);
}

/** True when `version` is at least one major version behind `latest`. */
function isMajorBehind(version, latest) {
  const current = semver.major(version);
  const newest = semver.major(latest);
  return newest > current;
}

module.exports = { satisfies, compare, isMajorBehind };
