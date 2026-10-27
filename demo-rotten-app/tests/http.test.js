const { getJson, getText } = require("../src/http");

/**
 * A fake HTTP client with the same `get(url, callback)` interface the module
 * expects. Injecting it keeps the suite deterministic and offline: no request
 * ever leaves the process.
 */
function fakeClient(error, response, body) {
  return {
    get(url, callback) {
      if (error) {
        callback(error);
        return;
      }
      callback(null, response, body);
    },
  };
}

describe("http helper", () => {
  it("resolves with the parsed JSON body on a 200 response", async () => {
    const client = fakeClient(null, { statusCode: 200 }, JSON.stringify({ ok: true }));

    await expect(getJson("https://example.test/data", client)).resolves.toEqual({ ok: true });
  });

  it("rejects when the transport reports an error", async () => {
    const client = fakeClient(new Error("socket hang up"), undefined, undefined);

    await expect(getJson("https://example.test/data", client)).rejects.toThrow("socket hang up");
  });

  it("rejects on a non-2xx status", async () => {
    const client = fakeClient(null, { statusCode: 500 }, "boom");

    await expect(getJson("https://example.test/data", client)).rejects.toThrow("status 500");
  });

  it("resolves with the raw body as text", async () => {
    const client = fakeClient(null, { statusCode: 200 }, "plain text");

    await expect(getText("https://example.test/data", client)).resolves.toBe("plain text");
  });
});