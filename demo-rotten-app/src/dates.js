/**
 * Date formatting and arithmetic.
 *
 * Uses `moment`, which is in maintenance mode upstream. Ghost Maintainer is expected
 * to migrate this file to `dayjs` and keep the test suite green.
 */
const moment = require("moment");

const DEFAULT_FORMAT = "YYYY-MM-DD";

/** Format a date-ish value using an explicit moment-compatible format string. */
function formatDate(value, format = DEFAULT_FORMAT) {
  return moment(value).format(format);
}

/** Return the date `days` after `value`, formatted as YYYY-MM-DD. */
function addDays(value, days) {
  return moment(value).add(days, "days").format(DEFAULT_FORMAT);
}

/** Return the date `months` before `value`, formatted as YYYY-MM-DD. */
function subtractMonths(value, months) {
  return moment(value).subtract(months, "months").format(DEFAULT_FORMAT);
}

/** Whole days between two dates, ignoring time-of-day. */
function daysBetween(from, to) {
  const start = moment(from).startOf("day");
  const end = moment(to).startOf("day");
  return end.diff(start, "days");
}

module.exports = { formatDate, addDays, subtractMonths, daysBetween, DEFAULT_FORMAT };
