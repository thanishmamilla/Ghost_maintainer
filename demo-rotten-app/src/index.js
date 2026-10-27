/**
 * Public surface of the demo application.
 *
 * Note: `jsonwebtoken` is installed as a dependency but deliberately never imported
 * anywhere in this source tree. It exists to demonstrate the "installed but not
 * observed" reachability case, where a vulnerable package may still be reachable
 * through code the scanner does not understand.
 */
const dates = require("./dates");
const objects = require("./objects");
const args = require("./args");
const http = require("./http");
const versions = require("./versions");

module.exports = { dates, objects, args, http, versions };
