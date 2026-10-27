/**
 * Tiny HTTP helper.
 *
 * Uses the deprecated `request` package (npm deprecation notice, last publish 2020).
 *
 * The HTTP client sits behind an injectable parameter, so the transport can be
 * swapped without touching the callers or the tests. Ghost Maintainer's
 * `request` to `axios` migration replaces the client behind that same interface,
 * which is what lets the existing test suite prove the migration did not break
 * anything.
 */
const request = require("request");

/**
 * Perform a GET request and resolve with `{ statusCode, body }`.
 *
 * @param {string} url the URL to request
 * @param {{ get: Function }} [client] an HTTP client exposing `get(url, callback)`
 */
function fetchRaw(url, client = request) {
  return new Promise((resolve, reject) => {
    client.get(url, (error, response, body) => {
      if (error) {
        reject(error);
        return;
      }
      resolve({ statusCode: response ? response.statusCode : 0, body });
    });
  });
}

/** GET a URL and resolve with the parsed JSON body. */
async function getJson(url, client) {
  const { statusCode, body } = await fetchRaw(url, client);
  if (statusCode < 200 || statusCode >= 300) {
    throw new Error(`Request failed with status ${statusCode}`);
  }
  return JSON.parse(body);
}

/** GET a URL and resolve with the raw response body as text. */
async function getText(url, client) {
  const { statusCode, body } = await fetchRaw(url, client);
  if (statusCode < 200 || statusCode >= 300) {
    throw new Error(`Request failed with status ${statusCode}`);
  }
  return body;
}

module.exports = { fetchRaw, getJson, getText };
