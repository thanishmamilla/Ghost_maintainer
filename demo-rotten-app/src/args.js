/**
 * Command-line argument parsing.
 *
 * Uses `minimist` 1.2.0, which has a critical prototype-pollution advisory
 * (GHSA-xvch-5gv4-984h) fixed in 1.2.6.
 */
const minimist = require("minimist");

const BOOLEAN_FLAGS = ["verbose", "help"];
const ALIASES = { v: "verbose", h: "help" };

/**
 * Parse process arguments into a normalised shape.
 * @param {string[]} argv raw arguments, without the node and script entries
 */
function parseArgs(argv) {
  const parsed = minimist(argv, {
    boolean: BOOLEAN_FLAGS,
    alias: ALIASES,
    default: { verbose: false, help: false },
  });

  return {
    verbose: Boolean(parsed.verbose),
    help: Boolean(parsed.help),
    target: typeof parsed.target === "string" ? parsed.target : null,
    flags: Object.keys(parsed)
      .filter((key) => !ALIASES[key] && !BOOLEAN_FLAGS.includes(key) && key !== "_")
      .sort(),
  };
}

/** Read the real process arguments. */
function parseProcessArgs() {
  return parseArgs(process.argv.slice(2));
}

module.exports = { parseArgs, parseProcessArgs, BOOLEAN_FLAGS, ALIASES };
